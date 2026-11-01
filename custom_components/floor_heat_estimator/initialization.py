"""Recover floor state from the last valid measured water temperature."""

from .fitting import prepare_points, read_samples
from .model import Estimator
from dataclasses import replace

UNKNOWN_MODES=(None,'','unknown','unavailable')


def valid_driver(sample, settings):
    return (sample.room is not None and sample.mode not in UNKNOWN_MODES
            and (sample.mode!=settings['active_mode'] or sample.power is not None))


def gap_problem(left, right, interior, settings):
    """Return a reason to reject a hold-input estimate, or None.

    Distinct non-heating modes have the same zero circuit heat input under
    this integration's heating-only assumption. They are equivalent drivers.
    """
    seconds=right.timestamp-left.timestamp
    if not 0<seconds<=settings['maximum_gap_minutes']*60:
        return 'Boundary interval exceeds the configured maximum gap'
    if abs(left.room-right.room)>.5:
        return 'Room temperature differs by more than 0.5 K across the gap'
    active=settings['active_mode']
    left_active,right_active=left.mode==active,right.mode==active
    if left_active!=right_active:
        return 'A boundary crosses between heating and non-heating mode'
    if left_active:
        if abs(left.power-right.power)>max(.05,.05*abs(left.power)):
            return 'Heating power changes across the gap'
        if any(s.mode not in (*UNKNOWN_MODES,active) for s in interior):
            return 'A known non-heating mode interrupts the heating gap'
    else:
        if any(s.mode==active for s in interior):
            return 'A known heating mode occurs inside the non-heating gap'
        if any(s.mode in UNKNOWN_MODES and s.power is not None and abs(s.power)>.05 for s in interior):
            return 'Nonzero thermal power is recorded while the mode is unknown'
    return None


def bridge_short_gaps(samples, settings):
    """Estimate bounded startup interruptions only between stable valid inputs.

    This is a declared hold-input assumption, never a new water observation.
    Missing tails, long gaps, heating/non-heating transitions or changing heating
    power stay invalid. Known non-heating boundary modes may differ.
    """
    result=list(samples)
    gaps=[]
    active=settings['active_mode']
    i=0
    while i<len(result):
        if valid_driver(result[i],settings):
            i+=1
            continue
        start=i
        while i<len(result) and not valid_driver(result[i],settings):i+=1
        if start==0 or i==len(result):continue
        left,right=result[start-1],result[i]
        seconds=right.timestamp-left.timestamp
        if gap_problem(left,right,result[start:i],settings) is not None:continue
        for j in range(start,i):
            s=result[j]
            result[j]=replace(s,room=left.room,power=left.power if left.mode==active else 0,
                              mode=s.mode if s.mode not in UNKNOWN_MODES else left.mode,
                              supply=None,return_temperature=None,flow=None)
        gaps.append({'start_timestamp':left.timestamp,'end_timestamp':right.timestamp,
                     'seconds':seconds,'samples':i-start,'boundary_modes':[left.mode,right.mode],
                     'assumption':('Assumed zero floor heat input between known non-heating boundary modes; held preceding room temperature'
                                   if left.mode!=active else 'Held preceding inputs between matching valid heating boundary states')})
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
    for position,sample in enumerate(samples[1:],1):
        data = engine.step(sample)
        report['replayed_samples'] += 1
        if data['nonlinear_power'] is None:
            report['reason'] = 'Missing or expired historical input at '+str(sample.timestamp)
            report.update(failed_timestamp=sample.timestamp,model_status=data['status'],
                missing_inputs=[key for key,value in (('room_temperature',sample.room),('mode',sample.mode),
                    ('heat_input',sample.power if sample.mode==settings['active_mode'] else 0)) if value is None])
            if data['status'] in ('invalid_input','gap_requires_observation'):
                left=next((s for s in reversed(samples[:position]) if valid_driver(s,settings)),None)
                right=next((s for s in samples[position:] if valid_driver(s,settings)),None)
                boundary=lambda s: None if s is None else {'timestamp':s.timestamp,'mode':s.mode,
                    'room_temperature_C':s.room,'floor_input_kW':s.power if s.mode==settings['active_mode'] else 0.}
                report['unbridged_gap']={'left':boundary(left),'right':boundary(right),
                    'reason':(gap_problem(left,right,[s for s in samples if left.timestamp<s.timestamp<right.timestamp],settings)
                              if left is not None and right is not None else 'No valid driving-input boundary on both sides')}
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
