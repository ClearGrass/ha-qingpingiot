"""Support for Qingping IoT sensors."""
from __future__ import annotations

import logging
import math

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from datetime import timedelta

from .const import (
    Capability,
    CONCENTRATION,
    CONF_TVOC_UNIT,
    DB,
    DEVICE_MODELS,
    DOMAIN,
    PERCENTAGE,
    PPB,
    INDEX,
    PPM,
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
from .coordinator import QingpingCoordinator

_LOGGER = logging.getLogger(__name__)


# Capability → Sensor description mapping
CAPABILITY_SENSOR_MAP: dict[Capability, dict] = {
    Capability.TEMPERATURE: {
        "sensor_type": SENSOR_TEMPERATURE,
        "name": "Temperature",
        "unit": UnitOfTemperature.CELSIUS,
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
        "unit": INDEX,
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


# Setup
async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Qingping sensors from a config entry."""
    coordinator: QingpingCoordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    mac = coordinator.mac
    name = coordinator.name
    model = coordinator.model

    model_info = DEVICE_MODELS[model]
    capabilities = model_info["capabilities"]

    device_info = {
        "identifiers": {(DOMAIN, mac)},
        "name": name,
        "manufacturer": "Qingping",
        "model": model,
    }

    sensors: list[SensorEntity] = []

    # Diagnostic sensors
    status_sensor = QingpingStatusSensor(coordinator, device_info)
    firmware_sensor = QingpingFirmwareSensor(coordinator, device_info)
    mac_sensor = QingpingMACSensor(coordinator, device_info)
    battery_state_sensor = QingpingBatteryStateSensor(coordinator, device_info)

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

        if desc.get("tlv_only") and not is_tlv:
            continue

        unit = desc.get("unit")
        entity_category = desc.get("entity_category")

        sensor = QingpingSensor(
            coordinator=coordinator,
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

    # Periodic online status check
    async def check_status(*_):
        coordinator.check_online_status()

    config_entry.async_on_unload(
        async_track_time_interval(hass, check_status, timedelta(seconds=60))
    )


# -- Diagnostic Sensors --


class QingpingStatusSensor(CoordinatorEntity, SensorEntity):
    """Device online/offline status."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: QingpingCoordinator, device_info: dict) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.name} Status"
        self._attr_unique_id = f"{coordinator.mac}_status"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str:
        return "online" if self.coordinator.is_online else "offline"


class QingpingFirmwareSensor(CoordinatorEntity, SensorEntity):
    """Device firmware version."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: QingpingCoordinator, device_info: dict) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.name} Firmware"
        self._attr_unique_id = f"{coordinator.mac}_firmware"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.get("firmware_version")


class QingpingMACSensor(CoordinatorEntity, SensorEntity):
    """Device MAC address."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: QingpingCoordinator, device_info: dict) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.name} MAC Address"
        self._attr_unique_id = f"{coordinator.mac}_mac"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.get("mac")


class QingpingBatteryStateSensor(CoordinatorEntity, SensorEntity):
    """Battery charging state."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: QingpingCoordinator, device_info: dict) -> None:
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.name} Battery State"
        self._attr_unique_id = f"{coordinator.mac}_battery_state"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> str:
        charging = self.coordinator.data.get("battery_charging")
        if charging is True:
            return "Charging"
        if charging is False:
            return "Discharging"
        return "Unknown"


# -- Main Sensor Entity --


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
        coordinator: QingpingCoordinator,
        sensor_type: str,
        friendly_name: str,
        unit: str | None,
        device_class: SensorDeviceClass,
        state_class: SensorStateClass,
        device_info: dict,
        entity_category: EntityCategory | None = None,
    ):
        super().__init__(coordinator)
        self._sensor_type = sensor_type
        self._is_unavailable = False

        self._attr_name = f"{coordinator.name} {friendly_name}"
        self._attr_unique_id = f"{coordinator.mac}_{sensor_type}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_device_info = device_info
        if entity_category:
            self._attr_entity_category = entity_category

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        data = self.coordinator.data

        # Try TLV data first
        sensor_data = data.get("sensor_data")
        if isinstance(sensor_data, dict):
            self._update_from_tlv_data(sensor_data, data.get("decoded", {}))

        # Try JSON sensor data list
        for entry in data.get("sensor_data_list", []):
            self._update_from_json_data(entry)

    def _update_from_tlv_data(self, data: dict, top_level: dict) -> None:
        """Update sensor from TLV decoded data."""
        # _LOGGER.debug(
        #     "[%s] TLV update sensor=%s, sensor_data=%s, top_level=%s",
        #     self.coordinator.mac, self._sensor_type, data, top_level,
        # )
        value = None

        if self._sensor_type == SENSOR_BATTERY:
            raw = top_level.get("battery") or data.get("battery")
            if raw is not None and raw >= 255:
                self._is_unavailable = True
                self._attr_native_value = None
                self.async_write_ha_state()
                return
            value = raw
        elif self._sensor_type == SENSOR_SIGNAL_STRENGTH:
            value = top_level.get("signalStrength")
            if value is not None and value >= 128:
                value -= 256
            elif value is None:
                value = data.get("rssi")
        elif self._sensor_type == SENSOR_ETVOC:
            value = data.get("tvoc")
        else:
            value = data.get(self._sensor_type)

        if value is not None:
            self._set_value(value)

    def _update_from_json_data(self, data: dict) -> None:
        """Update sensor from JSON sensorData."""
        if self._sensor_type not in data:
            return

        raw = data[self._sensor_type]
        if isinstance(raw, dict):
            value = raw.get("value")
            if self._sensor_type in (SENSOR_PM10, SENSOR_PM25) and value == 99999:
                self._is_unavailable = True
                self._attr_native_value = None
                self.async_write_ha_state()
                return
        else:
            value = raw

        if self._sensor_type == SENSOR_BATTERY and isinstance(value, int) and value >= 255:
            self._is_unavailable = True
            self._attr_native_value = None
            self.async_write_ha_state()
            return

        if value is not None:
            self._set_value(value)

    def _set_value(self, value) -> None:
        """Convert and set sensor value."""
        try:
            if self._sensor_type == SENSOR_TEMPERATURE:
                self._attr_native_value = round(float(value), 1)
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
        model = self.coordinator.model
        voc_unit = self.coordinator.data.get(CONF_TVOC_UNIT, "index")

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

    @property
    def icon(self):
        if self._sensor_type == SENSOR_BATTERY:
            charging = self.coordinator.data.get("battery_charging")
            if charging or self._attr_native_value is None:
                return "mdi:battery-charging"
            if self._attr_native_value is not None:
                level = int(self._attr_native_value)
                bucket = max(10, (level // 10) * 10) if level > 0 else 10
                if bucket >= 100:
                    return "mdi:battery"
                return f"mdi:battery-{bucket}"
        return super().icon

    @property
    def available(self) -> bool:
        if not self.coordinator.is_online:
            return False
        if self._sensor_type in (SENSOR_PM10, SENSOR_PM25):
            return not self._is_unavailable
        if self._sensor_type == SENSOR_BATTERY and self._is_unavailable:
            return False
        return True
