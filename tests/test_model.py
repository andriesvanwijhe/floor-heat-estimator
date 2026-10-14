"""Physical invariants and estimator lifecycle tests."""

import importlib.util
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for name in ("model", "const"):
    spec = importlib.util.spec_from_file_location(name, ROOT / "custom_components" / "floor_heat_estimator" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

from model import Estimator, Parameters, Sample, advance, finite_number
from const import DEFAULTS


class ModelTests(unittest.TestCase):
    def test_linear_decay_matches_closed_form(self):
        p = Parameters(2, 2.5)
        self.assertAlmostEqual(advance(30, 20, 0, 3600, p), 20 + 10 * math.exp(-0.25), places=10)

    def test_steady_input_preserves_equilibrium(self):
        for n in (1, 1.5, 0.7):
            p = Parameters(2, 2.5, n)
            self.assertAlmostEqual(advance(25, 20, 2.5, 3600, p), 25, places=8)

    def test_nonlinear_decay_conserves_energy(self):
        p = Parameters(2, 2.5, 1.5)
        # n=1.5 closed-form unforced decay, theta(0)=10.
        coefficient = 2.5 / 5 ** 1.5
        exact = 20 + (10 ** (-0.5) + 0.5 * coefficient / 2) ** (-2)
        self.assertAlmostEqual(advance(30, 20, 0, 3600, p), exact, delta=0.01)
        self.assertGreaterEqual(advance(20.001, 20, 0, 86400, Parameters(.1, 3, .5)), 20)

    def test_signed_heat_extraction_crosses_room_without_negative_output(self):
        p = Parameters(1, 2.5)
        t = advance(21, 20, -2, 3600, p)
        self.assertLess(t, 20)
        self.assertEqual(p.output(t, 20), 0)

    def test_room_warming_does_not_create_heat(self):
        self.assertEqual(Parameters(1, 2).output(20, 22), 0)

    def test_heat_extraction_at_room_temperature_is_not_damped(self):
        self.assertAlmostEqual(advance(20, 20, -2, 3600, Parameters(1, 2.5)), 18)

    def test_invalid_numbers(self):
        for value in ("unknown", "unavailable", "nan", "inf", None, True):
            self.assertIsNone(finite_number(value))
        self.assertEqual(finite_number("0"), 0)

    def test_invalid_parameters(self):
        for values in ((0, 1), (1, float("nan")), (1, 1, 4)):
            with self.assertRaises(ValueError):
                Parameters(*values)


class EstimatorTests(unittest.TestCase):
    def setUp(self):
        self.settings = {**DEFAULTS, "settling_minutes": 2}
        self.engine = Estimator(self.settings)
        self.t0 = 1700000000.

    def sample(self, minutes, **changes):
        values = dict(timestamp=self.t0 + minutes * 60, room=20., power=3., supply=30., return_temperature=26., flow=1200., mode="hc")
        values.update(changes)
        return Sample(**values)

    def seed(self):
        self.engine.step(self.sample(0))
        return self.engine.step(self.sample(2))

    def test_settling_and_correct_mean_temperature(self):
        self.assertIsNone(self.engine.step(self.sample(0))["linear_power"])
        result = self.engine.step(self.sample(2))
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["linear_temperature"], 28.)
        self.assertAlmostEqual(result["linear_power"], 8 * DEFAULTS["linear_conductance"])

    def test_domestic_hot_water_temperatures_and_input_are_ignored(self):
        self.seed()
        first = self.engine.step(self.sample(3, mode="dhw", supply=60, return_temperature=55, power=8))
        second = self.engine.step(self.sample(4, mode="dhw", supply=60, return_temperature=55, power=8))
        self.assertFalse(second["measurement_valid"])
        self.assertEqual(second["status"], "estimated")
        self.assertLess(second["linear_temperature"], first["linear_temperature"])
        self.assertLess(second["linear_temperature"], 35)

    def test_no_flow_readings_do_not_correct_state(self):
        self.seed()
        result = self.engine.step(self.sample(3, flow=0, supply=60, return_temperature=55))
        self.assertFalse(result["measurement_valid"])
        self.assertLess(result["linear_temperature"], 35)

    def test_unknown_input_invalidates_output_and_then_state(self):
        self.seed()
        self.assertIsNone(self.engine.step(self.sample(3, power=None))["linear_power"])
        result = self.engine.step(self.sample(4, flow=0))
        self.assertIsNone(result["linear_power"])

    def test_missing_room_temperature_is_not_zero(self):
        self.seed()
        self.assertEqual(self.engine.step(self.sample(3, room=None))["status"], "invalid_input")

    def test_long_gap_requires_observation(self):
        self.seed()
        result = self.engine.step(self.sample(30, mode="standby", flow=0))
        self.assertIsNone(result["linear_power"])
        self.assertEqual(result["status"], "gap_requires_observation")

    def test_restart_snapshot_and_prediction(self):
        self.seed()
        saved = self.engine.snapshot()
        other = Estimator(self.settings)
        self.assertTrue(other.restore(saved))
        sample = self.sample(3, mode="standby", flow=0)
        self.assertEqual(other.step(sample), self.engine.step(sample))

    def test_corrupt_and_future_snapshots_rejected(self):
        self.seed()
        saved = self.engine.snapshot()
        saved["last_observation"] += 60
        self.assertFalse(Estimator(self.settings).restore(saved))
        self.assertFalse(Estimator(self.settings).restore({}))

    def test_measurements_expire_without_refresh(self):
        self.seed()
        self.engine.settings["maximum_prediction_hours"] = .1
        for minute in range(3, 10):
            result = self.engine.step(self.sample(minute, mode="standby", flow=0))
        self.assertEqual(result["status"], "observation_expired")
        self.assertIsNone(result["linear_power"])

    def test_time_cannot_run_backwards(self):
        self.seed()
        with self.assertRaises(ValueError):
            self.engine.step(self.sample(1))


if __name__ == "__main__":
    unittest.main()

