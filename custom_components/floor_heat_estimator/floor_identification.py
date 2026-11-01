"""Cycle-balanced, multi-start profile identification of the floor model."""

from collections import Counter
from datetime import UTC, datetime
import math
import time

from .model import Parameters, advance


def evaluate(traces, parameters, check=lambda: None):
    """No assimilation: observations are targets, including settled restarts."""
    errors, cycles, restarts = [], [], []
    energy_in = energy_out = storage = 0.0
    for seed, steps in traces:
        temperature = seed
        for index, step in enumerate(steps):
            if index % 32 == 0:
                check()
            updated = advance(temperature, step.room, step.power, step.seconds, parameters)
            if not math.isfinite(updated) or not -100 <= updated <= 200:
                raise ValueError('Divergent temperature trajectory')
            supplied = step.power*step.seconds/3600
            stored = parameters.capacity*(updated-temperature)
            energy_in += supplied
            energy_out += supplied-stored
            storage += stored
            temperature = updated
            if step.target is not None:
                # Sign follows the visible jump that an observer would make.
                errors.append(step.target-temperature)
                cycles.append(step.cycle)
                restarts.append(step.restart)
    counts = Counter(cycles)
    weights = [math.sqrt(len(errors)/(len(counts)*counts[cycle])) for cycle in cycles]
    restart_errors = [e for e, restart in zip(errors, restarts) if restart]
    balanced_mse = sum((e*w)**2 for e,w in zip(errors,weights))/max(1,len(errors))
    metrics = {'cycle_balanced_rmse_K': math.sqrt(balanced_mse),
               'rmse_K': math.sqrt(sum(e*e for e in errors)/max(1,len(errors))),
               'cycle_count': len(counts), 'observation_count': len(errors),
               'restart_count': len(restart_errors),
               'restart_bias_K': sum(restart_errors)/len(restart_errors) if restart_errors else None,
               'restart_mae_K': sum(abs(e) for e in restart_errors)/len(restart_errors) if restart_errors else None,
               'restart_rmse_K': math.sqrt(sum(e*e for e in restart_errors)/len(restart_errors)) if restart_errors else None,
               'energy_balance': {'input_kWh': energy_in, 'output_kWh': energy_out,
                   'storage_change_kWh': storage, 'residual_kWh': energy_in-energy_out-storage}}
    return errors, weights, metrics


def identify(train, validation, settings, report, split, check, began):
    import numpy as np
    from scipy.optimize import least_squares

    low_n, high_n = settings.get('exponent_min',1.0), settings.get('exponent_max',1.6)
    if not .5 <= low_n < high_n <= 3:
        raise ValueError('Invalid exponent learning range')
    report.update(status='rejected', training_end_utc=datetime.fromtimestamp(split,UTC).isoformat(),
                  objective='Mean per-cycle mean squared temperature error; all valid hc readings',
                  simulation_policy='Continuous; initialize only at history start or genuine input gaps',
                  exponent_range=[low_n,high_n])
    lower = np.log([.01,.005])
    upper = np.log([100,100])

    def search(exponent, current, warm=None, linear=False):
        search_upper=np.log([100,50]) if linear else upper
        starts = [(current.capacity,current.output_at_5k), (1.,2.),
                  (max(.011,current.capacity*.5),max(.006,current.output_at_5k*1.25))]
        if warm is not None:
            starts[1] = (warm.capacity,warm.output_at_5k)
        def residual(values):
            check()
            candidate = Parameters(math.exp(values[0]),math.exp(values[1]),exponent)
            if candidate.output_at_5k/candidate.capacity > 150:
                return np.full(report['train_points'],1000.)
            errors,weights,_ = evaluate(train,candidate,check)
            return np.asarray(errors)*weights
        fits=[]
        for capacity,gain in starts:
            fit = least_squares(residual,np.clip(np.log([capacity,gain]),lower+1e-5,search_upper-1e-5),
                                bounds=(lower,search_upper),loss='linear',max_nfev=65,diff_step=1e-4)
            if fit.success and np.all(np.isfinite(fit.x)):
                fits.append(fit)
        if not fits:
            raise ValueError('Floor profile optimizer did not converge')
        fit=min(fits,key=lambda f:f.cost)
        fit['successful_start_count']=len(fits)
        candidate=Parameters(math.exp(fit.x[0]),math.exp(fit.x[1]),exponent)
        _,_,metrics=evaluate(train,candidate,check)
        return candidate,fit,metrics

    current_linear=Parameters(settings['linear_capacity'],5*settings['linear_conductance'])
    current_nonlinear=Parameters(settings['nonlinear_capacity'],settings['nonlinear_output_at_5k'],settings['exponent'])
    linear,linear_fit,linear_train=search(1.,current_linear,linear=True)
    profile=[]
    candidates=[]
    warm=current_nonlinear
    for exponent in np.linspace(low_n,high_n,7):
        check()
        candidate,fit,metrics = search(float(exponent),current_nonlinear,warm)
        warm=candidate
        point={'exponent':float(exponent),'training_cost_K2':metrics['cycle_balanced_rmse_K']**2,
               'capacity_kWh_per_K':candidate.capacity,'output_at_5K_kW':candidate.output_at_5k,
               'attempted_start_count':3,'successful_start_count':fit['successful_start_count']}
        profile.append(point)
        candidates.append((candidate,fit,metrics))
    nonlinear,nonlinear_fit,nonlinear_train=min(candidates,key=lambda item:item[2]['cycle_balanced_rmse_K'])
    # Refine the best profiled basin, while retaining the entire fixed-n scan.
    def joint_residual(values):
        check()
        candidate=Parameters(math.exp(values[0]),math.exp(values[1]),values[2])
        if candidate.output_at_5k/candidate.capacity>150:
            return np.full(report['train_points'],1000.)
        errors,weights,_=evaluate(train,candidate,check)
        return np.asarray(errors)*weights
    joint=least_squares(joint_residual,
        np.clip([math.log(nonlinear.capacity),math.log(nonlinear.output_at_5k),nonlinear.exponent],
                [*lower,low_n+1e-8],[*upper,high_n-1e-8]),
        bounds=([*lower,low_n],[*upper,high_n]),loss='linear',max_nfev=65,diff_step=1e-4)
    if joint.success and np.all(np.isfinite(joint.x)):
        candidate=Parameters(math.exp(joint.x[0]),math.exp(joint.x[1]),float(joint.x[2]))
        _,_,metrics=evaluate(train,candidate,check)
        if metrics['cycle_balanced_rmse_K']<=nonlinear_train['cycle_balanced_rmse_K']:
            nonlinear,nonlinear_fit,nonlinear_train=candidate,joint,metrics
    best=nonlinear_train['cycle_balanced_rmse_K']
    near=[p['exponent'] for p in profile if math.sqrt(p['training_cost_K2']) <= best+max(.02,.05*best)]
    at_exponent_bound=min(nonlinear.exponent-low_n,high_n-nonlinear.exponent)<.01*(high_n-low_n)
    weak=len(near)>1 and max(near)-min(near) >= .5*(high_n-low_n)
    profile_status='boundary_limited' if at_exponent_bound else 'weakly_identified' if weak else 'resolved'
    report.update(exponent_profile=profile,exponent_profile_status=profile_status,
        exponent_profile_near_optimal_range=[min(near),max(near)] if near else [nonlinear.exponent]*2,
        exponent_profile_interpretation='Training-cost sensitivity, not a statistical confidence interval',
        exponent_best={'exponent':nonlinear.exponent,'training_cost_K2':best**2})

    for name,candidate,fit,training,current in (
            ('linear',linear,linear_fit,linear_train,current_linear),
            ('nonlinear',nonlinear,nonlinear_fit,nonlinear_train,current_nonlinear)):
        _,_,held=evaluate(validation,candidate,check)
        _,_,baseline=evaluate(validation,current,check)
        singular=np.linalg.svd(fit.jac[:,:2],compute_uv=False)
        condition=float(singular[0]/singular[-1]) if singular[-1]>1e-10 else None
        physical_upper=np.log([100,50]) if name=='linear' else upper
        physical_bound=bool(np.any((fit.x[:2]-lower)/(physical_upper-lower)<.01)
                            or np.any((physical_upper-fit.x[:2])/(physical_upper-lower)<.01))
        checks={'physical_parameters':not physical_bound,
                'identifiable_capacity_and_gain':condition is not None and condition<10000,
                'absolute_temperature_error':held['cycle_balanced_rmse_K']<=settings['maximum_fit_rmse'],
                'temperature_vs_baseline':held['cycle_balanced_rmse_K']<=max(baseline['cycle_balanced_rmse_K']*1.05,.05),
                'restart_evidence':held['restart_count']>=1,
                'restart_absolute_error':held['restart_rmse_K'] is not None and held['restart_rmse_K']<=settings['maximum_fit_rmse'],
                'restart_vs_baseline':held['restart_mae_K'] is not None and held['restart_mae_K']<=max((baseline['restart_mae_K'] or 0)*1.05,.05),
                'restart_bias_vs_baseline':held['restart_bias_K'] is not None and abs(held['restart_bias_K'])<=max(abs(baseline['restart_bias_K'] or 0)*1.05,.05)}
        # A physical exponent boundary is allowed when predictions validate;
        # label its uncertainty explicitly rather than inventing precision.
        report['models'][name]={'capacity_kWh_per_K':candidate.capacity,'output_at_5K_kW':candidate.output_at_5k,
            'exponent':candidate.exponent,'conductance_coefficient':candidate.output_at_5k/5**candidate.exponent,
            'train_rmse_K':training['cycle_balanced_rmse_K'],'validation_rmse_K':held['cycle_balanced_rmse_K'],
            'baseline_validation_rmse_K':baseline['cycle_balanced_rmse_K'],'condition_number':condition,
            'at_bound':physical_bound or (name=='nonlinear' and at_exponent_bound),
            'exponent_identification':profile_status if name=='nonlinear' else 'fixed',
            'training':training,'validation':held,'baseline_validation':baseline,'checks':checks,
            'accepted':all(checks.values())}
    report['accepted']=all(v['accepted'] for v in report['models'].values())
    if report['accepted']:
        report.update(status='accepted',parameters={'linear_capacity':linear.capacity,
            'linear_conductance':linear.output_at_5k/5,'nonlinear_capacity':nonlinear.capacity,
            'nonlinear_output_at_5k':nonlinear.output_at_5k,'exponent':nonlinear.exponent},
            reason='Both models passed complete-cycle temperature and restart validation')
    else:
        failed=[name+': '+', '.join(k for k,v in m['checks'].items() if not v)
                for name,m in report['models'].items() if not m['accepted']]
        report['reason']='; '.join(failed)
    report['elapsed_seconds']=round(time.monotonic()-began,3)
    return report


def profile_svg(report):
    """Standalone local plot; derived calibration results only, no raw history."""
    points=report.get('exponent_profile',[])
    if not points:
        return None
    low,high=report['exponent_range']
    ymax=max(p['training_cost_K2'] for p in points)*1.05 or 1.
    coords=[(65+520*(p['exponent']-low)/(high-low),275-220*p['training_cost_K2']/ymax) for p in points]
    labels=''.join(f'<text x="{x:.1f}" y="298" text-anchor="middle">{p["exponent"]:.2f}</text>' for p,(x,y) in zip(points,coords))
    dots=''.join(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#e68a2e"><title>n={p["exponent"]:.4f}; cost={p["training_cost_K2"]:.6f} K²</title></circle>' for p,(x,y) in zip(points,coords))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="650" height="365" viewBox="0 0 650 365">'
        '<rect width="650" height="365" fill="white"/><g font-family="sans-serif" font-size="13" fill="#222">'
        '<text x="325" y="25" text-anchor="middle">Floor exponent: cycle-balanced training cost</text>'
        '<path d="M65 50V275H585" fill="none" stroke="#555"/>'
        f'<text x="60" y="55" text-anchor="end">{ymax:.4g}</text><text x="60" y="279" text-anchor="end">0</text>'
        f'<polyline points="'+ ' '.join(f'{x:.2f},{y:.2f}' for x,y in coords)+'" fill="none" stroke="#e68a2e"/>'
        +dots+labels+'<text x="325" y="323" text-anchor="middle">Exponent n; vertical axis: mean per-cycle MSE (K²)</text>'
        f'<text x="325" y="348" text-anchor="middle">{report["exponent_profile_status"]}; sensitivity profile, not a confidence interval</text></g></svg>')
