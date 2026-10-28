"""Energy-signature anchoring and held-out heat conservation."""

import importlib
import math
from pathlib import Path
import sys
from types import ModuleType
import unittest

ROOT=Path(__file__).resolve().parents[1]/'custom_components/floor_heat_estimator'
package=ModuleType('_house_energy_tests')
package.__path__=[str(ROOT)]
sys.modules[package.__name__]=package
energy=importlib.import_module('_house_energy_tests.house_energy')
model=importlib.import_module('_house_energy_tests.house_model')


def steady(days=8):
    # Constant room/outdoor temperatures ensure exactly zero storage change.
    # Varying sunshine exercises gain subtraction without an envelope transient.
    p=model.HouseParameters(ci=2,ce=20,rie=.5,rea=3.5,solar_area=6,internal_gain=.5)
    points=[]
    for n in range(days*288+1):
        hour=n/12
        solar=.15*max(0,math.sin(math.pi*((hour%24)-6)/12))
        q=(20-8)/(p.rie+p.rea)-p.solar_area*solar-p.internal_gain
        points.append([n*300,20,8,q,solar])
    return p,points


class EnergyTests(unittest.TestCase):
    def test_exact_energy_balance_and_units_when_storage_is_zero(self):
        p,points=steady()
        report=energy.energy_metrics(points,p)
        self.assertGreater(report['count'],0)
        self.assertLess(report['rmse_mean_load_kW'],1e-10)
        self.assertLess(abs(report['bias_mean_load_kW']),1e-10)

    def test_room_warming_adds_capacity_times_temperature_change(self):
        p,points=steady(2)
        warmed=[r.copy() for r in points]
        for r in warmed:r[1]+=r[0]/3600*.1
        cold=energy.required_heat(points,p)
        hot=energy.required_heat(warmed,p)
        # The room storage alone adds 0.2 kW; extra envelope exchange is
        # non-negative as a room warms above the initially equilibrated wall.
        self.assertGreater(sum(v for v in hot if v is not None)-sum(v for v in cold if v is not None),.2*48)

    def test_window_alignment_and_missing_data_are_not_zero_filled(self):
        p,points=steady(3)
        self.assertTrue(any(w['hours']==72 for w in energy.energy_windows(points)))
        points[400][4]=None
        windows=energy.energy_windows(points)
        self.assertFalse(any(w['start']<=400<=w['end'] for w in windows))
        points=points[:100]+[[r[0]+600,*r[1:]] for r in points[100:]]
        self.assertFalse(any(w['start']<100<=w['end'] for w in energy.energy_windows(points)))

    def test_short_collinear_signature_keeps_configured_gains_and_recovers_loss(self):
        p,points=steady(3)
        signature=energy.fit_signature(points,p)
        self.assertEqual(signature['status'],'conditional_signature')
        self.assertTrue(signature['gains_fixed_to_configured_values'])
        self.assertEqual(signature['internal_gain_kW'],.5)
        self.assertEqual(signature['solar_area_m2'],6)
        self.assertAlmostEqual(signature['heat_loss_kW_K'],.25,delta=.001)
        bad=model.HouseParameters(ci=20,ce=539,rie=.106,rea=1.905,solar_area=39,internal_gain=0)
        bounded=energy.constrain(bad,signature)
        lo,hi=signature['heat_loss_support_kW_K']
        self.assertLessEqual(1/(bounded.rie+bounded.rea),hi+1e-12)
        self.assertGreaterEqual(1/(bounded.rie+bounded.rea),lo-1e-12)
        self.assertEqual(bounded.internal_gain,.5)
        self.assertEqual(bounded.solar_area,6)

    def test_heat_loss_error_cannot_be_hidden_by_process_noise(self):
        p,points=steady()
        from dataclasses import replace
        bad=replace(p,rie=.25,rea=1.75,sigma_i=.5)
        self.assertGreater(energy.energy_metrics(points,bad)['rmse_mean_load_kW'],2)
        self.assertLess(energy.energy_metrics(points,p)['rmse_mean_load_kW'],1e-10)

    def test_held_out_energy_uses_no_training_windows(self):
        p,points=steady()
        split=points[5*288][0]
        report=energy.energy_metrics(points,p,after=split)
        self.assertGreater(report['count'],0)
        self.assertTrue(all(w['start_utc_epoch']>=split for w in report['windows']))

    def test_anchor_needs_a_complete_day(self):
        p,points=steady()
        self.assertEqual(energy.fit_signature(points[:200],p)['status'],'insufficient_energy_data')


if __name__=='__main__':unittest.main()
