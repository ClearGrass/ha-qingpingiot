"""Coordinator for Qingping IoT integration."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DEFAULT_DURATION,
    MQTT_TOPIC_PREFIX,
    OFFLINE_TIMEOUT_REALTIME,
    TLV_MODELS,
)
from .tlv import int_to_bytes_little_endian, is_tlv_format, tlv_decode, tlv_encode

_LOGGER = logging.getLogger(__name__)

MQTT_PUBLISH_RETRY_LIMIT = 3
MQTT_PUBLISH_RETRY_DELAY = 5


async def ensure_mqtt_connected(hass: HomeAssistant) -> bool:
    """Ensure MQTT is connected before publishing."""
    for _ in range(5):
        if mqtt.is_connected(hass):
            return True
        await asyncio.sleep(1)
    return False


class QingpingCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for a single Qingping MQTT device."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        mac: str,
        model: str,
        name: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"qingping_{mac}",
            update_interval=None,
        )
        self.config_entry = entry
        self.mac = mac
        self.model = model
        self.name = name

        self._unsub_mqtt: Any = None
        self._pending_setting_publishes: dict[str, asyncio.Task] = {}

        self.data: dict[str, Any] = {
            "online": False,
            "last_timestamp": 0,
            "firmware_version": None,
            "mac": None,
            "battery_charging": None,
            "sensor_data": {},
        }

    @property
    def is_online(self) -> bool:
        return self.data.get("online", False)

    @property
    def is_tlv(self) -> bool:
        return self.model in TLV_MODELS

    async def async_start(self) -> None:
        """Start MQTT subscription and periodic tasks."""
        self._unsub_mqtt = await mqtt.async_subscribe(
            self.hass,
            f"{MQTT_TOPIC_PREFIX}/{self.mac}/up",
            self._handle_message,
            1,
            encoding=None,
        )

        if self.is_tlv:
            await self._send_initial_tlv_config()

        async def delayed_publish() -> None:
            await asyncio.sleep(2)
            if await ensure_mqtt_connected(self.hass):
                await self.publish_config()

        asyncio.create_task(delayed_publish())

    async def async_stop(self) -> None:
        """Stop MQTT subscription and periodic tasks."""
        if self._unsub_mqtt:
            self._unsub_mqtt()
            self._unsub_mqtt = None
        for task in self._pending_setting_publishes.values():
            task.cancel()
        self._pending_setting_publishes.clear()

    async def _async_update_data(self) -> dict[str, Any]:
        return self.data

    # -- MQTT message handling --

    @callback
    def _handle_message(self, message: mqtt.ReceiveMessage) -> None:
        """Route incoming MQTT messages."""
        try:
            if is_tlv_format(message.payload):
                self._handle_tlv_message(message.payload)
            else:
                self._handle_json_message(message.payload)
        except json.JSONDecodeError:
            _LOGGER.error("[%s] Invalid JSON in MQTT message", self.mac)
        except Exception as e:
            _LOGGER.error("[%s] Error processing MQTT message: %s", self.mac, e)

    @callback
    def _handle_tlv_message(self, payload: bytes) -> None:
        """Process TLV binary payload."""
        try:
            cmd = payload[2] if len(payload) > 2 else 0
            decoded = tlv_decode(payload)
            if not decoded:
                return

            current_timestamp = int(time.time())

            new_data = dict(self.data)
            new_data["last_timestamp"] = current_timestamp

            if "version" in decoded:
                new_data["firmware_version"] = decoded["version"]

            new_data["mac"] = self.mac

            if "batteryCharging" in decoded:
                new_data["battery_charging"] = bool(decoded["batteryCharging"])

            sensor_data_list = decoded.get("sensorData", [])
            if not sensor_data_list:
                self.async_set_updated_data(new_data)
                self._update_online_status(new_data)
                return

            if cmd == 0x42 and isinstance(sensor_data_list, list) and len(sensor_data_list) > 1:
                data = sensor_data_list[-1]
            else:
                data = sensor_data_list[0] if isinstance(sensor_data_list, list) else sensor_data_list

            new_data["sensor_data"] = data
            new_data["decoded"] = decoded
            self.async_set_updated_data(new_data)
            self._update_online_status(new_data)

        except Exception as e:
            _LOGGER.error("[%s] Error processing TLV message: %s", self.mac, e)

    @callback
    def _handle_json_message(self, payload: bytes) -> None:
        """Process JSON payload (legacy devices)."""
        payload_dict = json.loads(payload)
        if not isinstance(payload_dict, dict):
            return

        message_type = payload_dict.get("type")
        current_timestamp = int(time.time())

        new_data = dict(self.data)
        new_data["last_timestamp"] = current_timestamp

        version = payload_dict.get("version")
        if version is not None:
            new_data["firmware_version"] = version

        mac_addr = payload_dict.get("mac")
        if mac_addr is not None:
            new_data["mac"] = mac_addr

        if message_type in (28, "28"):
            self.async_set_updated_data(new_data)
            self._update_online_status(new_data)
            return

        sensor_data_list = payload_dict.get("sensorData")
        if not isinstance(sensor_data_list, list) or not sensor_data_list:
            self.async_set_updated_data(new_data)
            self._update_online_status(new_data)
            return

        if message_type in (17, 13, "17", "13"):
            self.async_set_updated_data(new_data)
            self._update_online_status(new_data)
            return

        new_data["sensor_data_list"] = sensor_data_list
        self.async_set_updated_data(new_data)
        self._update_online_status(new_data)

    # -- Online status --

    @callback
    def _update_online_status(self, data: dict[str, Any] | None = None) -> None:
        """Determine online/offline based on timeout."""
        data = data or self.data
        last_ts = data.get("last_timestamp", 0)
        timeout = OFFLINE_TIMEOUT_REALTIME

        time_since = int(time.time()) - last_ts
        new_online = time_since <= timeout

        if data.get("online") != new_online:
            new_data = dict(data)
            new_data["online"] = new_online
            old_status = "online" if data.get("online") else "offline"
            new_status = "online" if new_online else "offline"
            _LOGGER.info("[%s] Status: %s -> %s", self.mac, old_status, new_status)

            self.async_set_updated_data(new_data)

            if new_online and not data.get("online"):
                asyncio.create_task(self._publish_config_on_recovery())

    @callback
    def check_online_status(self) -> None:
        """Periodic online status check (called by timer)."""
        self._update_online_status()

    async def _publish_config_on_recovery(self) -> None:
        """Re-publish config when device comes back online."""
        await asyncio.sleep(2)
        if await ensure_mqtt_connected(self.hass):
            await self.publish_config()

    # -- Config publishing --

    async def publish_config(self) -> None:
        """Publish configuration command to device via MQTT."""
        if not self.hass:
            return

        topic = f"{MQTT_TOPIC_PREFIX}/{self.mac}/down"

        if self.is_tlv:
            packets = {0x42: int_to_bytes_little_endian(600, 2)}
            payload = tlv_encode(0x32, packets)
        else:
            payload = json.dumps({
                "type": "12",
                "up_itvl": "5",
                "duration": DEFAULT_DURATION,
            })

        for attempt in range(MQTT_PUBLISH_RETRY_LIMIT):
            if not await ensure_mqtt_connected(self.hass):
                return
            try:
                await mqtt.async_publish(self.hass, topic, payload)
                return
            except HomeAssistantError as err:
                _LOGGER.warning(
                    "[%s] Config publish attempt %d failed: %s",
                    self.mac, attempt + 1, err,
                )
                if attempt < MQTT_PUBLISH_RETRY_LIMIT - 1:
                    await asyncio.sleep(MQTT_PUBLISH_RETRY_DELAY)

    # -- Initial TLV config --

    async def _send_initial_tlv_config(self) -> None:
        """Send initial config to a new TLV device."""
        native_temp_unit = self.hass.config.units.temperature_unit
        temp_unit = "fahrenheit" if native_temp_unit == UnitOfTemperature.FAHRENHEIT else "celsius"

        packets = {
            0x42: int_to_bytes_little_endian(600, 2),
            0x19: bytes([1 if temp_unit == "fahrenheit" else 0]),
        }

        # if self.model == "CGP22C":
        #     packets[0x3C] = int_to_bytes_little_endian(10, 2)

        payload = tlv_encode(0x32, packets)
        await mqtt.async_publish(self.hass, f"qingping/{self.mac}/down", payload)
        _LOGGER.info("[%s] Initial TLV config sent", self.mac)
