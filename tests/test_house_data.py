"""House unit conversion, interval integration and source provenance."""

from datetime import UTC, datetime
import importlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]/"custom_components/floor_heat_estimator"
package = ModuleType("_house_data_tests")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
data = importlib.import_module("_house_data_tests.house_data")
const = importlib.import_module("_house_data_tests.const")


class HouseDataTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026,1,1,tzinfo=UTC).timestamp()
        self.settings = {**const.DEFAULTS, **{key:"sensor."+key for key in const.SOURCE_KEYS}}

    def rows(self):
        return [{"t":self.start+m*60,"room":20,"outdoor":5,"heat":float(m),"solar":.2,"calibration":"a"}
                for m in range(11)]

    def test_solar_units_and_missing_state_are_explicit(self):
        for unit in ("W/m²","W/m2","W/m^2"):
            self.assertEqual(data.solar_state(SimpleNamespace(state="500",attributes={"unit_of_measurement":unit})),.5)
        self.assertEqual(data.solar_state(SimpleNamespace(state="0",attributes={"unit_of_measurement":"W/m²"})),0)
        self.assertIsNone(data.solar_state(None))
        self.assertIsNone(data.solar_state(SimpleNamespace(state="1",attributes={"unit_of_measurement":"kWh/m²"})))
        self.assertIsNone(data.solar_state(SimpleNamespace(state="unavailable",attributes={"unit_of_measurement":"W/m²"})))

    def test_bins_preserve_power_energy_and_do_not_use_future_room(self):
        rows = self.rows()
        rows[6]["room"] = 99
        bins = data.make_bins(rows)
        self.assertEqual(len(bins),2)
        self.assertEqual(bins[0][3],2)  # mean of held 0,1,2,3,4 kW
        self.assertEqual(bins[0][1],20)
        self.assertEqual(bins[0][4],.2)

    def test_missing_inputs_gaps_and_calibration_changes_break_bins(self):
        rows = self.rows()
        rows[2]["solar"] = None
        self.assertEqual(len(data.make_bins(rows)),1)
        rows = self.rows()
        rows[2]["calibration"] = 'different'
        self.assertEqual(len(data.make_bins(rows)),1)
        rows = self.rows()
        rows[4]["t"] += 10000
        self.assertLess(len(data.make_bins(rows,maximum_gap=90)),2)

    def test_imported_live_overlap_is_deduplicated_and_live_wins(self):
        with TemporaryDirectory() as folder:
            row = {"time_utc":datetime.fromtimestamp(self.start,UTC).isoformat(),
                   "room":20,"outdoor":5,"heat":2,"solar":.2,"calibration":"old"}
            data.write_rows(Path(folder)/'imported','2026-01-01',[row])
            data.append_row(folder,{**row,"heat":3,"calibration":"new"})
            values,bad = data.read_rows(folder,self.start+60,28)
        self.assertEqual(bad,0)
        self.assertEqual(len(values),1)
        self.assertEqual(values[0]['heat'],3)
        self.assertEqual(values[0]['calibration'],'new')

    def test_raw_floor_replay_overrides_historical_estimates_with_one_calibration(self):
        from test_fitting import synthetic_samples
        raw = synthetic_samples()
        rows = [{"t":p.timestamp,"room":p.room,"outdoor":5,"heat":999,"solar":.2,"calibration":"old"} for p in raw]
        with patch.object(data,'read_rows',return_value=(rows,0)), patch.object(data,'read_samples',return_value=(raw,0)):
            request = data.prepare_fit_request('/unused','/unused',self.settings,raw[-1].timestamp,True)
        self.assertEqual(request['input_provenance'],'raw_floor_replay')
        self.assertGreater(len(request['points']),10)
        self.assertTrue(all(point[3]<100 for point in request['points']))

    def test_source_and_gate_changes_invalidate_history_but_coefficients_do_not(self):
        original = data.house_signature(self.settings)
        self.assertNotEqual(original,data.house_signature({**self.settings,'house_solar':'sensor.other'}))
        self.assertNotEqual(original,data.house_signature({**self.settings,'flow_threshold':150}))
        self.assertEqual(original,data.house_signature({**self.settings,'linear_capacity':10}))
        self.assertNotEqual(data.floor_signature(self.settings),data.floor_signature({**self.settings,'linear_capacity':10}))


if __name__=='__main__':
    unittest.main()
