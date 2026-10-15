"""Background fitting, persistence, scheduling and safe parameter application."""

import asyncio
from copy import deepcopy
import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "floor_heat_estimator"
package = ModuleType("_floor_learning_tests")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package


class FakeError(Exception):
    pass


class FakeStore:
    def __init__(self, *args, **kwargs):
        self.saved = None
        self.fail_with_parameters = False

    async def async_load(self):
        return deepcopy(self.saved)

    async def async_save(self, data):
        if self.fail_with_parameters and data["active_parameters"] is not None:
            raise OSError("storage unavailable")
        self.saved = deepcopy(data)


modules = {name: ModuleType(name) for name in
           ("homeassistant", "homeassistant.exceptions", "homeassistant.helpers", "homeassistant.helpers.storage")}
modules["homeassistant.exceptions"].HomeAssistantError = FakeError
modules["homeassistant.helpers.storage"].Store = FakeStore
with patch.dict(sys.modules, modules):
    learning = importlib.import_module("_floor_learning_tests.learning")
const = importlib.import_module("_floor_learning_tests.const")
model = importlib.import_module("_floor_learning_tests.model")


class LearningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings = {**const.DEFAULTS, **{key: "sensor." + key for key in const.SOURCE_KEYS}}
        self.parameters = dict(linear_capacity=.4, linear_conductance=.5, nonlinear_capacity=.45,
                               nonlinear_output_at_5k=2.5, exponent=1.4)
        self.result = {"status": "accepted", "accepted": True, "parameters": self.parameters.copy()}
        hass = SimpleNamespace(data={}, config=SimpleNamespace(path=lambda *args: "/config/floor"),
                               async_add_executor_job=AsyncMock(side_effect=lambda *args: deepcopy(self.result)))
        entry = SimpleNamespace(entry_id="test", async_create_background_task=lambda hass, coro, name: asyncio.create_task(coro))
        self.coordinator = SimpleNamespace(hass=hass, entry=entry, settings=settings,
            configured_settings=settings.copy(), engine=model.Estimator(settings),
            async_request_refresh=AsyncMock(), async_update_listeners=Mock())
        self.manager = learning.LearningManager(self.coordinator)

    async def run_fit(self, **kwargs):
        self.manager.start_fit(**kwargs)
        await self.manager.task

    async def test_success_updates_live_parameters_and_persists_before_activation(self):
        self.coordinator.engine.temperatures = {"linear": 27, "nonlinear": 28}
        await self.run_fit()
        self.assertEqual(self.manager.parameter_source, "learned_fit")
        self.assertEqual(self.coordinator.engine.parameters["linear"].capacity, .4)
        self.assertEqual(self.manager.store.saved["active_parameters"], self.parameters)
        self.assertEqual(self.manager.status, "applied")
        self.assertEqual(self.coordinator.engine.temperatures, {"linear": 27, "nonlinear": 28})

    async def test_preview_preserves_live_parameters_and_persistent_active_values(self):
        original = self.coordinator.settings.copy()
        await self.run_fit(apply=False)
        self.assertEqual(self.coordinator.settings, original)
        self.assertIsNone(self.manager.active_parameters)
        self.assertEqual(self.manager.status, "accepted")
        self.assertFalse(self.manager.report["applied"])

    async def test_manual_apply_override_and_legacy_flag_reach_worker(self):
        self.coordinator.settings["apply_fitted_parameters"] = False
        await self.run_fit(include_legacy=True, apply=True)
        arguments = self.coordinator.hass.async_add_executor_job.call_args.args
        self.assertTrue(arguments[-1])
        self.assertEqual(self.manager.parameter_source, "learned_fit")

    async def test_rejection_or_missing_data_never_replaces_current_parameters(self):
        self.result = {"status": "insufficient_data", "accepted": False, "parameters": {}}
        await self.run_fit()
        self.assertIsNone(self.manager.active_parameters)
        self.assertEqual(self.manager.status, "insufficient_data")

    async def test_failed_persistence_never_changes_live_coefficients(self):
        original = self.coordinator.settings.copy()
        self.manager.store.fail_with_parameters = True
        with self.assertLogs(learning._LOGGER, level="ERROR"):
            await self.run_fit()
        self.assertEqual(self.manager.status, "error")
        self.assertEqual(self.coordinator.settings, original)
        self.assertIsNone(self.manager.active_parameters)

    async def test_silently_logged_storage_failure_is_detected_by_readback(self):
        store = self.manager.store
        original_save = store.async_save

        async def silently_fail_new_parameters(data):
            if data["active_parameters"] is None:
                await original_save(data)

        store.async_save = silently_fail_new_parameters
        with self.assertLogs(learning._LOGGER, level="ERROR"):
            await self.run_fit()
        self.assertEqual(self.manager.status, "error")
        self.assertIsNone(self.manager.active_parameters)
        self.assertEqual(self.coordinator.settings["linear_capacity"], const.DEFAULTS["linear_capacity"])

    async def test_restart_restores_parameters_attempt_and_schedule(self):
        await self.run_fit()
        restored = learning.LearningManager(self.coordinator)
        # Restore compares the original manual configuration, not the learned values.
        restored.signature = self.manager.signature
        restored.store.saved = self.manager.store.saved
        await restored.async_load()
        self.assertEqual(restored.active_parameters, self.parameters)
        self.assertEqual(restored.last_attempt, self.manager.last_attempt)
        self.assertEqual(restored.next_refit - restored.last_attempt, 7 * 86400)

    async def test_changed_source_or_manual_coefficient_invalidates_saved_fit(self):
        await self.run_fit()
        for key, value in (("room_temperature", "sensor.other_room"), ("linear_conductance", .8)):
            settings = {**self.coordinator.configured_settings, key: value}
            other = SimpleNamespace(**{**self.coordinator.__dict__, "settings": settings})
            restored = learning.LearningManager(other)
            restored.store.saved = self.manager.store.saved
            await restored.async_load()
            self.assertIsNone(restored.active_parameters)

    async def test_refit_interval_and_enable_switch(self):
        self.manager.last_attempt = 1000
        self.manager.start_fit = Mock()
        due = 1000 + 7 * 86400
        self.manager.maybe_schedule(due - 1)
        self.manager.start_fit.assert_not_called()
        self.manager.maybe_schedule(due)
        self.manager.start_fit.assert_called_once()
        self.coordinator.settings["refit_interval_days"] = 14
        self.assertEqual(self.manager.next_refit, 1000 + 14 * 86400)
        self.coordinator.settings["learning_enabled"] = False
        self.assertIsNone(self.manager.next_refit)
        self.coordinator.settings.update(learning_enabled=True, record_samples=False)
        self.assertIsNone(self.manager.next_refit)

    async def test_duplicate_fit_is_refused_and_reset_waits_for_fit(self):
        self.manager.start_fit()
        with self.assertRaises(FakeError):
            self.manager.start_fit()
        with self.assertRaises(FakeError):
            await self.manager.async_reset()
        await self.manager.task
        await self.manager.async_reset()
        self.assertEqual(self.manager.parameter_source, "manual_configuration")
        self.assertIsNone(self.manager.store.saved["active_parameters"])
        self.assertEqual(self.coordinator.settings["linear_capacity"], const.DEFAULTS["linear_capacity"])

    async def test_shutdown_cancels_background_job_and_signals_worker(self):
        entered = asyncio.Event()

        async def slow_worker(*args):
            entered.set()
            await asyncio.Event().wait()

        self.coordinator.hass.async_add_executor_job.side_effect = slow_worker
        self.manager.start_fit()
        await entered.wait()
        await self.manager.async_shutdown()
        self.assertTrue(self.manager.cancel.is_set())
        self.assertTrue(self.manager.task.cancelled())
        self.assertFalse(self.manager.busy)

    async def test_corrupt_stored_coefficients_and_timestamps_are_ignored(self):
        self.manager.store.saved = {"signature": self.manager.signature,
            "active_parameters": {**self.parameters, "exponent": "1.4"},
            "last_attempt": 1e100, "last_accepted": -100}
        await self.manager.async_load()
        self.assertIsNone(self.manager.active_parameters)
        self.assertIsNone(self.manager.last_attempt)
        self.assertIsNone(self.manager.last_accepted)


if __name__ == "__main__":
    unittest.main()
