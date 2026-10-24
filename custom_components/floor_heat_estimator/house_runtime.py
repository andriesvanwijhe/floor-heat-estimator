"""House state lifecycle, persistence, recording and isolated fitting process."""

import asyncio
from collections import deque
from datetime import UTC, datetime
import json
import logging
import math
import os
from pathlib import Path
import sys
from threading import Event

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .house_const import HOUSE_KEYS
from .house_data import (append_row, floor_signature, house_signature, make_bins,
                         prepare_fit_request, settings_signature, solar_state)
from .house_model import HouseEstimator, HouseParameters
from .source import numeric_state

_LOGGER = logging.getLogger(__name__)


def configured_parameters(settings):
    return HouseParameters(**{key.removeprefix("house_"): settings[key] for key in HOUSE_KEYS})


def valid_parameters(values):
    if not isinstance(values, dict) or set(values) != set(HouseParameters().to_dict()):
        return False
    try:
        p = HouseParameters(**values)
        return (.02 <= p.ci <= 1000 and .02 <= p.ce <= 1000 and .01 <= p.rie <= 100
                and .01 <= p.rea <= 100 and .001 <= p.solar_area <= 500
                and .0001 <= p.sigma_i <= .5 and .0001 <= p.sigma_e <= .5 and .01 <= p.sigma_y <= .5)
    except (TypeError, ValueError):
        return False


async def run_worker(request, maximum_seconds):
    """A separate process avoids Python filter loops competing for HA's GIL."""
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
           "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}
    # HA can install integration requirements in /config/deps and add that
    # directory to sys.path rather than the system environment. The isolated
    # worker must see the same dependency paths as the running integration.
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(path for path in sys.path if isinstance(path, str) and path))
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(Path(__file__).with_name("house_fit.py")),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=env)
    try:
        payload = json.dumps(request, allow_nan=False).encode()
        stdout, stderr = await asyncio.wait_for(process.communicate(payload), maximum_seconds)
        if process.returncode:
            raise ValueError("House fitting process exited unsuccessfully: "+stderr.decode(errors="replace")[-500:])
        result = json.loads(stdout)
        if not isinstance(result, dict):
            raise ValueError("Invalid house fitting response")
        return result
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()


class HouseManager:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.hass, self.entry = coordinator.hass, coordinator.entry
        self.signature = settings_signature(coordinator.settings)
        self.store = Store(self.hass, 1, f"{DOMAIN}.house.{self.entry.entry_id}", atomic_writes=True)
        self.state_store = Store(self.hass, 1, f"{DOMAIN}.house_state.{self.entry.entry_id}", atomic_writes=True)
        self.engine = HouseEstimator(configured_parameters(coordinator.settings))
        self.active_parameters = None
        self.active_intervals = {}
        self.calibrated_floor = None
        self.last_attempt_floor = None
        self.report = {"status": "waiting", "reason": "No house fit attempted"}
        self.history_import = None
        self.last_attempt = self.last_accepted = None
        self.started = datetime.now(UTC).timestamp()
        self.task = None
        self.cancel = Event()
        self.busy = False
        self.importing = False
        self.minute = None
        self.rows = deque(maxlen=3000)
        self.own_heat_source = False

    @property
    def settings(self):
        return self.coordinator.settings

    @property
    def folder(self):
        return self.hass.config.path(DOMAIN, self.entry.entry_id, "house", house_signature(self.settings))

    @property
    def parameter_source(self):
        return "learned_fit" if self.active_parameters is not None else "manual_configuration"

    @property
    def status(self):
        return "importing_history" if self.importing else "fitting" if self.busy else self.report.get("status", "waiting")

    @property
    def needs_import(self):
        return self.settings["house_import_history_on_start"] and self.history_import is None

    @property
    def next_refit(self):
        if not self.settings["house_learning_enabled"] or not self.settings["record_samples"]:
            return None
        if self.needs_import:
            return self.started+120
        if self.own_heat_source and self.last_attempt_floor != floor_signature(self.settings):
            return self.started+120
        return self.last_attempt+self.settings["house_refit_interval_days"]*86400 if self.last_attempt else self.started+120

    def data(self):
        p = self.engine.parameters
        data = {"house_fit_status": self.status, "house_ci": p.ci, "house_ce": p.ce,
                "house_rie": p.rie, "house_rea": p.rea, "house_solar_area": p.solar_area,
                "house_hie": 1000/p.rie, "house_hea": 1000/p.rea,
                "house_heat_loss_coefficient": 1000/(p.rie+p.rea),
                "house_last_attempt": self.last_attempt, "house_last_accepted": self.last_accepted,
                "house_next_refit": self.next_refit,
                "house_point_count": self.report.get("observation_count")}
        for horizon in (1, 6, 24):
            data[f"house_validation_{horizon}h"] = self.report.get("validation", {}).get(str(horizon), {}).get("rmse_K")
        state = self.engine.state
        if state is not None:
            data.update(house_interior_temperature=state[0], house_envelope_temperature=state[1],
                        house_envelope_uncertainty=math.sqrt(self.engine.covariance[2]),
                        house_interior_envelope_flow=(state[0]-state[1])/p.rie)
            if self.rows and self.rows[-1]["outdoor"] is not None:
                data["house_outdoor_heat_flow"] = (state[1]-self.rows[-1]["outdoor"])/p.rea
        if self.rows and self.rows[-1]["solar"] is not None:
            data["house_solar_gain"] = p.solar_area*self.rows[-1]["solar"]
        return data

    async def async_load(self):
        from homeassistant.helpers import entity_registry as er
        registry = er.async_get(self.hass)
        own_entity = registry.async_get_entity_id("sensor", DOMAIN, f"{self.entry.entry_id}_nonlinear_power")
        self.own_heat_source = own_entity == self.settings["house_heat_input"]
        saved = await self.store.async_load()
        if isinstance(saved, dict):
            imported = saved.get("history_import")
            if isinstance(imported, dict) and imported.get("source_signature") == house_signature(self.settings):
                self.history_import = imported
            if saved.get("signature") == self.signature:
                for key in ("last_attempt", "last_accepted"):
                    value = saved.get(key)
                    if isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= self.started+300:
                        setattr(self, key, value)
                self.report = saved.get("report") if isinstance(saved.get("report"), dict) else self.report
                self.last_attempt_floor = saved.get("last_attempt_floor")
                if valid_parameters(saved.get("active_parameters")):
                    self.active_parameters = saved["active_parameters"]
                    self.calibrated_floor = saved.get("calibrated_floor")
                    self.engine = HouseEstimator(HouseParameters(**self.active_parameters))
                    if isinstance(saved.get("active_intervals"), dict):
                        self.active_intervals = {key: interval for key, interval in saved["active_intervals"].items()
                            if key in ("ci", "ce", "rie", "rea", "solar_area") and isinstance(interval, dict)
                            and all(isinstance(interval.get(k), (int, float)) and math.isfinite(interval[k])
                                    and interval[k] > 0 for k in ("lower_95", "upper_95"))}
        state = await self.state_store.async_load()
        if (isinstance(state, dict) and state.get("signature") == self.signature
                and state.get("parameters") == self.engine.parameters.to_dict()
                and state.get("floor") == (floor_signature(self.settings) if self.own_heat_source else None)):
            snapshot = state.get("snapshot")
            if isinstance(snapshot, dict) and isinstance(snapshot.get("timestamp"), (int, float)):
                if 0 <= self.started-snapshot["timestamp"] <= 600:
                    self.engine.restore(snapshot)

    async def _save(self, active, calibrated_floor, intervals=None):
        data = {"signature": self.signature, "active_parameters": active,
                "calibrated_floor": calibrated_floor, "last_attempt_floor": self.last_attempt_floor,
                "last_attempt": self.last_attempt, "last_accepted": self.last_accepted,
                "history_import": self.history_import, "report": self.report}
        data["active_intervals"] = (self.active_intervals if intervals is None else intervals) if active is not None else {}
        await self.store.async_save(data)
        if await self.store.async_load() != data:
            raise OSError("Could not verify persisted house coefficients")

    async def save_state(self):
        await self.state_store.async_save({"signature": self.signature,
            "parameters": self.engine.parameters.to_dict(), "snapshot": self.engine.snapshot(),
            "floor": floor_signature(self.settings) if self.own_heat_source else None})

    async def async_update(self, now, floor_data=None):
        minute = int(now.timestamp()//60)
        if minute == self.minute:
            return
        self.minute = minute
        states = {name: self.hass.states.get(self.settings[key]) for name, key in (
            ("room", "room_temperature"), ("outdoor", "house_outdoor_temperature"),
            ("heat", "house_heat_input"), ("solar", "house_solar"))}
        heat_state = states["heat"]
        calibration = (floor_signature(self.settings) if self.own_heat_source else
                       (heat_state.attributes.get("calibration_signature", "external_unversioned") if heat_state else "unknown"))
        row = {"t": now.timestamp(), "room": numeric_state(states["room"], "room_temperature"),
               "outdoor": numeric_state(states["outdoor"], "room_temperature"),
               "heat": numeric_state(heat_state, "heat_input"), "solar": solar_state(states["solar"]),
               "calibration": calibration}
        if self.own_heat_source and floor_data is not None:
            row["heat"] = floor_data.get("nonlinear_power")
        self.rows.append(row)
        if self.settings["record_samples"]:
            recorded = {"time_utc": now.isoformat(), **{key: row[key] for key in ("room", "outdoor", "heat", "solar", "calibration")}}
            try:
                await self.hass.async_add_executor_job(append_row, self.folder, recorded)
            except OSError:
                _LOGGER.exception("Could not record house sample")
        # Only a small recent buffer is binned on the event loop. Fitting and
        # full-history binning use executor/process workers.
        bins = make_bins(list(self.rows)[-30:], self.settings["house_max_gap_minutes"]*60, now.timestamp())
        for point in bins:
            self.engine.step(point)
        if (self.engine.timestamp is not None
                and now.timestamp()-self.engine.timestamp > self.settings["house_max_gap_minutes"]*60):
            self.engine.state = self.engine.covariance = self.engine.timestamp = None
        if minute % 15 == 0:
            await self.save_state()
        due = self.next_refit
        if due is not None and now.timestamp() >= due and not self.busy:
            self.start_fit(import_history=self.needs_import)

    def start_fit(self, import_history=False, apply=None):
        if self.busy:
            raise HomeAssistantError("A house fit is already queued or running")
        self.busy = True
        self.cancel.clear()
        self.task = self.entry.async_create_background_task(
            self.hass, self._fit(import_history, apply), f"{DOMAIN} house fit {self.entry.entry_id}")
        self.coordinator.async_update_listeners()

    async def _fit(self, import_history, apply):
        began = datetime.now(UTC).timestamp()
        self.last_attempt = began
        try:
            self.report = {"status": "interrupted", "reason": "House fit did not finish before shutdown"}
            await self._save(self.active_parameters, self.calibrated_floor)
            lock = self.hass.data.setdefault(f"{DOMAIN}_fit_lock", asyncio.Lock())
            async with lock:
                settings = self.settings.copy()
                snapshot_floor = floor_signature(settings) if self.own_heat_source else None
                self.last_attempt_floor = snapshot_floor
                floor_folder = self.hass.config.path(DOMAIN, self.entry.entry_id)
                if import_history:
                    from .house_history import async_import_house
                    self.importing = True
                    self.coordinator.async_update_listeners()
                    try:
                        floor_report = None
                        if self.own_heat_source:
                            from .history_import import async_import_history
                            floor_report = await async_import_history(self.hass, floor_folder,
                                {**settings, "learning_days": settings["house_learning_days"]}, began, self.cancel)
                        self.history_import = await async_import_house(self.hass, str(Path(self.folder)/"imported"), settings, began, self.cancel)
                        self.history_import["floor_history"] = floor_report
                    except Exception as err:
                        _LOGGER.exception("House history import failed; using available local samples")
                        self.history_import = {"status": "error", "reason": str(err)}
                    finally:
                        self.importing = False
                    self.history_import["source_signature"] = house_signature(settings)
                    await self._save(self.active_parameters, self.calibrated_floor)
                    self.coordinator.async_update_listeners()
                request = await self.hass.async_add_executor_job(
                    prepare_fit_request, self.folder, floor_folder, settings, began, self.own_heat_source)
                request.update(baseline=self.engine.parameters.to_dict(),
                    maximum_rmse=settings["house_max_fit_rmse"], max_seconds=settings["house_max_fit_minutes"]*60)
                self.report = await run_worker(request, settings["house_max_fit_minutes"]*60+5)
                self.report.update(input_provenance=request["input_provenance"],
                    minute_sample_count=request["minute_sample_count"], skipped_rows=request["skipped_rows"],
                    source_coverage_minutes=request.get("source_coverage_minutes"),
                    first_usable_bin_utc=request.get("first_usable_bin_utc"),
                    last_usable_bin_utc=request.get("last_usable_bin_utc"),
                    elapsed_total_seconds=round(datetime.now(UTC).timestamp()-began, 3))
                should_apply = settings["house_apply_fitted_parameters"] if apply is None else apply
                if self.own_heat_source and floor_signature(self.settings) != snapshot_floor:
                    self.report.update(status="superseded", accepted=False, reason="Floor calibration changed while fitting; retry with the current calibration")
                if self.report.get("accepted") and should_apply:
                    values = self.report.get("parameters")
                    if not valid_parameters(values):
                        raise ValueError("Invalid fitted house parameters")
                    engine = HouseEstimator(HouseParameters(**values))
                    if not engine.restore(self.report.get("final_state")):
                        raise ValueError("Invalid fitted house state")
                    for point in make_bins(list(self.rows), settings["house_max_gap_minutes"]*60):
                        engine.step(point)
                    previous_accepted = self.last_accepted
                    self.last_accepted = datetime.now(UTC).timestamp()
                    self.report.update(status="applied", applied=True)
                    try:
                        await self._save(values, snapshot_floor, self.report.get("approximate_confidence_intervals", {}))
                    except Exception:
                        self.last_accepted = previous_accepted
                        raise
                    self.active_parameters, self.calibrated_floor, self.engine = values, snapshot_floor, engine
                    self.active_intervals = self.report.get("approximate_confidence_intervals", {})
                    await self.save_state()
                else:
                    self.report["applied"] = False
                    await self._save(self.active_parameters, self.calibrated_floor)
        except TimeoutError:
            self.report = {"status": "timeout", "reason": "House fitting process exceeded the time limit", "applied": False}
            await self._save(self.active_parameters, self.calibrated_floor)
        except asyncio.CancelledError:
            self.cancel.set()
            raise
        except Exception as err:
            _LOGGER.exception("House fitting failed")
            self.report = {"status": "error", "reason": str(err), "applied": False}
            try:
                await self._save(self.active_parameters, self.calibrated_floor)
            except Exception:
                _LOGGER.exception("Could not persist house fit diagnostics")
        finally:
            self.busy = self.importing = False
            self.coordinator.async_update_listeners()

    async def async_reset(self):
        if self.busy:
            raise HomeAssistantError("Wait for the current house fit to finish")
        old_report = self.report
        self.report = {"status": "manual_parameters", "reason": "Restored configured manual house coefficients", "applied": False}
        try:
            await self._save(None, None)
        except Exception:
            self.report = old_report
            raise
        self.active_parameters = self.calibrated_floor = None
        self.active_intervals = {}
        self.engine = HouseEstimator(configured_parameters(self.settings))
        self.coordinator.async_update_listeners()

    async def async_shutdown(self):
        self.cancel.set()
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        await self.save_state()
