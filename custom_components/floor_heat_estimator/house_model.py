"""Bacher & Madsen (2011), Fig. 1: continuous two-state RC model.

Pure standard-library mathematics. Time is hours, power kW, resistance
K/kW, capacity kWh/K, solar kW/m², and process diffusion K²/hour.
Exact constant-input transitions and diffusion integrals; no Euler steps.
"""

from dataclasses import asdict, dataclass
import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def midnight_due(timestamp, previous, timezone):
    """First completed local-midnight bin; retry missing room data until 01:00."""
    local = datetime.fromtimestamp(timestamp, UTC).astimezone(ZoneInfo(timezone))
    return local.hour == 0 and (previous is None or
        datetime.fromtimestamp(previous, UTC).astimezone(ZoneInfo(timezone)).date() < local.date())


@dataclass(frozen=True)
class HouseParameters:
    ci: float = 5.0
    ce: float = 30.0
    rie: float = 1.0
    rea: float = 4.0
    solar_area: float = 10.0
    internal_gain: float = 0.5
    sigma_i: float = 0.02
    sigma_e: float = 0.01
    sigma_y: float = 0.1

    def __post_init__(self):
        if not all(not isinstance(v, bool) and math.isfinite(v)
                   and (v >= 0 if key == "internal_gain" else v > 0)
                   for key, v in asdict(self).items()):
            raise ValueError("House coefficients/noise must be positive and finite; internal gain may be zero")

    def to_dict(self):
        return asdict(self)


def multiply(a, b):
    return (a[0]*b[0]+a[1]*b[2], a[0]*b[1]+a[1]*b[3],
            a[2]*b[0]+a[3]*b[2], a[2]*b[1]+a[3]*b[3])


def transition(parameters, hours):
    """Return Phi, Qd and input gains for a two-dimensional linear SDE."""
    p = parameters
    a, b = -1/(p.rie*p.ci), 1/(p.rie*p.ci)
    c, d = 1/(p.rie*p.ce), -(1/p.rie+1/p.rea)/p.ce
    disc = math.sqrt((a-d)**2+4*b*c)
    first, second = (a+d+disc)/2, (a+d-disc)/2
    f = ((a-second)/disc, b/disc, c/disc, (d-second)/disc)
    g = (1-f[0], -f[1], -f[2], 1-f[3])
    e1, e2 = math.exp(first*hours), math.exp(second*hours)
    phi = tuple(e1*x+e2*y for x, y in zip(f, g))
    diffusion = (p.sigma_i**2, 0, 0, p.sigma_e**2)
    q = [0.0]*4
    for lam, left in ((first, f), (second, g)):
        for mu, right in ((first, f), (second, g)):
            scale = math.expm1((lam+mu)*hours)/(lam+mu)
            term = multiply(multiply(left, diffusion), (right[0], right[2], right[1], right[3]))
            for i in range(4):
                q[i] += scale*term[i]
    x, y, z, w = phi
    # x_next = Phi*x + outdoor_gain*Ta + heating_gain*(Qh+Aw*G+Qinternal).
    gains = (1-x-y, 1-z-w, (1-x)*(p.rie+p.rea)-y*p.rea,
             -z*(p.rie+p.rea)+(1-w)*p.rea)
    return phi, (max(q[0], 0), (q[1]+q[2])/2, max(q[3], 0)), gains


def advance(state, outdoor, heat, solar, parameters, coefficients):
    phi, _, gains = coefficients
    power = heat+parameters.solar_area*solar+parameters.internal_gain
    return (phi[0]*state[0]+phi[1]*state[1]+gains[0]*outdoor+gains[2]*power,
            phi[2]*state[0]+phi[3]*state[1]+gains[1]*outdoor+gains[3]*power)


def predict_covariance(covariance, coefficients):
    (a, b, c, d), q, _ = coefficients
    x, y, z = covariance
    return (a*a*x+2*a*b*y+b*b*z+q[0],
            a*c*x+(a*d+b*c)*y+b*d*z+q[1],
            c*c*x+2*c*d*y+d*d*z+q[2])


def correct(state, covariance, observed, variance):
    x, cross, z = covariance
    innovation = observed-state[0]
    s = x+variance
    if not math.isfinite(s) or s <= 0:
        raise ValueError("Invalid house innovation variance")
    corrected = (state[0]+x/s*innovation, state[1]+cross/s*innovation)
    posterior = (max(x*variance/s, 1e-12), cross*variance/s,
                 max(z-cross*cross/s, 1e-12))
    return corrected, posterior, innovation, s


def initial_state(room, outdoor, parameters):
    envelope = (parameters.rea*room+parameters.rie*outdoor)/(parameters.rie+parameters.rea)
    return (room, envelope), (1.0, 0.0, 25.0)


class HouseEstimator:
    """Live filtering of completed five-minute input bins, without future data."""

    def __init__(self, parameters=None, correction_timezone=None):
        self.parameters = parameters or HouseParameters()
        self.correction_timezone = correction_timezone
        self.last_correction = None
        self.timestamp = None
        self.state = None
        self.covariance = None

    def step(self, point):
        t, room, outdoor, heat, solar = point
        if self.timestamp is not None and t <= self.timestamp:
            return
        if any(value is None or not math.isfinite(value) for value in (outdoor, heat, solar)):
            self.state = self.covariance = self.timestamp = None
            return
        if self.timestamp is None or t-self.timestamp > 301:
            if room is None:
                self.state = self.covariance = self.timestamp = None
                return
            self.state, self.covariance = initial_state(room, outdoor, self.parameters)
        else:
            coefficients = transition(self.parameters, (t-self.timestamp)/3600)
            self.state = advance(self.state, outdoor, heat, solar, self.parameters, coefficients)
            self.covariance = predict_covariance(self.covariance, coefficients)
        if room is not None and (self.correction_timezone is None or
                midnight_due(t, self.last_correction, self.correction_timezone)):
            self.state, self.covariance, _, _ = correct(
                self.state, self.covariance, room, self.parameters.sigma_y**2)
            self.last_correction = t
        self.timestamp = t

    def snapshot(self):
        if self.state is None:
            return None
        return {"timestamp": self.timestamp, "state": list(self.state), "covariance": list(self.covariance),
                "last_correction": self.last_correction}

    def restore(self, saved):
        try:
            t = saved["timestamp"]
            state, cov = saved["state"], saved["covariance"]
            if (len(state) != 2 or len(cov) != 3 or cov[0] <= 0 or cov[2] <= 0
                    or cov[0]*cov[2] < cov[1]**2-1e-9
                    or not all(math.isfinite(v) for v in [t, *state, *cov])):
                return False
        except (KeyError, TypeError, ValueError):
            return False
        correction = saved.get("last_correction")
        if correction is not None and (not isinstance(correction, (int, float)) or
                not math.isfinite(correction) or correction > t):
            return False
        self.timestamp, self.state, self.covariance = t, tuple(state), tuple(cov)
        self.last_correction = correction
        return True
