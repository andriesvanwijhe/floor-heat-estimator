"""Acceptance tolerance semantics without running the numerical optimizer."""
import importlib
from pathlib import Path
import sys
from types import ModuleType
import unittest

package = ModuleType('_floor_tolerance_tests')
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'custom_components/floor_heat_estimator')]
sys.modules[package.__name__] = package
identification = importlib.import_module(package.__name__ + '.floor_identification')
const = importlib.import_module(package.__name__ + '.const')


class ToleranceTests(unittest.TestCase):
    def test_defaults_preserve_existing_baseline_rules(self):
        for error in (None, 0, .02, -.02, 1, -1):
            self.assertAlmostEqual(identification.baseline_error_limit(const.DEFAULTS,error),
                                   max(abs(error or 0)*1.05,.05))

    def test_doubling_tolerances_doubles_allowance_not_entire_baseline(self):
        settings = {**const.DEFAULTS, 'floor_fit_baseline_worsening_percent':10,
                    'floor_fit_baseline_error_floor':.1}
        self.assertAlmostEqual(identification.baseline_error_limit(settings,1),1.1)
        self.assertAlmostEqual(identification.baseline_error_limit(settings,0),.1)
        self.assertAlmostEqual(identification.baseline_error_limit(settings,-1),1.1)
        self.assertLessEqual(.759432,identification.baseline_error_limit(settings,.692613))

    def test_zero_allowances_require_matching_or_improving_baseline(self):
        settings = {'floor_fit_baseline_worsening_percent':0,'floor_fit_baseline_error_floor':0}
        self.assertEqual(identification.baseline_error_limit(settings,.3),.3)
        self.assertEqual(identification.baseline_error_limit(settings,None),0)


if __name__ == '__main__':
    unittest.main()
