"""Sampling-aware checks for independently observable thermal modes."""
import math


def structure(p, span_hours, timestep_hours=1/12):
    a,b=-1/(p.rie*p.ci),1/(p.rie*p.ci)
    c,d=1/(p.rie*p.ce),-(1/p.rie+1/p.rea)/p.ce
    disc=math.sqrt((a-d)**2+4*b*c)
    times=sorted(-1/v for v in ((a+d+disc)/2,(a+d-disc)/2))
    fraction=p.rie/(p.rie+p.rea)
    reasons=[]
    if not .02 <= fraction <= .98: reasons.append('resistance_split_at_boundary')
    if times[0]<2*timestep_hours: reasons.append('fast_mode_below_sampling_resolution')
    if times[1]>max(168,3*span_hours): reasons.append('slow_mode_exceeds_record')
    if times[1]/times[0]<2: reasons.append('modes_not_separated')
    if min(p.ci,p.ce)<=.0202 or max(p.ci,p.ce)>=990: reasons.append('capacity_at_physical_bound')
    return {'supported':not reasons,'reasons':reasons,'time_constants_hours':times,'resistance_fraction':fraction}


def first_order_errors(points, split, check):
    """Independent 1R1C training and later open-loop endpoint comparison."""
    from scipy.optimize import least_squares
    train=[row for row in points if row[0]<split]
    def simulate(x, rows):
        h,c,area,gain=math.exp(x[0]),math.exp(x[1]),math.exp(x[2]),x[3]
        state=last=None
        errors=[]
        for t,room,outdoor,heat,solar in rows:
            if state is None or last is None or t-last>301:state=room
            elif state is not None:
                equilibrium=outdoor+(heat+area*solar+gain)/h
                state=equilibrium+(state-equilibrium)*math.exp(-h/(12*c))
            if state is not None and room is not None:errors.append(state-room)
            last=t
        return errors
    def residual(x):
        check()
        return simulate(x,train)
    if sum(r[1] is not None for r in train)<24:return {}
    fit=least_squares(residual,[math.log(.2),math.log(10),math.log(10),.5],
        bounds=([math.log(.005),math.log(.02),math.log(.001),0],
                [math.log(50),math.log(2000),math.log(500),5]),max_nfev=80)
    if not fit.success:return {}
    errors={1:[],6:[],24:[]}
    for origin in range(0,len(points),12):
        check()
        if points[origin][0]<split or points[origin][1] is None:continue
        for steps in (12,72,288):
            if origin+steps>=len(points):continue
            rows=points[origin:origin+steps+1]
            if any(b[0]-a[0]>301 for a,b in zip(rows,rows[1:])) or rows[-1][1] is None:continue
            values=simulate(fit.x,rows)
            if values:errors[steps//12].append(values[-1])
    return {str(k):{'rmse_K':math.sqrt(sum(e*e for e in v)/len(v)),'count':len(v)} for k,v in errors.items() if v}
