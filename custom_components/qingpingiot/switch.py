"""Support for Qingping IoT switch entities."""
from __future__ import annotations

import logging

from homeassistant.components import mqtt
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME, CONF_MODEL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MQTT_TOPIC_PREFIX,
    TLV_MODELS,
    Capability,
    DEVICE_MODELS,
)
from .coordinator import QingpingCoordinator
from .tlv import tlv_encode

_LOGGER = logging.getLogger(__name__)

# Capability -> switch config: (translation_key, tlv_key)
CAPABILITY_SWITCH_MAP: dict[Capability, tuple[str, int]] = {
    Capability.CO2_ASC: ("co2_asc", 0x40),
    Capability.LED_INDICATOR: ("led_indicator", 0x63),
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Qingping switch entities from a config entry."""
    mac = config_entry.data[CONF_MAC]
    model = config_entry.data[CONF_MODEL]
    coordinator: QingpingCoordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    device_info = {
        "identifiers": {(DOMAIN, mac)},
        "name": config_entry.data[CONF_NAME],
        "manufacturer": "Qingping",
        "model": model,
    }

    model_info = DEVICE_MODELS.get(model)
    if not model_info or model not in TLV_MODELS:
        async_add_entities([])
        return

    entities: list[SwitchEntity] = []

    for cap in model_info["capabilities"]:
        if cap not in CAPABILITY_SWITCH_MAP:
            continue
        translation_key, tlv_key = CAPABILITY_SWITCH_MAP[cap]
        entities.append(
            QingpingTLVSwitch(
                coordinator, config_entry, mac, device_info,
                translation_key, tlv_key,
            )
        )

    async_add_entities(entities)


class QingpingTLVSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for TLV devices, sends command via TLV protocol."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: QingpingCoordinator,
        config_entry: ConfigEntry,
        mac: str,
        device_info: dict,
        translation_key: str,
        tlv_key: int,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._mac = mac
        self._conf_key = translation_key
        self._tlv_key = tlv_key
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{mac}_{translation_key}"
        self._attr_device_info = device_info
        self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.get(self._conf_key, False)

    async def async_turn_on(self) -> None:
        self.coordinator.data[self._conf_key] = True
        self.async_write_ha_state()
        await self._send_tlv(1)

    async def async_turn_off(self) -> None:
        self.coordinator.data[self._conf_key] = False
        self.async_write_ha_state()
        await self._send_tlv(0)

    async def _send_tlv(self, value: int) -> None:
        packets = {self._tlv_key: bytes([value])}
        payload = tlv_encode(0x32, packets)
        topic = f"{MQTT_TOPIC_PREFIX}/{self._mac}/down"
        await mqtt.async_publish(self.hass, topic, payload)
        _LOGGER.debug("[%s] Sent TLV %s=%d (key=0x%02X)", self._mac, self._conf_key, value, self._tlv_key)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._conf_key not in self.coordinator.data:
            self.coordinator.data[self._conf_key] = False
        self.async_write_ha_state()
