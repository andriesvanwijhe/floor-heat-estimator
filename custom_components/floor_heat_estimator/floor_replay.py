"""Reconstruct continuous floor heat, retaining interval energy for house bins."""

from .fitting import prepare_points
from .initialization import bridge_short_gaps
from .model import Estimator


def reconstruct_floor(samples, settings):
    samples,gaps=bridge_short_gaps([p.sample for p in prepare_points(samples,settings)],settings)
    engine=Estimator(settings)
    intervals=[]
    segments={}
    for sample in samples:
        result=engine.step(sample)
        interval=result['interval_energy']
        if interval is not None and result['status']!='observation_expired':
            intervals.append(interval)
        for name,balance in result['energy_balance'].items():
            segments[(name,balance['start_timestamp'])]=balance
    totals={}
    for name in engine.parameters:
        ledgers=[balance for (kind,_),balance in segments.items() if kind==name]
        totals[name]={key:sum(b[key] for b in ledgers)
                      for key in ('input_kWh','output_kWh','storage_change_kWh','residual_kWh')}
        totals[name]['segment_count']=len(ledgers)
    return intervals,{'policy':'Continuous; no measurement corrections or restart resets',
                      'models':totals,'estimated_gaps':gaps,'interval_count':len(intervals)}


def merge_floor_intervals(rows, intervals, calibration, maximum_gap):
    """Split both timelines at boundaries so mean heat preserves exact energy.

    Power is the integral over a floor interval, not an instantaneous endpoint
    sampled and held for the next minute. Missing intervals remain missing.
    """
    if not rows:
        return []
    rows=sorted(rows,key=lambda row:row['t'])
    times={r['t'] for r in rows}
    first,last=rows[0]['t'],rows[-1]['t']
    for interval in intervals:
        times.update(t for t in (interval['start'],interval['end']) if first<t<last)
    result=[]
    row_index=interval_index=0
    for t in sorted(times):
        while row_index+1<len(rows) and rows[row_index+1]['t']<=t:
            row_index+=1
        while interval_index<len(intervals) and intervals[interval_index]['end']<=t:
            interval_index+=1
        row={key:value for key,value in rows[row_index].items() if key!='heat_since_previous'}
        if t-row['t']>maximum_gap:
            row.update(room=None,outdoor=None,solar=None)
        row.update(t=t,heat=None,calibration=calibration)
        if interval_index<len(intervals):
            interval=intervals[interval_index]
            if interval['start']<=t<interval['end']:
                row['heat']=interval['output_kWh']['nonlinear']*3600/(interval['end']-interval['start'])
        result.append(row)
    return result
