"""Solar-independent thermal fitting and signed observed heat diagnostics."""

from dataclasses import replace
from datetime import datetime
import importlib
import math
from pathlib import Path
import sys
from types import ModuleType
import unittest

ROOT = Path(__file__).resolve().parents[1]/'custom_components/floor_heat_estimator'
package = ModuleType('_house_solar_balance_tests')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
model = importlib.import_module(package.__name__+'.house_model')
balance = importlib.import_module(package.__name__+'.house_balance')
simulation = importlib.import_module(package.__name__+'.house_simulation')
fitting = importlib.import_module(package.__name__+'.house_fit')
from test_house_simulation import series


class SolarWeightTests(unittest.TestCase):
    def test_sunny_periods_and_sunset_memory_receive_less_thermal_weight(self):
        points = [[n*300,20.,5.,2.,.5 if n<12 else 0.] for n in range(24*12+1)]
        data = simulation.SimulationData(points,solar_uncertainty=5,solar_memory_hours=3)
        self.assertLess(data.solar_weights[0],.1)
        self.assertLess(data.solar_weights[24],.2)  # one hour after sunset
        self.assertGreater(data.solar_weights[-1],.99)
        # Changing physical parameters cannot change fixed data weights.
        weights = data.solar_weights.copy()
        data.evaluate(model.HouseParameters())
        data.evaluate(model.HouseParameters(ci=2,solar_area=30))
        self.assertTrue((data.solar_weights==weights).all())

    def test_interval_weight_accounts_for_sun_inside_change_window(self):
        points = [[n*300,20.,5.,2.,.5 if n==3 else 0.] for n in range(13)]
        raw = simulation.SimulationData(points)
        weighted = simulation.SimulationData(points,solar_uncertainty=5)
        _,ends,weights = weighted.pairs[6]
        index = list(ends).index(6)
        self.assertLess(weights[index]/raw.pairs[6][2][index],.2)

    def test_validation_metrics_are_unweighted_even_when_training_weights_change(self):
        p,points,_ = series(3)
        plain = simulation.SimulationData(points)
        weighted = simulation.SimulationData(points,solar_uncertainty=5)
        result = plain.evaluate(p)
        self.assertEqual(plain.metrics(result),weighted.metrics(result))
        self.assertEqual(plain.imbalance_metrics(result,60),weighted.imbalance_metrics(result,60))

    def test_gain_stage_fits_with_thermal_parameters_frozen(self):
        p,points,_ = series(warmup_days=10)
        prior = replace(p,ci=3,ce=10,solar_area=3)
        result = fitting.fit_house({'points':points,'baseline':prior.to_dict(),
            'solar_uncertainty':5,'solar_memory_hours':3,'starts':1,
            'gain_modes':{'solar_area':'free','internal_gain':'free'},
            'max_iterations':220,'max_seconds':60,'correction_timezone':'Europe/Amsterdam'})
        self.assertTrue(result['optimizer_converged'],result.get('reason'))
        self.assertTrue(result['gain_fit_stage']['enabled'])
        self.assertTrue(result['gain_fit_stage']['converged'])
        for key in ('ci','ce','rie','rea'):
            self.assertEqual(result['candidate_parameters'][key],result['rc_identification_parameters'][key])
        self.assertAlmostEqual(result['candidate_parameters']['solar_area'],p.solar_area,delta=.5)
        self.assertAlmostEqual(result['candidate_parameters']['ci'],p.ci,delta=.15)
        self.assertIn('rms_W',result['heat_imbalance']['validation']['60'])
        self.assertEqual(result['solar_weighting']['uncertain_effective_area_m2'],5)


class HeatImbalanceTests(unittest.TestCase):
    def test_signed_energy_residual_and_watt_units(self):
        p = model.HouseParameters(ci=2,rie=.5,internal_gain=0)
        # No modeled heat or temperature movement: warming observed room by
        # 1 K in one hour requires 2 kWh storage + 1 kWh envelope exchange.
        self.assertAlmostEqual(balance.interval_imbalance((20,20),(20,20),20,21,20,0,0,p,1),3)
        self.assertAlmostEqual(balance.interval_imbalance((20,20),(20,20),20,19,20,0,0,p,1),-3)
        tracker = balance.HeatBalanceTracker()
        for n in range(12):
            residual=balance.interval_imbalance((20,20),(20,20),20+n/120,
                20+(n+1)/120,20,0,0,p,1/12)
            tracker.push((n*300,(n+1)*300,residual),p)
        self.assertAlmostEqual(tracker.data(60)['value_W'],300,places=8)
        self.assertAlmostEqual(tracker.data(60)['bias_W'],300,places=8)
        self.assertAlmostEqual(tracker.data(60)['rms_W'],300,places=8)

    def test_gaps_missing_observations_and_calibration_changes_never_fill_zero(self):
        p = model.HouseParameters()
        tracker = balance.HeatBalanceTracker()
        for n in range(6):tracker.push((n*300,(n+1)*300,.01),p)
        self.assertTrue(tracker.data(30))
        tracker.push(None,p)
        self.assertFalse(tracker.data(30))
        for n in range(6):tracker.push((n*300,(n+1)*300,.01),p)
        tracker.push((2100,2400,.01),p)
        self.assertFalse(tracker.data(30))
        tracker.push((2400,2700,.01),replace(p,ci=2))
        self.assertFalse(tracker.data(30))

    def test_midnight_correction_is_excluded_from_storage_energy(self):
        engine = model.HouseEstimator(model.HouseParameters(ci=2,rie=.5,internal_gain=0),
                                      'Europe/Amsterdam')
        t = datetime.fromisoformat('2026-11-03T23:55:00+01:00').timestamp()
        engine.timestamp=t
        engine.state=(20,20)
        engine.previous_room=21
        engine.covariance=(1,0,25)
        engine.step([t+300,21,20,0,0])
        self.assertGreater(engine.state[0],20.9)
        # Observed room remained at 21: only its exchange with Te=20 appears.
        self.assertAlmostEqual(engine.last_heat_balance_interval[2],2/12,places=10)

    def test_old_snapshot_and_missing_room_need_complete_new_windows(self):
        engine=model.HouseEstimator()
        self.assertTrue(engine.restore({'timestamp':0,'state':[20,18],'covariance':[1,0,25]}))
        engine.step([300,20,5,2,0])
        self.assertIsNone(engine.last_heat_balance_interval)
        engine.step([600,20,5,2,0])
        self.assertIsNotNone(engine.last_heat_balance_interval)
        engine.step([900,None,5,2,0])
        self.assertIsNone(engine.last_heat_balance_interval)
        engine.step([1200,20,5,2,0])
        self.assertIsNone(engine.last_heat_balance_interval)

    def test_fit_imbalance_is_unweighted_required_minus_recorded_heat(self):
        p=model.HouseParameters(ci=2,rie=.5,internal_gain=0)
        points=[[n*300,20,20,0,0] for n in range(25)]
        points[6][1]=None
        data=simulation.SimulationData(points)
        report=data.imbalance_metrics(data.evaluate(p),30)
        self.assertLess(abs(report['bias_W']),1e-8)
        self.assertLess(report['rms_W'],1e-8)
        self.assertLess(report['count'],len(points)-6)


if __name__=='__main__':unittest.main()
