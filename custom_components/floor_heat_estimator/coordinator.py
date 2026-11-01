"""Read source entities, update the model, and record minute samples."""

import csv
import asyncio
from threading import Event
from datetime import timedelta
import logging
from pathlib import Path

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DEFAULTS, DOMAIN, SOURCE_KEYS
from .fitting import source_signature
from .learning import LearningManager
from .house_runtime import HouseManager
from .model import Estimator, Sample, finite_number
from .source import numeric_state

_LOGGER = logging.getLogger(__name__)


def append_record(directory, timestamp, record):
    """Disk I/O runs in an executor, outside the HA event loop."""
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / f"{timestamp.date().isoformat()}.csv"
    new_file = not file.exists() or file.stat().st_size == 0
    with file.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(record))
        if new_file:
            writer.writeheader()
        writer.writerow(record)


class FloorCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=60))
        self.entry = entry
        self.settings = {**DEFAULTS, **entry.data, **entry.options}
        self.configured_settings = self.settings.copy()
        self.engine = Estimator(self.settings)
        self.learning = LearningManager(self)
        self.house = HouseManager(self) if self.settings["house_enabled"] else None
        self.last_recorded_minute = None
        self.restored = False
        self.pending_restore = None
        self.initialization_report = {"status":"waiting"}
        self.initialization_task = None
        self.initialization_cancel = Event()
        self.initialization_attempt = 0
        self.initialization_complete = False

    @callback
    def subscribe(self):
        @callback
        def source_changed(event):
            self.hass.async_create_task(self.async_request_refresh())
        # House inputs are sampled by the existing minute timer. Subscribing to
        # this entry's own exponent output would create a refresh feedback loop
        # while the floor temperature is decaying between measurements.
        self.entry.async_on_unload(async_track_state_change_event(
            self.hass, [self.settings[key] for key in SOURCE_KEYS], source_changed))

    def restore(self, stored):
        if self.restored or self.engine.last_observation is not None or not isinstance(stored, dict):
            return
        expected = {key: self.settings[key] for key in SOURCE_KEYS}
        gates = {key: self.settings[key] for key in ("active_mode", "flow_threshold", "settling_minutes")}
        if stored.get("sources") == expected and stored.get("gates") == gates:
            # Source integrations may still be starting. Keep the snapshot until
            # current driving inputs are valid, rather than immediately losing it.
            self.pending_restore = stored

    def stored_state(self):
        if self.pending_restore is not None:
            return self.pending_restore
        return {"sources": {key: self.settings[key] for key in SOURCE_KEYS},
                "gates": {key: self.settings[key] for key in ("active_mode", "flow_threshold", "settling_minutes")},
                "model_state": self.engine.snapshot()}

    @property
    def parameter_source(self):
        return self.learning.parameter_source

    async def _async_update_data(self):
        now = dt_util.utcnow()
        states = {key: self.hass.states.get(self.settings[key]) for key in SOURCE_KEYS}
        numeric = {key: numeric_state(state, key) for key, state in states.items() if key != "mode"}
        mode = states["mode"].state if states["mode"] else None
        sample = Sample(now.timestamp(), numeric["room_temperature"], numeric["heat_input"],
                        numeric["supply_temperature"], numeric["return_temperature"], numeric["flow"], mode)
        if (self.pending_restore is not None and sample.room is not None
                and mode not in (None, "", "unknown", "unavailable")
                and (mode != self.settings["active_mode"] or sample.power is not None)):
            stored = self.pending_restore.get("model_state", {})
            previous_time = finite_number((stored.get("previous") or {}).get("timestamp"))
            if previous_time is not None and previous_time <= sample.timestamp:
                self.restored = self.engine.restore(stored)
            self.pending_restore = None
        data = self.engine.step(sample)
        if data['measurement_valid'] and data['nonlinear_power'] is not None:
            self.initialization_complete=True
            self.initialization_report={'status':'live_observation','measurement_timestamp':sample.timestamp}
        if data['nonlinear_power'] is None:self.initialization_complete=False
        if not self.initialization_complete and (self.initialization_task is None or self.initialization_task.done()) and now.timestamp()-self.initialization_attempt > 300:
            self.initialization_attempt = now.timestamp()
            self.initialization_task = self.entry.async_create_background_task(
                self.hass,self._initialize_floor(now.timestamp()),f"{DOMAIN} floor history initialization")
        if not self.initialization_complete:
            for key in ('linear_power','nonlinear_power','linear_temperature','nonlinear_temperature'):data[key]=None
            data['status']='initialization_incomplete'
        minute = int(now.timestamp() // 60)
        if self.settings["record_samples"] and minute != self.last_recorded_minute:
            record = {"time_utc": now.isoformat(), **numeric, "mode": mode, **data}
            directory = self.hass.config.path(DOMAIN, self.entry.entry_id, "learning", source_signature(self.settings))
            try:
                await self.hass.async_add_executor_job(append_record, directory, now, record)
            except OSError:
                _LOGGER.exception("Could not write floor heat training sample")
            else:
                self.last_recorded_minute = minute
        if self.house:
            try:
                await self.house.async_update(now, data)
            except Exception:
                _LOGGER.exception("House update failed; floor estimation continues")
        self.learning.maybe_schedule(now.timestamp())
        return data

    async def _initialize_floor(self, timestamp):
        from .initialization import load_floor
        directory = self.hass.config.path(DOMAIN,self.entry.entry_id)
        settings = self.settings.copy()
        self.initialization_report = {"status":"loading_history"}
        try:
            try:
                from .history_import import async_import_history
                import_settings={**settings,'learning_days':min(settings['learning_days'],max(2,settings['maximum_prediction_hours']/24+1))}
                await async_import_history(self.hass,directory,import_settings,timestamp,self.initialization_cancel)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning('Recorder recovery import failed; trying retained local samples: %s',err)
            replay_now=max(timestamp,self.engine.previous.timestamp if self.engine.previous else timestamp)
            engine,report = await self.hass.async_add_executor_job(load_floor,directory,settings,replay_now)
            self.initialization_report = report
            # Never replace a newer live observation or a changed calibration.
            if engine is not None and settings == self.settings and (self.engine.last_observation is None or self.engine.last_observation <= engine.last_observation):
                current = self.engine.previous
                if current and current.timestamp >= engine.previous.timestamp:
                    if current.timestamp-engine.previous.timestamp > settings['maximum_gap_minutes']*60:
                        self.initialization_report = {'status':'initialization_incomplete','reason':'New inputs arrived too late to bridge replay'}
                        return
                    engine.step(current)
                    # Invalid live inputs must not replace a successful historical replay.
                    if engine._input(current) is None or current.room is None:
                        self.initialization_report={**report,'status':'initialization_incomplete','reason':'Current live inputs have not recovered yet'}
                        return
                self.engine = engine
                self.initialization_complete=True
                self.pending_restore = None
                await self.async_request_refresh()
            elif engine is None:
                self.async_update_listeners()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self.initialization_report = {'status':'initialization_incomplete','reason':str(err)}
            _LOGGER.exception('Floor initialization failed')

    async def async_shutdown(self):
        self.initialization_cancel.set()
        if self.initialization_task and not self.initialization_task.done():
            self.initialization_task.cancel()
            try:
                await self.initialization_task
            except asyncio.CancelledError:
                pass

