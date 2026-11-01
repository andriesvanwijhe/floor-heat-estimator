"""Background initialization activation, stale-result rejection and cancellation."""
import asyncio
import importlib
from pathlib import Path
import sys
from threading import Event
from types import ModuleType,SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
import test_house_runtime as framework

modules=framework.modules.copy()
for name in ('homeassistant.core','homeassistant.helpers.event','homeassistant.helpers.update_coordinator','homeassistant.util','homeassistant.util.dt'):
    modules[name]=ModuleType(name)
modules['homeassistant.core'].callback=lambda fn:fn
modules['homeassistant.helpers.event'].async_track_state_change_event=lambda *args:lambda:None
modules['homeassistant.helpers.update_coordinator'].DataUpdateCoordinator=type('Base',(),{})
modules['homeassistant.util'].dt=modules['homeassistant.util.dt']
with patch.dict(sys.modules,modules):
    coordinator=importlib.import_module('_house_runtime_tests.coordinator')
initialization=importlib.import_module('_house_runtime_tests.initialization')
history=importlib.import_module('_house_runtime_tests.history_import')
model=importlib.import_module('_house_runtime_tests.model')


class InitializationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.c=coordinator.FloorCoordinator.__new__(coordinator.FloorCoordinator)
        self.c.settings={**coordinator.DEFAULTS,**{k:'sensor.'+k for k in coordinator.SOURCE_KEYS}}
        self.c.hass=SimpleNamespace(config=SimpleNamespace(path=lambda *args:str(Path('unused').joinpath(*args))),
            async_add_executor_job=AsyncMock(side_effect=lambda fn,*args:fn(*args)))
        self.c.entry=SimpleNamespace(entry_id='test')
        self.c.engine=model.Estimator(self.c.settings)
        self.c.initialization_cancel=Event()
        self.c.pending_restore=None
        self.c.async_request_refresh=AsyncMock()
        self.c.initialization_complete=False
        self.seed=model.Estimator(self.c.settings)
        self.seed.temperatures={'linear':27,'nonlinear':27}
        self.seed.last_observation=100000
        self.seed.previous=model.Sample(100100,20,0,30,26,0,'off')

    async def test_history_result_activates_without_waiting_for_circulation(self):
        with patch.object(history,'async_import_history',AsyncMock()),patch.object(initialization,'load_floor',return_value=(self.seed,{'status':'replayed'})):
            await self.c._initialize_floor(100100)
        self.assertTrue(self.c.initialization_complete)
        self.assertIs(self.c.engine,self.seed)
        self.c.async_request_refresh.assert_awaited_once()

    async def test_later_live_measurement_is_never_overwritten(self):
        current=self.c.engine
        current.last_observation=100200
        with patch.object(history,'async_import_history',AsyncMock()),patch.object(initialization,'load_floor',return_value=(self.seed,{'status':'replayed'})):
            await self.c._initialize_floor(100200)
        self.assertIs(self.c.engine,current)
        self.c.async_request_refresh.assert_not_awaited()

    async def test_valid_restored_trajectory_beats_history_with_same_last_observation(self):
        current=self.c.engine
        current.last_observation=self.seed.last_observation
        current.previous=self.seed.previous
        current.temperatures={'linear':25.,'nonlinear':26.}
        self.c.initialization_complete=True
        with patch.object(history,'async_import_history',AsyncMock()),patch.object(initialization,'load_floor',return_value=(self.seed,{'status':'replayed'})):
            await self.c._initialize_floor(100100)
        self.assertIs(self.c.engine,current)
        self.assertEqual(self.c.engine.temperatures['nonlinear'],26.)
        self.assertEqual(self.c.initialization_report['status'],'retained_continuous_state')

    async def test_shutdown_cancels_recovery_task(self):
        self.c.initialization_task=asyncio.create_task(asyncio.sleep(100))
        await self.c.async_shutdown()
        self.assertTrue(self.c.initialization_cancel.is_set())
        self.assertTrue(self.c.initialization_task.cancelled())

    async def test_snapshot_restore_requires_matching_sources_gates_and_calibration(self):
        self.c.restored=False
        saved=self.c.stored_state()
        self.c.restore(saved)
        self.assertEqual(self.c.pending_restore,saved)
        self.c.pending_restore=None
        self.c.settings['exponent']=1.2
        self.c.restore(saved)
        self.assertIsNone(self.c.pending_restore)


if __name__=='__main__':unittest.main()
