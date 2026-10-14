"""Read source entities, update the model, and record minute samples."""

import csv
from datetime import timedelta
import logging
from pathlib import Path

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DEFAULTS, DOMAIN, SOURCE_KEYS
from .model import Estimator, Sample, finite_number

_LOGGER = logging.getLogger(__name__)


def numeric_state(state, kind):
    if state is None:
        return None
    number = finite_number(state.state)
    if number is None:
        return None
    unit = str(state.attributes.get("unit_of_measurement") or "").strip().lower()
    if kind == "heat_input":
        if unit == "w":
            return number / 1000
        return number if unit == "kw" else None
    if kind == "flow":
        if unit in ("l/h", "l/hr", "l/hour"):
            return number
        if unit in ("l/min", "l/m"):
            return number * 60
        if unit in ("m³/h", "m3/h"):
            return number * 1000
        return None
    if unit in ("°f", "f"):
        return (number - 32) * 5 / 9
    return number if unit in ("°c", "c", "") else None


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
        self.engine = Estimator(self.settings)
        self.last_recorded_minute = None
        self.restored = False
        self.pending_restore = None

    @callback
    def subscribe(self):
        @callback
        def source_changed(event):
            self.hass.async_create_task(self.async_request_refresh())
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
        minute = int(now.timestamp() // 60)
        if self.settings["record_samples"] and minute != self.last_recorded_minute:
            record = {"time_utc": now.isoformat(), **numeric, "mode": mode, **data}
            directory = self.hass.config.path(DOMAIN, self.entry.entry_id)
            try:
                await self.hass.async_add_executor_job(append_record, directory, now, record)
            except OSError:
                _LOGGER.exception("Could not write floor heat training sample")
            else:
                self.last_recorded_minute = minute
        return data

