"""Recover floor state from the last valid measured water temperature."""

from .fitting import prepare_points, read_samples
from .model import Estimator


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
    report.update(measurement_timestamp=point.sample.timestamp,replayed_samples=0)
    if engine._input(point.sample) is None:
        report['reason']='Heat input unavailable at the last measured temperature'
        return None,report
    for p in points[index+1:]:
        data = engine.step(p.sample)
        report['replayed_samples'] += 1
        if data['nonlinear_power'] is None:
            report['reason'] = 'Missing or expired historical input at '+str(p.sample.timestamp)
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
