"""House persistence, scheduling, process shutdown and safe activation."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
import importlib
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]/"custom_components/floor_heat_estimator"
package = ModuleType("_house_runtime_tests")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package


class FakeError(Exception): pass


class FakeStore:
    def __init__(self,*args,**kwargs):
        self.saved = None
        self.fail_parameters = False
    async def async_load(self): return deepcopy(self.saved)
    async def async_save(self,data):
        if self.fail_parameters and data.get('active_parameters') is not None:
            raise OSError('Storage unavailable')
        self.saved = deepcopy(data)


modules = {name:ModuleType(name) for name in ('homeassistant','homeassistant.exceptions',
            'homeassistant.helpers','homeassistant.helpers.storage','homeassistant.helpers.entity_registry')}
modules['homeassistant.exceptions'].HomeAssistantError = FakeError
modules['homeassistant.helpers.storage'].Store = FakeStore
registry = SimpleNamespace(async_get_entity_id=lambda *args:'sensor.floor_heat_estimator_exponent_heat_output')
modules['homeassistant.helpers.entity_registry'].async_get = lambda hass: registry
modules['homeassistant.helpers'].entity_registry = modules['homeassistant.helpers.entity_registry']
with patch.dict(sys.modules,modules):
    runtime = importlib.import_module('_house_runtime_tests.house_runtime')
const = importlib.import_module('_house_runtime_tests.const')


class HouseRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = {**const.DEFAULTS,'house_enabled':True,
                    **{key:'sensor.'+key for key in const.SOURCE_KEYS}}
        hass = SimpleNamespace(data={},states=SimpleNamespace(get=lambda entity:None),
            config=SimpleNamespace(path=lambda *args:str(Path(self.temp.name).joinpath(*args)),components=set()),
            async_add_executor_job=AsyncMock(side_effect=lambda fn,*args:fn(*args)))
        entry = SimpleNamespace(entry_id='test',async_create_background_task=lambda hass,coro,name:asyncio.create_task(coro))
        self.coordinator = SimpleNamespace(hass=hass,entry=entry,settings=settings,async_update_listeners=Mock())
        self.manager = runtime.HouseManager(self.coordinator)
        values = runtime.HouseParameters(ci=2,ce=20,rie=.5,rea=3,solar_area=8).to_dict()
        self.accepted = {'status':'accepted','accepted':True,'parameters':values,
            'approximate_confidence_intervals':{'ci':{'lower_95':1.5,'upper_95':2.5}},
            'final_state':{'timestamp':int(datetime.now(UTC).timestamp()//300)*300,
                           'state':[21,18],'covariance':[.01,0,.05]}}
        self.request = {'points':[],'input_provenance':'test','minute_sample_count':100,'skipped_rows':0}

    async def fit(self,result=None,apply=None):
        with patch.object(runtime,'prepare_fit_request',return_value=self.request), \
             patch.object(runtime,'run_worker',AsyncMock(return_value=deepcopy(result or self.accepted))):
            self.manager.start_fit(apply=apply)
            await self.manager.task

    async def test_accepted_coefficients_are_persisted_then_activated(self):
        await self.fit()
        self.assertEqual(self.manager.parameter_source,'learned_fit')
        self.assertEqual(self.manager.engine.parameters.ci,2)
        self.assertEqual(self.manager.store.saved['active_parameters']['ci'],2)
        self.assertEqual(self.manager.active_intervals['ci']['lower_95'],1.5)
        self.assertEqual(self.manager.data()['house_heat_loss_coefficient'],1000/3.5)

    async def test_preview_and_rejection_preserve_active_coefficients(self):
        await self.fit()
        original = self.manager.engine.parameters
        preview = deepcopy(self.accepted)
        preview['parameters']['ci']=3
        await self.fit(preview,apply=False)
        self.assertEqual(self.manager.engine.parameters,original)
        self.assertEqual(self.manager.active_intervals['ci']['lower_95'],1.5)
        await self.fit({'status':'rejected','accepted':False,'parameters':{}})
        self.assertEqual(self.manager.engine.parameters,original)

    async def test_storage_failure_never_activates_candidate(self):
        original = self.manager.engine.parameters
        self.manager.store.fail_parameters = True
        with self.assertLogs(runtime._LOGGER,level='ERROR'):
            await self.fit()
        self.assertEqual(self.manager.engine.parameters,original)
        self.assertIsNone(self.manager.active_parameters)
        self.assertEqual(self.manager.status,'error')

    async def test_floor_calibration_change_discards_candidate_and_schedules_retry(self):
        self.manager.own_heat_source=True
        async def changed(*args):
            self.coordinator.settings['linear_capacity'] *= 2
            return deepcopy(self.accepted)
        with patch.object(runtime,'prepare_fit_request',return_value=self.request),patch.object(runtime,'run_worker',changed):
            self.manager.start_fit()
            await self.manager.task
        self.assertEqual(self.manager.status,'superseded')
        self.assertIsNone(self.manager.active_parameters)
        self.assertNotEqual(self.manager.last_attempt_floor,runtime.floor_signature(self.coordinator.settings))

    async def test_automatic_schedule_uses_separate_weekly_interval_and_import(self):
        self.assertEqual(self.manager.next_refit,self.manager.started+120)
        self.manager.history_import={'status':'completed'}
        self.manager.last_attempt=1000
        self.assertEqual(self.manager.next_refit,1000+7*86400)
        self.coordinator.settings['house_learning_enabled']=False
        self.assertIsNone(self.manager.next_refit)

    async def test_duplicate_fit_is_refused_and_shutdown_cancels_worker(self):
        entered=asyncio.Event()
        async def slow(*args):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(runtime,'prepare_fit_request',return_value=self.request),patch.object(runtime,'run_worker',slow):
            self.manager.start_fit()
            await entered.wait()
            with self.assertRaises(FakeError):self.manager.start_fit()
            with self.assertRaises(FakeError):await self.manager.async_reset()
            await self.manager.async_shutdown()
        self.assertTrue(self.manager.task.cancelled())
        self.assertFalse(self.manager.busy)

    async def test_timeout_preserves_existing_model(self):
        original=self.manager.engine.parameters
        with patch.object(runtime,'prepare_fit_request',return_value=self.request), \
             patch.object(runtime,'run_worker',AsyncMock(side_effect=TimeoutError)):
            self.manager.start_fit()
            await self.manager.task
        self.assertEqual(self.manager.status,'timeout')
        self.assertEqual(self.manager.engine.parameters,original)

    async def test_restart_restores_parameters_and_manual_reset_keeps_history(self):
        await self.fit()
        restored=runtime.HouseManager(self.coordinator)
        restored.store.saved=self.manager.store.saved
        restored.state_store.saved=self.manager.state_store.saved
        with patch.dict(sys.modules,modules):
            await restored.async_load()
        self.assertEqual(restored.engine.parameters.ci,2)
        self.assertEqual(restored.last_attempt,self.manager.last_attempt)
        self.assertTrue(restored.own_heat_source)
        restored.history_import={'status':'completed'}
        await restored.async_reset()
        self.assertEqual(restored.parameter_source,'manual_configuration')
        self.assertEqual(restored.engine.parameters.ci,5)
        self.assertEqual(restored.history_import['status'],'completed')

    async def test_corrupt_stored_parameters_and_future_state_are_ignored(self):
        self.manager.store.saved={'signature':self.manager.signature,'active_parameters':{'ci':'bad'}}
        self.manager.state_store.saved={'signature':self.manager.signature,'parameters':self.manager.engine.parameters.to_dict(),
             'floor':runtime.floor_signature(self.coordinator.settings),
             'snapshot':{'timestamp':self.manager.started+86400,'state':[21,19],'covariance':[1,0,1]}}
        with patch.dict(sys.modules,modules):await self.manager.async_load()
        self.assertIsNone(self.manager.active_parameters)
        self.assertIsNone(self.manager.engine.state)

    async def test_real_subprocess_protocol_and_timeout_cleanup(self):
        # HA requirement paths may exist only in sys.path, not environment.
        with patch.dict(runtime.os.environ,{'PYTHONPATH':''}):
            result=await runtime.run_worker({'points':[],'baseline':runtime.HouseParameters().to_dict()},20)
        self.assertEqual(result['status'],'insufficient_data')
        with self.assertRaises(TimeoutError):
            await runtime.run_worker({'points':[]},.00001)


if __name__=='__main__':unittest.main()
