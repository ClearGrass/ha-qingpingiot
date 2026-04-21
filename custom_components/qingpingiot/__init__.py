"""The Qingping IoT integration."""
from __future__ import annotations


from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_MODEL, CONF_NAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import QingpingCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Qingping IoT from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    mac = entry.data[CONF_MAC]
    model = entry.data[CONF_MODEL]
    name = entry.data[CONF_NAME]

    coordinator = QingpingCoordinator(hass, entry, mac, model, name)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(coordinator.async_stop)
    await coordinator.async_start()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
