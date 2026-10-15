"""Entity contract tests with minimal Home Assistant framework stand-ins."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "floor_heat_estimator"


def load_sensor():
    """Load the real entity code without requiring a running HA installation."""
    class CoordinatorEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    class SensorEntity:
        pass

    class RestoreEntity:
        pass

    modules = {}
    for name in ("homeassistant", "homeassistant.components", "homeassistant.components.sensor",
                 "homeassistant.const", "homeassistant.helpers", "homeassistant.helpers.entity",
                 "homeassistant.helpers.restore_state", "homeassistant.helpers.update_coordinator"):
        modules[name] = ModuleType(name)
    modules["homeassistant.components.sensor"].SensorDeviceClass = SimpleNamespace(
        POWER="power", TEMPERATURE="temperature", DURATION="duration")
    modules["homeassistant.components.sensor"].SensorEntity = SensorEntity
    modules["homeassistant.components.sensor"].SensorStateClass = SimpleNamespace(MEASUREMENT="measurement")
    modules["homeassistant.const"].EntityCategory = SimpleNamespace(DIAGNOSTIC="diagnostic")
    modules["homeassistant.const"].UnitOfPower = SimpleNamespace(KILO_WATT="kW")
    modules["homeassistant.const"].UnitOfTemperature = SimpleNamespace(CELSIUS="C")
    modules["homeassistant.helpers.entity"].DeviceInfo = lambda **kwargs: kwargs
    modules["homeassistant.helpers.restore_state"].RestoreEntity = RestoreEntity
    modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = CoordinatorEntity
    package = ModuleType("_floor_sensor_test")
    package.__path__ = [str(ROOT)]
    modules[package.__name__] = package
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("_floor_sensor_test.sensor", ROOT / "sensor.py")
        sensor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sensor)
        defaults = sys.modules["_floor_sensor_test.const"].DEFAULTS.copy()
    return sensor, defaults


SENSOR, DEFAULTS = load_sensor()


class ParameterSensorTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = SimpleNamespace(settings=DEFAULTS.copy(), data={}, last_update_success=False)
        self.entry = SimpleNamespace(entry_id="example")

    def entity(self, key):
        description = next(d for d in SENSOR.PARAMETER_DESCRIPTIONS if d[0] == key)
        return SENSOR.FloorParameterSensor(self.coordinator, self.entry, *description)

    def test_parameters_remain_available_without_observation_or_successful_update(self):
        for key, _, _ in SENSOR.PARAMETER_DESCRIPTIONS:
            entity = self.entity(key)
            self.assertTrue(entity.available)
            self.assertIsInstance(entity.native_value, (int, float))
            self.assertEqual(entity.extra_state_attributes["parameter_source"], "manual_configuration")

    def test_time_constant_uses_current_capacity_and_conductance(self):
        entity = self.entity("linear_time_constant")
        self.assertAlmostEqual(entity.native_value, 32.3526874, places=5)
        self.coordinator.settings.update(linear_capacity=2, linear_conductance=0.5)
        self.assertEqual(entity.native_value, 240)
        self.assertEqual(entity._attr_native_unit_of_measurement, "min")

    def test_changed_settings_are_reflected_without_stale_defaults(self):
        for key in ("linear_conductance", "linear_capacity", "nonlinear_capacity", "exponent",
                    "nonlinear_output_at_5k"):
            entity = self.entity(key)
            self.coordinator.settings[key] = 2.25
            self.assertEqual(entity.native_value, 2.25)

    def test_exponent_is_numeric_and_status_remains_textual(self):
        exponent = self.entity("exponent")
        self.assertEqual(exponent._attr_state_class, "measurement")
        self.assertEqual(exponent._attr_suggested_display_precision, 4)
        self.assertIsNone(exponent._attr_native_unit_of_measurement)
        status = SENSOR.FloorSensor(self.coordinator, self.entry, "status", "Status", None, None)
        self.assertFalse(hasattr(status, "_attr_suggested_display_precision"))

    def test_nonlinear_coefficient_matches_normalized_model(self):
        entity = self.entity("nonlinear_output_at_5k")
        self.coordinator.settings.update(nonlinear_output_at_5k=2, exponent=2)
        self.assertEqual(entity.native_value, 2)
        self.assertAlmostEqual(entity.extra_state_attributes["coefficient_kW_per_K_to_n"], 0.08)

    def test_setup_adds_six_existing_and_six_parameter_entities_with_unique_ids(self):
        entities = []
        hass = SimpleNamespace(data={"floor_heat_estimator": {"example": self.coordinator}})
        asyncio.run(SENSOR.async_setup_entry(hass, self.entry, entities.extend))
        self.assertEqual(len(entities), 12)
        self.assertEqual(len({e._attr_unique_id for e in entities}), 12)
        parameter_entities = [e for e in entities if isinstance(e, SENSOR.FloorParameterSensor)]
        self.assertEqual(len(parameter_entities), 6)
        self.assertTrue(all(e._attr_entity_category == "diagnostic" for e in parameter_entities))


if __name__ == "__main__":
    unittest.main()
