"""Support for Qingping IoT sensors."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from datetime import timedelta

from homeassistant.components import mqtt
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_MODEL, CONF_NAME, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    Capability,
    CONCENTRATION,
    CONF_REPORT_MODE,
    CONF_TVOC_UNIT,
    DB,
    DEFAULT_DURATION,
    DEVICE_MODELS,
    DOMAIN,
    JSON_MODELS,
    MQTT_TOPIC_PREFIX,
    OFFLINE_TIMEOUT_HISTORIC,
    OFFLINE_TIMEOUT_REALTIME,
    PERCENTAGE,
    PPB,
    PPM,
    REPORT_MODE_HISTORIC,
    REPORT_MODE_REALTIME,
    SENSOR_BATTERY,
    SENSOR_CO2,
    SENSOR_ETVOC,
    SENSOR_HUMIDITY,
    SENSOR_LIGHT,
    SENSOR_NOISE,
    SENSOR_PM10,
    SENSOR_PM25,
    SENSOR_PRESSURE,
    SENSOR_SIGNAL_STRENGTH,
    SENSOR_TEMPERATURE,
    SENSOR_TVOC,
    TLV_MODELS,
)
from .tlv import int_to_bytes_little_endian, is_tlv_format, tlv_decode, tlv_encode

_LOGGER = logging.getLogger(__name__)

MQTT_PUBLISH_RETRY_LIMIT = 3
MQTT_PUBLISH_RETRY_DELAY = 5
SETTING_CHANGE_DELAY = 5

_pending_setting_publishes: dict[str, asyncio.Task] = {}


# Capability → Sensor description mapping
CAPABILITY_SENSOR_MAP: dict[Capability, dict] = {
    Capability.TEMPERATURE: {
        "sensor_type": SENSOR_TEMPERATURE,
        "name": "Temperature",
        "unit_fn": lambda hass: UnitOfTemperature.FAHRENHEIT
        if hass.config.units.temperature_unit == UnitOfTemperature.FAHRENHEIT
        else UnitOfTemperature.CELSIUS,
        "device_class": SensorDeviceClass.TEMPERATURE,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    Capability.HUMIDITY: {
        "sensor_type": SENSOR_HUMIDITY,
        "name": "Humidity",
        "unit": PERCENTAGE,
        "device_class": SensorDeviceClass.HUMIDITY,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    Capability.CO2: {
        "sensor_type": SENSOR_CO2,
        "name": "CO2",
        "unit": PPM,
        "device_class": SensorDeviceClass.CO2,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    Capability.PM25: {
        "sensor_type": SENSOR_PM25,
        "name": "PM2.5",
        "unit": CONCENTRATION,
        "device_class": SensorDeviceClass.PM25,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    Capability.PM10: {
        "sensor_type": SENSOR_PM10,
        "name": "PM10",
        "unit": CONCENTRATION,
        "device_class": SensorDeviceClass.PM10,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    Capability.TVOC: {
        "sensor_type": SENSOR_TVOC,
        "name": "TVOC",
        "unit": PPB,
        "device_class": SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    Capability.ETVOC: {
        "sensor_type": SENSOR_ETVOC,
        "name": "eTVOC",
        "unit": None,
        "device_class": SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    Capability.NOISE: {
        "sensor_type": SENSOR_NOISE,
        "name": "Noise",
        "unit": DB,
        "device_class": SensorDeviceClass.SOUND_PRESSURE,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    Capability.PRESSURE: {
        "sensor_type": SENSOR_PRESSURE,
        "name": "Pressure",
        "unit": "kPa",
        "device_class": SensorDeviceClass.PRESSURE,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    Capability.LIGHT: {
        "sensor_type": SENSOR_LIGHT,
        "name": "Light",
        "unit": "lx",
        "device_class": SensorDeviceClass.ILLUMINANCE,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    Capability.BATTERY: {
        "sensor_type": SENSOR_BATTERY,
        "name": "Battery",
        "unit": PERCENTAGE,
        "device_class": SensorDeviceClass.BATTERY,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
    },
    Capability.SIGNAL_STRENGTH: {
        "sensor_type": SENSOR_SIGNAL_STRENGTH,
        "name": "Signal Strength",
        "unit": "dBm",
        "device_class": SensorDeviceClass.SIGNAL_STRENGTH,
        "state_class": SensorStateClass.MEASUREMENT,
        "entity_category": EntityCategory.DIAGNOSTIC,
        "tlv_only": True,
    },
}


async def ensure_mqtt_connected(hass: HomeAssistant) -> bool:
    """Ensure MQTT is connected before publishing."""
    for _ in range(5):
        if mqtt.is_connected(hass):
            return True
        await asyncio.sleep(1)
    return False


# Setup
async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Qingping sensors from a config entry."""
    mac = config_entry.data[CONF_MAC]
    name = config_entry.data[CONF_NAME]
    model = config_entry.data[CONF_MODEL]
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    model_info = DEVICE_MODELS[model]
    capabilities = model_info["capabilities"]

    device_info = {
        "identifiers": {(DOMAIN, mac)},
        "name": name,
        "manufacturer": "Qingping",
        "model": model,
    }

    sensors: list[SensorEntity] = []

    # Diagnostic sensors (always present)
    status_sensor = QingpingStatusSensor(coordinator, config_entry, mac, name, device_info)
    firmware_sensor = QingpingFirmwareSensor(coordinator, mac, name, device_info)
    mac_sensor = QingpingMACSensor(coordinator, mac, name, device_info)
    battery_state_sensor = QingpingBatteryStateSensor(coordinator, mac, name, device_info)

    sensors.append(status_sensor)
    sensors.append(firmware_sensor)
    sensors.append(mac_sensor)

    if Capability.BATTERY in capabilities:
        sensors.append(battery_state_sensor)

    # Sensor entities based on capabilities
    is_tlv = model in TLV_MODELS
    for cap in capabilities:
        if cap not in CAPABILITY_SENSOR_MAP:
            continue
        desc = CAPABILITY_SENSOR_MAP[cap]

        # Signal strength only for TLV devices
        if desc.get("tlv_only") and not is_tlv:
            continue

        unit = desc.get("unit") or desc.get("unit_fn", lambda h: None)(hass)
        entity_category = desc.get("entity_category")

        sensor = QingpingSensor(
            coordinator=coordinator,
            config_entry=config_entry,
            mac=mac,
            name=name,
            sensor_type=desc["sensor_type"],
            friendly_name=desc["name"],
            unit=unit,
            device_class=desc["device_class"],
            state_class=desc["state_class"],
            device_info=device_info,
            entity_category=entity_category,
        )
        sensors.append(sensor)

    async_add_entities(sensors)

    # Store references
    hass.data[DOMAIN][config_entry.entry_id]["sensors"] = sensors

    # Send initial config for TLV devices
    if is_tlv:
        await _send_initial_tlv_config(hass, config_entry, mac, model)

    # Subscribe to MQTT
    async def message_received(message: mqtt.ReceiveMessage) -> None:
        """Handle new MQTT messages."""
        try:
            if is_tlv_format(message.payload):
                _handle_tlv_message(message)
            else:
                _handle_json_message(message)
        except json.JSONDecodeError:
            _LOGGER.error("Invalid JSON in MQTT message")
        except Exception as e:
            _LOGGER.error("Error processing MQTT message: %s", e)

    def _handle_tlv_message(message: mqtt.ReceiveMessage) -> None:
        """Handle TLV binary format messages."""
        try:
            cmd = message.payload[2] if len(message.payload) > 2 else 0
            decoded = tlv_decode(message.payload)
            if not decoded:
                return

            # Update diagnostic sensors
            current_timestamp = int(time.time())
            if status_sensor.hass:
                status_sensor.update_timestamp(current_timestamp)
            if "version" in decoded and firmware_sensor.hass:
                firmware_sensor.update_version(decoded["version"])
            if mac_sensor.hass:
                mac_sensor.update_mac(mac)

            # Battery charging state
            if "batteryCharging" in decoded and battery_state_sensor.hass:
                new_charging = decoded["batteryCharging"]
                old_charging = battery_state_sensor.native_value == "Charging"
                battery_state_sensor.update_battery_state(1 if new_charging else 0)

                if new_charging != old_charging:
                    asyncio.create_task(
                        _auto_switch_report_mode(
                            hass, config_entry, mac, new_charging, model
                        )
                    )

            # Sensor data
            sensor_data = decoded.get("sensorData", [])
            if not sensor_data:
                return

            # Use last entry for historical data, first for others
            if cmd == 0x42 and isinstance(sensor_data, list) and len(sensor_data) > 1:
                data = sensor_data[-1]
            else:
                data = sensor_data[0] if isinstance(sensor_data, list) else sensor_data

            # Update all sensor entities
            for sensor in sensors:
                if not isinstance(sensor, QingpingSensor) or not sensor.hass:
                    continue
                sensor.update_from_tlv_data(data, decoded)

        except Exception as e:
            _LOGGER.error("Error processing TLV message: %s", e)

    def _handle_json_message(message: mqtt.ReceiveMessage) -> None:
        """Handle JSON format messages (legacy devices)."""
        payload = json.loads(message.payload)
        if not isinstance(payload, dict):
            return

        message_type = payload.get("type")
        current_timestamp = int(time.time())

        # Update diagnostic sensors
        if status_sensor.hass:
            status_sensor.update_timestamp(current_timestamp)

        version = payload.get("version")
        if version is not None and firmware_sensor.hass:
            firmware_sensor.update_version(version)

        mac_addr = payload.get("mac")
        if mac_addr is not None and mac_sensor.hass:
            mac_sensor.update_mac(mac_addr)

        # Type 28 = settings update
        if message_type in (28, "28"):
            return

        # Sensor data
        sensor_data_list = payload.get("sensorData")
        if not isinstance(sensor_data_list, list) or not sensor_data_list:
            return

        if message_type in (17, 13, "17", "13"):
            return

        for data in sensor_data_list:
            for sensor in sensors:
                if not isinstance(sensor, QingpingSensor) or not sensor.hass:
                    continue
                sensor.update_from_json_data(data)

    await mqtt.async_subscribe(
        hass, f"{MQTT_TOPIC_PREFIX}/{mac}/up", message_received, 1, encoding=None
    )

    # Periodic config publish
    async def publish_config_wrapper(*_):
        if await ensure_mqtt_connected(hass):
            for sensor in sensors:
                if isinstance(sensor, QingpingSensor):
                    await sensor.publish_config()
                    break

    hass.data[DOMAIN][config_entry.entry_id]["remove_timer"] = async_track_time_interval(
        hass, publish_config_wrapper, timedelta(seconds=int(DEFAULT_DURATION))
    )

    # Initial publish after entities are ready
    async def delayed_publish():
        await asyncio.sleep(2)
        if await ensure_mqtt_connected(hass):
            await publish_config_wrapper()

    asyncio.create_task(delayed_publish())


# Auto report mode switch
async def _auto_switch_report_mode(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    mac: str,
    is_charging: bool,
    model: str,
) -> None:
    """Auto-switch report mode based on battery charging state."""
    if model not in ("CGP22C", "CGP23W", "CGP22W"):
        return

    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    if is_charging:
        packets = {0x42: int_to_bytes_little_endian(21600, 2)}
        new_mode = REPORT_MODE_REALTIME
    else:
        packets = {0x42: int_to_bytes_little_endian(0, 2)}
        new_mode = REPORT_MODE_HISTORIC

    payload = tlv_encode(0x32, packets)
    await mqtt.async_publish(hass, f"qingping/{mac}/down", payload)

    coordinator.data[CONF_REPORT_MODE] = new_mode
    new_data = dict(config_entry.data)
    new_data[CONF_REPORT_MODE] = new_mode
    hass.config_entries.async_update_entry(config_entry, data=new_data)
    await coordinator.async_request_refresh()


# Initial TLV config
async def _send_initial_tlv_config(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    mac: str,
    model: str,
) -> None:
    """Send initial config to a new TLV device."""
    if CONF_REPORT_MODE in config_entry.data:
        return

    native_temp_unit = hass.config.units.temperature_unit
    temp_unit = "fahrenheit" if native_temp_unit == UnitOfTemperature.FAHRENHEIT else "celsius"

    new_data = dict(config_entry.data)
    new_data[CONF_REPORT_MODE] = REPORT_MODE_REALTIME
    hass.config_entries.async_update_entry(config_entry, data=new_data)

    packets = {
        0x42: int_to_bytes_little_endian(21600, 2),
        0x19: bytes([1 if temp_unit == "fahrenheit" else 0]),
    }

    if model == "CGP22C":
        packets[0x3C] = int_to_bytes_little_endian(10, 2)

    payload = tlv_encode(0x32, packets)
    await mqtt.async_publish(hass, f"qingping/{mac}/down", payload)
    _LOGGER.info("[%s] Initial TLV config sent", mac)


# Diagnostic Sensors
class QingpingStatusSensor(CoordinatorEntity, SensorEntity):
    """Device online/offline status."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry, mac, name, device_info):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._mac = mac
        self._attr_name = f"{name} Status"
        self._attr_unique_id = f"{mac}_status"
        self._attr_device_info = device_info
        self._attr_native_value = "offline"
        self._last_timestamp = 0
        self._last_status = "online"

    @callback
    def update_timestamp(self, timestamp: int) -> None:
        """Update last received timestamp and check status."""
        self._last_timestamp = int(timestamp)
        self._update_status()

    @callback
    def _update_status(self) -> None:
        """Determine online/offline based on timeout."""
        if not self.hass:
            return

        model = self._config_entry.data.get(CONF_MODEL, "")
        if model in TLV_MODELS:
            report_mode = self.coordinator.data.get(CONF_REPORT_MODE, REPORT_MODE_HISTORIC)
            timeout = OFFLINE_TIMEOUT_REALTIME if report_mode == REPORT_MODE_REALTIME else OFFLINE_TIMEOUT_HISTORIC
        else:
            timeout = OFFLINE_TIMEOUT_REALTIME

        time_since = int(time.time()) - self._last_timestamp
        new_status = "online" if time_since <= timeout else "offline"

        if self._attr_native_value != new_status:
            old_status = self._attr_native_value
            self._attr_native_value = new_status
            self.async_write_ha_state()
            _LOGGER.info("[%s] Status: %s -> %s", self._mac, old_status, new_status)

            if self._last_status == "offline" and new_status == "online":
                asyncio.create_task(self._publish_config_on_recovery())

            self._last_status = new_status

    async def _publish_config_on_recovery(self) -> None:
        """Re-publish config when device comes back online."""
        if not self.hass:
            return
        await asyncio.sleep(2)
        sensors = self.hass.data[DOMAIN][self._config_entry.entry_id].get("sensors", [])
        for sensor in sensors:
            if isinstance(sensor, QingpingSensor):
                await sensor.publish_config()
                break

    async def async_added_to_hass(self) -> None:
        """Set up periodic status check."""
        await super().async_added_to_hass()
        self._update_status()

        async def check_status(*_):
            self._update_status()

        self.async_on_remove(
            async_track_time_interval(self.hass, check_status, timedelta(seconds=60))
        )


class QingpingFirmwareSensor(CoordinatorEntity, SensorEntity):
    """Device firmware version."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, mac, name, device_info):
        super().__init__(coordinator)
        self._mac = mac
        self._attr_name = f"{name} Firmware"
        self._attr_unique_id = f"{mac}_firmware"
        self._attr_device_info = device_info
        self._attr_native_value = None

    @callback
    def update_version(self, version: str) -> None:
        self._attr_native_value = version
        self.async_write_ha_state()


class QingpingMACSensor(CoordinatorEntity, SensorEntity):
    """Device MAC address."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, mac, name, device_info):
        super().__init__(coordinator)
        self._mac = mac
        self._attr_name = f"{name} MAC Address"
        self._attr_unique_id = f"{mac}_mac"
        self._attr_device_info = device_info
        self._attr_native_value = None

    @callback
    def update_mac(self, mac: str) -> None:
        self._attr_native_value = mac
        self.async_write_ha_state()


class QingpingBatteryStateSensor(CoordinatorEntity, SensorEntity):
    """Battery charging state (Charging / Discharging / Fully Charged)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, mac, name, device_info):
        super().__init__(coordinator)
        self._mac = mac
        self._attr_name = f"{name} Battery State"
        self._attr_unique_id = f"{mac}_battery_state"
        self._attr_device_info = device_info
        self._attr_native_value = "Discharging"

    @callback
    def update_battery_state(self, status: int) -> None:
        if status == 1:
            self._attr_native_value = "Charging"
        elif status == 2:
            self._attr_native_value = "Fully Charged"
        elif status == 0:
            self._attr_native_value = "Discharging"
        else:
            self._attr_native_value = "Unknown"
        self.async_write_ha_state()


# Main Sensor Entity
def _get_voc_device_class(unit: str | None) -> SensorDeviceClass:
    """Get appropriate device class for VOC sensor based on unit."""
    if unit == "index":
        return SensorDeviceClass.AQI
    if unit in ("ppb", "ppm"):
        return SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS
    if unit == "mg/m³":
        return SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS
    return SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS


class QingpingSensor(CoordinatorEntity, SensorEntity):
    """Generic Qingping sensor entity."""

    def __init__(
        self,
        coordinator,
        config_entry,
        mac,
        name,
        sensor_type,
        friendly_name,
        unit,
        device_class,
        state_class,
        device_info,
        entity_category=None,
    ):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._mac = mac
        self._sensor_type = sensor_type
        self._battery_charging = False
        self._is_unavailable = False

        self._attr_name = f"{name} {friendly_name}"
        self._attr_unique_id = f"{mac}_{sensor_type}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_device_info = device_info
        if entity_category:
            self._attr_entity_category = entity_category

    @callback
    def update_from_tlv_data(self, data: dict, top_level: dict) -> None:
        """Update sensor from TLV decoded data."""
        value = None

        if self._sensor_type == SENSOR_BATTERY:
            value = top_level.get("battery") or data.get("battery")
        elif self._sensor_type == SENSOR_SIGNAL_STRENGTH:
            value = top_level.get("signalStrength")
            if value is not None and value >= 128:
                value -= 256
            elif value is None:
                value = data.get("rssi")
        else:
            # Map sensor_type to TLV data key
            key = self._sensor_type
            value = data.get(key)

        if value is not None:
            self._set_value(value)

    @callback
    def update_from_json_data(self, data: dict) -> None:
        """Update sensor from JSON sensorData."""
        if self._sensor_type not in data:
            return

        raw = data[self._sensor_type]
        if isinstance(raw, dict):
            value = raw.get("value")
            if self._sensor_type in (SENSOR_PM10, SENSOR_PM25) and value == 99999:
                self._set_unavailable()
                return
        else:
            value = raw

        if value is not None:
            self._set_value(value)

    @callback
    def _set_value(self, value) -> None:
        """Convert and set sensor value."""
        try:
            if self._sensor_type == SENSOR_TEMPERATURE:
                temp_c = float(value)
                if self._attr_native_unit_of_measurement == UnitOfTemperature.FAHRENHEIT:
                    self._attr_native_value = round(temp_c * 9 / 5 + 32, 1)
                else:
                    self._attr_native_value = round(temp_c, 1)
            elif self._sensor_type == SENSOR_HUMIDITY:
                self._attr_native_value = round(float(value), 1)
            elif self._sensor_type == SENSOR_PRESSURE:
                self._attr_native_value = round(float(value), 2)
            elif self._sensor_type in (SENSOR_TVOC, SENSOR_ETVOC):
                self._update_voc_value(int(value))
            else:
                self._attr_native_value = int(value)

            self._is_unavailable = False
            self.async_write_ha_state()
        except ValueError:
            _LOGGER.error("Invalid value for %s: %s", self._sensor_type, value)

    def _update_voc_value(self, raw_value: int) -> None:
        """Update TVOC/eTVOC with unit conversion."""
        model = self._config_entry.data.get(CONF_MODEL, "")
        voc_unit = self.coordinator.data.get(CONF_TVOC_UNIT, "ppb")

        if model == "CGS1":
            voc_value = raw_value
            if voc_unit == "ppm":
                voc_value /= 1000
            elif voc_unit == "mg/m³":
                voc_value /= 218.77
            self._attr_native_value = round(voc_value, 3)
            self._attr_native_unit_of_measurement = voc_unit
        else:
            voc_value = raw_value
            if voc_unit == "ppb":
                voc_value = (math.log(501 - voc_value) - 6.24) * -2215.4
                voc_value = int(round(float(voc_value), 0))
            elif voc_unit == "mg/m³":
                voc_value = (math.log(501 - voc_value) - 6.24) * -2215.4
                voc_value = (voc_value * 4.5 * 10 + 5) / 10 / 1000
                voc_value = round(voc_value, 3)
            self._attr_native_value = voc_value
            self._attr_native_unit_of_measurement = None if voc_unit == "index" else voc_unit

        self._attr_device_class = _get_voc_device_class(voc_unit)

    @callback
    def update_battery_charging(self, is_charging: bool) -> None:
        if self._sensor_type == SENSOR_BATTERY:
            self._battery_charging = is_charging
            self.async_write_ha_state()

    @callback
    def _set_unavailable(self) -> None:
        self._is_unavailable = True
        self._attr_native_value = None
        self.async_write_ha_state()

    @property
    def icon(self):
        if self._sensor_type == SENSOR_BATTERY:
            if self._battery_charging:
                return "mdi:battery-charging"
            if self._attr_native_value is not None:
                level = int(self._attr_native_value)
                if level <= 10:
                    return "mdi:battery-10"
                if level <= 20:
                    return "mdi:battery-20"
                if level <= 30:
                    return "mdi:battery-30"
                if level <= 40:
                    return "mdi:battery-40"
                if level <= 50:
                    return "mdi:battery-50"
                if level <= 60:
                    return "mdi:battery-60"
                if level <= 70:
                    return "mdi:battery-70"
                if level <= 80:
                    return "mdi:battery-80"
                if level <= 90:
                    return "mdi:battery-90"
                return "mdi:battery"
        return super().icon

    @property
    def available(self) -> bool:
        if not self.hass:
            return False
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})
        sensors = entry_data.get("sensors", [])
        status_sensor = next(
            (s for s in sensors if isinstance(s, QingpingStatusSensor)), None
        )
        is_online = status_sensor.native_value == "online" if status_sensor else False

        if self._sensor_type in (SENSOR_PM10, SENSOR_PM25):
            return is_online and not self._is_unavailable
        return is_online

    async def publish_config(self) -> None:
        """Publish configuration message to MQTT."""
        if not self.hass:
            return

        model = self._config_entry.data.get(CONF_MODEL, "")
        topic = f"{MQTT_TOPIC_PREFIX}/{self._mac}/down"

        if model in TLV_MODELS:
            report_mode = self._config_entry.data.get(CONF_REPORT_MODE, REPORT_MODE_HISTORIC)
            if report_mode == REPORT_MODE_REALTIME:
                packets = {0x42: int_to_bytes_little_endian(21600, 2)}
            else:
                packets = {0x42: int_to_bytes_little_endian(0, 2)}
            payload = tlv_encode(0x32, packets)
        else:
            payload = json.dumps({
                "type": "12",
                "up_itvl": "15",
                "duration": DEFAULT_DURATION,
            })

        for attempt in range(MQTT_PUBLISH_RETRY_LIMIT):
            if not await ensure_mqtt_connected(self.hass):
                return
            try:
                await mqtt.async_publish(self.hass, topic, payload)
                return
            except HomeAssistantError as err:
                _LOGGER.warning("[%s] Config publish attempt %d failed: %s", self._mac, attempt + 1, err)
                if attempt < MQTT_PUBLISH_RETRY_LIMIT - 1:
                    await asyncio.sleep(MQTT_PUBLISH_RETRY_DELAY)
