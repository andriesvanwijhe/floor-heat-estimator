"""Regression coverage for complete cycles and heat delivered to the house."""

from dataclasses import replace
import importlib
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

package=ModuleType('_floor_continuity_tests')
package.__path__=[str(Path(__file__).resolve().parents[1]/'custom_components/floor_heat_estimator')]
sys.modules[package.__name__]=package
model=importlib.import_module(package.__name__+'.model')
fitting=importlib.import_module(package.__name__+'.fitting')
identify=importlib.import_module(package.__name__+'.floor_identification')
replay=importlib.import_module(package.__name__+'.floor_replay')
data=importlib.import_module(package.__name__+'.house_data')
const=importlib.import_module(package.__name__+'.const')


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.settings={**const.DEFAULTS,'settling_minutes':2,
                       **{key:'sensor.'+key for key in const.SOURCE_KEYS}}
        self.start=1767225600.

    def sample(self,m,**values):
        return model.Sample(self.start+m*60,values.get('room',20),values.get('power',3),
            values.get('supply',30),values.get('return_temperature',26),
            values.get('flow',1200),values.get('mode','hc'))

    def test_valid_readings_and_restart_never_overwrite_predicted_storage(self):
        engine=model.Estimator(self.settings)
        engine.step(self.sample(0));engine.step(self.sample(2))
        seed=engine.temperatures.copy()
        reference=seed.copy()
        previous=self.sample(2)
        for minute in range(3,24):
            sample=self.sample(minute,mode='standby' if minute<15 else 'hc',
                flow=0 if minute<15 else 1200,power=0 if minute<15 else 3,
                supply=45,return_temperature=41)
            for name in reference:
                reference[name]=model.advance(reference[name],previous.room,engine._input(previous),60,engine.parameters[name])
            result=engine.step(sample)
            for name in reference:
                self.assertAlmostEqual(engine.temperatures[name],reference[name],places=10)
            if minute==17:
                self.assertTrue(result['restart_observation'])
                self.assertGreater(result['nonlinear_observation_error'],10)
                self.assertFalse(result['initialized_from_measurement'])
            previous=sample
        for name,balance in engine.energy_balance().items():
            self.assertAlmostEqual(balance['input_kWh']-balance['output_kWh'],
                engine.parameters[name].capacity*(engine.temperatures[name]-seed[name]),places=11)
            self.assertAlmostEqual(balance['residual_kWh'],0,places=11)

    def test_startup_snapshot_preserves_energy_ledger_and_restart_errors(self):
        engine=model.Estimator(self.settings)
        for m in range(10):engine.step(self.sample(m))
        other=model.Estimator(self.settings)
        self.assertTrue(other.restore(engine.snapshot()))
        sample=self.sample(10,mode='standby',flow=0)
        self.assertEqual(other.step(sample),engine.step(sample))

    def test_trace_spans_six_hours_and_scores_restart_first_reading(self):
        from test_fitting import synthetic_samples
        samples=synthetic_samples()
        points=fitting.prepare_points(samples,self.settings)
        traces=fitting.build_traces(points,self.settings)
        self.assertEqual(len(traces),1)
        self.assertGreater(sum(step.seconds for step in traces[0][1]),30*3600)
        observed=[p for p in points if p.observation is not None]
        targets=[step for step in traces[0][1] if step.target is not None]
        self.assertEqual(len(targets),len(observed)-1)
        self.assertEqual(sum(step.restart for step in targets),5)

    def test_cycle_weights_do_not_give_long_cycles_more_total_weight(self):
        Step=fitting.Step
        parameters=model.Parameters(1,2)
        steps=[Step(60,20,0,30,1,False) for _ in range(10)]
        steps += [Step(60,20,0,30,2,True)]
        errors,weights,metrics=identify.evaluate([(30,steps)],parameters)
        self.assertAlmostEqual(sum(w*w for w in weights[:10]),weights[-1]**2)
        self.assertEqual(metrics['restart_count'],1)

    def test_house_bins_preserve_floor_interval_energy_despite_timestamp_offsets(self):
        samples=[self.sample(m+.037,mode='hc' if m<10 or m>=25 else 'standby',
                    flow=1200 if m<10 or m>=25 else 0,power=3 if m<10 or m>=25 else 0,
                    supply=50 if m>=25 else 30,return_temperature=46 if m>=25 else 26)
                 for m in range(46)]
        intervals,balance=replay.reconstruct_floor(samples,self.settings)
        rows=[{'t':self.start+m*60,'room':20,'outdoor':5,'solar':.1,'heat':999,'calibration':'old'}
              for m in range(46)]
        merged=replay.merge_floor_intervals(rows,intervals,'new',600)
        bins=data.make_bins(merged,600,self.start+45*60)
        for t,room,outdoor,heat,solar in bins:
            expected=sum(max(0,min(t,s['end'])-max(t-300,s['start']))/(s['end']-s['start'])
                         *s['output_kWh']['nonlinear'] for s in intervals)
            self.assertAlmostEqual(heat*300/3600,expected,places=11)
        self.assertGreater(len(bins),5)
        self.assertEqual(balance['models']['nonlinear']['segment_count'],1)
        self.assertAlmostEqual(balance['models']['nonlinear']['residual_kWh'],0,places=11)

    def test_house_live_backward_interval_means_are_used_in_correct_interval(self):
        rows=[{'t':self.start+m*60,'room':20,'outdoor':5,'solar':.1,'heat':999,
               'heat_since_previous':float(m),'calibration':'new'} for m in range(6)]
        self.assertEqual(data.make_bins(rows)[0][3],3.)

    def test_old_house_csv_is_upgraded_without_losing_rows(self):
        import csv
        from datetime import UTC,datetime
        with TemporaryDirectory() as folder:
            path=Path(folder)/'2026-01-01.csv'
            row={'time_utc':datetime.fromtimestamp(self.start,UTC).isoformat(),
                 'room':20,'outdoor':5,'heat':3,'solar':.1,'calibration':'old'}
            with path.open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(row));writer.writeheader();writer.writerow(row)
            data.append_row(folder,{**row,'time_utc':datetime.fromtimestamp(self.start+60,UTC).isoformat(),
                                    'heat_since_previous':2.5})
            values,bad=data.read_rows(folder,self.start+120,28)
        self.assertEqual(bad,0);self.assertEqual(len(values),2)
        self.assertNotIn('heat_since_previous',values[0])
        self.assertEqual(values[1]['heat_since_previous'],2.5)


class ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_fitting import synthetic_samples
        cls.samples=synthetic_samples()
        cls.result=fitting.fit_samples(cls.samples,{**const.DEFAULTS,'exponent_max':1.3},cls.samples[-1].timestamp)

    def test_range_scan_and_boundary_report_do_not_claim_resolved_exponent(self):
        report=self.result
        self.assertEqual(len(report['exponent_profile']),7)
        self.assertEqual(report['exponent_profile_status'],'boundary_limited')
        self.assertAlmostEqual(report['models']['nonlinear']['exponent'],1.3,places=4)
        self.assertTrue(all(1<=p['exponent']<=1.3 and p['attempted_start_count']==3
                            for p in report['exponent_profile']))
        self.assertFalse(set(report['training_cycle_ids']) & set(report['validation_cycle_ids']))
        self.assertGreaterEqual(report['models']['nonlinear']['validation']['restart_count'],1)

    def test_profile_plot_is_standalone_svg_with_cost_axes(self):
        root=ET.fromstring(identify.profile_svg(self.result))
        self.assertEqual(root.tag,'{http://www.w3.org/2000/svg}svg')
        self.assertEqual(len(root.findall('.//{http://www.w3.org/2000/svg}circle')),7)
        self.assertIn('not a confidence interval',ET.tostring(root,encoding='unicode'))


if __name__=='__main__':unittest.main()
