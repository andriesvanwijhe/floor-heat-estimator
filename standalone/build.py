"""Build a small HA-independent source distribution; optionally compile it."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

MODULES=('const','house_const','model','fitting','initialization','floor_identification',
         'house_model','house_balance','house_forecast','dynamic_forecast','house_fit',
         'house_simulation','house_structure','house_energy')


def build(output):
    here=Path(__file__).resolve().parent
    source=here.parent/'custom_components/floor_heat_estimator'
    output=Path(output).resolve();package=output/'thermal_core'
    package.mkdir(parents=True,exist_ok=True)
    (package/'__init__.py').write_text('"""Portable thermal identification and forecasting."""\n')
    for name in MODULES:shutil.copyfile(source/f'{name}.py',package/f'{name}.py')
    shutil.copyfile(here/'thermal_service.py',output/'thermal_service.py')
    (output/'requirements.txt').write_text('scipy==1.18.1\n')
    if (here/'examples').exists():
        shutil.copytree(here/'examples',output/'examples',dirs_exist_ok=True)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='dist/standalone')
    parser.add_argument('--compile',action='store_true',help='Requires Nuitka and a C compiler on the build machine')
    args=parser.parse_args();output=build(args.output)
    if args.compile:
        subprocess.run([sys.executable,'-m','nuitka','--mode=standalone','--jobs=1',
            '--include-package=thermal_core','--output-dir=compiled','thermal_service.py'],
            cwd=output,check=True)
    print(output)
