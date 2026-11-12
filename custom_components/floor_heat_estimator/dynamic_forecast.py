"""Standalone coupled house/floor prediction. No Home Assistant imports.

Hourly controls are thermal kW delivered by the pump to the floor circuit.
The house transition is exact for constant five-minute floor output. A
monotone implicit midpoint floor solve couples its energy to that transition.
Only the optimizer imports NumPy/SciPy, in a short-lived worker process.
"""

from datetime import UTC, datetime
import math
import time

if __package__:
    from .house_model import HouseParameters, transition
    from .model import Parameters
else:
    from house_model import HouseParameters, transition
    from model import Parameters


class PredictionDeadline(Exception):
    """An unfinished search must not be published as an optimized forecast."""


def _floor_flux(difference, feedback, floor):
    """q = Q5 * max((difference-feedback*q)/5, 0)^n; unique q >= 0."""
    if difference <= 0:
        return 0., 0.
    lo, hi = 0., difference/feedback
    q = min(hi, floor.output_at_5k*(difference/5)**floor.exponent)
    for _ in range(40):
        theta = max(difference-feedback*q, 0.)
        value = floor.output_at_5k*(theta/5)**floor.exponent
        residual = q-value
        if abs(residual) <= 1e-11*max(1., q):
            break
        if residual > 0: hi = q
        else: lo = q
        slope = (floor.exponent*value/theta if theta > 1e-14 else 0.)
        trial = q-residual/(1+feedback*slope)
        q = trial if lo < trial < hi else (lo+hi)/2
    theta = max(difference-feedback*q, 0.)
    slope = floor.exponent*q/theta if theta > 1e-14 else 0.
    return q, slope/(1+feedback*slope)


class CoupledModel:
    """Three states: existing room/envelope model plus the existing floor."""

    def __init__(self, house, floor, step_hours=1/12):
        self.house, self.floor, self.dt = house, floor, step_hours
        self.phi, _, self.gains = transition(house, step_hours)

    def step(self, state, outdoor, solar, power):
        ti, te, tf = state
        p, f, dt = self.house, self.floor, self.dt
        a, b, c, d = self.phi
        ga, ge, gh, ghe = self.gains
        gains = p.solar_area*solar+p.internal_gain
        bi = a*ti+b*te+ga*outdoor+gh*gains
        be = c*ti+d*te+ge*outdoor+ghe*gains
        feedback = .5*(dt/f.capacity+gh)
        difference = tf-.5*(ti+bi)+.5*dt*power/f.capacity
        output, slope = _floor_flux(difference, feedback, f)
        result = (bi+gh*output, be+ghe*output,
                  tf+dt*(power-output)/f.capacity)
        # Exact whole-system balance defines integrated outdoor exchange.
        storage = p.ci*(result[0]-ti)+p.ce*(result[1]-te)+f.capacity*(result[2]-tf)
        outdoor_energy = dt*(power+gains)-storage
        return result, output, outdoor_energy, slope

    def rollout(self, hours, initial, controls, check=lambda: None, derivatives=False):
        import numpy as np
        count = len(hours)
        if len(controls) != count:
            raise ValueError('One control is required for every forecast interval')
        states, powers, losses, hour_ids = [], [], [], []
        state = tuple(initial)
        sensitivity = np.zeros((3, count))
        jacobian = []
        a, b, c, d = self.phi
        _, _, gh, ghe = self.gains
        dt, cf = self.dt, self.floor.capacity
        for hour, row in enumerate(hours):
            steps = int(round(row['seconds']/3600/dt))
            if steps < 1 or abs(steps*dt*3600-row['seconds']) > 1e-4:
                raise ValueError('Forecast intervals must align with five-minute steps')
            for _ in range(steps):
                check()
                state, output, loss, slope = self.step(state, row['outdoor_C'],row['solar_kW_m2'],float(controls[hour]))
                if not all(math.isfinite(v) and -100 < v < 200 for v in state):
                    raise ValueError('Divergent coupled thermal prediction')
                if derivatives:
                    si, se, sf = sensitivity
                    di, de = a*si+b*se, c*si+d*se
                    dq = slope*(sf-.5*(si+di))
                    dq[hour] += slope*.5*dt/cf
                    sensitivity = np.array([di+gh*dq, de+ghe*dq, sf-dt*dq/cf])
                    sensitivity[2,hour] += dt/cf
                    jacobian.append(sensitivity[0].copy())
                states.append(state);powers.append(output);losses.append(loss);hour_ids.append(hour)
        return {'states':np.asarray(states),'floor_output':np.asarray(powers),
                'outdoor_energy':np.asarray(losses),'hour_ids':np.asarray(hour_ids),
                'room_jacobian':np.asarray(jacobian) if derivatives else None}


def predict(request):
    """Optimize the whole horizon; solar overshoot is never clipped to target.

    Comfort is a soft constraint with reported shortfall, so a cold startup or
    insufficient pump never makes the numerical problem impossible. A separate
    maximum-power rollout diagnoses whether the floor/pump can meet comfort.
    """
    import numpy as np
    from scipy.optimize import minimize
    began = time.monotonic()
    limit = float(request.get('max_seconds',600))
    def check():
        if time.monotonic()-began > limit:
            raise PredictionDeadline('Prediction exceeded its configured runtime limit')
    p, f = HouseParameters(**request['house']), Parameters(**request['floor'])
    initial = tuple(request['initial'])
    hours = request['hours']
    target = float(request.get('target_C',20))
    maximum = float(request.get('maximum_power_kW',8))
    if (len(initial)!=3 or not all(math.isfinite(v) and -50<v<100 for v in initial)
            or not math.isfinite(target) or not 5<=target<=30
            or not math.isfinite(maximum) or not 0<maximum<=100
            or not 1<=len(hours)<=49 or not 0<limit<=600):
        raise ValueError('Invalid dynamic prediction inputs')
    previous_end = None
    for row in hours:
        if (not all(isinstance(row.get(k),(int,float)) and not isinstance(row[k],bool)
                    and math.isfinite(row[k]) for k in ('start','seconds','outdoor_C','solar_kW_m2'))
                or row['seconds']<=0 or row['seconds']>3600 or row['solar_kW_m2']<0
                or (previous_end is not None and abs(row['start']-previous_end)>1e-4)):
            raise ValueError('Incomplete or non-contiguous weather forecast')
        previous_end=row['start']+row['seconds']
    model = CoupledModel(p,f)
    duration = np.array([r['seconds']/3600 for r in hours])
    steady = np.array([max(0,(target-r['outdoor_C'])/(p.rie+p.rea)
                          -p.solar_area*r['solar_kW_m2']-p.internal_gain) for r in hours])
    seed = np.clip(steady,0,maximum)
    zero = model.rollout(hours,initial,np.zeros(len(hours)),check)
    max_run = model.rollout(hours,initial,np.full(len(hours),maximum),check)
    # Start from a known schedule; both bounds and sensitivities are physical.
    cache = {}
    def objective(values):
        check()
        key=values.tobytes()
        if key not in cache:
            result = model.rollout(hours,initial,values,check,True)
            shortfall = np.minimum(result['states'][:,0]-target,0)
            # kWh objective plus squared comfort deficit in K²-hours.
            # 1000 kWh/K² makes deficits explicit and economically negligible.
            weight = 1000*model.dt
            cost = float(duration@values+weight*(shortfall@shortfall)+.005*np.sum(np.diff(values)**2))
            gradient = duration+2*weight*shortfall@result['room_jacobian']
            delta=np.diff(values)
            gradient[:-1]-=.01*delta;gradient[1:]+=.01*delta
            cache.clear();cache[key]=(cost,gradient,result)
        return cache[key]
    try:
        fit = minimize(lambda x:objective(x)[0],seed,jac=lambda x:objective(x)[1],
            method='SLSQP',bounds=[(0,maximum)]*len(hours),
            options={'maxiter':200,'ftol':1e-7})
        check()
    except PredictionDeadline:
        return {'status':'timeout','reason':'No optimized forecast published; runtime limit exceeded',
                'hourly':[],'elapsed_seconds':time.monotonic()-began}
    if not fit.success:
        return {'status':'optimizer_failed','reason':str(fit.message),'hourly':[],
                'elapsed_seconds':time.monotonic()-began}
    result=model.rollout(hours,initial,fit.x,check)
    hourly=[]
    display_end=hours[0]['start']+24*3600
    before=np.array(initial)
    for index,row in enumerate(hours):
        if row['start']>=display_end:break
        mask=result['hour_ids']==index
        seconds=min(row['seconds'],display_end-row['start'])
        steps=int(round(seconds/3600/model.dt))
        states=result['states'][mask][:steps]
        end=states[-1]
        hours_in_row=seconds/3600
        loss=float(result['outdoor_energy'][mask][:steps].sum())
        supplied=float(fit.x[index]*hours_in_row)
        floor_energy=float(result['floor_output'][mask][:steps].sum()*model.dt)
        solar_energy=p.solar_area*row['solar_kW_m2']*hours_in_row
        internal=p.internal_gain*hours_in_row
        stored=float(p.ci*(end[0]-before[0])+p.ce*(end[1]-before[1])+f.capacity*(end[2]-before[2]))
        minimum=float(states[:,0].min())
        hourly.append({'start':datetime.fromtimestamp(row['start'],UTC).isoformat(),
            'end':datetime.fromtimestamp(row['start']+seconds,UTC).isoformat(),
            'duration_hours':float(hours_in_row),'outdoor_temperature_C':row['outdoor_C'],
            'ghi_W_m2':row['solar_kW_m2']*1000,'heat_load_kW':float(fit.x[index]),
            'pump_heat_input_kW':float(fit.x[index]),'pump_energy_kWh':supplied,
            'room_temperature_C':float(end[0]),'minimum_room_temperature_C':min(float(before[0]),minimum),
            'envelope_temperature_C':float(end[1]),'floor_temperature_C':float(end[2]),
            'outdoor_heat_loss_kW':loss/hours_in_row, 'outdoor_heat_loss_kWh':loss,
            'floor_heat_output_kW':floor_energy/hours_in_row,
            'solar_gain_kWh':solar_energy,'internal_gain_kWh':internal,
            'storage_change_kWh':stored,'comfort_shortfall_K':max(0,target-min(float(before[0]),minimum)),
            'energy_balance_residual_kWh':supplied+solar_energy+internal-loss-stored})
        before=end
    # Optimize the extended horizon, publish exactly the next 24h (or all of a
    # deliberately shorter standalone test horizon). No horizon-end reset.
    display_steps=sum(int(round(r['duration_hours']/model.dt)) for r in hourly)
    predicted_room=result['states'][:display_steps,0]
    worst=max(0.,target-min(initial[0],float(predicted_room.min())))
    possible=max(0.,target-min(initial[0],float(max_run['states'][:display_steps,0].min())))
    display_hours=sum(r['duration_hours'] for r in hourly)
    return {'status':'ready' if worst<=.05 else 'comfort_shortfall',
        'method':'coupled house/exponent floor; hourly thermal input; five-minute internal steps',
        'initial_room_temperature_C':initial[0],'initial_envelope_temperature_C':initial[1],
        'initial_floor_temperature_C':initial[2],'target_temperature_C':target,
        'maximum_pump_power_kW':maximum,'hourly':hourly,
        'energy_kWh':sum(r['pump_energy_kWh'] for r in hourly),
        'hours':float(display_hours),'planning_horizon_hours':float(duration.sum()),
        'peak_load_kW':max(r['pump_heat_input_kW'] for r in hourly),
        'average_load_kW':sum(r['pump_energy_kWh'] for r in hourly)/display_hours,
        'minimum_room_temperature_C':min(initial[0],float(predicted_room.min())),
        'maximum_room_temperature_C':max(initial[0],float(predicted_room.max())),
        'maximum_comfort_shortfall_K':worst,'maximum_power_shortfall_K':possible,
        'no_pump_minimum_room_temperature_C':min(initial[0],float(zero['states'][:display_steps,0].min())),
        'optimizer_iterations':int(fit.nit),'optimizer_converged':True,
        'elapsed_seconds':time.monotonic()-began,
        'correction_policy':'Current measured room at initialization; no future resets or corrections',
        'interpretation':'Thermal kW to floor, not electrical power. Hourly averages assume modulation or duty cycling.'}


if __name__=='__main__':
    import json
    import sys
    try: result=predict(json.load(sys.stdin))
    except Exception as error:
        result={'status':'error','reason':f'{type(error).__name__}: {error}','hourly':[]}
    print(json.dumps(result,allow_nan=False))
