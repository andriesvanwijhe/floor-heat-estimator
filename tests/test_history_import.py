"""Recorder alignment, downtime, cancellation and local-recording precedence."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
import importlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components/floor_heat_estimator"
package = ModuleType("_floor_history_tests")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
history = importlib.import_module("_floor_history_tests.history_import")
fitting = importlib.import_module("_floor_history_tests.fitting")
const = importlib.import_module("_floor_history_tests.const")


def state(timestamp, value, unit=None):
    return SimpleNamespace(last_updated=datetime.fromtimestamp(timestamp, UTC),
                           state=str(value), attributes={"unit_of_measurement": unit})


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.settings = {**const.DEFAULTS, **{key: "sensor." + key for key in const.SOURCE_KEYS}}
        self.start = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
        self.states = {self.settings[key]: [state(self.start, value, unit)]
                       for key, value, unit in (("heat_input", 3000, "W"),
                           ("room_temperature", 68, "°F"), ("supply_temperature", 30, "°C"),
                           ("return_temperature", 28, None), ("flow", 20, "l/min"), ("mode", "hc", None))}

    def reconstruct(self, minutes=6, runs=None):
        return history.reconstruct(self.states, self.settings, self.start,
            self.start + minutes * 60, runs if runs is not None else [(self.start-1, self.start+86400)], Event())

    def test_alignment_holds_unchanged_values_and_converts_historical_units(self):
        rows = self.reconstruct()
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[-1]["heat_input"], 3)
        self.assertEqual(rows[-1]["room_temperature"], 20)
        self.assertEqual(rows[-1]["flow"], 1200)
        self.assertEqual(rows[0]["mode"], "hc")  # synthetic day seed is not a new cycle

    def test_unavailable_is_not_interpolated_or_zero_filled(self):
        self.states[self.settings["heat_input"]] += [state(self.start+90, "unavailable", "W"),
                                                    state(self.start+210, 2000, "W")]
        rows = self.reconstruct()
        self.assertIsNone(rows[2]["heat_input"])
        self.assertIsNone(rows[3]["heat_input"])
        self.assertEqual(rows[4]["heat_input"], 2)

    def test_brief_gate_and_invalid_state_interruptions_cannot_disappear(self):
        self.states[self.settings["mode"]] += [state(self.start+70, "dhw"), state(self.start+80, "hc")]
        self.states[self.settings["room_temperature"]] += [state(self.start+130, "unknown"), state(self.start+140, 20, "°C")]
        rows = self.reconstruct()
        self.assertIsNone(rows[2]["mode"])
        self.assertEqual(rows[3]["mode"], "hc")
        self.assertIsNone(rows[3]["room_temperature"])
        self.assertEqual(rows[4]["room_temperature"], 20)

    def test_short_flow_interruption_restarts_settling(self):
        self.states[self.settings["flow"]] += [state(self.start+70, 0, "l/h"), state(self.start+80, 1200, "l/h")]
        rows = self.reconstruct(20)
        with TemporaryDirectory() as folder:
            history.write_day(folder, self.settings, "2026-01-01", rows, Event())
            samples, _ = fitting.read_samples(folder, self.settings, self.start+1200)
        points = fitting.prepare_points(samples, self.settings)
        self.assertIsNone(points[12].observation)
        self.assertIsNotNone(points[13].observation)

    def test_downtime_and_run_restart_do_not_hold_stale_sensor_values(self):
        runs = [(self.start-100, self.start+120), (self.start+240, self.start+600)]
        # Only mode reappears after restart; all numerical values must stay missing.
        self.states[self.settings["mode"]].append(state(self.start+245, "hc"))
        rows = self.reconstruct(8, runs)
        self.assertIsNone(rows[2]["mode"])
        self.assertIsNone(rows[3]["heat_input"])
        self.assertIsNone(rows[6]["heat_input"])
        self.assertEqual(rows[6]["mode"], "hc")

    def test_missing_entities_have_no_invented_values(self):
        self.states.pop(self.settings["flow"])
        self.assertTrue(all(row["flow"] is None for row in self.reconstruct()))
        self.assertTrue(all(row["mode"] is None for row in self.reconstruct(runs=[])))

    def test_live_records_win_same_minute_and_import_is_idempotent(self):
        rows = self.reconstruct()
        with TemporaryDirectory() as folder:
            for _ in range(2):
                history.write_day(folder, self.settings, "2026-01-01", rows, Event())
            live = Path(folder)/"learning"/fitting.source_signature(self.settings)/"2026-01-01.csv"
            row = {**rows[2], "time_utc": datetime.fromtimestamp(self.start+145, UTC).isoformat(), "heat_input": 9}
            import csv
            with live.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row))
                writer.writeheader()
                writer.writerow(row)
            samples, bad = fitting.read_samples(folder, self.settings, self.start+600)
            self.assertEqual(bad, 0)
            self.assertEqual(len(samples), 6)
            self.assertEqual(samples[2].power, 9)
            self.assertEqual(samples[2].timestamp, self.start+145)

    def test_cancellation_does_not_replace_existing_import_file(self):
        with TemporaryDirectory() as folder:
            rows = self.reconstruct()
            history.write_day(folder, self.settings, "2026-01-01", rows, Event())
            target = next(Path(folder).rglob("*.csv"))
            original = target.read_bytes()
            cancel = Event()
            cancel.set()
            with self.assertRaises(asyncio.CancelledError):
                history.write_day(folder, self.settings, "2026-01-01", [], cancel)
            self.assertEqual(target.read_bytes(), original)
            with self.assertRaises(asyncio.CancelledError):
                history.reconstruct({}, self.settings, self.start, self.start+60, [], cancel)

    def test_recorded_heating_cycles_can_be_imported_and_fitted_end_to_end(self):
        from test_fitting import synthetic_samples
        samples = synthetic_samples()
        states = {self.settings[key]: [] for key in const.SOURCE_KEYS}
        for sample in samples:
            for key, value in zip(const.SOURCE_KEYS, (sample.power, sample.room, sample.supply,
                                                     sample.return_temperature, sample.flow, sample.mode)):
                unit = "kW" if key == "heat_input" else "l/h" if key == "flow" else "°C"
                states[self.settings[key]].append(state(sample.timestamp, value, unit))
        rows = history.reconstruct(states, self.settings, samples[0].timestamp,
            samples[-1].timestamp+60, [(samples[0].timestamp-1, samples[-1].timestamp+60)], Event())
        with TemporaryDirectory() as folder:
            for day in sorted({row["time_utc"][:10] for row in rows}):
                history.write_day(folder, self.settings, day,
                                  [row for row in rows if row["time_utc"].startswith(day)], Event())
            result = fitting.fit_recordings(folder, self.settings, samples[-1].timestamp)
        self.assertTrue(result["accepted"], result)
        self.assertEqual(result["cycle_count"], 6)
        self.assertAlmostEqual(result["parameters"]["exponent"], 1.4, delta=.1)

    def test_recorder_query_requests_all_sources_and_excludes_uncertain_runs(self):
        # Adapter contract test: exercise the real query function around mocked
        # HA database objects; numerical reconstruction remains real.
        class Expression:
            def __lt__(self, other): return self
            def __gt__(self, other): return self
            def __or__(self, other): return self
            def is_(self, other): return self
            def where(self, *args): return self

        run_schema = SimpleNamespace(start=Expression(), end=Expression())
        dt = lambda seconds: datetime.fromtimestamp(self.start+seconds, UTC)
        runs = [SimpleNamespace(start=dt(-100), end=dt(120), closed_incorrect=False),
                SimpleNamespace(start=dt(120), end=dt(240), closed_incorrect=True),
                SimpleNamespace(start=dt(240), end=None, closed_incorrect=False),
                SimpleNamespace(start=dt(300), end=None, closed_incorrect=False)]
        session = SimpleNamespace(execute=Mock(return_value=SimpleNamespace(scalars=lambda: iter(runs))))
        @contextmanager
        def session_scope(**kwargs):
            self.assertTrue(kwargs["read_only"])
            yield session

        modules = {name: ModuleType(name) for name in (
            "sqlalchemy", "homeassistant", "homeassistant.components", "homeassistant.components.recorder",
            "homeassistant.components.recorder.db_schema", "homeassistant.components.recorder.util")}
        modules["sqlalchemy"].select = lambda schema: Expression()
        recorder = modules["homeassistant.components.recorder"]
        recorder.history = SimpleNamespace(get_significant_states=Mock(return_value=self.states))
        recorder.get_instance = lambda hass: SimpleNamespace(
            recorder_runs_manager=SimpleNamespace(recording_start=dt(300)))
        modules["homeassistant.components.recorder.db_schema"].RecorderRuns = run_schema
        modules["homeassistant.components.recorder.util"].session_scope = session_scope
        with patch.dict(sys.modules, modules):
            rows = history.query_day(object(), self.settings, dt(0), dt(480), Event())
        kwargs = recorder.history.get_significant_states.call_args.kwargs
        self.assertFalse(kwargs["significant_changes_only"])
        self.assertFalse(kwargs["no_attributes"])
        self.assertFalse(kwargs["minimal_response"])
        self.assertEqual(len(kwargs["entity_ids"]), 6)
        self.assertIsNone(rows[2]["heat_input"])  # incorrectly closed run
        self.assertIsNone(rows[4]["heat_input"])  # historic open run
        self.assertIsNone(rows[6]["heat_input"])  # new run has not repopulated sensors


class ImportOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_batches_use_recorder_executor_and_report_missing_history(self):
        settings = {**const.DEFAULTS, "learning_days": 2,
                    **{key: "sensor." + key for key in const.SOURCE_KEYS}}
        now = datetime(2026, 1, 3, 12, tzinfo=UTC).timestamp()
        instance = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=[]))
        module = ModuleType("homeassistant.components.recorder")
        module.get_instance = lambda hass: instance
        modules = {name: ModuleType(name) for name in ("homeassistant", "homeassistant.components")}
        modules["homeassistant.components.recorder"] = module
        hass = SimpleNamespace(config=SimpleNamespace(components={"recorder"}),
                               async_add_executor_job=AsyncMock(side_effect=lambda fn, *args: fn(*args)))
        with TemporaryDirectory() as folder, patch.dict(sys.modules, modules):
            result = await history.async_import_history(hass, folder, settings, now, Event())
            self.assertEqual(instance.async_add_executor_job.await_count, 3)
            self.assertEqual(result["status"], "no_usable_history")
            self.assertEqual(len(result["missing_entities"]), 6)
            self.assertEqual(result["complete_sample_count"], 0)
            calls = instance.async_add_executor_job.call_args_list
            for earlier, later in zip(calls, calls[1:]):
                self.assertEqual(earlier.args[4], later.args[3])

    async def test_disabled_recorder_has_clear_error(self):
        module = ModuleType("homeassistant.components.recorder")
        module.get_instance = lambda hass: None
        modules = {name: ModuleType(name) for name in ("homeassistant", "homeassistant.components")}
        modules["homeassistant.components.recorder"] = module
        hass = SimpleNamespace(config=SimpleNamespace(components=set()))
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(ValueError, "Recorder is not enabled"):
            await history.async_import_history(hass, "/unused", {}, 1000, Event())


if __name__ == "__main__":
    unittest.main()
