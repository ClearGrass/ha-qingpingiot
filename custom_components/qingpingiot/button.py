"""Support for Qingping IoT button entities."""
from __future__ import annotations

import logging

from homeassistant.components import mqtt
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME, CONF_MODEL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    Capability,
    DEVICE_MODELS,
    DOMAIN,
    MQTT_TOPIC_PREFIX,
    TLV_MODELS,
)
from .coordinator import QingpingCoordinator
from .tlv import tlv_encode

_LOGGER = logging.getLogger(__name__)

# Capability -> button config: (translation_key, tlv_key, tlv_value)
CAPABILITY_BUTTON_MAP: dict[Capability, tuple[str, int, int]] = {
    Capability.CO2_CALIBRATION: ("co2_calibration", 0x41, 1),
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Qingping button entities from a config entry."""
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

    entities: list[ButtonEntity] = []

    for cap in model_info["capabilities"]:
        if cap not in CAPABILITY_BUTTON_MAP:
            continue
        translation_key, tlv_key, tlv_value = CAPABILITY_BUTTON_MAP[cap]
        entities.append(
            QingpingTLVButton(
                coordinator, config_entry, mac, device_info,
                translation_key, tlv_key, tlv_value,
            )
        )

    async_add_entities(entities)


class QingpingTLVButton(CoordinatorEntity, ButtonEntity):
    """Button entity for TLV devices, sends one-shot command via TLV protocol."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: QingpingCoordinator,
        config_entry: ConfigEntry,
        mac: str,
        device_info: dict,
        translation_key: str,
        tlv_key: int,
        tlv_value: int,
    ) -> None:
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._mac = mac
        self._tlv_key = tlv_key
        self._tlv_value = tlv_value
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{mac}_{translation_key}"
        self._attr_device_info = device_info
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        packets = {self._tlv_key: bytes([self._tlv_value])}
        payload = tlv_encode(0x32, packets)
        topic = f"{MQTT_TOPIC_PREFIX}/{self._mac}/down"
        await mqtt.async_publish(self.hass, topic, payload)
        _LOGGER.debug("[%s] Sent TLV button %s (key=0x%02X, val=%d)", self._mac, self._attr_translation_key, self._tlv_key, self._tlv_value)
