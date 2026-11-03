"""Continuous fitting must reproduce dynamics, not measurement corrections."""

from dataclasses import replace
import importlib
import math
from pathlib import Path
import sys
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]/'custom_components/floor_heat_estimator'
package = ModuleType('_house_simulation_tests')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
model = importlib.import_module('_house_simulation_tests.house_model')
simulation = importlib.import_module('_house_simulation_tests.house_simulation')
fitting = importlib.import_module('_house_simulation_tests.house_fit')


def series(days=10, warmup_days=0):
    """Fixed, intermittent heat and smoothly varying solar; no cycle classifier."""
    p = model.HouseParameters(ci=1.2, ce=7, rie=.5, rea=3.5,
        solar_area=8, internal_gain=.4)
    state = (20, 15)
    coefficient = model.transition(p, 1/12)
    points, truth = [], []
    for n in range((days+warmup_days)*288+1):
        h = n/12
        outdoor = 5+3*math.sin(h/23)+math.sin(h/7)
        heat = 3.4+.4*(n//288%4) if (n//12)%3 == 0 else .1
        solar = .45*max(0, math.sin(math.pi*((h%24)-6)/12))*(.4+.2*(n//288%4))
        if n:
            state = model.advance(state,outdoor,heat,solar,p,coefficient)
        if n >= warmup_days*288:
            points.append([n*300,state[0],outdoor,heat,solar])
            truth.append(state)
    return p,points,truth


class ContinuousSimulationTests(unittest.TestCase):
    def test_exact_propagation_matches_independent_stepwise_state_model(self):
        import numpy as np
        p,points,truth = series(3)
        data = simulation.SimulationData(points)
        offset = truth[0][1]-model.initial_state(points[0][1],points[0][2],p)[0][1]
        result = data.evaluate(p,offsets={points[0][0]:offset})
        np.testing.assert_allclose(result['states'],truth,atol=2e-11)
        # The observed Ti trapezoidal integral has a small O(dt^2) error;
        # the hidden Te integral is exact rather than left-held.
        self.assertLess(data.energy_metrics(result)['rmse_mean_load_kW'],.002)

    def test_no_midnight_restart_or_measurement_resets_and_noise_cannot_help(self):
        import numpy as np
        p,points,_ = series(2)
        first = simulation.SimulationData(points).evaluate(p)
        altered = [r.copy() for r in points]
        altered[288][1] += 10
        altered[100][1] = None
        changed = replace(p,sigma_i=.5,sigma_e=.5,sigma_y=.5)
        second = simulation.SimulationData(altered).evaluate(changed)
        np.testing.assert_allclose(first['states'],second['states'],atol=1e-12)
        self.assertAlmostEqual(second['errors'][288]-first['errors'][288],-10)

    def test_only_missing_drivers_and_genuine_gaps_seed_new_segments(self):
        p,points,_ = series(2)
        points[100][1] = None
        points[200][3] = None
        points[400:] = [[r[0]+600,*r[1:]] for r in points[400:]]
        data = simulation.SimulationData(points)
        self.assertEqual([v[0] for v in data.segments],[0,201,400])
        self.assertNotIn(100,data.targets)
        self.assertFalse(any(w['start']<200<w['end'] for w in data.windows))
        for left,right,_ in data.pairs.values():
            self.assertTrue(all(data.segment_id[a]==data.segment_id[b] for a,b in zip(left,right)))

    def test_profile_initial_hidden_state_is_exact_regularized_least_squares(self):
        import numpy as np
        p,points,truth = series(2)
        data = simulation.SimulationData(points)
        result = data.evaluate(p,profile=True)
        offset = result['offsets'][0]
        self.assertLess(abs(offset-(truth[0][1]-model.initial_state(20,5,p)[0][1])),.1)
        def cost(value):
            residual = data.evaluate(p,offsets={0:value})['residual']
            return float(residual@residual)
        self.assertLess(cost(offset),cost(offset-.1))
        self.assertLess(cost(offset),cost(offset+.1))
        np.testing.assert_allclose(result['residual'],data.evaluate(p,offsets={0:offset})['residual'],atol=1e-12)

    def test_short_period_terms_detect_muted_response_with_fixed_data_selection(self):
        p,points,_ = series(4)
        data = simulation.SimulationData(points)
        good = data.evaluate(p,profile=True)
        bad = data.evaluate(replace(p,ci=p.ci*4),profile=True)
        good_metrics, bad_metrics = data.metrics(good),data.metrics(bad)
        self.assertLess(good_metrics['changes']['60']['rmse_K'],.01)
        self.assertGreater(bad_metrics['changes']['60']['rmse_K'],.2)
        self.assertLess(bad_metrics['changes']['60']['rms_amplitude_ratio'],.6)
        self.assertLess(float(good['residual']@good['residual']),float(bad['residual']@bad['residual']))

    def test_validation_targets_cannot_change_training_offsets_or_earlier_states(self):
        import numpy as np
        p,points,_ = series(4)
        split = 3*86400
        train = simulation.SimulationData([r for r in points if r[0]<split])
        offsets = train.evaluate(p,profile=True)['offsets']
        original = simulation.SimulationData(points)
        first = original.evaluate(p,offsets=offsets)
        altered = [r.copy() for r in points]
        for row in altered:
            if row[0]>=split: row[1]+=5
        second_data = simulation.SimulationData(altered)
        second = second_data.evaluate(p,offsets=offsets)
        np.testing.assert_allclose(first['states'],second['states'],atol=1e-12)
        self.assertGreater(second_data.metrics(second,after=split)['rmse_K'],4.9)
        self.assertTrue(all(v['start_utc_epoch']>=split for v in original.energy_metrics(first,after=split)['windows']))

    def test_fit_recovers_fast_cycles_solar_and_total_resistance_from_bad_inertia(self):
        p,points,_ = series(warmup_days=10)
        baseline = replace(p,ci=5,ce=12,rie=1,rea=3,solar_area=3)
        result = fitting.fit_house({'points':points,'baseline':baseline.to_dict(),
            'gain_modes':{'solar_area':'free','internal_gain':'free'},
            'starts':2,'max_iterations':240,'max_seconds':120,'correction_timezone':'Europe/Amsterdam'})
        self.assertTrue(result['accepted'],result)
        for key in ('ci','ce','rie','rea','solar_area'):
            self.assertAlmostEqual(result['parameters'][key],getattr(p,key),delta=getattr(p,key)*.12)
        self.assertLess(result['validation_simulation']['changes']['60']['rmse_K'],.03)
        self.assertGreater(result['validation_simulation']['changes']['60']['rms_amplitude_ratio'],.9)
        self.assertLess(result['validation_simulation']['rmse_K'],.1)
        self.assertEqual(result['parameters']['sigma_i'],baseline.sigma_i)
        self.assertEqual(result['fitting_method'],'continuous_simulation_v1')
        self.assertEqual(result['approximate_confidence_intervals'],{})
        self.assertTrue(result['energy_anchor_enforced'])


if __name__ == '__main__':
    unittest.main()
