"""House imports preserve zero solar, missing data and recording-run gaps."""

import asyncio
from datetime import UTC, datetime
import importlib
from pathlib import Path
import sys
from threading import Event
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]/'custom_components/floor_heat_estimator'
package=ModuleType('_house_history_tests')
package.__path__=[str(ROOT)]
sys.modules[package.__name__]=package
history=importlib.import_module('_house_history_tests.house_history')
const=importlib.import_module('_house_history_tests.const')


class HouseHistoryTests(unittest.TestCase):
    def setUp(self):
        self.settings={**const.DEFAULTS,'room_temperature':'sensor.room'}
        self.start=datetime(2026,1,1,tzinfo=UTC)
        self.end=datetime(2026,1,1,0,10,tzinfo=UTC)
        def state(value,unit,minutes=0):
            return SimpleNamespace(state=str(value),last_updated=datetime.fromtimestamp(self.start.timestamp()+minutes*60,UTC),
                                   attributes={'unit_of_measurement':unit})
        self.state=state
        self.states={self.settings[key]:[state(value,unit)] for key,value,unit in (
            ('room_temperature',20,'°C'),('house_outdoor_temperature',5,'°C'),
            ('house_heat_input',2000,'W'),('house_solar',0,'W/m²'))}

    def query(self,runs=None):
        runs=runs if runs is not None else [(self.start.timestamp()-1,self.end.timestamp())]
        with patch.object(history,'load_history',return_value=(self.states,runs)):
            return history.query_house_day(None,self.settings,self.start,self.end,Event())

    def test_night_zero_is_valid_but_unavailable_solar_is_not_zero(self):
        rows=self.query()
        self.assertEqual(rows[0]['solar'],0)
        self.assertEqual(rows[0]['heat'],2)
        self.states[self.settings['house_solar']].append(self.state('unavailable','W/m²',3))
        rows=self.query()
        self.assertIsNone(rows[3]['solar'])
        self.assertIsNone(rows[8]['solar'])

    def test_downtime_is_missing_and_restart_requires_new_source_values(self):
        rows=self.query([(self.start.timestamp()-1,self.start.timestamp()+120),
                         (self.start.timestamp()+240,self.end.timestamp())])
        self.assertIsNone(rows[2]['heat'])
        self.assertIsNone(rows[6]['heat'])

    def test_cancelled_query_does_not_return_partial_training(self):
        cancel=Event();cancel.set()
        with patch.object(history,'load_history',return_value=(self.states,[])):
            with self.assertRaises(asyncio.CancelledError):
                history.query_house_day(None,self.settings,self.start,self.end,cancel)


if __name__=='__main__':unittest.main()
