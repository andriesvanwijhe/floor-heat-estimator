"""Experimental floor heat estimator."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .const import DOMAIN
from .coordinator import FloorCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register actions independently of entry setup, per HA service guidance."""
    def selected_coordinator(call):
        coordinators = hass.data.get(DOMAIN, {})
        entry_id = call.data.get("entry_id")
        if entry_id is None and len(coordinators) == 1:
            return next(iter(coordinators.values()))
        if entry_id not in coordinators:
            raise HomeAssistantError("Select a loaded Floor Heat Estimator configuration")
        return coordinators[entry_id]

    async def fit_now(call):
        coordinator = selected_coordinator(call)
        coordinator.learning.start_fit(call.data.get("include_legacy", False), call.data.get("apply"))
        return {"status": "queued", "entry_id": coordinator.entry.entry_id}

    async def import_history_and_fit(call):
        coordinator = selected_coordinator(call)
        coordinator.learning.start_fit(import_history=True, apply=call.data.get("apply"))
        return {"status": "queued", "entry_id": coordinator.entry.entry_id}

    async def reset_parameters(call):
        await selected_coordinator(call).learning.async_reset()

    entry_schema = {vol.Optional("entry_id"): str}
    hass.services.async_register(DOMAIN, "fit_now", fit_now,
        schema=vol.Schema({**entry_schema, vol.Optional("include_legacy", default=False): bool,
                           vol.Optional("apply"): bool}), supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "import_history_and_fit", import_history_and_fit,
        schema=vol.Schema({**entry_schema, vol.Optional("apply"): bool}),
        supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(DOMAIN, "reset_parameters", reset_parameters, schema=vol.Schema(entry_schema))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = FloorCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await coordinator.learning.async_load()
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.subscribe()
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.data[DOMAIN][entry.entry_id].learning.async_shutdown()
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        return True
    return False

