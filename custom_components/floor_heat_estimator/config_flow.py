"""UI setup and editable settings for the floor heat estimator."""

import math

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DEFAULTS, DOMAIN, SOURCE_KEYS
from .house_const import HOUSE_LIMITS, HOUSE_SOURCES

LIMITS = {
    "flow_threshold": (0, 10000, "l/h"),
    "settling_minutes": (0, 120, "min"),
    "linear_capacity": (0.01, 100, "kWh/K"),
    "linear_conductance": (0.001, 10, "kW/K"),
    "nonlinear_capacity": (0.01, 100, "kWh/K"),
    "nonlinear_output_at_5k": (0.001, 100, "kW"),
    "exponent": (0.5, 3, None),
    "maximum_gap_minutes": (1, 60, "min"),
    "maximum_prediction_hours": (1, 168, "h"),
    "learning_days": (2, 90, "d"),
    "refit_interval_days": (1, 90, "d"),
    "maximum_fit_rmse": (0.05, 5, "K"),
}


def source_schema(defaults):
    fields = {vol.Required("name", default=defaults.get("name", DEFAULTS["name"])): selector.TextSelector()}
    for key in SOURCE_KEYS:
        marker = vol.Required(key, default=defaults[key]) if key in defaults else vol.Required(key)
        fields[marker] = selector.EntitySelector(selector.EntitySelectorConfig(
            domain=["sensor", "input_number", "input_select"] if key == "mode" else ["sensor", "input_number"]))
    fields[vol.Required("active_mode", default=defaults.get("active_mode", DEFAULTS["active_mode"]))] = selector.TextSelector()
    return vol.Schema(fields)


def parameter_schema(defaults):
    fields = {}
    for key, (low, high, unit) in LIMITS.items():
        settings = dict(min=low, max=high, step="any", mode=selector.NumberSelectorMode.BOX)
        if unit is not None:
            settings["unit_of_measurement"] = unit
        fields[vol.Required(key, default=defaults.get(key, DEFAULTS[key]))] = selector.NumberSelector(
            selector.NumberSelectorConfig(**settings))
    fields[vol.Required("record_samples", default=defaults.get("record_samples", True))] = selector.BooleanSelector()
    for key in ("learning_enabled", "apply_fitted_parameters", "import_history_on_start"):
        fields[vol.Required(key, default=defaults.get(key, DEFAULTS[key]))] = selector.BooleanSelector()
    return vol.Schema(fields)


def validate_sources(hass, values):
    errors = {}
    if not values["name"].strip() or not values["active_mode"].strip():
        errors["base"] = "empty_name_or_mode"
    for key in SOURCE_KEYS:
        state = hass.states.get(values[key])
        if state is None:
            errors[key] = "entity_missing"
        elif key != "mode":
            # Validate units even when the entity is temporarily unavailable.
            unit = str(state.attributes.get("unit_of_measurement") or "").strip().lower()
            allowed = ({"kw", "w"} if key == "heat_input" else
                       {"l/h", "l/hr", "l/hour", "l/min", "l/m", "m³/h", "m3/h"} if key == "flow" else
                       {"°c", "c", "°f", "f", ""})
            if unit not in allowed:
                errors[key] = "unsupported_unit"
    return errors


def validate_parameters(values):
    for key, (low, high, _) in LIMITS.items():
        value = values.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            return {key: "invalid_parameter"}
    if values.get("learning_enabled") and not values.get("record_samples"):
        return {"base": "learning_requires_recording"}
    return {}


def house_schema(defaults):
    fields = {vol.Required("house_enabled", default=defaults["house_enabled"]): selector.BooleanSelector()}
    fields[vol.Required("house_forecast_source", default=defaults["house_forecast_source"])] = selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["sensor"]))
    for key in HOUSE_SOURCES:
        fields[vol.Required(key, default=defaults[key])] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["sensor", "input_number"]))
    for key, (low, high, unit) in HOUSE_LIMITS.items():
        fields[vol.Required(key, default=defaults[key])] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=low, max=high, step="any",
                mode=selector.NumberSelectorMode.BOX, unit_of_measurement=unit))
    for key in ("house_learning_enabled", "house_apply_fitted_parameters", "house_import_history_on_start"):
        fields[vol.Required(key, default=defaults[key])] = selector.BooleanSelector()
    return vol.Schema(fields)


def validate_house(hass, values, record_samples):
    for key, (low, high, _) in HOUSE_LIMITS.items():
        value = values.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            return {key: "invalid_parameter"}
    if not values["house_enabled"]:
        return {}
    if values["house_learning_enabled"] and not record_samples:
        return {"base": "learning_requires_recording"}
    for key in HOUSE_SOURCES:
        state = hass.states.get(values[key])
        if state is None:
            return {key: "entity_missing"}
        unit = str(state.attributes.get("unit_of_measurement") or "").lower().replace(" ", "")
        allowed = ({"w", "kw"} if key == "house_heat_input" else
                   {"w/m²", "w/m2", "w/m^2", "kw/m²", "kw/m2", "kw/m^2"} if key == "house_solar" else
                   {"°c", "c", "°f", "f", ""})
        if unit not in allowed:
            return {key: "house_unsupported_unit"}
    return {}


class FloorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors = validate_sources(self.hass, user_input)
            if not errors:
                self._sources = user_input
                return await self.async_step_parameters()
        return self.async_show_form(step_id="user", data_schema=source_schema(user_input or {}), errors=errors)

    async def async_step_parameters(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors = validate_parameters(user_input)
            if not errors:
                self._parameters = user_input
                return await self.async_step_house()
        return self.async_show_form(step_id="parameters", data_schema=parameter_schema(user_input or DEFAULTS), errors=errors)

    async def async_step_house(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors = validate_house(self.hass, user_input, self._parameters["record_samples"])
            if not errors:
                return self.async_create_entry(title=self._sources["name"],
                    data={**self._sources, **self._parameters, **user_input})
        return self.async_show_form(step_id="house", data_schema=house_schema({**DEFAULTS, **(user_input or {})}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FloorOptionsFlow()


class FloorOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        defaults = {**DEFAULTS, **self.config_entry.data, **self.config_entry.options}
        errors = {}
        if user_input is not None:
            errors = validate_sources(self.hass, user_input)
            if not errors:
                self._sources = user_input
                return await self.async_step_parameters()
        return self.async_show_form(step_id="init", data_schema=source_schema(user_input or defaults), errors=errors)

    async def async_step_parameters(self, user_input=None):
        defaults = {**DEFAULTS, **self.config_entry.data, **self.config_entry.options}
        errors = {}
        if user_input is not None:
            errors = validate_parameters(user_input)
            if not errors:
                self._parameters = user_input
                return await self.async_step_house()
        return self.async_show_form(step_id="parameters", data_schema=parameter_schema(user_input or defaults), errors=errors)


    async def async_step_house(self, user_input=None):
        defaults = {**DEFAULTS, **self.config_entry.data, **self.config_entry.options}
        errors = {}
        if user_input is not None:
            errors = validate_house(self.hass, user_input, self._parameters["record_samples"])
            if not errors:
                return self.async_create_entry(title="", data={**self._sources, **self._parameters, **user_input})
        return self.async_show_form(step_id="house", data_schema=house_schema({**defaults, **(user_input or {})}), errors=errors)
