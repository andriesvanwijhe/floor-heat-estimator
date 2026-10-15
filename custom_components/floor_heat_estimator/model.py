"""Pure thermal model: no Home Assistant or third-party dependencies.

Time is UTC epoch seconds, temperatures are degrees C, power is kW,
capacity is kWh/K, and circulation is litres/hour.
"""

from dataclasses import asdict, dataclass
import math


def finite_number(value):
    """Accept only finite numerical values, never convert missing data to zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) else None


@dataclass(frozen=True)
class Parameters:
    capacity: float
    output_at_5k: float
    exponent: float = 1.0

    def __post_init__(self):
        if not all(math.isfinite(x) and x > 0 for x in (self.capacity, self.output_at_5k, self.exponent)):
            raise ValueError("Thermal parameters must be positive finite numbers")
        if not 0.5 <= self.exponent <= 3.0:
            raise ValueError("Exponent must be between 0.5 and 3")

    def output(self, temperature, room):
        return self.output_at_5k * (max(temperature - room, 0.0) / 5.0) ** self.exponent


def advance(temperature, room, power, seconds, parameters):
    """Advance the heating-only balance, keeping signed heat input.

Exact linear update with zero output below room temperature. For the
nonlinear model, use small adaptive explicit steps with a conservative
energy limiter, preserving the heat balance without overshooting room
temperature due solely to heat loss.
"""
    hours = max(seconds, 0.0) / 3600.0
    c = parameters.capacity
    if parameters.exponent == 1.0:
        k = parameters.output_at_5k / 5.0
        theta = temperature - room
        if theta <= 0:
            if power <= 0 or -theta * c / power >= hours:
                return temperature + power * hours / c
            hours -= -theta * c / power
            theta = 0.0
        if power < 0 and theta > 0:
            crossing = c / k * math.log1p(k * theta / -power)
            if crossing < hours:
                return room + power * (hours - crossing) / c
        return room + power / k + (theta - power / k) * math.exp(-k * hours / c)
    remaining = hours
    temperature = float(temperature)
    while remaining > 1e-12:
        theta = max(temperature - room, 0.0)
        loss = parameters.output(temperature, room)
        # At most 15 seconds, and at most 2% of the current excess-temperature
        # energy dissipated per step. The limiter also handles n < 1 at zero.
        step = min(remaining, 15.0 / 3600.0)
        if loss > 0 and theta > 1e-8:
            step = min(step, max(0.02 * c * theta / loss, 1e-7))
        loss_energy = min(loss * step, c * theta + max(power, 0.0) * step)
        temperature += (power * step - loss_energy) / c
        remaining -= step
    return temperature


@dataclass(frozen=True)
class Sample:
    timestamp: float
    room: float | None
    power: float | None
    supply: float | None
    return_temperature: float | None
    flow: float | None
    mode: str | None


class Estimator:
    """Measurement correction and open-loop prediction for both models."""

    def __init__(self, settings):
        self.settings = settings
        self.parameters = {
            "linear": Parameters(settings["linear_capacity"], 5 * settings["linear_conductance"]),
            "nonlinear": Parameters(settings["nonlinear_capacity"], settings["nonlinear_output_at_5k"], settings["exponent"]),
        }
        self.temperatures = {"linear": None, "nonlinear": None}
        self.previous = None
        self.active_since = None
        self.last_observation = None
        self.status = "waiting_for_observation"

    def _input(self, sample):
        if sample.mode in (None, "unknown", "unavailable", ""):
            return None
        # V1 is for heating-only circuits: input is assumed zero outside the
        # configured heating mode. This assumption is explicit in setup/docs.
        return sample.power if sample.mode == self.settings["active_mode"] else 0.0

    def step(self, sample):
        if self.previous and sample.timestamp < self.previous.timestamp:
            raise ValueError("Sample timestamps must be monotonic")
        gap = sample.timestamp - self.previous.timestamp if self.previous else 0.0
        if gap > self.settings["maximum_gap_minutes"] * 60:
            self.active_since = None
        circulating = (sample.mode == self.settings["active_mode"] and sample.flow is not None
                       and sample.flow > self.settings["flow_threshold"])
        if not circulating:
            self.active_since = None
        elif self.active_since is None:
            self.active_since = sample.timestamp
        observation_valid = (circulating and sample.room is not None and sample.supply is not None
                             and sample.return_temperature is not None
                             and sample.timestamp - self.active_since >= self.settings["settling_minutes"] * 60)
        current_input = self._input(sample)
        if self.previous:
            previous_input = self._input(self.previous)
            if (gap > self.settings["maximum_gap_minutes"] * 60 or previous_input is None
                    or self.previous.room is None):
                self.temperatures = {"linear": None, "nonlinear": None}
                self.status = "gap_requires_observation"
            else:
                for key, temperature in self.temperatures.items():
                    if temperature is not None:
                        self.temperatures[key] = advance(temperature, self.previous.room, previous_input, gap, self.parameters[key])
        # Measurement correction is allowed independently of power availability;
        # output remains unavailable until all current driving inputs are valid.
        if observation_valid:
            measured = (sample.supply + sample.return_temperature) / 2
            self.temperatures = {"linear": measured, "nonlinear": measured}
            self.last_observation = sample.timestamp
            self.status = "observed"
        elif all(value is not None for value in self.temperatures.values()):
            self.status = "estimated"
        if current_input is None or sample.room is None:
            self.status = "invalid_input"
        age = sample.timestamp - self.last_observation if self.last_observation is not None else None
        if age is not None and age > self.settings["maximum_prediction_hours"] * 3600:
            self.status = "observation_expired"
        available = (current_input is not None and sample.room is not None
                     and age is not None and age <= self.settings["maximum_prediction_hours"] * 3600
                     and all(value is not None for value in self.temperatures.values()))
        self.previous = sample
        return {
            "status": self.status,
            "measurement_valid": bool(observation_valid),
            "observation_age_minutes": age / 60 if age is not None else None,
            "linear_temperature": self.temperatures["linear"] if available else None,
            "nonlinear_temperature": self.temperatures["nonlinear"] if available else None,
            "linear_power": self.parameters["linear"].output(self.temperatures["linear"], sample.room) if available else None,
            "nonlinear_power": self.parameters["nonlinear"].output(self.temperatures["nonlinear"], sample.room) if available else None,
        }

    def snapshot(self):
        return {"version": 1, "temperatures": self.temperatures.copy(),
                "previous": asdict(self.previous) if self.previous else None,
                "active_since": self.active_since, "last_observation": self.last_observation}

    def restore(self, saved):
        """Validate a stored snapshot completely before assigning any state."""
        try:
            if saved.get("version") != 1:
                return False
            temps = {key: finite_number(saved["temperatures"][key]) for key in self.temperatures}
            raw = saved["previous"]
            timestamp = finite_number(raw["timestamp"])
            if timestamp is None or timestamp <= 0:
                return False
            previous = Sample(timestamp, *(finite_number(raw[key]) for key in
                ("room", "power", "supply", "return_temperature", "flow")), mode=raw.get("mode"))
            last = finite_number(saved.get("last_observation"))
            active = finite_number(saved.get("active_since"))
            if (last is not None and last > timestamp) or (active is not None and active > timestamp):
                return False
            if any(value is not None and not -100 < value < 200 for value in temps.values()):
                return False
        except (KeyError, TypeError, AttributeError):
            return False
        self.temperatures, self.previous = temps, previous
        self.last_observation, self.active_since = last, active
        return True

