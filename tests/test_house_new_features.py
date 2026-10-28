"""Daily correction, early provisional fits and steady-state forecast energy."""

from datetime import UTC, datetime, time, timedelta
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]/'custom_components/floor_heat_estimator'
package = ModuleType('_house_new_tests')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
model = importlib.import_module('_house_new_tests.house_model')
forecast = importlib.import_module('_house_new_tests.house_forecast')
fitting = importlib.import_module('_house_new_tests.house_fit')


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


class MidnightTests(unittest.TestCase):
    def test_daytime_is_open_loop_then_midnight_corrects_once(self):
        engine = model.HouseEstimator(correction_timezone='Europe/Amsterdam')
        engine.step([stamp('2026-10-27T23:50:00+01:00'),20,5,2,0])
        before=engine.state
        engine.step([stamp('2026-10-27T23:55:00+01:00'),30,5,2,0])
        expected=model.advance(before,5,2,0,engine.parameters,model.transition(engine.parameters,1/12))
        self.assertEqual(engine.state,expected)
        engine.step([stamp('2026-10-28T00:00:00+01:00'),30,5,2,0])
        self.assertGreater(engine.state[0],expected[0]+1)
        self.assertEqual(engine.last_correction,engine.timestamp)
        corrected=engine.state
        engine.step([stamp('2026-10-28T00:05:00+01:00'),10,5,2,0])
        self.assertEqual(engine.state,model.advance(corrected,5,2,0,engine.parameters,model.transition(engine.parameters,1/12)))

    def test_missing_midnight_retries_and_restart_does_not_correct_twice(self):
        engine=model.HouseEstimator(correction_timezone='Europe/Amsterdam')
        engine.step([stamp('2026-10-27T23:55:00+01:00'),20,5,2,0])
        engine.step([stamp('2026-10-28T00:00:00+01:00'),None,5,2,0])
        self.assertIsNone(engine.last_correction)
        engine.step([stamp('2026-10-28T00:05:00+01:00'),22,5,2,0])
        saved=engine.snapshot()
        restored=model.HouseEstimator(correction_timezone='Europe/Amsterdam')
        self.assertTrue(restored.restore(saved))
        restored.step([stamp('2026-10-28T00:10:00+01:00'),30,5,2,0])
        self.assertEqual(restored.last_correction,saved['last_correction'])

    def test_local_midnight_differs_from_utc_and_gaps_initialize(self):
        self.assertTrue(model.midnight_due(stamp('2026-06-01T22:00:00+00:00'),None,'Europe/Amsterdam'))
        self.assertFalse(model.midnight_due(stamp('2026-06-01T00:00:00+00:00'),None,'Europe/Amsterdam'))
        engine=model.HouseEstimator(correction_timezone='Europe/Amsterdam')
        engine.step([stamp('2026-10-28T12:00:00+01:00'),20,5,2,0])
        engine.step([stamp('2026-10-28T13:00:00+01:00'),24,5,2,0])
        self.assertEqual(engine.state[0],24)
        self.assertIsNone(engine.last_correction)


class DemandTests(unittest.TestCase):
    def payload(self, today='2026-10-28T12:00:00+01:00'):
        now=datetime.fromisoformat(today)
        zone=ZoneInfo('Europe/Amsterdam')
        day=now.astimezone(zone).date()+timedelta(days=1)
        start=datetime.combine(day,time(),zone).timestamp()
        end=datetime.combine(day+timedelta(days=1),time(),zone).timestamp()
        times=list(range(int(start),int(end)+1,3600))
        return now,{'time':times,'temperature_2m':[5.0]*len(times),'shortwave_radiation':[0.0]*len(times)}

    def run_forecast(self, payload, now, parameters=None):
        return forecast.tomorrow_demand(payload,parameters or model.HouseParameters(),now,'Europe/Amsterdam')

    def test_steady_state_balances_gains_and_integrates_energy(self):
        now,data=self.payload()
        result=self.run_forecast(data,now)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['average_load_kW'],2.5)
        self.assertEqual(result['energy_kWh'],60)
        self.assertEqual(result['hours'],24)
        data['shortwave_radiation']=[100]*25
        self.assertEqual(self.run_forecast(data,now)['energy_kWh'],36)
        data['shortwave_radiation']=[1000]*25
        self.assertEqual(self.run_forecast(data,now)['energy_kWh'],0)

    def test_preceding_hour_radiation_alignment_and_temperature_endpoints(self):
        now,data=self.payload()
        data['shortwave_radiation'][0]=1000
        data['shortwave_radiation'][1]=100
        data['temperature_2m'][0]=3
        result=self.run_forecast(data,now)
        self.assertEqual(result['hourly'][0]['outdoor_temperature_C'],4)
        self.assertAlmostEqual(result['hourly'][0]['heat_load_kW'],1.7)

    def test_dst_days_have_correct_total_hours_and_unique_offsets(self):
        for today,hours in [('2026-03-28T12:00:00+01:00',23),('2026-10-24T12:00:00+02:00',25)]:
            now,data=self.payload(today)
            result=self.run_forecast(data,now)
            self.assertEqual(result['status'],'ready')
            self.assertEqual(result['hours'],hours)
            self.assertEqual(result['energy_kWh'],hours*2.5)

    def test_missing_null_negative_and_duplicate_data_are_unavailable(self):
        for kind in ('missing','null','negative','duplicate'):
            now,data=self.payload()
            if kind=='missing': data['time'].pop()
            if kind=='null': data['shortwave_radiation'][10]=None
            if kind=='negative': data['shortwave_radiation'][10]=-1
            if kind=='duplicate': data['time'][10]=data['time'][9]
            result=self.run_forecast(data,now)
            self.assertEqual(result['status'],'incomplete_forecast',kind)
            self.assertNotIn('energy_kWh',result)

    def test_naive_local_timestamps_supported_but_ambiguous_dst_rejected(self):
        for today,status in [('2026-10-28T12:00:00+01:00','ready'),('2026-10-24T12:00:00+02:00','incomplete_forecast')]:
            now,data=self.payload(today)
            data['time']=[datetime.fromtimestamp(t,ZoneInfo('Europe/Amsterdam')).replace(tzinfo=None).isoformat() for t in data['time']]
            self.assertEqual(self.run_forecast(data,now)['status'],status)


class ForcedFitTests(unittest.TestCase):
    def test_force_still_requires_real_data(self):
        result=fitting.fit_house({'points':[],'force':True,'baseline':model.HouseParameters().to_dict()})
        self.assertFalse(result['accepted'])
        self.assertIn('twelve hours',result['reason'])

    def test_short_unexcited_fit_can_be_provisional_but_not_validated(self):
        parameters=model.HouseParameters()
        start=stamp('2026-10-26T00:00:00+00:00')
        points=[[start+n*300,20,5,2,0] for n in range(2*288+1)]
        bad_prior=model.HouseParameters(ci=20,ce=539,rie=.106,rea=1.905,solar_area=39,internal_gain=0)
        request={'points':points,'baseline':bad_prior.to_dict(),'energy_prior':parameters.to_dict(),'starts':1,'max_seconds':30}
        self.assertEqual(fitting.fit_house(request)['status'],'insufficient_data')
        def optimizer(fn, values, **kwargs):
            return SimpleNamespace(x=values,fun=fn(values),success=True,message='test converged')
        with patch('scipy.optimize.minimize',optimizer):
            result=fitting.fit_house({**request,'force':True,'correction_timezone':'Europe/Amsterdam'})
        self.assertTrue(result['accepted'],result)
        self.assertTrue(result['provisional'])
        self.assertEqual(result['status'],'forced_provisional')
        self.assertIn('seven_day_coverage',result['bypassed_checks'])
        self.assertIn('input_excitation',result['bypassed_checks'])
        self.assertIn('held_out_validation',result['bypassed_checks'])
        self.assertIsNotNone(result['final_state'])
        self.assertTrue(result['energy_anchor_enforced'])
        self.assertEqual(result['parameters']['internal_gain'],parameters.internal_gain)
        lo,hi=result['energy_signature']['heat_loss_support_kW_K']
        h=1/(result['parameters']['rie']+result['parameters']['rea'])
        self.assertTrue(lo-1e-12<=h<=hi+1e-12)
        self.assertLess(h,1/(bad_prior.rie+bad_prior.rea)*.6)


if __name__=='__main__':unittest.main()
