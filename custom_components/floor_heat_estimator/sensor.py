"""Power estimates and model diagnostics."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfPower, UnitOfTemperature
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION

DESCRIPTIONS = (
    ("linear_power", "Linear heat output", UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
    ("nonlinear_power", "Exponent heat output", UnitOfPower.KILO_WATT, SensorDeviceClass.POWER),
    ("linear_temperature", "Linear effective temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    ("nonlinear_temperature", "Exponent effective temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
    ("status", "Model status", None, None),
    ("observation_age_minutes", "Time since observation", "min", SensorDeviceClass.DURATION),
)

# Parameters remain readable even when the model has no valid observation.
PARAMETER_DESCRIPTIONS = (
    ("linear_conductance", "Linear conductance K", "kW/K"),
    ("linear_capacity", "Linear thermal inertia C", "kWh/K"),
    ("nonlinear_capacity", "Exponent thermal inertia C", "kWh/K"),
    ("exponent", "Heat output exponent", None),
    ("nonlinear_output_at_5k", "Exponent heat output at 5 K", "kW"),
    ("linear_time_constant", "Linear time constant", "min"),
)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        *(FloorSensor(coordinator, entry, *description) for description in DESCRIPTIONS),
        *(FloorParameterSensor(coordinator, entry, *description)
          for description in PARAMETER_DESCRIPTIONS),
    ])


class FloorSensor(CoordinatorEntity, SensorEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator, entry, key, name, unit, device_class):
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        if unit is not None:
            self._attr_suggested_display_precision = 3 if key.endswith("power") else 2
        if unit is not None:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        if not key.endswith("power"):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.settings["name"], manufacturer="Floor Heat Estimator",
            model="Experimental thermal model", sw_version=VERSION)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if self.key == "linear_power":
            previous = await self.async_get_last_state()
            if previous:
                self.coordinator.restore(previous.attributes.get("estimator_state"))
                await self.coordinator.async_request_refresh()

    @property
    def available(self):
        return super().available and self.coordinator.data.get(self.key) is not None

    @property
    def native_value(self):
        return self.coordinator.data.get(self.key)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        settings = self.coordinator.settings
        attributes = {"experimental": True, "measurement_valid": data["measurement_valid"],
                      "model_status": data["status"]}
        if self.key == "linear_power":
            attributes.update({"conductance_kW_per_K": settings["linear_conductance"],
                "capacity_kWh_per_K": settings["linear_capacity"],
                "time_constant_hours": settings["linear_capacity"] / settings["linear_conductance"],
                "estimator_state": self.coordinator.stored_state()})
        elif self.key == "nonlinear_power":
            attributes.update({"exponent": settings["exponent"],
                "output_at_5K_kW": settings["nonlinear_output_at_5k"],
                "coefficient_kW_per_K_to_n": settings["nonlinear_output_at_5k"] / 5 ** settings["exponent"],
                "capacity_kWh_per_K": settings["nonlinear_capacity"]})
        return attributes


class FloorParameterSensor(FloorSensor):
    """Expose the configured model coefficients, without requiring live inputs."""

    def __init__(self, coordinator, entry, key, name, unit):
        device_class = SensorDeviceClass.DURATION if key == "linear_time_constant" else None
        super().__init__(coordinator, entry, key, name, unit, device_class)
        self._attr_suggested_display_precision = 2 if key == "linear_time_constant" else 4
        # Also marks the unitless exponent as numeric for history charts.
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self):
        # These describe the loaded configuration, independently of telemetry.
        return True

    @property
    def native_value(self):
        settings = self.coordinator.settings
        if self.key == "linear_time_constant":
            return 60 * settings["linear_capacity"] / settings["linear_conductance"]
        return settings[self.key]

    @property
    def extra_state_attributes(self):
        attributes = {"experimental": True, "parameter_source": "manual_configuration"}
        if self.key == "nonlinear_output_at_5k":
            settings = self.coordinator.settings
            attributes.update({
                "exponent": settings["exponent"],
                "coefficient_kW_per_K_to_n": settings["nonlinear_output_at_5k"] / 5 ** settings["exponent"],
            })
        return attributes

