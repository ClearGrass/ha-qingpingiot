"""Constants for the Qingping IoT integration."""

from __future__ import annotations

from enum import StrEnum
from typing import Final, TypedDict

from homeassistant.const import Platform

# Integration
DOMAIN: Final = "qingpingiot"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
]

# Config keys
CONF_MAC: Final = "mac"
CONF_NAME: Final = "name"
CONF_MODEL: Final = "model"
CONF_DEVICE: Final = "device"

# MQTT
MQTT_TOPIC_PREFIX: Final = "qingping"

# Sensor types
SENSOR_BATTERY: Final = "battery"
SENSOR_TEMPERATURE: Final = "temperature"
SENSOR_HUMIDITY: Final = "humidity"
SENSOR_CO2: Final = "co2"
SENSOR_PM25: Final = "pm25"
SENSOR_PM10: Final = "pm10"
SENSOR_TVOC: Final = "tvoc"
SENSOR_ETVOC: Final = "tvoc_index"
SENSOR_NOISE: Final = "noise"
SENSOR_PRESSURE: Final = "pressure"
SENSOR_LIGHT: Final = "light"
SENSOR_SIGNAL_STRENGTH: Final = "signal_strength"


class Capability(StrEnum):
    """Device capability identifiers."""

    BATTERY = "battery"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    CO2 = "co2"
    PM25 = "pm25"
    PM10 = "pm10"
    TVOC = "tvoc"
    ETVOC = "tvoc_index"
    NOISE = "noise"
    PRESSURE = "pressure"
    LIGHT = "light"
    SIGNAL_STRENGTH = "signal_strength"
    # Control capabilities
    CO2_ASC = "co2_asc"
    CO2_CALIBRATION = "co2_calibration"
    LED_INDICATOR = "led_indicator"


class Protocol(StrEnum):
    """Communication protocol types."""

    BLE = "ble"
    MQTT = "mqtt"


# Units
PPM: Final = "ppm"
PPB: Final = "ppb"
CONCENTRATION: Final = "µg/m³"
DB: Final = "dB"

# TLV intervals
CONF_REPORT_INTERVAL: Final = "report_interval"  # Minutes (KEY 0x04)
CONF_SAMPLE_INTERVAL: Final = "sample_interval"  # Seconds (KEY 0x05)

# Offsets
CONF_TEMPERATURE_OFFSET: Final = "temperature_offset"
CONF_HUMIDITY_OFFSET: Final = "humidity_offset"
CONF_CO2_OFFSET: Final = "co2_offset"
CONF_PM25_OFFSET: Final = "pm25_offset"
CONF_PM10_OFFSET: Final = "pm10_offset"
CONF_NOISE_OFFSET: Final = "noise_offset"
CONF_TVOC_OFFSET: Final = "tvoc_offset"
CONF_PRESSURE_OFFSET: Final = "pressure_offset"

DEFAULT_OFFSET: Final = 0

# LED indicator
CONF_LED_INDICATOR: Final = "led_indicator"

# JSON protocol config
ATTR_TYPE: Final = "type"
ATTR_UP_ITVL: Final = "up_itvl"
ATTR_DURATION: Final = "duration"

DEFAULT_TYPE: Final = "12"
DEFAULT_DURATION: Final = "21600"


# Device model definitions
# key 是 HA 插件内部使用的逻辑型号，后续实体根据此 key 决定行为
class DeviceModelInfo(TypedDict):
    """Device model metadata."""

    name: str
    protocols: list[str]
    capabilities: list[Capability]


DEVICE_MODELS: dict[str, DeviceModelInfo] = {
    # -- Robb 室内环境检测仪/Qingping Indoor Environment Monitor --
    "CGR1W": {
        "name": "青萍室内环境检测仪 (CGR1W/Q5EU1/CGR1C/CGR1L/CGR1LH)",
        "protocols": [Protocol.MQTT],
        "capabilities": [
            Capability.TEMPERATURE,
            Capability.HUMIDITY,
            Capability.CO2,
            Capability.PM25,
            Capability.PM10,
            Capability.TVOC,
            Capability.NOISE,
            Capability.LIGHT,
            Capability.BATTERY,
        ],
    },
    # -- Frog S 青萍商用多功能检测仪/Qingping Multi-Role Monitor Pro --
    "CGF2W": {
        "name": "青萍商用多功能检测仪 (CGF2W/CGF2C/CGF2L/CGF2LH/CGF2P-T40/CGF2P-T40i/CGF2P-TH40/CGF2P-C/CGF2P-HC-F/CGF2P-T70/CGF2P-T200)",
        "protocols": [Protocol.MQTT],
        "capabilities": [
            Capability.TEMPERATURE,
            Capability.HUMIDITY,
        ],
    },
}

# 用于 config_flow 的下拉选项
MODEL_OPTIONS: Final = [
    {"label": info["name"], "value": model}
    for model, info in DEVICE_MODELS.items()
]

# JSON 协议设备
JSON_MODELS: Final = [m for m in ("CGS1", "CGS2", "CGDN1") if m in DEVICE_MODELS]

# TLV 协议设备
TLV_MODELS: Final = [m for m in DEVICE_MODELS if m not in JSON_MODELS]
