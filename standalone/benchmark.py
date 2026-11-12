"""Run separate measured service processes; store benchmark inputs privately.

This is a workload benchmark, not an RK3326 emulator. Run the same script on the
target board for acceptance. --private-history adds a supplied comparison CSV.
"""
import argparse
import csv
from datetime import datetime, UTC
import json
import math
from pathlib import Path
import platform
import subprocess
import sys
import time

from build import build


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='benchmark-results')
    parser.add_argument('--private-history',help='Optional comparison CSV; never include it in a public release')
    args=parser.parse_args()
    folder=Path(args.output).resolve();folder.mkdir(parents=True,exist_ok=True)
    package=build(folder/'service')
    house={'ci':5.,'ce':20.,'rie':.6,
           'rea':3.7,'solar_area':10.,'internal_gain':.5,
           'sigma_i':.02,'sigma_e':.01,'sigma_y':.1}
    start=1794528000
    hours=[]
    for i in range(48):
        local_hour=i%24
        hours.append({'start':start+3600*i,'seconds':3600,
            'outdoor_C':5+2*math.sin((local_hour-10)*math.pi/12),
            'solar_kW_m2':max(0,.35*math.sin((local_hour-8)*math.pi/8)) if 8<=local_hour<=16 else 0})
    prediction={'operation':'dynamic','request':{'house':house,
        'floor':{'capacity':.198873,'output_at_5k':1.615004,'exponent':1.500063},
        'initial':[20.1,18,28],'hours':hours,'max_seconds':600,'maximum_power_kW':8}}
    inputs={'prediction_48h':prediction}
    weather={'time':[start+3600*i for i in range(73)],
             'temperature_2m':[5.]*73,'shortwave_radiation':[0.]*73}
    inputs['steady_prediction']={'operation':'steady','request':{'house':house,'weather':weather,
        'now':datetime.fromtimestamp(start,UTC).isoformat(),'timezone':'UTC'}}
    if args.private_history:
        points=[]
        for row in csv.DictReader(Path(args.private_history).open(encoding='utf-8')):
            try:
                point=[datetime.fromisoformat(row['time_local']).timestamp(),float(row['measured_room_C']),
                    float(row['outdoor_C']),float(row['recorded_floor_output_kW']),float(row['ghi_W_m2'])/1000]
                if all(math.isfinite(v) for v in point):points.append(point)
            except (ValueError,KeyError):continue
        inputs['house_fit_14d']={'operation':'fit_house','request':{'points':points,
            'baseline':house,'energy_prior':house,'dynamics_prior':house,
            'force':True,'max_seconds':120,'gain_modes':{'solar_area':'prior','internal_gain':'prior'},
            'correction_timezone':'Europe/Amsterdam','solar_uncertainty':5,'solar_memory_hours':3,
            'capacity_prior_strength':1,'ratio_prior_strength':1}}
    # Known nonlinear floor response, 14 daily heating/cooldown cycles. The
    # source generator is outside the measured worker, so startup RAM is fair.
    sys.path.insert(0,str(package))
    from thermal_core.model import Parameters, advance
    floor=Parameters(.25,1.6,1.4);tf=25.;samples=[]
    for minute in range(14*1440):
        room=20+.2*math.sin(minute/330)
        active=minute%1440<480
        power=(4 if minute%1440<180 else 2) if active else 0
        samples.append({'timestamp':start+minute*60,'room':room,'power':power,
            'supply':tf+1 if active else None,'return_temperature':tf-1 if active else None,
            'flow':1200 if active else 0,'mode':'hc' if active else 'standby'})
        tf=advance(tf,room,power,60,floor)
    inputs['floor_fit_14d']={'operation':'fit_floor','request':{'samples':samples,
        'settings':{'learning_days':28,'maximum_fit_rmse':2},
        'now':samples[-1]['timestamp'],'max_seconds':120}}
    reports={}
    for name,request in inputs.items():
        source=folder/f'{name}-input.json';destination=folder/f'{name}-result.json'
        source.write_text(json.dumps(request,allow_nan=False),encoding='utf-8')
        began=time.monotonic()
        process=subprocess.run([sys.executable,str(package/'thermal_service.py'),str(source),
            '--measure','--output',str(destination)],capture_output=True,text=True,timeout=160)
        if not destination.exists():raise RuntimeError(process.stderr or process.stdout)
        result=json.loads(destination.read_text())
        reports[name]={'status':result.get('status'),'process_exit_code':process.returncode,
            'cold_process_wall_seconds':time.monotonic()-began,'resources':result.get('resources'),
            'optimizer_iterations':result.get('optimizer_iterations'),
            'observation_count':result.get('observation_count')}
        print(name,json.dumps(reports[name]),flush=True)
    summary={'platform':platform.platform(),'machine':platform.machine(),
        'python':sys.version,'measurements':reports,
        'note':'Desktop measurements; not RK3326 measurements. Includes cold process import and numerical libraries.'}
    (folder/'benchmark-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')


if __name__=='__main__':main()
