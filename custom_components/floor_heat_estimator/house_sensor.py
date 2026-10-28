"""House states, physical coefficients and fit diagnostics on the same device."""

from datetime import UTC, datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .house_data import floor_signature

DESCRIPTIONS = (
    ("house_envelope_temperature", "House envelope temperature", "°C"),
    ("house_interior_temperature", "House estimated interior temperature", "°C"),
    ("house_envelope_uncertainty", "House envelope temperature standard uncertainty", "K"),
    ("house_solar_gain", "House estimated solar gain", "kW"),
    ("house_interior_envelope_flow", "House interior to envelope heat flow", "kW"),
    ("house_outdoor_heat_flow", "House envelope to outdoor heat flow", "kW"),
    ("house_ci", "House interior heat capacity", "kWh/K"),
    ("house_ce", "House envelope heat capacity", "kWh/K"),
    ("house_rie", "House interior envelope resistance", "K/kW"),
    ("house_rea", "House envelope outdoor resistance", "K/kW"),
    ("house_solar_area", "House effective solar area", "m²"),
    ("house_internal_gain", "House internal heat gain", "W"),
    ("house_hie", "House interior envelope transfer coefficient", "W/K"),
    ("house_hea", "House envelope outdoor transfer coefficient", "W/K"),
    ("house_heat_loss_coefficient", "House overall heat loss coefficient", "W/K"),
    ("house_fit_status", "House fit status", None),
    ("house_last_attempt", "House last fit attempt", "timestamp"),
    ("house_last_accepted", "House last applied fit", "timestamp"),
    ("house_next_refit", "House next automatic fit", "timestamp"),
    ("house_validation_1h", "House 1 hour validation error", "K"),
    ("house_validation_6h", "House 6 hour validation error", "K"),
    ("house_validation_24h", "House 24 hour validation error", "K"),
    ("house_point_count", "House fit observation count", None),
    ("house_tomorrow_heat_load", "House tomorrow average heat load", "kW"),
    ("house_tomorrow_heat_energy", "House tomorrow heating energy", "kWh"),
    ("house_tomorrow_peak_heat_load", "House tomorrow peak heat load", "kW"),
)


def house_entities(coordinator, entry):
    return [HouseSensor(coordinator, entry, *description) for description in DESCRIPTIONS]


class HouseSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, key, name, unit):
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
        if unit == "timestamp":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif key != "house_fit_status":
            self._attr_native_unit_of_measurement = unit
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 0 if key == "house_point_count" else 3
            if unit == "°C":
                self._attr_device_class = SensorDeviceClass.TEMPERATURE
            elif unit in ("kW", "W"):
                self._attr_device_class = SensorDeviceClass.POWER
            elif unit == "kWh":
                self._attr_device_class = SensorDeviceClass.ENERGY
            if key.startswith("house_tomorrow_"):
                self._attr_entity_category = None
                self._attr_state_class = None

    @property
    def native_value(self):
        value = self.coordinator.house.data().get(self.key)
        if self.key in ("house_last_attempt", "house_last_accepted", "house_next_refit") and value is not None:
            return datetime.fromtimestamp(value, UTC)
        return value

    @property
    def available(self):
        return self.native_value is not None

    @property
    def extra_state_attributes(self):
        manager = self.coordinator.house
        attributes = {"experimental": True, "parameter_source": manager.parameter_source,
                      "model": "Bacher & Madsen (2011), Figure 1 + constant internal gain",
                      "floor_calibration_changed": bool(manager.own_heat_source and manager.calibrated_floor
                          and manager.calibrated_floor != floor_signature(manager.settings)),
                      "last_applied_fit": datetime.fromtimestamp(manager.last_accepted, UTC).isoformat()
                          if manager.last_accepted is not None else None}
        if self.key == "house_fit_status":
            attributes.update(report=manager.report, history_import=manager.history_import,
                heat_source=manager.settings["house_heat_input"],
                thermostat_source=manager.settings["room_temperature"],
                solar_source=manager.settings["house_solar"],
                automatic_learning=manager.settings["house_learning_enabled"] and manager.settings["record_samples"])
        if self.key == "house_envelope_uncertainty":
            attributes["interpretation"] = "Conditional Kalman state standard deviation; excludes parameter and floor-input uncertainty"
        if self.key == "house_interior_temperature":
            attributes.update(correction_policy="local_midnight", timezone=manager.timezone,
                              last_correction_utc=datetime.fromtimestamp(manager.engine.last_correction, UTC).isoformat()
                              if manager.engine.last_correction is not None else None,
                              initialization="Thermostat reading at startup or after missing drivers")
        if self.key.startswith("house_tomorrow_"):
            attributes.update(manager.forecast, forecast_entity=manager.settings["house_forecast_source"])
        if manager.engine.timestamp is not None:
            attributes["state_time_utc"] = datetime.fromtimestamp(manager.engine.timestamp, UTC).isoformat()
        parameter = self.key.removeprefix("house_")
        interval = manager.active_intervals.get(parameter)
        if interval:
            attributes["approximate_95_percent_interval"] = (
                {"lower_95": 1000*interval["lower_95"], "upper_95": 1000*interval["upper_95"],
                 "unit": "W"} if parameter == "internal_gain" else interval)
        if parameter == "internal_gain":
            attributes["interpretation"] = "Fitted constant combined occupancy and electrical-device heat gain into the interior node"
        resistance = {"house_hie": "rie", "house_hea": "rea"}.get(self.key)
        if resistance and resistance in manager.active_intervals:
            interval = manager.active_intervals[resistance]
            attributes["approximate_95_percent_interval"] = {
                "lower_95": 1000/interval["upper_95"], "upper_95": 1000/interval["lower_95"]}
        return attributes
