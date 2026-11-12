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
    if power == 0 and temperature > room:
        # Exact power-law cooldown is both faster and independent of substep
        # size. Off periods dominate long training histories on small CPUs.
        theta=temperature-room
        delta=parameters.exponent-1
        rate=parameters.output_at_5k/(5**parameters.exponent*c)
        if abs(delta)<1e-8:
            return room+theta*math.exp(-rate*hours)
        factor=delta*rate*theta**delta*hours
        return room if factor<=-1 else room+theta*math.exp(-math.log1p(factor)/delta)
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
    """Continuous energy balance; water observations initialize or validate."""

    def __init__(self, settings):
        self.settings = settings
        self.parameters = {
            "linear": Parameters(settings["linear_capacity"], 5 * settings["linear_conductance"]),
            "nonlinear": Parameters(settings["nonlinear_capacity"], settings["nonlinear_output_at_5k"], settings["exponent"]),
        }
        if not settings.get('legacy_linear_enabled',False):
            self.parameters.pop('linear')
        self.temperatures = dict.fromkeys(self.parameters)
        self.previous = None
        self.active_since = None
        self.last_observation = None
        self.status = "waiting_for_observation"
        self.last_observed_run = None
        self.energy = {}
        self.last_restart_errors = dict.fromkeys(self.parameters)

    def _begin_energy(self, timestamp=None):
        self.energy = {key: {'initial_temperature': value, 'input_kWh': 0.0,
                            'output_kWh': 0.0, 'capacity': self.parameters[key].capacity,
                            'start_timestamp': timestamp if timestamp is not None else self.previous.timestamp if self.previous else None}
                       for key, value in self.temperatures.items() if value is not None}

    def energy_balance(self):
        result = {}
        for key, ledger in self.energy.items():
            if self.temperatures[key] is None:
                continue
            storage = ledger['capacity'] * (self.temperatures[key]-ledger['initial_temperature'])
            result[key] = {'input_kWh': ledger['input_kWh'], 'output_kWh': ledger['output_kWh'],
                           'storage_change_kWh': storage,
                           'start_timestamp': ledger.get('start_timestamp'),
                           'capacity': ledger['capacity'], 'initial_temperature': ledger['initial_temperature'],
                           'residual_kWh': ledger['input_kWh']-ledger['output_kWh']-storage}
        return result

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
        interval = None
        if self.previous:
            previous_input = self._input(self.previous)
            if (gap > self.settings["maximum_gap_minutes"] * 60 or previous_input is None
                    or self.previous.room is None):
                self.temperatures = dict.fromkeys(self.parameters)
                self.energy = {}
                self.status = "gap_requires_observation"
            else:
                if any(key not in self.energy or self.energy[key]['capacity'] != self.parameters[key].capacity
                       for key, value in self.temperatures.items() if value is not None):
                    self._begin_energy()
                outputs = {}
                for key, temperature in self.temperatures.items():
                    if temperature is not None:
                        updated = advance(temperature, self.previous.room, previous_input, gap, self.parameters[key])
                        energy_in = previous_input*gap/3600
                        energy_out = energy_in-self.parameters[key].capacity*(updated-temperature)
                        outputs[key] = energy_out
                        self.energy[key]['input_kWh'] += energy_in
                        self.energy[key]['output_kWh'] += energy_out
                        self.temperatures[key] = updated
                if gap > 0 and len(outputs) == len(self.parameters):
                    interval = {'start': self.previous.timestamp, 'end': sample.timestamp,
                                'input_kWh': previous_input*gap/3600, 'output_kWh': outputs}
        measured = (sample.supply+sample.return_temperature)/2 if observation_valid else None
        residuals = {key: measured-temperature if measured is not None and temperature is not None else None
                     for key, temperature in self.temperatures.items()}
        initialized = False
        restart = False
        if observation_valid:
            # Never inject/remove stored energy at a normal heating restart or
            # subsequent reading. Only initialize an unobservable/gapped state.
            if any(value is None for value in self.temperatures.values()):
                self.temperatures = dict.fromkeys(self.temperatures, measured)
                self._begin_energy(sample.timestamp)
                initialized = True
            else:
                restart = self.last_observed_run != self.active_since
                if restart:
                    self.last_restart_errors = residuals.copy()
            self.last_observation = sample.timestamp
            self.last_observed_run = self.active_since
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
            "linear_temperature": self.temperatures.get("linear") if available else None,
            "nonlinear_temperature": self.temperatures["nonlinear"] if available else None,
            "linear_power": self.parameters["linear"].output(self.temperatures["linear"], sample.room) if available and 'linear' in self.parameters else None,
            "nonlinear_power": self.parameters["nonlinear"].output(self.temperatures["nonlinear"], sample.room) if available else None,
            "measured_temperature": measured,
            "linear_observation_error": residuals.get('linear'),
            "nonlinear_observation_error": residuals['nonlinear'],
            "restart_observation": restart,
            "initialized_from_measurement": initialized,
            "interval_energy": interval,
            "energy_balance": self.energy_balance(),
            "linear_restart_error": self.last_restart_errors.get('linear'),
            "nonlinear_restart_error": self.last_restart_errors['nonlinear'],
            "linear_energy_balance_residual": self.energy_balance().get('linear',{}).get('residual_kWh'),
            "nonlinear_energy_balance_residual": self.energy_balance().get('nonlinear',{}).get('residual_kWh'),
        }

    def snapshot(self):
        return {"version": 2, "temperatures": self.temperatures.copy(),
                "previous": asdict(self.previous) if self.previous else None,
                "active_since": self.active_since, "last_observation": self.last_observation,
                "last_observed_run": self.last_observed_run, "energy": {key:dict(value) for key,value in self.energy.items()},
                "last_restart_errors": self.last_restart_errors.copy()}

    def restore(self, saved):
        """Validate a stored snapshot completely before assigning any state."""
        try:
            if saved.get("version") not in (1, 2):
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
        self.last_observed_run = finite_number(saved.get('last_observed_run'))
        restart_errors=saved.get('last_restart_errors')
        self.last_restart_errors={key:finite_number(restart_errors.get(key)) if isinstance(restart_errors,dict) else None for key in self.temperatures}
        self._begin_energy()
        energy = saved.get('energy')
        if isinstance(energy, dict) and all(key in energy and isinstance(energy[key], dict)
                and all(finite_number(energy[key].get(k)) is not None
                        for k in ('initial_temperature', 'input_kWh', 'output_kWh', 'capacity'))
                and energy[key]['capacity'] == self.parameters[key].capacity for key in self.energy):
            self.energy = {key: dict(energy[key]) for key in self.energy}
        return True

