"""Exact RC/SDE physics and stochastic identification against known truth."""

from datetime import UTC, datetime
import importlib
import math
from pathlib import Path
import sys
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]/"custom_components/floor_heat_estimator"
package = ModuleType("_house_math_tests")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
model = importlib.import_module("_house_math_tests.house_model")
fitting = importlib.import_module("_house_math_tests.house_fit")


def synthetic_house(days=9):
    import numpy as np
    rng = np.random.default_rng(124)
    truth = model.HouseParameters(ci=1.0, ce=8, rie=.4, rea=3.6, solar_area=6,
                                  sigma_i=.02, sigma_e=.01, sigma_y=.06)
    coeff = model.transition(truth, 1/12)
    q = coeff[1]
    root = np.linalg.cholesky([[q[0], q[1]], [q[1], q[2]]])
    state = (19, 15)
    start = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    points = []
    heat = 2.4
    for step in range(days*288+1):
        hour = step/12
        outdoor = 5+3*math.sin(2*math.pi*(hour-8)/24)+2*math.sin(2*math.pi*hour/120)
        solar = .55*max(0, math.sin(math.pi*((hour%24)-6)/12))*(.4+.6*((int(hour//24)%3)/2))
        if step % 48 == 0:
            heat = float(rng.choice([.8, 1.8, 3.2, 4.0]))
        if step:
            state = model.advance(state, outdoor, heat, solar, truth, coeff)
            noise = root@rng.normal(size=2)
            state = (state[0]+float(noise[0]), state[1]+float(noise[1]))
        measured = state[0]+float(rng.normal(scale=truth.sigma_y))
        points.append([start+step*300, measured, outdoor, heat, solar])
    return truth, points


class HouseMathTests(unittest.TestCase):
    def test_transition_and_diffusion_match_independent_matrix_exponential(self):
        import numpy as np
        from scipy.linalg import expm
        for p in (model.HouseParameters(), model.HouseParameters(ci=.02, ce=.1, rie=.02, rea=.03)):
            a = np.array([[-1/(p.rie*p.ci), 1/(p.rie*p.ci)],
                          [1/(p.rie*p.ce), -(1/p.rie+1/p.rea)/p.ce]])
            d = np.diag([p.sigma_i**2, p.sigma_e**2])
            hours = 1/12 if p.ci > .1 else .00001
            van_loan = expm(np.block([[a, d], [np.zeros((2,2)), -a.T]])*hours)
            phi, q, _ = model.transition(p, hours)
            np.testing.assert_allclose(np.array(phi).reshape(2,2), van_loan[:2,:2], atol=1e-12)
            np.testing.assert_allclose([[q[0],q[1]],[q[1],q[2]]], van_loan[:2,2:]@van_loan[:2,:2].T, atol=1e-12)

    def test_constant_input_equilibrium_satisfies_both_heat_balances(self):
        p = model.HouseParameters()
        outdoor, heat, solar = 5, 2, .2
        power = heat+p.solar_area*solar+p.internal_gain
        equilibrium = (outdoor+(p.rie+p.rea)*power, outdoor+p.rea*power)
        result = model.advance(equilibrium, outdoor, heat, solar, p, model.transition(p, 24))
        self.assertAlmostEqual(result[0], equilibrium[0], places=10)
        self.assertAlmostEqual(result[1], equilibrium[1], places=10)

    def test_zero_heat_approaches_outdoor_and_signed_heat_is_preserved(self):
        p = model.HouseParameters(internal_gain=0)
        cooled = model.advance((22,20), 5, 0, 0, p, model.transition(p, 2000))
        extracted = model.advance((22,20), 5, -2, 0, p, model.transition(p, 2000))
        self.assertAlmostEqual(cooled[0], 5, places=2)
        self.assertLess(extracted[0], 5)

    def test_internal_gain_defaults_to_500w_and_enters_interior_only(self):
        p = model.HouseParameters()
        self.assertEqual(p.internal_gain,.5)
        hours = 1e-5
        warmed = model.advance((20,20),20,0,0,p,model.transition(p,hours))
        self.assertAlmostEqual((warmed[0]-20)/hours,p.internal_gain/p.ci,delta=1e-5)
        self.assertLess(abs(warmed[1]-20)/hours,1e-6)

    def test_internal_gain_may_be_zero_but_not_negative_or_boolean(self):
        self.assertEqual(model.HouseParameters(internal_gain=0).internal_gain,0)
        for value in (-.1,True,float('nan')):
            with self.assertRaises(ValueError):model.HouseParameters(internal_gain=value)

    def test_filter_updates_hidden_state_without_replacing_it_with_room(self):
        estimator = model.HouseEstimator()
        estimator.step([1000, 21, 5, 2, 0])
        previous = estimator.state[1]
        estimator.step([1300, 22, 5, 2, 0])
        self.assertNotEqual(estimator.state[1], previous)
        self.assertNotEqual(estimator.state[1], 22)
        self.assertGreater(estimator.covariance[0]*estimator.covariance[2]-estimator.covariance[1]**2, 0)

    def test_gaps_and_missing_drivers_never_silently_use_zero(self):
        estimator = model.HouseEstimator()
        estimator.step([1000,21,5,2,0])
        estimator.step([1300, None, 5,2,0])
        self.assertIsNotNone(estimator.state)
        estimator.step([1600,21,5,None,0])
        self.assertIsNone(estimator.state)
        estimator.step([3000,None,5,2,0])
        self.assertIsNone(estimator.state)

    def test_snapshot_roundtrip_and_corrupt_covariance_rejection(self):
        estimator = model.HouseEstimator()
        estimator.step([1000,21,5,2,0])
        restored = model.HouseEstimator()
        self.assertTrue(restored.restore(estimator.snapshot()))
        self.assertEqual(restored.state, estimator.state)
        self.assertFalse(restored.restore({"timestamp":1000,"state":[21,19],"covariance":[1,5,1]}))

    def test_observed_information_at_zero_gain_matches_analytic_curvature(self):
        import numpy as np
        size=len(fitting.PARAMETERS)
        truth=np.diag(np.arange(1,size+1,dtype=float))
        truth[0,fitting.GAIN_INDEX]=truth[fitting.GAIN_INDEX,0]=.2
        values=np.linspace(.1,.9,size)
        values[fitting.GAIN_INDEX]=0
        def objective(x):
            self.assertGreaterEqual(x[fitting.GAIN_INDEX],0)
            return float(.5*x@truth@x)
        np.testing.assert_allclose(fitting.observed_information(objective,values),truth,atol=1e-7)


class HouseIdentificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.truth, cls.points = synthetic_house()

    def test_likelihood_prefers_known_truth_to_bad_physics(self):
        good = fitting.filter_points(self.points, self.truth)[0]
        bad = fitting.filter_points(self.points, model.HouseParameters())[0]
        self.assertLess(good, bad)

    def test_forecast_validation_does_not_correct_using_future_room_data(self):
        split = self.points[-1][0]-2*86400
        first, _ = fitting.forecast_errors(self.points, self.truth, split)
        # A single future target spike must remain a prediction error.
        altered = [row.copy() for row in self.points]
        altered[-1][1] += 20
        second, _ = fitting.forecast_errors(altered, self.truth, split)
        self.assertGreater(second['24']['rmse_K'], first['24']['rmse_K'])

    def test_insufficient_and_unexcited_data_cannot_fit(self):
        request = {"points": self.points[:100], "baseline": self.truth.to_dict()}
        self.assertEqual(fitting.fit_house(request)["status"], "insufficient_data")
        request["points"] = [[r[0],20,5,2,0] for r in self.points]
        self.assertEqual(fitting.fit_house(request)["status"], "poor_excitation")

    def test_deadline_stops_optimizer(self):
        with self.assertRaises(fitting.FitDeadline):
            fitting.fit_house({"points":self.points, "baseline":self.truth.to_dict(), "max_seconds":-1})

    def test_fitting_recovers_physical_coefficients_and_passes_held_out_forecasts(self):
        baseline = self.truth.to_dict()
        baseline.update(ci=1.4, ce=6, rie=.55, rea=3.1, solar_area=4.5,
                        internal_gain=.2, sigma_i=.03, sigma_e=.015, sigma_y=.08)
        result = fitting.fit_house({"points":self.points,"baseline":baseline,"starts":1,
                                    "max_iterations":220,"max_seconds":120,"maximum_rmse":1})
        self.assertTrue(result["accepted"], result)
        for key in ("ci","ce","rie","rea","solar_area"):
            self.assertAlmostEqual(result["parameters"][key], getattr(self.truth,key), delta=getattr(self.truth,key)*.1)
            self.assertGreater(result["approximate_confidence_intervals"][key]["upper_95"], 0)
        # The constant gain is correlated with overall heat loss. On this
        # finite noisy record, require recovery within 100 W and coverage by
        # the reported interval rather than asserting false 10% precision.
        self.assertAlmostEqual(result['parameters']['internal_gain'],self.truth.internal_gain,delta=.1)
        interval=result['approximate_confidence_intervals']['internal_gain']
        self.assertLess(interval['lower_95'],self.truth.internal_gain)
        self.assertGreater(interval['upper_95'],self.truth.internal_gain)
        self.assertLess(result["validation"]["24"]["rmse_K"], .15)

    def test_optimizer_coordinates_preserve_an_exact_zero_internal_gain(self):
        p=model.HouseParameters(internal_gain=0)
        restored=fitting.unpack(fitting.pack(p))
        self.assertEqual(restored.internal_gain,0)
        self.assertEqual(fitting.BOUNDS[fitting.GAIN_INDEX],(0,5))


if __name__ == '__main__':
    unittest.main()
