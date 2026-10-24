"""Read-only daily imports of the four house inputs from Recorder."""

import asyncio
from datetime import UTC, datetime, timedelta
import math
from threading import Event

from .history_import import load_history
from .house_data import solar_state, write_rows
from .source import numeric_state


def query_house_day(hass, settings, start, end, cancel):
    sources = {"room": settings["room_temperature"], "outdoor": settings["house_outdoor_temperature"],
               "heat": settings["house_heat_input"], "solar": settings["house_solar"]}
    states, runs = load_history(hass, list(sources.values()), start, end, cancel)
    events = []
    for key, entity_id in sources.items():
        for state in states.get(entity_id, []):
            value = (solar_state(state) if key == "solar" else
                     numeric_state(state, "heat_input" if key == "heat" else "room_temperature"))
            calibration = state.attributes.get("calibration_signature", "external_unversioned") if key == "heat" else None
            events.append((state.last_updated.timestamp(), 1, key, value, calibration))
    for left, _ in runs:
        if start.timestamp() <= left < end.timestamp():
            events.append((left, 0, "reset", None, None))
    events.sort(key=lambda event: (event[0], event[1]))
    values = dict.fromkeys(sources)
    calibration = "external_unversioned"
    rows = []
    position = 0
    tick = math.ceil(start.timestamp()/60)*60
    previous = tick-60
    while tick < end.timestamp():
        if cancel.is_set():
            raise asyncio.CancelledError
        invalid = set()
        while position < len(events) and events[position][0] <= tick:
            stamp, _, key, value, fingerprint = events[position]
            position += 1
            if key == "reset":
                values = dict.fromkeys(sources)
                calibration = "external_unversioned"
                if stamp > previous:
                    invalid.update(sources)
            else:
                values[key] = value
                if value is None and stamp > previous:
                    invalid.add(key)
                if key == "heat":
                    if fingerprint != calibration and stamp > previous:
                        invalid.add(key)
                    calibration = fingerprint
        row = dict(values) if any(left <= tick < right for left, right in runs) else dict.fromkeys(sources)
        for key in invalid:
            row[key] = None
        rows.append({"time_utc": datetime.fromtimestamp(tick, UTC).isoformat(), **row, "calibration": calibration})
        previous, tick = tick, tick+60
    return rows


async def async_import_house(hass, directory, settings, now, cancel=None):
    from homeassistant.components.recorder import get_instance
    if "recorder" not in hass.config.components:
        raise ValueError("Recorder is not enabled")
    instance = get_instance(hass)
    cancel = cancel or Event()
    end = datetime.fromtimestamp(math.floor(now/60)*60, UTC)
    start = end-timedelta(days=settings["house_learning_days"])
    result = {"status": "completed", "requested_start_utc": start.isoformat(),
              "requested_end_utc": end.isoformat(), "complete_sample_count": 0,
              "first_complete_sample_utc": None, "last_complete_sample_utc": None}
    while start < end:
        if cancel.is_set():
            raise asyncio.CancelledError
        stop = min(datetime.combine(start.date()+timedelta(days=1), datetime.min.time(), UTC), end)
        rows = await instance.async_add_executor_job(query_house_day, hass, settings, start, stop, cancel)
        await hass.async_add_executor_job(write_rows, directory, start.date().isoformat(), rows)
        for row in rows:
            if all(row[key] is not None for key in ("room", "outdoor", "heat", "solar")):
                result["complete_sample_count"] += 1
                result["first_complete_sample_utc"] = result["first_complete_sample_utc"] or row["time_utc"]
                result["last_complete_sample_utc"] = row["time_utc"]
        start = stop
        await asyncio.sleep(0)
    if not result["complete_sample_count"]:
        result["status"] = "no_complete_history"
    return result
