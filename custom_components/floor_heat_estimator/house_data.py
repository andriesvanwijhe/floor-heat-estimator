"""House source normalization, minute storage, and energy-preserving bins."""

import csv
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from .const import FIT_KEYS, SOURCE_KEYS
from .fitting import read_samples, source_signature
from .house_const import HOUSE_KEYS, HOUSE_SOURCES
from .model import Estimator, finite_number
from .source import numeric_state

FIELDS = ("time_utc", "room", "outdoor", "heat", "solar", "calibration")


def solar_state(state):
    if state is None:
        return None
    value = finite_number(state.state)
    if value is None or value < 0:
        return None
    unit = str(state.attributes.get("unit_of_measurement") or "").lower().replace(" ", "")
    if unit in ("w/m²", "w/m2", "w/m^2"):
        return value/1000
    return value if unit in ("kw/m²", "kw/m2", "kw/m^2") else None


def house_signature(settings):
    keys = (*HOUSE_SOURCES, "room_temperature", "house_max_gap_minutes", *SOURCE_KEYS,
            "active_mode", "flow_threshold", "settling_minutes", "maximum_gap_minutes")
    encoded = json.dumps({key: settings[key] for key in keys}, sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()[:20]


def floor_signature(settings):
    encoded = json.dumps({key: settings[key] for key in FIT_KEYS}, sort_keys=True)
    return source_signature(settings)+hashlib.sha256(encoded.encode()).hexdigest()[:20]


def settings_signature(settings):
    raw = json.dumps({key: settings[key] for key in HOUSE_KEYS}, sort_keys=True)
    return house_signature(settings)+hashlib.sha256(raw.encode()).hexdigest()[:20]


def write_rows(folder, day, rows):
    """Imports never overwrite live samples and use unique atomic temp files."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8",
                                         dir=folder, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, folder/f"{day}.csv")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def append_row(folder, row):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    file = folder/f"{row['time_utc'][:10]}.csv"
    new_file = not file.exists() or file.stat().st_size == 0
    with file.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def read_rows(folder, now, days):
    """Deduplicate by minute, imported first, live last."""
    folder = Path(folder)
    cutoff = now-days*86400
    rows = {}
    bad_rows = 0
    for file in sorted((folder/"imported").glob("????-??-??.csv"))+sorted(folder.glob("????-??-??.csv")):
        if datetime.fromisoformat(file.stem).replace(tzinfo=UTC).timestamp()+86400 < cutoff:
            continue
        with file.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                try:
                    stamp = datetime.fromisoformat(row["time_utc"])
                    if stamp.tzinfo is None:
                        raise ValueError("Naive history timestamp")
                    t = stamp.timestamp()
                    if cutoff <= t <= now:
                        rows[int(t//60)] = {"t": t, **{key: finite_number(row.get(key))
                            for key in ("room", "outdoor", "heat", "solar")}, "calibration": row.get("calibration") or "unknown"}
                except (KeyError, ValueError, TypeError):
                    bad_rows += 1
    return sorted(rows.values(), key=lambda row: row["t"]), bad_rows


def make_bins(rows, maximum_gap=600, now=None):
    """Each endpoint holds mean preceding-interval drivers and endpoint room.

    Integrate held inputs over actual seconds. Reject a bin if any driver was
    missing, if the input calibration changed, or a source gap exceeds the cap.
    Missing room observations can be predicted through, but are not training
    targets. Five-minute endpoints are strictly in the past.
    """
    accumulators = {}
    rows = sorted(rows, key=lambda row: row["t"])
    for previous, current in zip(rows, rows[1:]):
        left, right = previous["t"], current["t"]
        if right-left <= 0 or right-left > maximum_gap:
            continue
        valid = all(previous[key] is not None for key in ("outdoor", "heat", "solar"))
        valid = valid and previous["calibration"] == current["calibration"]
        while left < right:
            endpoint = (math.floor(left/300)+1)*300
            stop = min(right, endpoint)
            bucket = accumulators.setdefault(endpoint, [0.0, 0.0, 0.0, 0.0, None, None, set()])
            seconds = stop-left
            if valid:
                bucket[0] += seconds
                for index, key in enumerate(("outdoor", "heat", "solar"), 1):
                    bucket[index] += previous[key]*seconds
            bucket[6].add(previous["calibration"])
            # The first sample at/after the endpoint is not a future room
            # observation. Use it only at the actual endpoint; otherwise use
            # the held previous state (at most one recording interval old).
            if stop == endpoint:
                bucket[4] = current["room"] if right == endpoint else previous["room"]
                bucket[5] = endpoint
            left = stop
    result = []
    for t, bucket in sorted(accumulators.items()):
        if (bucket[0] >= 299.99 and bucket[5] == t and len(bucket[6]) == 1
                and (now is None or t <= now)):
            result.append([t, bucket[4], bucket[1]/300, bucket[2]/300, bucket[3]/300])
    return result


def prepare_fit_request(folder, floor_folder, settings, now, own_heat_source):
    rows, bad = read_rows(folder, now, settings["house_learning_days"])
    provenance = "external_entity"
    if own_heat_source:
        # Raw source CSVs, never a previously generated floor estimate, are
        # replayed with one frozen floor calibration for the whole window.
        replay_settings = {**settings, "learning_days": settings["house_learning_days"]}
        floor_samples, _ = read_samples(floor_folder, replay_settings, now)
        engine = Estimator(settings)
        replay = {}
        for sample in floor_samples:
            replay[int(sample.timestamp//60)] = engine.step(sample)["nonlinear_power"]
        for row in rows:
            row["heat"] = replay.get(int(row["t"]//60))
            row["calibration"] = floor_signature(settings)
        provenance = "raw_floor_replay"
    else:
        # External sources lacking calibration metadata are explicitly marked
        # unverified. Known calibration changes form gaps, never mixed bins.
        provenance = "external_entity_calibration_not_verified"
    points = make_bins(rows, settings["house_max_gap_minutes"]*60, now)
    return {"points": points,
            "input_provenance": provenance, "skipped_rows": bad,
            "minute_sample_count": len(rows),
            "source_coverage_minutes": {key:sum(row[key] is not None for row in rows)
                for key in ("room", "outdoor", "heat", "solar")},
            "first_usable_bin_utc": datetime.fromtimestamp(points[0][0], UTC).isoformat() if points else None,
            "last_usable_bin_utc": datetime.fromtimestamp(points[-1][0], UTC).isoformat() if points else None}
