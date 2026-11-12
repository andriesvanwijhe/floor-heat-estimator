"""Energy conservation, sensitivities and forecast behavior, independent of HA."""
import importlib
from pathlib import Path
import sys
from types import ModuleType
import unittest
import numpy as np

package=ModuleType('_dynamic_forecast_tests')
package.__path__=[str(Path(__file__).resolve().parents[1]/'custom_components/floor_heat_estimator')]
sys.modules[package.__name__]=package
dynamic=importlib.import_module(package.__name__+'.dynamic_forecast')
weather=importlib.import_module(package.__name__+'.house_forecast')


class DynamicTests(unittest.TestCase):
    def request(self,room=20,floor=28,solar=0,power=8,hours=24):
        p=dynamic.HouseParameters(ci=2,ce=15,rie=.6,rea=3.7,solar_area=10,internal_gain=.5)
        return {'house':p.to_dict(),'floor':{'capacity':.25,'output_at_5k':1.6,'exponent':1.5},
            'initial':[room,18,floor],'maximum_power_kW':power,'max_seconds':60,
            'hours':[{'start':1794441600+3600*i,'seconds':3600,'outdoor_C':5,'solar_kW_m2':solar} for i in range(hours)]}

    def test_total_energy_and_positive_floor_output(self):
        r=self.request();p=dynamic.HouseParameters(**r['house']);f=dynamic.Parameters(**r['floor'])
        model=dynamic.CoupledModel(p,f);state=tuple(r['initial'])
        for power in (0,1,8,0)*8:
            old=state;state,q,loss,_=model.step(old,5,.1,power)
            stored=sum(c*(b-a) for c,a,b in zip((p.ci,p.ce,f.capacity),old,state))
            self.assertAlmostEqual(stored,model.dt*(power+p.solar_area*.1+p.internal_gain)-loss,places=12)
            self.assertAlmostEqual(f.capacity*(state[2]-old[2]),model.dt*(power-q),places=12)
            self.assertGreaterEqual(q,0)

    def test_analytic_control_sensitivity_matches_finite_difference(self):
        r=self.request(hours=4);m=dynamic.CoupledModel(dynamic.HouseParameters(**r['house']),dynamic.Parameters(**r['floor']))
        controls=np.array([1.,3.,0.,2.]);base=m.rollout(r['hours'],r['initial'],controls,derivatives=True)
        for i in range(4):
            plus=controls.copy();minus=controls.copy();plus[i]+=1e-4;minus[i]-=1e-4
            numerical=(m.rollout(r['hours'],r['initial'],plus)['states'][:,0]-m.rollout(r['hours'],r['initial'],minus)['states'][:,0])/2e-4
            np.testing.assert_allclose(numerical,base['room_jacobian'][:,i],atol=1e-7,rtol=1e-5)

    def test_five_minute_coupling_agrees_with_independent_high_accuracy_ode(self):
        from scipy.integrate import solve_ivp
        r=self.request(hours=6);p=dynamic.HouseParameters(**r['house']);f=dynamic.Parameters(**r['floor'])
        coupled=dynamic.CoupledModel(p,f)
        controls=[3.]*6
        predicted=coupled.rollout(r['hours'],r['initial'],controls)['states'][-1]
        def derivative(t,x):
            ti,te,tf=x;q=f.output(tf,ti)
            return [(q+p.internal_gain-(ti-te)/p.rie)/p.ci,
                ((ti-te)/p.rie-(te-5)/p.rea)/p.ce,(3-q)/f.capacity]
        reference=solve_ivp(derivative,(0,6),r['initial'],rtol=1e-10,atol=1e-11).y[:,-1]
        np.testing.assert_allclose(predicted,reference,atol=.015,rtol=0)

    def test_planner_holds_comfort_without_resetting_room(self):
        result=dynamic.predict(self.request())
        self.assertEqual(result['status'],'ready',result)
        self.assertGreater(result['energy_kWh'],0)
        self.assertGreaterEqual(result['minimum_room_temperature_C'],19.95)
        self.assertTrue(all(abs(r['energy_balance_residual_kWh'])<1e-10 for r in result['hourly']))

    def test_solar_overheating_is_predicted_without_fictitious_cooling(self):
        result=dynamic.predict(self.request(room=22,floor=22,solar=.5))
        self.assertEqual(result['status'],'ready',result)
        self.assertGreater(result['maximum_room_temperature_C'],22)
        self.assertLess(result['energy_kWh'],1e-3)
        self.assertTrue(all(r['pump_heat_input_kW']>=0 for r in result['hourly']))

    def test_capacity_shortfall_is_exposed(self):
        result=dynamic.predict(self.request(power=.1))
        self.assertEqual(result['status'],'comfort_shortfall',result)
        self.assertGreater(result['maximum_power_shortfall_K'],.1)

    def test_hot_floor_reduces_first_hour_pump_demand(self):
        cold=dynamic.predict(self.request(floor=22))
        warm=dynamic.predict(self.request(floor=34))
        self.assertLess(warm['hourly'][0]['pump_energy_kWh'],cold['hourly'][0]['pump_energy_kWh'])

    def test_weather_keeps_partial_hour_energy_and_missing_data(self):
        start=1794441600
        attrs={'time':[start+i*3600 for i in range(50)],'temperature_2m':[5]*50,
               'shortwave_radiation':list(range(50))}
        rows=weather.weather_intervals(attrs,start+900,'Europe/Amsterdam',48)
        self.assertEqual(len(rows),49)
        self.assertEqual(rows[0]['seconds'],2700)
        self.assertEqual(rows[-1]['seconds'],900)
        self.assertAlmostEqual(sum(r['seconds'] for r in rows),48*3600)
        self.assertEqual(rows[0]['solar_kW_m2'],.001)
        attrs['shortwave_radiation'][3]=None
        self.assertEqual(weather.weather_intervals(attrs,start,'Europe/Amsterdam'),[])

    def test_extended_plan_publishes_exactly_twenty_four_hours(self):
        request=self.request(hours=48)
        result=dynamic.predict(request)
        self.assertEqual(result['planning_horizon_hours'],48)
        self.assertEqual(result['hours'],24)
        self.assertAlmostEqual(sum(r['duration_hours'] for r in result['hourly']),24)

    def test_default_floor_model_runs_exponent_only_and_restores_legacy_snapshot(self):
        model=importlib.import_module(package.__name__+'.model')
        defaults=importlib.import_module(package.__name__+'.const').DEFAULTS
        engine=model.Estimator(defaults)
        self.assertEqual(set(engine.parameters),{'nonlinear'})
        legacy=model.Estimator({**defaults,'legacy_linear_enabled':True,'settling_minutes':0})
        sample=model.Sample(1794441600,20,3,30,26,1200,'hc')
        legacy.step(sample)
        self.assertTrue(engine.restore(legacy.snapshot()))
        self.assertEqual(set(engine.temperatures),{'nonlinear'})
        result=engine.step(model.Sample(1794441660,20,3,30,26,1200,'hc'))
        self.assertIsNone(result['linear_power'])
        self.assertIsNotNone(result['nonlinear_power'])


if __name__=='__main__':unittest.main()
