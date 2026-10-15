"""Persistent fit lifecycle and background scheduling for a config entry."""

import asyncio
from datetime import UTC, datetime
import logging
from threading import Event

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import DOMAIN, FIT_KEYS
from .fitting import configuration_signature, fit_recordings, source_signature
from .model import Parameters, finite_number

_LOGGER = logging.getLogger(__name__)


def valid_parameters(values):
    if not isinstance(values, dict) or set(values) != set(FIT_KEYS):
        return False
    limits = {"linear_capacity": (0.01, 100), "linear_conductance": (0.001, 10),
              "nonlinear_capacity": (0.01, 100), "nonlinear_output_at_5k": (0.001, 100),
              "exponent": (0.5, 3)}
    return all(isinstance(values.get(key), (int, float)) and finite_number(values.get(key)) is not None
               and low <= values[key] <= high
               for key, (low, high) in limits.items())


class LearningManager:
    """One queued/running fit per entry, one numerical worker per integration."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.hass = coordinator.hass
        self.entry = coordinator.entry
        self.store = Store(self.hass, 1, f"{DOMAIN}.learning.{self.entry.entry_id}", atomic_writes=True)
        self.signature = configuration_signature(coordinator.settings)
        self.active_parameters = None
        self.history_import = None
        self.importing = False
        self.last_attempt = None
        self.last_accepted = None
        self.report = {"status": "waiting", "reason": "No fit attempted"}
        self.task = None
        self.cancel = Event()
        self.started = datetime.now(UTC).timestamp()
        self.busy = False

    async def async_load(self):
        saved = await self.store.async_load()
        if isinstance(saved, dict):
            imported = saved.get("history_import")
            if isinstance(imported, dict) and imported.get("source_signature") == source_signature(self.coordinator.settings):
                self.history_import = imported
        if not isinstance(saved, dict) or saved.get("signature") != self.signature:
            return
        for key in ("last_attempt", "last_accepted"):
            stamp = finite_number(saved.get(key))
            setattr(self, key, stamp if stamp is not None and 0 <= stamp <= self.started + 300 else None)
        if isinstance(saved.get("report"), dict):
            self.report = saved["report"]
        if valid_parameters(saved.get("active_parameters")):
            self.active_parameters = saved["active_parameters"]
            self._activate(self.active_parameters)

    @property
    def parameter_source(self):
        return "learned_fit" if self.active_parameters is not None else "manual_configuration"

    @property
    def status(self):
        return "importing_history" if self.importing else "fitting" if self.busy else self.report.get("status", "waiting")

    @property
    def next_refit(self):
        settings = self.coordinator.settings
        if not settings["learning_enabled"] or not settings["record_samples"]:
            return None
        if self.needs_history_import:
            return self.started + 60
        return (self.last_attempt + settings["refit_interval_days"] * 86400
                if self.last_attempt is not None else self.started + 60)

    @property
    def needs_history_import(self):
        return self.coordinator.settings["import_history_on_start"] and self.history_import is None

    def maybe_schedule(self, timestamp):
        due = self.next_refit
        if due is not None and timestamp >= due and not self.busy:
            if self.needs_history_import:
                self.start_fit(import_history=True)
            else:
                self.start_fit()

    def start_fit(self, include_legacy=False, apply=None, import_history=False):
        if self.busy:
            raise HomeAssistantError("A fit for this configuration is already queued or running")
        self.busy = True
        self.cancel.clear()
        self.task = self.entry.async_create_background_task(
            self.hass, self._async_fit(include_legacy, apply, import_history), f"{DOMAIN} fit {self.entry.entry_id}")
        self.coordinator.async_update_listeners()

    async def _save(self, active_parameters):
        saved = {"signature": self.signature, "active_parameters": active_parameters,
                 "last_attempt": self.last_attempt, "last_accepted": self.last_accepted, "report": self.report,
                 "history_import": self.history_import}
        await self.store.async_save(saved)
        # HA Store logs some filesystem errors instead of raising them. Read
        # back the invalidated storage cache before activating new coefficients.
        if await self.store.async_load() != saved:
            raise OSError("Could not verify persisted learning state")

    def _activate(self, parameters):
        self.coordinator.settings.update(parameters)
        settings = self.coordinator.settings
        self.coordinator.engine.parameters = {
            "linear": Parameters(settings["linear_capacity"], 5 * settings["linear_conductance"]),
            "nonlinear": Parameters(settings["nonlinear_capacity"], settings["nonlinear_output_at_5k"], settings["exponent"]),
        }

    async def _async_fit(self, include_legacy, apply, import_history=False):
        self.last_attempt = datetime.now(UTC).timestamp()
        try:
            # Failed attempts also survive restart and respect the refit interval.
            self.report = {"status": "interrupted", "reason": "Fit did not finish before shutdown"}
            await self._save(self.active_parameters)
            lock = self.hass.data.setdefault(f"{DOMAIN}_fit_lock", asyncio.Lock())
            async with lock:
                settings = self.coordinator.settings.copy()
                directory = self.hass.config.path(DOMAIN, self.entry.entry_id)
                if import_history:
                    from .history_import import async_import_history
                    self.importing = True
                    self.coordinator.async_update_listeners()
                    try:
                        self.history_import = await async_import_history(
                            self.hass, directory, settings, self.last_attempt, self.cancel)
                    except Exception as err:
                        _LOGGER.exception("Could not import Recorder history; fitting existing local recordings")
                        self.history_import = {"status": "error", "reason": str(err),
                                               "source_signature": source_signature(settings)}
                    finally:
                        self.importing = False
                        self.coordinator.async_update_listeners()
                    await self._save(self.active_parameters)
                self.report = await self.hass.async_add_executor_job(
                    fit_recordings, directory, settings, self.last_attempt, self.cancel, include_legacy)
                if self.history_import is not None:
                    self.report["history_import"] = self.history_import
            should_apply = settings["apply_fitted_parameters"] if apply is None else apply
            if self.report.get("accepted") and should_apply:
                parameters = self.report["parameters"]
                if not valid_parameters(parameters):
                    raise ValueError("Fitter returned invalid parameters")
                self.report.update(status="applied", applied=True)
                previous_accepted = self.last_accepted
                self.last_accepted = datetime.now(UTC).timestamp()
                try:
                    await self._save(parameters)
                except Exception:
                    self.last_accepted = previous_accepted
                    raise
                # Commit to the live models only after persistence succeeds.
                self.active_parameters = parameters
                self._activate(parameters)
                await self.coordinator.async_request_refresh()
            else:
                self.report["applied"] = False
                await self._save(self.active_parameters)
        except asyncio.CancelledError:
            self.cancel.set()
            raise
        except Exception as err:
            _LOGGER.exception("Floor heat fitting failed")
            self.report = {"status": "error", "applied": False, "reason": str(err)}
            try:
                await self._save(self.active_parameters)
            except Exception:
                _LOGGER.exception("Could not persist floor heat fit diagnostics")
        finally:
            self.busy = False
            self.importing = False
            self.coordinator.async_update_listeners()

    async def async_reset(self):
        if self.busy:
            raise HomeAssistantError("Wait for the current fit to finish before resetting parameters")
        previous_report = self.report
        self.report = {"status": "manual_parameters", "applied": False,
                       "reason": "Restored configured manual coefficients"}
        try:
            await self._save(None)
        except Exception:
            self.report = previous_report
            raise
        self.active_parameters = None
        self._activate({key: self.coordinator.configured_settings[key] for key in FIT_KEYS})
        await self.coordinator.async_request_refresh()

    async def async_shutdown(self):
        self.cancel.set()
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
