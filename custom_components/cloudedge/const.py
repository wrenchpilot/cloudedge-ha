"""Constants for the CloudEdge integration."""

DOMAIN = "cloudedge"

# Configuration keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_COUNTRY_CODE = "country_code"
CONF_REGION = "region"
CONF_BASE_URL = "base_url"
CONF_OPENAPI_BASE_URL = "openapi_base_url"
CONF_PHONE_CODE = "phone_code"
CONF_REFRESH_INTERVAL = "refresh_interval"

# Default values
DEFAULT_REFRESH_INTERVAL = 5  # minutes
DEFAULT_COUNTRY_CODE = "US"
DEFAULT_REGION = "AUTO"
DEFAULT_PHONE_CODE = "+1"

# Supported country codes and phone codes
COUNTRY_CODES = {
    "US": "+1",
    "IT": "+39",
    "DE": "+49",
    "FR": "+33",
    "UK": "+44",
    "ES": "+34",
    "NL": "+31",
    "CH": "+41",
    "AT": "+43",
    "BE": "+32",
    "SE": "+46",
    "NO": "+47",
    "DK": "+45",
    "FI": "+358",
    "PL": "+48",
    "CZ": "+420",
    "HU": "+36",
    "PT": "+351",
    "GR": "+30",
    "IE": "+353",
}

# Supported regions. AUTO means pick based on country_code, otherwise explicit region
REGIONS = ["AUTO", "EU", "US"]

# Device types mapping
DEVICE_TYPE_CAMERA = "Camera"
DEVICE_TYPE_DOORBELL = "Doorbell"
DEVICE_TYPE_SENSOR = "Sensor"

# Entity categories
ENTITY_CATEGORY_CONFIG = "config"
ENTITY_CATEGORY_DIAGNOSTIC = "diagnostic"

# Parameter names for switches
SWITCH_PARAMETERS = {
    "front_light": "FRONT_LIGHT_SWITCH",
    "motion_detection": "MOTION_DET_ENABLE",
    "led_enable": "LED_ENABLE",
    "sound_detection": "SOUND_DET_ENABLE",
    "push_notifications": "PUSH_ENABLE",
    "email_notifications": "EMAIL_ENABLE",
}

# Parameter names for sensors (mapped to parameter codes, not names)
SENSOR_PARAMETERS = {
    "battery_level": "154",        # BATTERY_PERCENT
    "wifi_strength": "1007",       # WIFI_STRENGTH
    "motion_sensitivity": "151",   # MOTION_DET_SENSITIVITY
    "speaker_volume": "152",       # SPEAK_VOLUME
    "device_temperature": "1012",  # DEVICE_TEMPERATURE
}

# Parameter codes that should be enabled by default for generic sensors (DIAGNOSTIC)
ENABLED_BY_DEFAULT_SENSOR_PARAMS = [
    "154",   # BATTERY_PERCENT - Battery percentage (diagnostic)
]

# Parameter names that should be enabled by default for switches (CONFIG)
ENABLED_BY_DEFAULT_SWITCH_PARAMS = [
    "MOTION_DET_ENABLE",  # Motion detection enable (config)
    "LED_ENABLE",         # LED enable (config)
]

# Sensor device classes
SENSOR_DEVICE_CLASS_BATTERY = "battery"
SENSOR_DEVICE_CLASS_SIGNAL_STRENGTH = "signal_strength"
SENSOR_DEVICE_CLASS_TEMPERATURE = "temperature"

# Sensor units
SENSOR_UNIT_PERCENTAGE = "%"
SENSOR_UNIT_CELSIUS = "°C"
SENSOR_UNIT_DECIBEL = "dB"