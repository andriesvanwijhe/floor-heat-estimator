"""Three-page setup, optional upgrade behavior and house entity metadata."""

import asyncio
from datetime import UTC, datetime
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]/'custom_components/floor_heat_estimator'
package = ModuleType('_house_ui_tests')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package


class Flow:
    def __init_subclass__(cls,**kwargs):pass
    def async_show_form(self,**kwargs):return {'type':'form',**kwargs}
    def async_create_entry(self,**kwargs):return {'type':'create_entry',**kwargs}


class CoordinatorEntity:
    def __init__(self,coordinator):self.coordinator=coordinator


modules={name:ModuleType(name) for name in ('voluptuous','homeassistant','homeassistant.config_entries',
    'homeassistant.core','homeassistant.helpers','homeassistant.helpers.selector','homeassistant.components',
    'homeassistant.components.sensor','homeassistant.const','homeassistant.helpers.entity',
    'homeassistant.helpers.update_coordinator')}
modules['voluptuous'].Schema=lambda value:value
modules['voluptuous'].Required=lambda name,**kwargs:name
modules['homeassistant.config_entries'].ConfigFlow=Flow
modules['homeassistant.config_entries'].OptionsFlow=Flow
modules['homeassistant.core'].callback=lambda fn:fn
selector=modules['homeassistant.helpers.selector']
for key in ('TextSelector','EntitySelector','EntitySelectorConfig','NumberSelector','NumberSelectorConfig','BooleanSelector'):
    setattr(selector,key,lambda *args,**kwargs:kwargs)
selector.NumberSelectorMode=SimpleNamespace(BOX='box')
modules['homeassistant.helpers'].selector=selector
modules['homeassistant.components.sensor'].SensorEntity=type('SensorEntity',(),{})
modules['homeassistant.components.sensor'].SensorDeviceClass=SimpleNamespace(TIMESTAMP='timestamp',TEMPERATURE='temperature',POWER='power')
modules['homeassistant.components.sensor'].SensorStateClass=SimpleNamespace(MEASUREMENT='measurement')
modules['homeassistant.const'].EntityCategory=SimpleNamespace(DIAGNOSTIC='diagnostic')
modules['homeassistant.helpers.entity'].DeviceInfo=lambda **kwargs:kwargs
modules['homeassistant.helpers.update_coordinator'].CoordinatorEntity=CoordinatorEntity
with patch.dict(sys.modules,modules):
    config=importlib.import_module('_house_ui_tests.config_flow')
    sensor=importlib.import_module('_house_ui_tests.house_sensor')
const=importlib.import_module('_house_ui_tests.const')
house_const=importlib.import_module('_house_ui_tests.house_const')
model=importlib.import_module('_house_ui_tests.house_model')


class HouseUITests(unittest.TestCase):
    def setUp(self):
        self.defaults={**const.DEFAULTS,**{key:'sensor.'+key for key in const.SOURCE_KEYS}}
        def state(entity):
            unit='W/m²' if entity==self.defaults['house_solar'] else 'kW' if entity==self.defaults['house_heat_input'] else '°C'
            return SimpleNamespace(state='unavailable',attributes={'unit_of_measurement':unit})
        self.hass=SimpleNamespace(states=SimpleNamespace(get=state))

    def test_existing_configuration_defaults_to_house_disabled(self):
        self.assertFalse(self.defaults['house_enabled'])
        self.assertEqual(self.defaults['house_refit_interval_days'],7)
        self.assertEqual(self.defaults['house_max_fit_minutes'],15)
        self.assertEqual(self.defaults['house_internal_gain'],500)

    def test_house_validation_accepts_units_without_requiring_current_readings(self):
        values={**house_const.HOUSE_DEFAULTS,'house_enabled':True}
        self.assertEqual(config.validate_house(self.hass,values,True),{})
        values['house_solar_area']=-1
        self.assertIn('house_solar_area',config.validate_house(self.hass,values,True))

    def test_disabled_house_does_not_require_solar_or_heating_entity(self):
        self.hass.states.get=lambda entity:None
        self.assertEqual(config.validate_house(self.hass,house_const.HOUSE_DEFAULTS,True),{})
        self.assertIn('house_heat_input',config.validate_house(self.hass,{**house_const.HOUSE_DEFAULTS,'house_enabled':True},True))

    def test_three_page_configuration_keeps_floor_and_house_settings(self):
        flow=config.FloorConfigFlow()
        flow.hass=self.hass
        flow._sources={key:self.defaults[key] for key in (*const.SOURCE_KEYS,'name','active_mode')}
        parameters={key:self.defaults[key] for key in (*config.LIMITS,'record_samples','learning_enabled','apply_fitted_parameters','import_history_on_start')}
        with patch.dict(sys.modules,modules):
            result=asyncio.run(flow.async_step_parameters(parameters))
            self.assertEqual(result['step_id'],'house')
            created=asyncio.run(flow.async_step_house(house_const.HOUSE_DEFAULTS.copy()))
        self.assertEqual(created['type'],'create_entry')
        self.assertEqual(created['data']['heat_input'],self.defaults['heat_input'])
        self.assertIn('house_learning_days',created['data'])

    def test_entities_have_unique_ids_and_text_status_is_not_numeric(self):
        params=model.HouseParameters()
        manager=SimpleNamespace(data=lambda:{'house_fit_status':'waiting','house_ci':params.ci,'house_last_attempt':1700000000},
            parameter_source='manual_configuration',own_heat_source=False,calibrated_floor=None,
            settings=self.defaults,last_accepted=None,report={'status':'waiting'},history_import=None,
            engine=SimpleNamespace(timestamp=None),active_intervals={})
        coordinator=SimpleNamespace(house=manager)
        entities=sensor.house_entities(coordinator,SimpleNamespace(entry_id='test'))
        self.assertEqual(len(entities),23)
        self.assertEqual(len({entity._attr_unique_id for entity in entities}),23)
        status=next(entity for entity in entities if entity.key=='house_fit_status')
        self.assertEqual(status.native_value,'waiting')
        self.assertFalse(hasattr(status,'_attr_suggested_display_precision'))
        self.assertEqual(status.extra_state_attributes['parameter_source'],'manual_configuration')
        stamp=next(entity for entity in entities if entity.key=='house_last_attempt')
        self.assertEqual(stamp.native_value,datetime.fromtimestamp(1700000000,UTC))
        self.assertFalse(hasattr(stamp,'_attr_state_class'))

    def test_internal_gain_entity_and_confidence_interval_use_watts(self):
        manager=SimpleNamespace(data=lambda:{'house_internal_gain':500},parameter_source='learned_fit',
            own_heat_source=False,calibrated_floor=None,settings=self.defaults,last_accepted=None,
            engine=SimpleNamespace(timestamp=None),active_intervals={'internal_gain':{'lower_95':.3,'upper_95':.7}})
        entity=sensor.HouseSensor(SimpleNamespace(house=manager),SimpleNamespace(entry_id='test'),
                                  'house_internal_gain','House internal heat gain','W')
        self.assertEqual(entity.native_value,500)
        self.assertEqual(entity._attr_device_class,'power')
        self.assertEqual(entity.extra_state_attributes['approximate_95_percent_interval'],
                         {'lower_95':300,'upper_95':700,'unit':'W'})


if __name__=='__main__':unittest.main()
