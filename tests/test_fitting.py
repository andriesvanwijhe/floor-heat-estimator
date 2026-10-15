"""Identification, data isolation, gating and chronological validation tests."""

import csv
from datetime import UTC, datetime
import importlib
from pathlib import Path
import math
import sys
from tempfile import TemporaryDirectory
from threading import Event
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "floor_heat_estimator"
package = ModuleType("_floor_fit_tests")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
fitting = importlib.import_module("_floor_fit_tests.fitting")
model = importlib.import_module("_floor_fit_tests.model")
DEFAULTS = importlib.import_module("_floor_fit_tests.const").DEFAULTS


def synthetic_samples():
    parameters = model.Parameters(0.35, 2.2, 1.4)
    temperature, room, power = 20.0, 20.0, 0.0
    samples = []
    start = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    for minute in range(6 * 360):
        temperature = model.advance(temperature, room, power, 60, parameters)
        room = 20 + 0.3 * math.sin(minute / 130)
        active = minute % 360 < 150
        power = (3.8 if minute % 360 < 65 else 1.3) if active else 0.0
        measured = temperature + 0.015 * math.sin(minute * 0.8)
        samples.append(model.Sample(start + minute * 60, room, power,
            measured + 1 if active else 70, measured - 1 if active else 60,
            1200 if active else 0, "hc" if active else "standby"))
    return samples


class FittingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = synthetic_samples()
        cls.result = fitting.fit_samples(cls.samples, DEFAULTS, cls.samples[-1].timestamp)

    def test_recovers_known_exponent_model(self):
        result = self.result
        self.assertTrue(result["accepted"], result)
        self.assertAlmostEqual(result["parameters"]["nonlinear_capacity"], 0.35, delta=0.04)
        self.assertAlmostEqual(result["parameters"]["nonlinear_output_at_5k"], 2.2, delta=0.08)
        self.assertAlmostEqual(result["parameters"]["exponent"], 1.4, delta=0.08)
        self.assertLess(result["models"]["nonlinear"]["validation_rmse_K"], 0.1)

    def test_validation_ceiling_rejects_both_parameter_updates_atomically(self):
        result = fitting.fit_samples(self.samples, {**DEFAULTS, "maximum_fit_rmse": 0.05}, self.samples[-1].timestamp)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["parameters"], {})
        self.assertFalse(result["models"]["linear"]["accepted"])
        self.assertTrue(result["models"]["nonlinear"]["accepted"])

    def test_validation_is_later_and_excludes_whole_training_cycle(self):
        split = datetime.fromisoformat(self.result["training_end_utc"]).timestamp()
        self.assertEqual(split, self.samples[4 * 360].timestamp)
        self.assertGreater(self.result["validation_points"], 6)

    def test_fewer_than_four_cycles_cannot_replace_parameters(self):
        result = fitting.fit_samples(self.samples[:3 * 360], DEFAULTS, self.samples[-1].timestamp)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["parameters"], {})

    def test_learning_window_changes_available_training_data(self):
        result = fitting.fit_samples(self.samples, {**DEFAULTS, "learning_days": 1}, self.samples[-1].timestamp)
        self.assertLess(result["sample_count"], len(self.samples))
        self.assertFalse(result["accepted"])

    def test_flat_observations_are_rejected_before_optimization(self):
        samples = [model.Sample(s.timestamp, 20, s.power, 26, 24, s.flow, s.mode) for s in self.samples]
        result = fitting.fit_samples(samples, DEFAULTS, samples[-1].timestamp)
        self.assertEqual(result["status"], "poor_excitation")
        self.assertFalse(result["accepted"])

    def test_off_mode_water_temperatures_never_become_observations(self):
        points = fitting.prepare_points(self.samples, DEFAULTS)
        self.assertTrue(all(p.observation is None for p in points if p.sample.mode != "hc"))
        self.assertTrue(all(p.observation is None for p in points if p.sample.flow == 0))

    def test_gap_restarts_settling_for_training_and_live_estimator(self):
        start = self.samples[0].timestamp
        samples = [model.Sample(start + m * 60, 20, 3, 30, 28, 1200, "hc") for m in (0, 10, 90, 95, 100)]
        points = fitting.prepare_points(samples, DEFAULTS)
        self.assertIsNone(points[2].observation)
        self.assertIsNone(points[3].observation)
        self.assertIsNotNone(points[4].observation)
        estimator = model.Estimator(DEFAULTS)
        values = [estimator.step(s) for s in samples]
        self.assertFalse(values[2]["measurement_valid"])
        self.assertFalse(values[3]["measurement_valid"])
        self.assertTrue(values[4]["measurement_valid"])

    def test_missing_drivers_break_traces_and_are_not_zero_filled(self):
        samples = self.samples[:80].copy()
        middle = samples[40]
        samples[40] = model.Sample(middle.timestamp, None, None, 30, 28, 1200, "hc")
        traces = fitting.build_traces(fitting.prepare_points(samples, DEFAULTS), DEFAULTS)
        self.assertEqual(len(traces), 2)

    def test_timeout_and_cancellation_stop_numerical_work(self):
        with self.assertRaises(TimeoutError):
            fitting.fit_samples(self.samples, DEFAULTS, self.samples[-1].timestamp, budget_seconds=-1)
        cancelled = Event()
        cancelled.set()
        with self.assertRaises(InterruptedError):
            fitting.fit_samples(self.samples, DEFAULTS, self.samples[-1].timestamp, cancel=cancelled)


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.settings = {**DEFAULTS, **{key: "sensor." + key for key in
            ("heat_input", "room_temperature", "supply_temperature", "return_temperature", "flow", "mode")}}
        self.now = datetime(2026, 1, 30, 12, tzinfo=UTC).timestamp()

    def write(self, directory, file, timestamp):
        path = Path(directory) / file
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=("time_utc", "heat_input", "room_temperature",
                "supply_temperature", "return_temperature", "flow", "mode"))
            writer.writeheader()
            writer.writerow(dict(time_utc=timestamp, heat_input=2, room_temperature=20,
                supply_temperature=30, return_temperature=28, flow=1200, mode="hc"))

    def test_unsigned_legacy_requires_explicit_opt_in(self):
        with TemporaryDirectory() as directory:
            self.write(directory, "2026-01-30.csv", "2026-01-30T10:00:00+00:00")
            self.assertEqual(fitting.read_samples(directory, self.settings, self.now)[0], [])
            self.assertEqual(len(fitting.read_samples(directory, self.settings, self.now, True)[0]), 1)

    def test_source_and_gating_changes_isolate_recordings(self):
        with TemporaryDirectory() as directory:
            signature = fitting.source_signature(self.settings)
            self.write(directory, f"learning/{signature}/2026-01-30.csv", "2026-01-30T10:00:00+00:00")
            self.assertEqual(len(fitting.read_samples(directory, self.settings, self.now)[0]), 1)
            changed = {**self.settings, "flow_threshold": 500}
            self.assertEqual(fitting.read_samples(directory, changed, self.now)[0], [])

    def test_old_future_and_naive_timestamps_are_excluded(self):
        with TemporaryDirectory() as directory:
            self.write(directory, "2026-01-30.csv", "2026-01-30T10:00:00")
            self.write(directory, "2026-02-01.csv", "2026-02-01T10:00:00+00:00")
            self.write(directory, "2025-01-01.csv", "2025-01-01T10:00:00+00:00")
            samples, bad = fitting.read_samples(directory, self.settings, self.now, True)
            self.assertEqual(samples, [])
            self.assertEqual(bad, 1)


if __name__ == "__main__":
    unittest.main()
