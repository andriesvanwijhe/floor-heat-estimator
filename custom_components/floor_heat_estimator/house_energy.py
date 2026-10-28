"""Measured low-frequency energy signature and independent heat-balance checks.

Power kW, time hours, GHI kW/m². No room-observation corrections are used
to infer the envelope trajectory in the heat-balance calculation.
"""

import math


def energy_windows(points):
    """Matched complete 24/72-hour windows; never bridge missing inputs/room."""
    windows = []
    for hours, stride in ((24, 6), (72, 24)):
        count = hours*12
        for start in range(0, len(points)-count, stride*12):
            block = points[start:start+count+1]
            if any(any(v is None or not math.isfinite(v) for v in row) for row in block):
                continue
            if any(abs(b[0]-a[0]-300) > 1 for a,b in zip(block,block[1:])):
                continue
            room = sum((a[1]+b[1])/2 for a,b in zip(block,block[1:]))/count
            outdoor, heat, solar = (sum(row[i] for row in block[1:])/count for i in (2,3,4))
            windows.append({"start": start, "end": start+count, "hours": hours,
                "delta_K": room-outdoor, "heat_kW": heat, "solar_kW_m2": solar,
                "room_change_K_per_h": (block[-1][1]-block[0][1])/hours})
    return windows


def fit_signature(points, baseline):
    """Fit Qh = H*(Ti-Ta) - A*G - I + Ceff*dTi/dt before dynamics.

    Ceff is a nuisance storage proxy, not a published physical capacity.
    Short/collinear records keep configured gains fixed instead of inventing
    their independent identification. Support bands include a model-error
    floor and are explicitly not statistical confidence intervals.
    """
    import numpy as np
    from scipy.optimize import lsq_linear
    windows = energy_windows(points)
    if not windows:
        return {"status": "insufficient_energy_data", "reason": "Need a continuous measured 24-hour training window"}
    x = np.array([[w['delta_K'], -w['solar_kW_m2'], -1, w['room_change_K_per_h']] for w in windows])
    y = np.array([w['heat_kW'] for w in windows])
    scales = np.linalg.norm(x,axis=0)
    scaled = x/np.maximum(scales,1e-12)
    condition = float(np.linalg.cond(scaled))
    independent_days = (points[-1][0]-points[0][0])/86400
    joint = len(windows) >= 12 and independent_days >= 5 and condition < 100
    capacity = baseline.ci+baseline.ce*baseline.rea/(baseline.rie+baseline.rea)
    prior = np.array([1/(baseline.rie+baseline.rea),baseline.solar_area,baseline.internal_gain,capacity])
    if joint:
        indices = [0,1,2,3]
        lower,upper = [.005,.001,0,0], [50,500,5,2000]
    else:
        indices = [0,3]
        lower,upper = [.005,0], [50,2000]
        y = y-x[:,1]*prior[1]-x[:,2]*prior[2]
    design = x[:,indices]
    # Overlapping windows provide shape information, not independent days.
    weight = math.sqrt(min(1,independent_days/len(windows)))
    widths = np.array([.5,50,1,100])[indices]
    regularizer = np.diag(1/widths)*.1
    result = lsq_linear(np.vstack((weight*design,regularizer)),
                        np.concatenate((weight*y,regularizer@prior[indices])),bounds=(lower,upper))
    values = prior.copy()
    values[indices] = result.x
    residual = x@values-np.array([w['heat_kW'] for w in windows])
    rmse = float(np.sqrt(np.mean(residual**2)))
    # Broad support for approximation/seasonal drift; never call this a CI.
    delta = max(.25*values[0], 2*max(.1,rmse)/max(float(np.sqrt(np.mean(x[:,0]**2))),1))
    support = [max(.005,float(values[0]-delta)),min(50,float(values[0]+delta))]
    return {"status": "joint_signature" if joint else "conditional_signature",
        "heat_loss_kW_K": float(values[0]), "heat_loss_support_kW_K": support,
        "solar_area_m2": float(values[1]), "internal_gain_kW": float(values[2]),
        "storage_proxy_kWh_K": float(values[3]), "rmse_kW": rmse,
        "window_count": len(windows), "independent_days": independent_days,
        "design_condition": condition if math.isfinite(condition) else None,
        "gains_fixed_to_configured_values": not joint,
        "support_interpretation": "Engineering support band including storage-proxy model error; not a confidence interval",
        "windows": windows}


def constrain(parameters, signature):
    """Keep dynamics inside the signature's heat-loss and gain support."""
    from dataclasses import replace
    resistance = parameters.rie+parameters.rea
    lo,hi = signature['heat_loss_support_kW_K']
    h = min(max(1/resistance,lo),hi)
    scale = 1/(h*resistance)
    total = 1/h
    rie = min(max(parameters.rie*scale,.01,total-100),100,total-.01)
    if signature['gains_fixed_to_configured_values']:
        area, gain = signature['solar_area_m2'],signature['internal_gain_kW']
    else:
        area = min(max(parameters.solar_area,signature['solar_area_m2']*.5),signature['solar_area_m2']*1.5)
        gain = min(max(parameters.internal_gain,max(0,signature['internal_gain_kW']-.5)),
                   min(5,signature['internal_gain_kW']+.5))
    return replace(parameters,rie=rie,rea=total-rie,
                   solar_area=area,internal_gain=gain)


def required_heat(points, parameters):
    """Energy needed for the MEASURED room trajectory, integrating hidden Te.

    Te obeys Ce*dTe=(Ti-Te)/Rie+(Ta-Te)/Rea. Integrate this scalar equation
    exactly for constant midpoint room/outdoor over each five-minute bin.
    Room storage is Ci*deltaTi; inferred envelope exchange is integrated
    analytically. Missing measurements split segments; no zero-filled gaps.
    """
    p = parameters
    envelope = None
    previous = None
    result = []
    rate = (1/p.rie+1/p.rea)/p.ce
    for row in points:
        t,room,outdoor,heat,solar = row
        if any(v is None or not math.isfinite(v) for v in row):
            previous = envelope = None
            result.append(None)
            continue
        if previous is None or abs(t-previous[0]-300)>1:
            envelope = (p.rea*room+p.rie*outdoor)/(p.rie+p.rea)
            result.append(None)
        else:
            hours = (t-previous[0])/3600
            midroom = (previous[1]+room)/2
            equilibrium = (p.rea*midroom+p.rie*outdoor)/(p.rie+p.rea)
            decay = math.exp(-rate*hours)
            integral_te = equilibrium*hours+(envelope-equilibrium)*(-math.expm1(-rate*hours))/rate
            energy = p.ci*(room-previous[1])+(midroom*hours-integral_te)/p.rie
            energy -= (p.solar_area*solar+p.internal_gain)*hours
            result.append(energy)
            envelope = equilibrium+(envelope-equilibrium)*decay
        previous = row
    return result


def energy_metrics(points, parameters, windows=None, after=None):
    """Signed, unclipped heating energy; training and held-out windows separate."""
    estimates = required_heat(points,parameters)
    errors, observed, records = [],[],[]
    for w in windows if windows is not None else energy_windows(points):
        start,end,hours = w['start'],w['end'],w['hours']
        if after is not None and points[start][0] < after:
            continue
        values = estimates[start+1:end+1]
        if any(v is None for v in values):
            continue
        predicted = sum(values)
        measured = w['heat_kW']*hours
        error = predicted-measured
        errors.append(error/hours)
        observed.append(abs(measured)/hours)
        records.append({"hours":hours,"start_utc_epoch":points[start][0],
                        "measured_kWh":measured,"predicted_kWh":predicted,"error_kWh":error})
    if not errors:
        return {"count":0,"windows":[]}
    return {"count":len(errors),"rmse_mean_load_kW":math.sqrt(sum(v*v for v in errors)/len(errors)),
            "bias_mean_load_kW":sum(errors)/len(errors),
            "mean_measured_load_kW":sum(observed)/len(observed),"windows":records}
