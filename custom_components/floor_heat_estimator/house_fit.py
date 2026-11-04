"""Isolated, energy-anchored continuous identification of the Fig. 1 model.

This file also runs as a worker script. It never imports Home Assistant.
Only a JSON request is read from stdin and a JSON report written to stdout.
"""

import math
import time
from datetime import UTC, datetime

if __package__:
    from .house_simulation import SimulationData, CHANGE_SCALES, TEMPERATURE_SCALE, ENERGY_SCALE
    from .house_structure import structure, first_order_errors
    from .house_energy import constrain, energy_metrics, fit_signature
    from .house_model import HouseEstimator, HouseParameters, advance, correct, initial_state, predict_covariance, transition
else:
    from house_simulation import SimulationData, CHANGE_SCALES, TEMPERATURE_SCALE, ENERGY_SCALE
    from house_structure import structure, first_order_errors
    from house_energy import constrain, energy_metrics, fit_signature
    from house_model import HouseEstimator, HouseParameters, advance, correct, initial_state, predict_covariance, transition

PARAMETERS = ("ci", "ce", "rie", "rea", "solar_area", "internal_gain", "sigma_i", "sigma_e", "sigma_y")
BOUNDS = ((.02, 1000), (.02, 1000), (.01, 100), (.01, 100), (.001, 500),
          (0, 5), (.0001, .5), (.0001, .5), (.01, .5))
PHYSICAL_COUNT = 6
GAIN_INDEX = PARAMETERS.index("internal_gain")


class FitDeadline(Exception):
    pass


def unpack(log_values):
    # The constant internal gain is linear in kW, so zero is a valid fitted
    # result. Strictly positive capacities, resistances and noise use logs.
    return HouseParameters(**{key: float(value) if i == GAIN_INDEX else math.exp(value)
                              for i, (key, value) in enumerate(zip(PARAMETERS, log_values))})


def pack(parameters):
    return [getattr(parameters, key) if i == GAIN_INDEX else math.log(getattr(parameters, key))
            for i, key in enumerate(PARAMETERS)]


def observed_information(objective, values, h=.002):
    """Numerical curvature, using one-sided gain differences at zero."""
    import numpy as np
    values = np.asarray(values, dtype=float)
    size = len(values)
    hessian = np.zeros((size, size))
    centre = objective(values)
    eye = np.eye(size)*h
    for i in range(size):
        if i == GAIN_INDEX and values[i] < h:
            hessian[i, i] = (objective(values+2*eye[i])-2*objective(values+eye[i])+centre)/(h*h)
        else:
            hessian[i, i] = (objective(values+eye[i])-2*centre+objective(values-eye[i]))/(h*h)
        for j in range(i):
            if GAIN_INDEX in (i, j) and values[GAIN_INDEX] < h:
                other = j if i == GAIN_INDEX else i
                value = (objective(values+eye[GAIN_INDEX]+eye[other])-objective(values+eye[GAIN_INDEX]-eye[other])
                         -objective(values+eye[other])+objective(values-eye[other]))/(2*h*h)
            else:
                value = (objective(values+eye[i]+eye[j])-objective(values+eye[i]-eye[j])
                         -objective(values-eye[i]+eye[j])+objective(values-eye[i]-eye[j]))/(4*h*h)
            hessian[i, j] = hessian[j, i] = value
    return hessian


def filter_points(points, parameters, check=lambda: None, retain=False, initial_offset=0):
    """Innovations likelihood; diffuse hidden-state initialization per segment.

    Discard the first six hours of each segment from the likelihood while
    still filtering them. Missing driver intervals are already split in bins.
    """
    coefficients = transition(parameters, 1/12)
    state = covariance = last = segment_start = None
    nll, count = 0.0, 0
    states, innovations = [], []
    variance = parameters.sigma_y**2
    for index, (t, observed, outdoor, heat, solar) in enumerate(points):
        if index % 256 == 0:
            check()
        if last is None or t-last > 301:
            if observed is None:
                if retain:
                    states.append(None)
                last = None
                continue
            state, covariance = initial_state(observed, outdoor, parameters)
            state=(state[0],state[1]+initial_offset)
            segment_start = t
        else:
            state = advance(state, outdoor, heat, solar, parameters, coefficients)
            covariance = predict_covariance(covariance, coefficients)
        if observed is not None:
            state, covariance, innovation, s = correct(state, covariance, observed, variance)
            if t-segment_start >= 6*3600:
                nll += .5*(math.log(2*math.pi*s)+innovation*innovation/s)
                count += 1
                if retain:
                    innovations.append(innovation/math.sqrt(s))
        if retain:
            states.append((state, covariance))
        last = t
    if not count or not math.isfinite(nll):
        return 1e30, 0, states, innovations
    return nll, count, states, innovations


def daily_states(points,parameters,timezone,initial_offset=0):
    engine=HouseEstimator(parameters,timezone)
    states=[]
    for point in points:
        seed=engine.timestamp is None or point[0]-engine.timestamp>301
        engine.step(point)
        if seed and engine.state is not None:
            engine.state=(engine.state[0],engine.state[1]+initial_offset)
        states.append((engine.state,engine.covariance) if engine.state is not None else None)
    return engine,states


def forecast_errors(points, parameters, split_time, check=lambda: None, timezone=None, initial_offset=0):
    """Retrospective forecasts use actual future DRIVERS, no future room data.

    Every hourly origin uses only the filter state available at that origin.
    Each horizon is independently simulated without any measurement updates.
    """
    if timezone is not None:
        _,states=daily_states(points,parameters,timezone,initial_offset)
    else:
        _, _, states, _ = filter_points(points, parameters, check, retain=True,initial_offset=initial_offset)
    coefficients = transition(parameters, 1/12)
    errors = {1: [], 6: [], 24: []}
    persistence = {1: [], 6: [], 24: []}
    for origin in range(0, len(points), 12):
        check()
        if points[origin][0] < split_time or states[origin] is None or points[origin][1] is None:
            continue
        state = states[origin][0]
        for step in range(1, min(288, len(points)-origin-1)+1):
            row = points[origin+step]
            if row[0]-points[origin+step-1][0] > 301:
                break
            state = advance(state, row[2], row[3], row[4], parameters, coefficients)
            if step in (12, 72, 288) and row[1] is not None:
                horizon = step//12
                errors[horizon].append(state[0]-row[1])
                persistence[horizon].append(points[origin][1]-row[1])

    def metrics(values):
        return {str(h): {"rmse_K": math.sqrt(sum(e*e for e in v)/len(v)),
                         "bias_K": sum(v)/len(v), "count": len(v)}
                for h, v in values.items() if v}
    return metrics(errors), metrics(persistence)


def fit_house(request):
    import numpy as np
    from scipy.optimize import least_squares, lsq_linear

    began = time.monotonic()
    budget = request.get("max_seconds", 900)

    def check():
        if time.monotonic()-began > budget:
            raise FitDeadline("House fit exceeded the configured time limit")

    check()

    points = request["points"]
    force = request.get("force", False)
    report = {"status": "insufficient_data", "accepted": False,
              "parameters": {}, "point_count": len(points),
              "model": "Bacher-Madsen-2011-Figure-1+constant-internal-gain",
              "parameter_units": {"ci": "kWh/K", "ce": "kWh/K", "rie": "K/kW", "rea": "K/kW",
                                  "solar_area": "m²", "internal_gain": "kW"},
              "input_provenance": request.get("input_provenance", "unknown"),
              "validation_correction_policy": "local_midnight" if request.get("correction_timezone") else "five_minute",
              "forced": force, "provisional": force, "bypassed_checks": []}
    measured = [row for row in points if row[1] is not None]
    report["observation_count"] = len(measured)
    if force and (not points or len(measured) < 96 or points[-1][0]-points[0][0] < 12*3600):
        report["reason"] = "Even a forced fit needs twelve hours and 96 measured five-minute points"
        return report
    if (len(measured) < 7*288*.8 or not points
            or points[-1][0]-points[0][0] < 6.9*86400):
        if not force:
            report["reason"] = "Need at least seven days spanning 6.9 days, with 80% measured five-minute coverage"
            return report
        report["bypassed_checks"].append("seven_day_coverage")
    span = points[-1][0]-points[0][0]
    split = points[-1][0]-(.25*span if force else max(2*86400, .25*span))
    train = [row for row in points if row[0] < split]
    train_measured = [row for row in train if row[1] is not None]
    validation = [row for row in measured if row[0] >= split]
    report.update(training_end_utc=datetime.fromtimestamp(split, UTC).isoformat(),
                  training_point_count=len(train_measured), validation_point_count=len(validation))
    if len(train_measured) < 3*288*.8 or len(validation) < 2*288*.8:
        if not force:
            report["reason"] = "Need at least three training days and two later validation days at 80% coverage"
            return report
        report["bypassed_checks"].append("training_and_validation_coverage")
    if not train_measured:
        return report
    ranges = {name: max(row[index] for row in train if row[index] is not None)
              -min(row[index] for row in train if row[index] is not None)
              for name, index in (("room_K", 1), ("outdoor_K", 2), ("heat_kW", 3), ("solar_kW_per_m2", 4))}
    report["input_ranges"] = ranges
    if (ranges["room_K"] < .5 or ranges["outdoor_K"] < 3 or ranges["heat_kW"] < .5
            or ranges["solar_kW_per_m2"] < .05
            or sum(row[4] > .05 for row in train) < 24 or sum(row[4] < .005 for row in train) < 24):
        if not force:
            report.update(status="poor_excitation", reason="Need varied room/outdoor temperature and heat input, plus sunny and dark periods")
            return report
        report["bypassed_checks"].append("input_excitation")

    baseline = HouseParameters(**request["baseline"])
    energy_prior = HouseParameters(**request.get("energy_prior", request["baseline"]))
    dynamics_prior = HouseParameters(**request.get("dynamics_prior", energy_prior.to_dict()))
    gain_modes = request.get("gain_modes")
    signature = fit_signature(train, energy_prior, gain_modes)
    report["energy_signature"] = signature
    report.update(fitting_method="continuous_simulation_v1",
        simulation_policy="No measurement or midnight corrections; seed only at genuine driving-input gaps",
        objective_scales={"temperature_K": TEMPERATURE_SCALE,
            "temperature_change_K": {str(k*5): v for k, v in CHANGE_SCALES.items()},
            "daily_mean_heat_kW": ENERGY_SCALE},
        selection_policy="Fixed chronological split, availability mask, segments, lags and calendar-day weights",
        noise_policy="Retain configured/active noise scales; noise is not optimized against simulation error")
    if "heat_loss_support_kW_K" not in signature:
        report.update(status="insufficient_data", reason=signature["reason"])
        return report
    if signature['gains_fixed_to_configured_values'] and not force:
        report.update(status="poor_energy_identification", reason="Energy signature cannot separate gains and heat loss; collect more varied days or use the provisional override")
        return report

    # Six physical coordinates: log Ci, log Ce, log H, log Rie/Rea,
    # log solar area, internal gain (kW). No optimized process-noise escape.
    lower = np.array([b[0] if i == GAIN_INDEX else math.log(b[0]) for i,b in enumerate(BOUNDS[:PHYSICAL_COUNT])])
    upper = np.array([b[1] if i == GAIN_INDEX else math.log(b[1]) for i,b in enumerate(BOUNDS[:PHYSICAL_COUNT])])
    lower[2], upper[2] = map(math.log, signature['heat_loss_support_kW_K'])
    h_min, h_max = signature['heat_loss_support_kW_K']
    # Independent rectangular bounds cannot enforce both resistor limits over
    # an uncertain H range (H=.005 allows only Rie=Rea=100). Keep a supported
    # fractional range and penalize individual physical-limit violations.
    fraction_min, fraction_max = .02, .98
    lower[3], upper[3] = math.log(fraction_min/(1-fraction_min)), math.log(fraction_max/(1-fraction_max))
    fixed_gains = signature['gains_fixed_to_configured_values']
    modes = signature['gain_modes']
    active = [i for i, key in enumerate(PARAMETERS[:PHYSICAL_COUNT]) if modes.get(key) != 'fixed']

    def anchored(values):
        h, ratio = math.exp(values[2]), math.exp(values[3])
        data = baseline.to_dict()
        data.update(ci=math.exp(values[0]), ce=math.exp(values[1]),
            rie=ratio/(h*(1+ratio)), rea=1/(h*(1+ratio)),
            solar_area=math.exp(values[4]), internal_gain=float(values[5]))
        for key in ('solar_area', 'internal_gain'):
            if modes[key] == 'fixed':
                data[key] = getattr(energy_prior, key)
        return HouseParameters(**data)

    seed = constrain(energy_prior, signature)
    initial = np.array(pack(seed)[:PHYSICAL_COUNT])
    initial[2:4] = math.log(1/(seed.rie+seed.rea)), math.log(seed.rie/seed.rea)
    initial = np.clip(initial, lower+1e-6, upper-1e-6)
    solar_uncertainty = request.get('solar_uncertainty',0)
    solar_memory = request.get('solar_memory_hours',3)
    if (not isinstance(solar_uncertainty,(int,float)) or isinstance(solar_uncertainty,bool)
            or not math.isfinite(solar_uncertainty) or not 0<=solar_uncertainty<=100
            or not isinstance(solar_memory,(int,float)) or isinstance(solar_memory,bool)
            or not math.isfinite(solar_memory) or not .25<=solar_memory<=48):
        raise ValueError('Invalid solar uncertainty or memory')
    dataset = SimulationData(train, check, solar_uncertainty, solar_memory)
    unweighted_dataset = SimulationData(train, check) if solar_uncertainty else dataset
    report['solar_weighting'] = {'uncertain_effective_area_m2':solar_uncertainty,
        'memory_hours':solar_memory,'reference_heat_uncertainty_kW':ENERGY_SCALE,
        'minimum_residual_weight':float(np.min(dataset.solar_weights)) if len(train) else None,
        'mean_residual_weight':float(np.mean(dataset.solar_weights)) if len(train) else None,
        'policy':'Fixed GHI and decaying exposure weights for RC identification; validation unweighted'}
    if solar_uncertainty:
        report['fitting_method']='continuous_simulation_solar_weighted_v1'
    if len(dataset.targets) < 24:
        report.update(reason="Need 24 measured training targets after segment initialization")
        return report
    h_centre = signature['heat_loss_kW_K']
    h_width = max(.05, math.log(h_max/h_centre), math.log(h_centre/h_min))

    def evaluate(values, data=dataset, profile=True, offsets=None):
        check()
        candidate = anchored(values)
        result = data.evaluate(candidate, profile=profile, offsets=offsets)
        penalties = [math.log((1/(candidate.rie+candidate.rea))/h_centre)/h_width]
        for resistance in (candidate.rie,candidate.rea):
            penalties.extend((100*max(0,math.log(.01/resistance)),
                              100*max(0,math.log(resistance/100))))
        for key in ('solar_area', 'internal_gain'):
            if modes[key] == 'prior':
                width = max(2, energy_prior.solar_area*.5) if key == 'solar_area' else .25
                penalties.append((getattr(candidate,key)-getattr(energy_prior,key))/width)
        penalties.append(math.sqrt(request.get('ratio_prior_strength',0))*math.log(
            (candidate.rie/candidate.rea)/(dynamics_prior.rie/dynamics_prior.rea)))
        for key in ('ci','ce'):
            penalties.append(math.sqrt(request.get('capacity_prior_strength',0))*math.log(
                getattr(candidate,key)/getattr(dynamics_prior,key)))
        result['all_residual'] = np.concatenate((result['residual'], penalties))
        return candidate, result

    def fit_gains(values, rc_result):
        """Affine gain-only solve at frozen RC and training initial states.

        Physical units make this a bounded linear least-squares problem. GHI
        uncertainty does not downweight this stage, and gains never feed back
        into thermal parameters or select new observations/initial offsets.
        """
        values=values.copy()
        gain_indices=[i for i in (4,5) if i in active]
        if not solar_uncertainty or not gain_indices:
            return values, anchored(values), rc_result, {'enabled':False,'converged':True}
        frozen=rc_result['offsets']
        candidate,base=evaluate(values,data=unweighted_dataset,profile=False,offsets=frozen)
        residual=base['all_residual']
        current=np.array([candidate.solar_area if i==4 else candidate.internal_gain for i in gain_indices])
        columns=[]
        for i,value in zip(gain_indices,current):
            delta=1.0 if i==4 else .1
            bound=500 if i==4 else 5
            delta=delta if value+delta<=bound else -delta
            trial=values.copy()
            trial[i]=math.log(value+delta) if i==4 else value+delta
            other=evaluate(trial,data=unweighted_dataset,profile=False,offsets=frozen)[1]['all_residual']
            columns.append((other-residual)/delta)
        design=np.array(columns).T
        gain_fit=lsq_linear(design,design@current-residual,
            bounds=([.001 if i==4 else 0 for i in gain_indices],
                    [500 if i==4 else 5 for i in gain_indices]),max_iter=200,tol=1e-10)
        check()
        for i,value in zip(gain_indices,gain_fit.x):
            values[i]=math.log(float(value)) if i==4 else float(value)
        candidate,result=evaluate(values,data=unweighted_dataset,profile=False,offsets=frozen)
        return values,candidate,result,{'enabled':True,'converged':bool(gain_fit.success),
            'objective':float(.5*np.dot(result['all_residual'],result['all_residual'])),
            'fixed_parameters':['ci','ce','rie','rea','initial_hidden_offsets'],
            'gain_parameters':[PARAMETERS[i] for i in gain_indices],
            'policy':'Unweighted temperature/change/energy residuals; configured gain modes and priors retained'}

    # Every start sees exactly the same observations and smooth residuals.
    fits = []
    offsets = ([0]*6, [-.7,.5,-.1,.5,.4,.1], [.5,-.5,.1,-.5,-.4,-.1])
    for offset in offsets[:request.get("starts",3)]:
        start = np.clip(initial+offset, lower+1e-6, upper-1e-6)
        def expand(values):
            full = start.copy()
            full[active] = values
            return full
        result = least_squares(lambda values: evaluate(expand(values))[1]['all_residual'], start[active],
            bounds=(lower[active],upper[active]),x_scale='jac',
            max_nfev=request.get("max_iterations",160),ftol=1e-9,xtol=1e-9,gtol=1e-9)
        result.x = expand(result.x)
        result.fun = float(result.cost)
        if math.isfinite(result.fun) and result.fun < 1e29:
            fits.append(result)
    if not fits:
        report.update(status="rejected",reason="No finite continuous-simulation solution")
        return report
    # A converged result takes priority; no validation data selects the start.
    best = min([v for v in fits if v.success] or fits,key=lambda v:v.fun)
    rc_values=best.x.copy()
    rc_candidate, rc_result = evaluate(rc_values)
    best.x,candidate,training_result,gain_stage=fit_gains(rc_values,rc_result)
    stages_converged=bool(best.success and gain_stage['converged'])
    report['rc_identification_parameters']=rc_candidate.to_dict()
    report['rc_identification_objective']=float(best.fun)
    report['gain_fit_stage']=gain_stage
    report.update(status="rejected",optimizer_converged=stages_converged,
        optimizer_message=str(best.message),combined_objective=float(.5*np.dot(training_result['all_residual'],training_result['all_residual'])),
        optimizer_starts=[{"converged":bool(v.success),"cost":float(v.fun)} for v in fits],
        candidate_parameters=candidate.to_dict(),
        initial_hidden_offsets_K=[{"start_utc":datetime.fromtimestamp(t,UTC).isoformat(),"offset_K":v}
            for t,v in training_result['offsets'].items()],
        training_simulation=dataset.metrics(training_result),
        resistance_anchor={"overall_heat_loss_kW_K":1/(candidate.rie+candidate.rea),
            "total_resistance_K_kW":candidate.rie+candidate.rea,
            "interior_fraction":candidate.rie/(candidate.rie+candidate.rea),
            "interpretation":"Energy signature constrains Rie + Rea; dynamics identifies their split"})
    structural = structure(candidate,(train[-1][0]-train[0][0])/3600)
    report['candidate_structure'] = structural

    # Local residual sensitivity diagnoses rank/conditioning. Overlapping change
    # pairs and engineering weights are NOT independent statistical observations.
    # Do not turn this curvature into purported 95% parameter confidence intervals.
    jacobian = []
    for i in active:
        step = 1e-4
        plus, minus = best.x.copy(),best.x.copy()
        plus[i] = min(upper[i],plus[i]+step)
        minus[i] = max(lower[i],minus[i]-step)
        denominator = plus[i]-minus[i]
        jacobian.append((evaluate(plus)[1]['data_residual']-evaluate(minus)[1]['data_residual'])/denominator)
    jacobian = np.array(jacobian).T
    norms = np.linalg.norm(jacobian,axis=0)
    singular = np.linalg.svd(jacobian/np.maximum(norms,1e-12),compute_uv=False)
    condition = float(singular[0]/singular[-1]) if len(singular) and singular[-1]>1e-12 else None
    support_fraction = (best.x[2]-lower[2])/(upper[2]-lower[2])
    support_bound = support_fraction < .01 or support_fraction > .99
    enough_signature = signature['signature_identifiable'] or (fixed_gains and signature['window_count']>=12 and signature['independent_days']>=5)
    identified = bool(enough_signature and not support_bound and condition is not None
                      and condition<1e5 and np.all(norms>1e-4))
    report.update(identifiable=identified,information_condition=condition,
        at_energy_support_bound=bool(support_bound),approximate_confidence_intervals={},
        confidence_interval_interpretation="Not reported: correlated simulation residuals and engineering weights do not define statistical confidence intervals",
        sensitivity_coordinates=[PARAMETERS[i] if i not in (2,3) else {2:'overall_heat_loss',3:'resistance_ratio'}[i] for i in active])
    restricted = False
    if force and (not structural['supported'] or not identified):
        from dataclasses import replace
        h = 1/(candidate.rie+candidate.rea)
        fraction = dynamics_prior.rie/(dynamics_prior.rie+dynamics_prior.rea)
        candidate = replace(candidate,ci=dynamics_prior.ci,ce=dynamics_prior.ce,
            rie=fraction/h,rea=(1-fraction)/h)
        restricted = True
        structural = structure(candidate,(train[-1][0]-train[0][0])/3600)
        fallback_values=np.array(pack(candidate)[:PHYSICAL_COUNT])
        fallback_values[2:4]=math.log(h),math.log(candidate.rie/candidate.rea)
        fallback_rc=evaluate(fallback_values)[1]
        best.x,candidate,training_result,gain_stage=fit_gains(fallback_values,fallback_rc)
        stages_converged=bool(best.success and gain_stage['converged'])
        report['gain_fit_stage']=gain_stage
    report.update(active_structure=structural,dynamics_prior_dominated=restricted,
                  candidate_parameters=candidate.to_dict())
    signature['dynamics_prior_dominated'] = restricted
    report['training_simulation'] = unweighted_dataset.metrics(training_result)
    report['optimizer_converged']=stages_converged
    report['initial_hidden_offsets_K']=[{'start_utc':datetime.fromtimestamp(t,UTC).isoformat(),'offset_K':v}
        for t,v in training_result['offsets'].items()]
    report['resistance_anchor']['interior_fraction']=candidate.rie/(candidate.rie+candidate.rea)
    initial_ok = all(abs(v)<15 for v in training_result['offsets'].values())
    report['initial_state_supported'] = initial_ok
    physical_values = np.array(pack(candidate)[:PHYSICAL_COUNT])
    physical_lower = np.array([b[0] if i==GAIN_INDEX else math.log(b[0]) for i,b in enumerate(BOUNDS[:PHYSICAL_COUNT])])
    physical_upper = np.array([b[1] if i==GAIN_INDEX else math.log(b[1]) for i,b in enumerate(BOUNDS[:PHYSICAL_COUNT])])
    at_bound = bool(np.any((physical_values[:GAIN_INDEX]-physical_lower[:GAIN_INDEX])/(physical_upper[:GAIN_INDEX]-physical_lower[:GAIN_INDEX])<.01)
        or np.any((physical_upper-physical_values)/(physical_upper-physical_lower)<.01))
    timezone = request.get('correction_timezone')
    validation_metrics,persistence = forecast_errors(points,candidate,split,check,timezone)
    baseline_metrics,_ = forecast_errors(points,baseline,split,check,timezone)
    report.update(validation=validation_metrics,baseline_validation=baseline_metrics,
                  persistence_validation=persistence,at_physical_bound=at_bound)
    first_order = first_order_errors(points,split,check,timezone, candidate.sigma_i, candidate.sigma_y)
    model_order_ok = bool(first_order and all(validation_metrics.get(str(h),{}).get('rmse_K',1e9)<first_order.get(str(h),{}).get('rmse_K',0)*.95 for h in (6,24)))
    report.update(first_order_validation=first_order,second_order_supported=model_order_ok)

    # Freeze initial-state estimates learned ONLY on training data. At subsequent
    # genuine validation gaps use a room seed and equilibrium Te (zero offset).
    all_data = SimulationData(points,check)
    full_result = all_data.evaluate(candidate,offsets=training_result['offsets'])
    baseline_offsets = dataset.evaluate(baseline,profile=True)['offsets']
    baseline_result = all_data.evaluate(baseline,offsets=baseline_offsets)
    report.update(validation_simulation=all_data.metrics(full_result,after=split),
                  baseline_validation_simulation=all_data.metrics(baseline_result,after=split))
    energy_training = unweighted_dataset.energy_metrics(training_result)
    energy_validation = all_data.energy_metrics(full_result,after=split)
    energy_baseline = all_data.energy_metrics(baseline_result,after=split)
    energy_ok = bool(energy_validation['count'] and
        energy_validation['rmse_mean_load_kW']<=max(.15,.25*energy_validation['mean_measured_load_kW']) and
        energy_validation['rmse_mean_load_kW']<=max(.15,1.05*energy_baseline.get('rmse_mean_load_kW',0)))
    report['heat_imbalance']={'interpretation':'Unweighted required heat minus recorded input; positive means additional heat is required',
        'training':{str(minutes):unweighted_dataset.imbalance_metrics(training_result,minutes) for minutes in (30,60)},
        'validation':{str(minutes):all_data.imbalance_metrics(full_result,minutes,after=split) for minutes in (30,60)}}
    report.update(energy_training=energy_training,energy_validation=energy_validation,
        baseline_energy_validation=energy_baseline,energy_validated=energy_ok,energy_anchor_enforced=True)
    enough = all(validation_metrics.get(str(h),{}).get('count',0)>=4 for h in (1,6,24))
    ceiling = request.get('maximum_rmse',1.0)
    validation_ok = enough and all(validation_metrics[str(h)]['rmse_K']<=ceiling
        and validation_metrics[str(h)]['rmse_K']<=max(.1,baseline_metrics[str(h)]['rmse_K']*1.05)
        and validation_metrics[str(h)]['rmse_K']<=max(.1,persistence[str(h)]['rmse_K']) for h in (6,24))
    new_metrics, old_metrics = report['validation_simulation'],report['baseline_validation_simulation']
    simulation_ok = bool(new_metrics['count']>=24 and new_metrics['rmse_K']<=ceiling
        and new_metrics['rmse_K']<=max(.1,old_metrics['rmse_K']*1.05))
    change_ok = all(new_metrics['changes'][str(lag*5)]['count']>=24 and
        new_metrics['changes'][str(lag*5)]['rmse_K']<=max(.05,1.05*old_metrics['changes'][str(lag*5)]['rmse_K'])
        for lag in CHANGE_SCALES)
    report.update(simulation_validated=simulation_ok,temperature_changes_validated=change_ok)
    checks = ((identified,'identifiability'),(not at_bound,'physical_bound_screen'),
        (initial_ok,'initial_state_support'),(validation_ok,'held_out_validation'),
        (energy_ok,'held_out_energy_validation'),(simulation_ok,'held_out_continuous_simulation'),
        (change_ok,'held_out_temperature_changes'),(model_order_ok,'second_order_improvement'))
    if force:
        report['bypassed_checks'].extend(label for passed,label in checks if not passed)
    report['validated'] = bool(stages_converged and structural['supported'] and not restricted and all(passed for passed,_ in checks))
    report['accepted'] = bool(stages_converged and structural['supported'] and initial_ok and not at_bound and (force or report['validated']))
    if report['accepted']:
        report.update(status='forced_provisional' if force else 'accepted',
            reason='Forced provisional fit; normal quality gates bypassed' if force else 'Continuous simulation, short-period changes, energy and held-out forecasts passed',
            parameters=candidate.to_dict())
        if timezone is not None:
            engine,_ = daily_states(points,candidate,timezone)
            report['final_state'] = engine.snapshot()
        else:
            _,_,states,_ = filter_points(points,candidate,check,retain=True)
            if states[-1] is not None:
                report['final_state'] = {'timestamp':points[-1][0],'state':list(states[-1][0]),'covariance':list(states[-1][1])}
    else:
        failures = [label for passed,label in checks if not passed]
        if not structural['supported']: failures.insert(0,'unsupported_two_state_structure')
        if not stages_converged: failures.insert(0,'optimizer_not_converged')
        report['reason'] = 'Previous parameters retained: '+', '.join(failures)
    report['elapsed_seconds'] = round(time.monotonic()-began,3)
    return report


if __name__ == "__main__":
    import json
    import sys
    try:
        result = fit_house(json.load(sys.stdin))
    except FitDeadline as err:
        result = {"status": "timeout", "accepted": False, "reason": str(err), "parameters": {}}
    except Exception as err:
        result = {"status": "error", "accepted": False, "reason": f"{type(err).__name__}: {err}", "parameters": {}}
    print(json.dumps(result, allow_nan=False))
