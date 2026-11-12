"""One-shot JSON thermal-model service, also suitable for Nuitka compilation.

Run with an input file or stdin. No Home Assistant installation is needed.
Keep stdin/stdout framing at the host layer (MQTT, Modbus, HTTP, files, etc.).
"""
from pathlib import Path
import argparse
import json
import os
import sys
import time

# Set before importing numerical libraries, including on small ARM boards.
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name]='1'

# Explicit imports make the standalone package visible to Python compilers.
# standalone/build.py copies only these numerical modules, with an empty init.
from thermal_core import (const, dynamic_forecast, fitting, house_fit,
                          house_forecast, house_model, model)


def dispatch(request):
    operation=request.get('operation','dynamic')
    payload=request.get('request',request)
    if operation=='dynamic':
        return dynamic_forecast.predict(payload)
    if operation=='steady':
        from datetime import datetime
        return house_forecast.tomorrow_demand(payload['weather'],house_model.HouseParameters(**payload['house']),
            datetime.fromisoformat(payload['now']),payload.get('timezone','UTC'),payload.get('target_C',20))
    if operation=='fit_house':
        payload={**payload,'max_seconds':min(3600,payload.get('max_seconds',3600))}
        return house_fit.fit_house(payload)
    if operation=='fit_floor':
        settings={**const.DEFAULTS,**payload.get('settings',{}),'legacy_linear_enabled':False}
        samples=[model.Sample(**row) for row in payload['samples']]
        return fitting.fit_samples(samples,settings,payload['now'],budget_seconds=min(3600,payload.get('max_seconds',3600)))
    raise ValueError('Unknown operation; choose dynamic, steady, fit_house or fit_floor')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',nargs='?',help='JSON request file; default stdin')
    parser.add_argument('--output',help='JSON response file; default stdout')
    parser.add_argument('--measure',action='store_true',help='Include wall time and process peak resident RAM')
    args=parser.parse_args()
    began=time.monotonic()
    try:
        if os.name=='posix':os.nice(10)
        request=json.loads(Path(args.input).read_text(encoding='utf-8')) if args.input else json.load(sys.stdin)
        result=dispatch(request)
    except Exception as error:
        result={'status':'error','reason':f'{type(error).__name__}: {error}'}
    if args.measure:
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD),
                    *[(name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]]
            counters=Counters();counters.cb=ctypes.sizeof(counters)
            kernel=ctypes.windll.kernel32;kernel.GetCurrentProcess.restype=wintypes.HANDLE
            get_memory=ctypes.windll.psapi.GetProcessMemoryInfo
            get_memory.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
            get_memory.restype=wintypes.BOOL
            if not get_memory(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb):
                raise OSError('Could not measure process memory')
            peak=counters.PeakWorkingSetSize/1024**2
        else:
            import resource
            peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024)
        result['resources']={'wall_seconds':time.monotonic()-began,'peak_resident_MiB':peak,
                             'platform':sys.platform,'python_version':sys.version.split()[0]}
    text=json.dumps(result,allow_nan=False)
    if args.output:Path(args.output).write_text(text+'\n',encoding='utf-8')
    else:print(text)
    return 1 if result.get('status') in ('error','timeout','optimizer_failed') else 0


if __name__=='__main__':sys.exit(main())
