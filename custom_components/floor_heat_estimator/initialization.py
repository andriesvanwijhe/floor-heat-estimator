"""Recover floor state from the last valid measured water temperature."""

from .fitting import prepare_points, read_samples
from .model import Estimator
from dataclasses import replace


def bridge_short_gaps(samples, settings):
    """Estimate bounded startup interruptions only between stable valid inputs.

    This is a declared hold-input assumption, never a new water observation.
    Missing tails, long gaps, mode changes or changing heating power stay invalid.
    """
    result=list(samples)
    gaps=[]
    active=settings['active_mode']
    def valid(s):
        return s.room is not None and s.mode not in (None,'','unknown','unavailable') and (s.mode!=active or s.power is not None)
    i=0
    while i<len(result):
        if valid(result[i]):
            i+=1
            continue
        start=i
        while i<len(result) and not valid(result[i]):i+=1
        if start==0 or i==len(result):continue
        left,right=result[start-1],result[i]
        seconds=right.timestamp-left.timestamp
        if not 0<seconds<=settings['maximum_gap_minutes']*60 or left.mode!=right.mode or abs(left.room-right.room)>.5:continue
        if any(s.mode not in (None,'','unknown','unavailable',left.mode) for s in result[start:i]):continue
        if left.mode==active and abs(left.power-right.power)>max(.05,.05*abs(left.power)):continue
        for j in range(start,i):
            s=result[j]
            result[j]=replace(s,room=left.room,power=left.power if left.mode==active else 0,
                              mode=left.mode,supply=None,return_temperature=None,flow=None)
        gaps.append({'start_timestamp':left.timestamp,'end_timestamp':right.timestamp,
                     'seconds':seconds,'samples':i-start,'assumption':'Held preceding inputs between matching valid boundary states'})
    return result,gaps


def replay_floor(samples, settings, now):
    points = prepare_points(samples,settings)
    valid = [i for i,p in enumerate(points) if p.observation is not None]
    report = {"status":"initialization_incomplete", "reason":"No valid circulation measurement in retained history"}
    if not valid:
        return None,report
    index = valid[-1]
    point = points[index]
    engine = Estimator(settings)
    engine.temperatures = dict.fromkeys(engine.temperatures,point.observation)
    engine.previous = point.sample
    engine.last_observation = point.sample.timestamp
    samples,gaps=bridge_short_gaps([p.sample for p in points[index:]],settings)
    report.update(measurement_timestamp=point.sample.timestamp,replayed_samples=0,estimated_gaps=gaps)
    if engine._input(point.sample) is None:
        report['reason']='Heat input unavailable at the last measured temperature'
        return None,report
    for sample in samples[1:]:
        data = engine.step(sample)
        report['replayed_samples'] += 1
        if data['nonlinear_power'] is None:
            report['reason'] = 'Missing or expired historical input at '+str(sample.timestamp)
            report.update(failed_timestamp=sample.timestamp,model_status=data['status'],
                missing_inputs=[key for key,value in (('room_temperature',sample.room),('mode',sample.mode),
                    ('heat_input',sample.power if sample.mode==settings['active_mode'] else 0)) if value is None])
            return None,report
    if now-engine.previous.timestamp > settings['maximum_gap_minutes']*60:
        report['reason'] = 'History does not reach the current timestep'
        return None,report
    if now-engine.last_observation > settings['maximum_prediction_hours']*3600:
        report['reason'] = 'Last valid measurement exceeds configured prediction age'
        return None,report
    report.update(status='replayed',reason='Reconstructed from measured temperature and historical inputs',
                  state_timestamp=engine.previous.timestamp)
    return engine,report


def load_floor(directory,settings,now):
    samples,_ = read_samples(directory,settings,now)
    return replay_floor(samples,settings,now)
