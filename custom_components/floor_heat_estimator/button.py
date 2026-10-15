"""Manual fitting from the device page without YAML."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FitNowButton(coordinator, entry), ImportHistoryButton(coordinator, entry)])


class FitNowButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Fit now"
    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry):
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_fit_now"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_press(self):
        self.coordinator.learning.start_fit()


class ImportHistoryButton(FitNowButton):
    _attr_name = "Import history and fit"
    _attr_icon = "mdi:database-import"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_import_history_and_fit"

    async def async_press(self):
        self.coordinator.learning.start_fit(import_history=True)
