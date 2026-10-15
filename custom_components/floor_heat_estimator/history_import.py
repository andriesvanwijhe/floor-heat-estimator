"""Import detailed Recorder history in daily batches, outside the event loop."""

import asyncio
import csv
from datetime import UTC, datetime, timedelta
import math
import os
from pathlib import Path

from .const import SOURCE_KEYS
from .fitting import source_signature
from .source import numeric_state


def reconstruct(history, settings, start, end, runs, cancel):
    """Hold recorded states on a minute grid; never interpolate missing states.

    Recorder stores changes, so an unchanged room/mode value is legitimate.
    Clean recording-run boundaries prevent holding states across HA downtime.
    Sub-minute gate/availability interruptions invalidate the following tick.
    """
    events = []
    for key in SOURCE_KEYS:
        for state in history.get(settings[key], []):
            stamp = state.last_updated.timestamp()
            value = state.state if key == "mode" else numeric_state(state, key)
            if key == "mode" and value in ("unknown", "unavailable", ""):
                value = None
            events.append((stamp, 1, key, value))
    for run_start, run_end in runs:
        if start <= run_start < end:
            events.append((run_start, 0, "reset", None))
    events.sort(key=lambda event: (event[0], event[1]))
    values = dict.fromkeys(SOURCE_KEYS)
    position = 0
    rows = []
    tick = math.ceil(start / 60) * 60
    previous_tick = tick - 60
    while tick < end:
        if cancel.is_set():
            raise asyncio.CancelledError
        invalid = set()
        gate_interrupted = False
        while position < len(events) and events[position][0] <= tick:
            stamp, _, key, value = events[position]
            position += 1
            if key == "reset":
                values = dict.fromkeys(SOURCE_KEYS)
                if stamp > previous_tick:
                    gate_interrupted = True
                continue
            old = values[key]
            values[key] = value
            if stamp > previous_tick:
                if value is None:
                    invalid.add(key)
                if key == "mode" and old is not None and old != value:
                    gate_interrupted = True
                if key == "flow" and old is not None and ((old > settings["flow_threshold"])
                                      != (value is not None and value > settings["flow_threshold"])):
                    gate_interrupted = True
        # Closed-incorrectly runs have no trustworthy stop time and are omitted
        # by the query. Keep missing rows to break fitting traces explicitly.
        covered = any(left <= tick < right for left, right in runs)
        row = dict(values) if covered else dict.fromkeys(SOURCE_KEYS)
        for key in invalid:
            row[key] = None
        if gate_interrupted:
            row["mode"] = None
        rows.append({"time_utc": datetime.fromtimestamp(tick, UTC).isoformat(), **row})
        previous_tick = tick
        tick += 60
    return rows


def query_day(hass, settings, start, end, cancel):
    """Use Recorder's database executor and history API with historical units."""
    from sqlalchemy import select
    from homeassistant.components.recorder import get_instance, history
    from homeassistant.components.recorder.db_schema import RecorderRuns
    from homeassistant.components.recorder.util import session_scope

    if cancel.is_set():
        raise asyncio.CancelledError
    states = history.get_significant_states(
        hass, start, end, entity_ids=[settings[key] for key in SOURCE_KEYS],
        include_start_time_state=True, significant_changes_only=False,
        minimal_response=False, no_attributes=False)
    # Read-only ORM access, never SQLite-specific SQL or direct database files.
    current_start = get_instance(hass).recorder_runs_manager.recording_start.timestamp()
    with session_scope(hass=hass, read_only=True) as session:
        recorded_runs = session.execute(select(RecorderRuns).where(
            RecorderRuns.start < end,
            (RecorderRuns.end > start) | RecorderRuns.end.is_(None))).scalars()
        runs = []
        for run in recorded_runs:
            left = run.start.replace(tzinfo=UTC).timestamp()
            if run.closed_incorrect:
                continue
            # An open historic run is not proof of continuous recording.
            if run.end is None and abs(left - current_start) > 1:
                continue
            right = run.end.replace(tzinfo=UTC).timestamp() if run.end else end.timestamp()
            runs.append((left, right))
    return reconstruct(states, settings, start.timestamp(), end.timestamp(), runs, cancel)


def write_day(directory, settings, day, rows, cancel):
    """Imported files are separate from live recording and atomically replaced."""
    if cancel.is_set():
        raise asyncio.CancelledError
    folder = Path(directory) / "learning" / source_signature(settings) / "imported"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{day}.csv"
    temporary = target.with_suffix(".tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["time_utc", *SOURCE_KEYS])
            writer.writeheader()
            writer.writerows(rows)
        if cancel.is_set():
            raise asyncio.CancelledError
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


async def async_import_history(hass, directory, settings, now, cancel):
    """Bounded daily reads, followed by ordinary executor CSV writes."""
    from homeassistant.components.recorder import get_instance

    if "recorder" not in hass.config.components:
        raise ValueError("Recorder is not enabled; existing local recordings remain usable")
    instance = get_instance(hass)
    end = datetime.fromtimestamp(math.floor(now / 60) * 60, UTC)
    start = end - timedelta(days=settings["learning_days"])
    result = {"status": "completed", "source_signature": source_signature(settings),
              "requested_start_utc": start.isoformat(), "requested_end_utc": end.isoformat(),
              "sample_count": 0, "minute_count": 0, "complete_sample_count": 0,
              "first_sample_utc": None, "last_sample_utc": None,
              "missing_entities": []}
    seen = set()
    while start < end:
        if cancel.is_set():
            raise asyncio.CancelledError
        stop = min(datetime.combine(start.date() + timedelta(days=1), datetime.min.time(), UTC), end)
        rows = await instance.async_add_executor_job(query_day, hass, settings, start, stop, cancel)
        await hass.async_add_executor_job(write_day, directory, settings, start.date().isoformat(), rows, cancel)
        result["minute_count"] += len(rows)
        for row in rows:
            if any(row[key] is not None for key in SOURCE_KEYS):
                result["sample_count"] += 1
            seen.update(key for key in SOURCE_KEYS if row[key] is not None)
            if all(row[key] is not None for key in SOURCE_KEYS):
                result["complete_sample_count"] += 1
                result["first_sample_utc"] = result["first_sample_utc"] or row["time_utc"]
                result["last_sample_utc"] = row["time_utc"]
        start = stop
    result["missing_entities"] = [settings[key] for key in SOURCE_KEYS if key not in seen]
    if not result["complete_sample_count"]:
        result["status"] = "no_usable_history"
    return result
