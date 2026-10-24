"""Isolated, bounded maximum-likelihood identification of the Fig. 1 model.

This file also runs as a worker script. It never imports Home Assistant.
Only a JSON request is read from stdin and a JSON report written to stdout.
"""

import math
import time
from datetime import UTC, datetime

if __package__:
    from .house_model import HouseParameters, advance, correct, initial_state, predict_covariance, transition
else:
    from house_model import HouseParameters, advance, correct, initial_state, predict_covariance, transition

PARAMETERS = ("ci", "ce", "rie", "rea", "solar_area", "sigma_i", "sigma_e", "sigma_y")
BOUNDS = ((.02, 1000), (.02, 1000), (.01, 100), (.01, 100), (.001, 500),
          (.0001, .5), (.0001, .5), (.01, .5))


class FitDeadline(Exception):
    pass


def unpack(log_values):
    return HouseParameters(**dict(zip(PARAMETERS, map(math.exp, log_values))))


def filter_points(points, parameters, check=lambda: None, retain=False):
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


def forecast_errors(points, parameters, split_time, check=lambda: None):
    """Retrospective forecasts use actual future DRIVERS, no future room data.

    Every hourly origin uses only the filter state available at that origin.
    Each horizon is independently simulated without any measurement updates.
    """
    _, _, states, _ = filter_points(points, parameters, check, retain=True)
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
    from scipy.optimize import minimize

    began = time.monotonic()
    budget = request.get("max_seconds", 900)

    def check():
        if time.monotonic()-began > budget:
            raise FitDeadline("House fit exceeded the configured time limit")

    points = request["points"]
    report = {"status": "insufficient_data", "accepted": False,
              "parameters": {}, "point_count": len(points),
              "model": "Bacher-Madsen-2011-Figure-1",
              "input_provenance": request.get("input_provenance", "unknown")}
    measured = [row for row in points if row[1] is not None]
    report["observation_count"] = len(measured)
    if (len(measured) < 7*288*.8 or not points
            or points[-1][0]-points[0][0] < 6.9*86400):
        report["reason"] = "Need at least seven days spanning 6.9 days, with 80% measured five-minute coverage"
        return report
    span = points[-1][0]-points[0][0]
    split = points[-1][0]-max(2*86400, .25*span)
    train = [row for row in points if row[0] < split]
    train_measured = [row for row in train if row[1] is not None]
    validation = [row for row in measured if row[0] >= split]
    report.update(training_end_utc=datetime.fromtimestamp(split, UTC).isoformat(),
                  training_point_count=len(train_measured), validation_point_count=len(validation))
    if len(train_measured) < 3*288*.8 or len(validation) < 2*288*.8:
        report["reason"] = "Need at least three training days and two later validation days at 80% coverage"
        return report
    if not train:
        return report
    ranges = {name: max(row[index] for row in train if row[index] is not None)
              -min(row[index] for row in train if row[index] is not None)
              for name, index in (("room_K", 1), ("outdoor_K", 2), ("heat_kW", 3), ("solar_kW_per_m2", 4))}
    report["input_ranges"] = ranges
    if (ranges["room_K"] < .5 or ranges["outdoor_K"] < 3 or ranges["heat_kW"] < .5
            or ranges["solar_kW_per_m2"] < .05
            or sum(row[4] > .05 for row in train) < 24 or sum(row[4] < .005 for row in train) < 24):
        report.update(status="poor_excitation", reason="Need varied room/outdoor temperature and heat input, plus sunny and dark periods")
        return report

    baseline = HouseParameters(**request["baseline"])
    lower = np.log([bound[0] for bound in BOUNDS])
    upper = np.log([bound[1] for bound in BOUNDS])
    initial = np.clip(np.log([getattr(baseline, key) for key in PARAMETERS]), lower+.01, upper-.01)

    def objective(values):
        check()
        try:
            return filter_points(train, unpack(values), check)[0]
        except (ValueError, OverflowError, ZeroDivisionError):
            return 1e30

    fits = []
    offsets = ([0]*8, [-.5, .5, -.5, .4, .3, 0, 0, 0], [.5, -.3, .4, -.4, -.3, 0, 0, 0])
    for offset in offsets[:request.get("starts", 3)]:
        start = np.clip(initial+offset, lower+.01, upper-.01)
        # First identify physical dynamics with fixed small noise scales.
        # Free process noise from the first iteration can absorb a poor initial
        # RC model and trap optimisation at an unphysical, high-noise optimum.
        def physical_objective(values):
            return objective(np.concatenate((values, start[5:])))
        warm = minimize(physical_objective, start[:5], method="L-BFGS-B",
                        bounds=list(zip(lower[:5], upper[:5])),
                        options={"maxiter": request.get("max_iterations", 160), "ftol": 1e-9, "maxls": 30})
        if np.isfinite(warm.fun):
            start[:5] = warm.x
        result = minimize(objective, start, method="L-BFGS-B", bounds=list(zip(lower, upper)),
                          options={"maxiter": request.get("max_iterations", 160), "ftol": 1e-9, "maxls": 30})
        if np.isfinite(result.fun) and result.fun < 1e29:
            fits.append(result)
    if not fits:
        report.update(status="rejected", reason="No finite likelihood solution")
        return report
    best = min(fits, key=lambda result: result.fun)
    candidate = unpack(best.x)
    report.update(status="rejected", optimizer_converged=bool(best.success),
                  optimizer_message=str(best.message), negative_log_likelihood=float(best.fun),
                  candidate_parameters=candidate.to_dict())

    # Observed-information Hessian in log coordinates, including nuisance noise
    # parameters. Marginalize noise with a Schur complement before evaluating
    # physical identifiability. Never report L-BFGS's approximate inverse as CI.
    h = .002
    hessian = np.zeros((8, 8))
    centre = objective(best.x)
    eye = np.eye(8)*h
    for i in range(8):
        hessian[i, i] = (objective(best.x+eye[i])-2*centre+objective(best.x-eye[i]))/(h*h)
        for j in range(i):
            value = (objective(best.x+eye[i]+eye[j])-objective(best.x+eye[i]-eye[j])
                     -objective(best.x-eye[i]+eye[j])+objective(best.x-eye[i]-eye[j]))/(4*h*h)
            hessian[i, j] = hessian[j, i] = value
    physical = hessian[:5, :5]-hessian[:5, 5:]@np.linalg.pinv(hessian[5:, 5:])@hessian[5:, :5]
    physical = (physical+physical.T)/2
    eigenvalues = np.linalg.eigvalsh(physical)
    identified = bool(eigenvalues[0] > 1e-6 and eigenvalues[-1]/eigenvalues[0] < 1e7)
    intervals = {}
    condition = float(eigenvalues[-1]/eigenvalues[0]) if eigenvalues[0] > 0 else None
    if identified:
        covariance = np.linalg.inv(physical)
        deviations = np.sqrt(np.maximum(np.diag(covariance), 0))
        identified = bool(np.max(deviations) < 1.1)
        for i, key in enumerate(PARAMETERS[:5]):
            uncertainty = min(float(deviations[i])*1.96, 30)
            value = getattr(candidate, key)
            intervals[key] = {"lower_95": value*math.exp(-uncertainty), "upper_95": value*math.exp(uncertainty),
                              "log_standard_error": float(deviations[i])}
    report.update(identifiable=identified, information_condition=condition,
                  approximate_confidence_intervals=intervals)
    at_bound = bool(np.any((best.x[:5]-lower[:5])/(upper[:5]-lower[:5]) < .01)
                    or np.any((upper[:5]-best.x[:5])/(upper[:5]-lower[:5]) < .01))
    validation_metrics, persistence = forecast_errors(points, candidate, split, check)
    baseline_metrics, _ = forecast_errors(points, baseline, split, check)
    report.update(validation=validation_metrics, baseline_validation=baseline_metrics,
                  persistence_validation=persistence, at_physical_bound=at_bound)
    enough = all(validation_metrics.get(str(h), {}).get("count", 0) >= 4 for h in (1, 6, 24))
    ceiling = request.get("maximum_rmse", 1.0)
    validation_ok = enough and all(
        validation_metrics[str(h)]["rmse_K"] <= ceiling
        and validation_metrics[str(h)]["rmse_K"] <= max(.1, baseline_metrics[str(h)]["rmse_K"]*1.05)
        and validation_metrics[str(h)]["rmse_K"] <= max(.1, persistence[str(h)]["rmse_K"])
        for h in (6, 24))
    _, _, states, innovations = filter_points(points, candidate, check, retain=True)
    if len(innovations) > 2:
        mean = sum(innovations)/len(innovations)
        denom = sum((v-mean)**2 for v in innovations)
        report["standardized_innovation_lag1"] = (sum((a-mean)*(b-mean) for a, b in zip(innovations, innovations[1:]))/denom if denom else 0)
    report["accepted"] = bool(best.success and identified and not at_bound and validation_ok)
    if report["accepted"]:
        report.update(status="accepted", reason="Physical identification and held-out 6/24-hour forecasts passed",
                      parameters=candidate.to_dict())
        if states[-1] is not None:
            report["final_state"] = {"timestamp": points[-1][0], "state": list(states[-1][0]), "covariance": list(states[-1][1])}
    else:
        report["reason"] = ("Optimizer did not converge" if not best.success else
                            "Physical parameters poorly identifiable" if not identified else
                            "Physical parameter reached a bound" if at_bound else
                            "Held-out forecasts failed coverage, error ceiling or baseline comparison")
    report["elapsed_seconds"] = round(time.monotonic()-began, 3)
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
