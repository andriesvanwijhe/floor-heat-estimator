"""Replay actual historical inputs from the latest valid circulation sample."""
from pathlib import Path
import importlib
import sys
from types import ModuleType
import unittest

package=ModuleType('_init_tests')
package.__path__=[str(Path(__file__).resolve().parents[1]/'custom_components/floor_heat_estimator')]
sys.modules[package.__name__]=package
model=importlib.import_module('_init_tests.model')
init=importlib.import_module('_init_tests.initialization')
const=importlib.import_module('_init_tests.const')
structure=importlib.import_module('_init_tests.house_structure')
house=importlib.import_module('_init_tests.house_model')


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.settings={**const.DEFAULTS,'settling_minutes':2}
        self.samples=[model.Sample(100000+n*60,20,3 if n<6 else 0,30,26,300 if n<6 else 0,'hc' if n<6 else 'off') for n in range(30)]

    def test_replay_matches_uninterrupted_model_during_cooldown(self):
        continuous=model.Estimator(self.settings)
        # Recovery is deliberately anchored at the latest raw measurement,
        # then predicted through cooldown, rather than resetting at restarts.
        continuous.temperatures={'linear':28.,'nonlinear':28.}
        continuous.previous=self.samples[5]
        continuous.last_observation=self.samples[5].timestamp
        for s in self.samples[6:]:continuous.step(s)
        replay,report=init.replay_floor(self.samples,self.settings,self.samples[-1].timestamp)
        self.assertEqual(report['status'],'replayed')
        self.assertEqual(report['measurement_timestamp'],self.samples[5].timestamp)
        self.assertAlmostEqual(replay.temperatures['nonlinear'],continuous.temperatures['nonlinear'])
        self.assertAlmostEqual(replay.temperatures['linear'],continuous.temperatures['linear'])

    def test_missing_room_or_power_prevents_initialization(self):
        from dataclasses import replace
        for key in ('room','power'):
            samples=self.samples.copy()
            # Missing power matters in active mode; missing room always matters.
            samples[15]=replace(samples[15],**{key:None,'mode':'hc'})
            engine,report=init.replay_floor(samples,self.settings,samples[-1].timestamp)
            self.assertIsNone(engine)
            self.assertEqual(report['status'],'initialization_incomplete')

    def test_no_flow_measurements_are_not_used(self):
        from dataclasses import replace
        samples=[replace(s,flow=0) for s in self.samples]
        self.assertIsNone(init.replay_floor(samples,self.settings,samples[-1].timestamp)[0])

    def test_missing_tail_is_not_extrapolated_with_current_power(self):
        self.assertIsNone(init.replay_floor(self.samples,self.settings,self.samples[-1].timestamp+3600)[0])

    def test_collapsed_or_unresolved_modes_rejected(self):
        self.assertFalse(structure.structure(house.HouseParameters(rie=5,rea=.01),72)['supported'])
        self.assertFalse(structure.structure(house.HouseParameters(ci=10,ce=999,rie=5,rea=.375),72)['supported'])
        self.assertTrue(structure.structure(house.HouseParameters(ci=1,ce=8,rie=.4,rea=3.6),168)['supported'])

    def test_short_startup_unavailable_rows_replay_with_declared_assumption(self):
        samples=self.samples.copy()
        samples[15]=model.Sample(samples[15].timestamp,None,None,None,None,None,None)
        samples[16]=model.Sample(samples[16].timestamp,None,None,None,None,None,'unavailable')
        replay,report=init.replay_floor(samples,self.settings,samples[-1].timestamp)
        reference,_=init.replay_floor(self.samples,self.settings,samples[-1].timestamp)
        self.assertEqual(report['status'],'replayed')
        self.assertEqual(len(report['estimated_gaps']),1)
        self.assertEqual(report['estimated_gaps'][0]['samples'],2)
        self.assertAlmostEqual(replay.temperatures['nonlinear'],reference.temperatures['nonlinear'])

    def test_startup_gap_cannot_bridge_changed_power_mode_room_or_long_gaps(self):
        from dataclasses import replace
        for kind in ('mode','room','long','tail','power'):
            samples=self.samples.copy()
            if kind=='power':samples=[replace(s,mode='hc',power=3,flow=0) if i>5 else s for i,s in enumerate(samples)]
            samples[15]=model.Sample(samples[15].timestamp,None,None,None,None,None,None)
            if kind=='mode':samples[16]=replace(samples[16],mode='dhw')
            if kind=='room':samples[16]=replace(samples[16],room=22)
            if kind=='power':samples[16]=replace(samples[16],power=5)
            if kind=='long':samples=[replace(s,timestamp=s.timestamp+1800) if i>=16 else s for i,s in enumerate(samples)]
            if kind=='tail':samples=samples[:16]
            self.assertIsNone(init.replay_floor(samples,self.settings,samples[-1].timestamp)[0],kind)


if __name__=='__main__':unittest.main()
