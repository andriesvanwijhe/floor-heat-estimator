"""The product distribution must be independent of Home Assistant."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_standalone_build',ROOT/'standalone/build.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


class StandaloneTests(unittest.TestCase):
    def test_generated_distribution_runs_steady_forecast_without_home_assistant(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=builder.build(directory)
            request={'operation':'steady','request':{'house':{},'now':'2026-01-01T00:00:00+00:00',
                'weather':{'time':[1767225600+i*3600 for i in range(49)],
                    'temperature_2m':[0]*49,'shortwave_radiation':[0]*49}}}
            result=subprocess.run([sys.executable,str(folder/'thermal_service.py')],
                input=json.dumps(request),capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            response=json.loads(result.stdout)
            self.assertEqual(response['status'],'ready')
            self.assertEqual(response['hours'],24)
            self.assertEqual(response['energy_kWh'],84)
            self.assertFalse((folder/'thermal_core/__init__.py').read_text().find('homeassistant')>=0)
            self.assertFalse((folder/'thermal_core/house_runtime.py').exists())


if __name__=='__main__':unittest.main()
