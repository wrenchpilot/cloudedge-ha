"""Number platform for CloudEdge integration.

Dynamically creates Number entities for all numeric parameters that can be edited.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .cloudedge.iot_parameters import (
    get_parameter_name,
    BOOLEAN_PARAMETERS,
    PERCENTAGE_PARAMETERS,
    CAPACITY_PARAMETERS,
    IOT_PARAMETERS,
)

_LOGGER = logging.getLogger(__name__)

# Read-only parameters that should NOT be exposed as Number entities
# These are status codes, not configurable values
READ_ONLY_PARAMETERS = {
    # Device info (read-only)
    "1",    # USER_ID
    "3",    # DEVICE_KEY
    "8",    # DEVICE_STATUS
    "9",    # DEVICE_MODE
    "10",   # CLOUD_VIDEO_EXPIRY
    "15",   # DEVICE_FLAGS
    "18",   # DEVICE_STATE
    "50",   # SERIAL_NUMBER
    "51",   # FIRMWARE_CODE
    "52",   # FIRMWARE_VERSION
    "54",   # TIME_ZONE
    "55",   # CAPABILITIES
    "56",   # MAC_ADDRESS
    "57",   # DNS_SERVERS
    "58",   # SUBNET_MASK
    "59",   # GATEWAY
    "60",   # LAST_CHECK_TIME
    "61",   # LICENSE_ID
    "62",   # SUPPORT_VERSION
    "63",   # DEVICE_MODEL
    "64",   # PLATFORM_CODE
    "65",   # DEVICE_ONLINE_TIME
    "66",   # TP
    "67",   # NVR_NEUTRAL_CHANNEL_CAPS
    "68",   # NVR_NEUTRAL_QR_CODE_KEY
    "69",   # MEDIA_QUANTITY
    "70",   # AFTER_SALE
    "72",   # DEVICE_NAME
    "100",  # WIFI_NAME
    "101",  # WIFI_SIGNAL_QUALITY (read-only signal strength)
    "114",  # SD_STATUS (status, not config)
    "115",  # SD_CAPACITY (read-only)
    "116",  # SD_REMAINING_CAPACITY (read-only)
    "119",  # SLEEP_TIME_LIST (complex JSON, not simple number)
    "122",  # ONVIF_PORT (special handling)
    "123",  # ONVIF_URL (string, not number)
    "125",  # ALARM_PLAN_LIST (complex JSON)
    "126",  # IP_ADDRESS (string, not number)
    "127",  # NET_MODE (status)
    "128",  # OTA_UPGRADE_STATUS (status)
    "130",  # RTMP_STREAM (string)
    "131",  # CHIME_PRO_RING_URI (string)
    "133",  # CHIME_PRO_MOTION_URI (string)
    "137",  # CHIME_PLAN (complex)
    "153",  # POWER_TYPE (status)
    "154",  # BATTERY_PERCENT (read-only percentage)
    "155",  # BATTERY_REMAINING (read-only)
    "156",  # CHARGE_STATUS (status)
    "159",  # WIRELESS_CHIME_SONGS (list)
    "169",  # FRONT_LIGHT_SCHEDULE (complex)
    "170",  # DOUBLE_PIR_STATUS (status)
    # Status parameters (1000+ range are typically read-only)
    "1007", # WIFI_STRENGTH
    "1010", # PIR_STATUS
    "1012", # DEVICE_TEMPERATURE
}

# Override configurations for specific parameters where we know the range
# Format: param_code -> {min, max, step, unit, icon}
PARAMETER_OVERRIDES = {
    "105": {"min": 5, "max": 300, "step": 5, "unit": "s", "icon": "mdi:record-circle"},  # SD_RECORD_DURATION
    "107": {"min": 0, "max": 100, "step": 1, "unit": "%", "icon": "mdi:motion-sensor"},   # PIR_DET_SENSITIVITY
    "110": {"min": 0, "max": 100, "step": 1, "unit": "%", "icon": "mdi:volume-high"},     # SOUND_DET_SENSITIVITY
    "136": {"min": 0, "max": 60, "step": 5, "unit": "min", "icon": "mdi:timer-sand"},     # CHIME_PRO_SNOOZE_INTERVAL
    "143": {"min": 0, "max": 100, "step": 1, "unit": "%", "icon": "mdi:brain"},           # SMART_DET_SENSITIVITY
    "151": {"min": 0, "max": 100, "step": 1, "unit": "%", "icon": "mdi:motion-sensor"},   # MOTION_DET_SENSITIVITY
    "152": {"min": 0, "max": 100, "step": 10, "unit": "%", "icon": "mdi:volume-high"},    # SPEAK_VOLUME
    "158": {"min": 0, "max": 100, "step": 10, "unit": "%", "icon": "mdi:bell"},           # WIRELESS_CHIME_VOLUME
    "163": {"min": 0, "max": 300, "step": 5, "unit": "s", "icon": "mdi:timer-sand"},      # BELL_SLEEP_DELAY
    "164": {"min": 0, "max": 60, "step": 5, "unit": "s", "icon": "mdi:timer"},            # BELL_ENTER_MESSAGE_TIME
    "165": {"min": 0, "max": 120, "step": 5, "unit": "s", "icon": "mdi:timer"},           # BELL_MAX_MESSAGE_TIME
    "168": {"min": 0, "max": 100, "step": 10, "unit": "%", "icon": "mdi:brightness-6"},   # FRONT_LIGHT_BRIGHTNESS
    "171": {"min": 0, "max": 300, "step": 5, "unit": "s", "icon": "mdi:timer-sand"},      # FRONT_LIGHT_PIR_DURATION
}

# Default config for unknown numeric parameters
DEFAULT_CONFIG = {
    "min": 0,
    "max": 100,
    "step": 1,
    "unit": None,
    "icon": "mdi:tune",
}


def _is_numeric_value(value: Any) -> bool:
    """Check if a value represents a numeric/integer parameter."""
    if value is None:
        return False
    
    # Handle dict format {value: x}
    if isinstance(value, dict):
        value = value.get("value", value)
    
    # Boolean-like values
    if isinstance(value, bool):
        return False
    if value in (0, 1, "0", "1", True, False):
        # Could be boolean, could be numeric - check context later
        return True
    
    # String values that aren't numbers
    if isinstance(value, str):
        # JSON objects/arrays
        if value.startswith("{") or value.startswith("["):
            return False
        # IP addresses, URLs, etc
        if "." in value and not value.replace(".", "").isdigit():
            return False
        if ":" in value or "/" in value:
            return False
        # Try to parse as number
        try:
            float(value)
            return True
        except ValueError:
            return False
    
    # Actual numbers
    if isinstance(value, (int, float)):
        return True
    
    return False


def _get_param_config(param_code: str, param_name: str, current_value: Any) -> dict:
    """Get configuration for a parameter, with smart defaults."""
    # Use override if available
    if param_code in PARAMETER_OVERRIDES:
        return PARAMETER_OVERRIDES[param_code].copy()
    
    # Make smart guesses based on parameter name
    config = DEFAULT_CONFIG.copy()
    name_upper = param_name.upper() if param_name else ""
    
    if "SENSITIVITY" in name_upper:
        config.update({"min": 0, "max": 100, "step": 1, "unit": "%", "icon": "mdi:tune"})
    elif "VOLUME" in name_upper:
        config.update({"min": 0, "max": 100, "step": 10, "unit": "%", "icon": "mdi:volume-high"})
    elif "BRIGHTNESS" in name_upper:
        config.update({"min": 0, "max": 100, "step": 10, "unit": "%", "icon": "mdi:brightness-6"})
    elif "DURATION" in name_upper or "TIME" in name_upper or "DELAY" in name_upper:
        config.update({"min": 0, "max": 300, "step": 5, "unit": "s", "icon": "mdi:timer"})
    elif "INTERVAL" in name_upper:
        config.update({"min": 0, "max": 60, "step": 5, "unit": "min", "icon": "mdi:timer-sand"})
    
    return config


def _should_be_number_entity(param_code: str, param_name: str, value: Any) -> bool:
    """Determine if a parameter should be exposed as a Number entity."""
    # Skip read-only parameters
    if param_code in READ_ONLY_PARAMETERS:
        return False
    
    # Skip boolean parameters (they're switches)
    if param_name and param_name.upper() in BOOLEAN_PARAMETERS:
        return False
    
    # Skip percentage/capacity parameters (they're read-only sensors)
    if param_name and param_name.upper() in PERCENTAGE_PARAMETERS:
        return False
    if param_name and param_name.upper() in CAPACITY_PARAMETERS:
        return False
    
    # Skip parameters that end with common non-editable suffixes
    if param_name:
        name_upper = param_name.upper()
        if any(suffix in name_upper for suffix in ["_STATUS", "_PERCENT", "_REMAINING", "_CAPACITY", "_URL", "_URI", "_ADDRESS", "_NAME", "_KEY", "_ID", "_LIST", "_CODE", "_VERSION"]):
            return False
    
    # Must have a numeric value
    if not _is_numeric_value(value):
        return False
    
    # Extract actual numeric value
    actual_value = value
    if isinstance(value, dict):
        actual_value = value.get("value", 0)
    try:
        num_val = float(actual_value)
    except (ValueError, TypeError):
        return False
    
    # Skip if it looks like a boolean (0 or 1 only, and name suggests enable/disable)
    if num_val in (0, 1) and param_name:
        name_upper = param_name.upper()
        if any(word in name_upper for word in ["ENABLE", "SWITCH", "MODE", "TYPE", "SELECTED"]):
            return False
    
    return True


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CloudEdge number entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    if not coordinator.data:
        _LOGGER.warning("No device data available for number platform")
        async_add_entities([])
        return

    entities: list[NumberEntity] = []
    created_params: set[tuple[str, str]] = set()  # (serial, param_code)

    for serial_number, device_data in coordinator.data.items():
        config = device_data.get("configuration") or {}
        device_name = device_data.get("name", serial_number)

        for param_code, current_value in config.items():
            # Skip if already created
            if (serial_number, param_code) in created_params:
                continue
            
            # Get parameter name
            param_name = get_parameter_name(param_code)
            
            # Check if this should be a Number entity
            if not _should_be_number_entity(param_code, param_name, current_value):
                continue
            
            # Get numeric value
            if isinstance(current_value, dict):
                current_value = current_value.get("value", 0)
            try:
                numeric_value = float(current_value)
            except (ValueError, TypeError):
                continue
            
            # Get configuration
            param_config = _get_param_config(param_code, param_name, current_value)
            
            entities.append(
                CloudEdgeNumber(
                    coordinator=coordinator,
                    serial_number=serial_number,
                    device_name=device_name,
                    param_code=param_code,
                    param_name=param_name,
                    param_config=param_config,
                    initial_value=numeric_value,
                )
            )
            created_params.add((serial_number, param_code))
            
            _LOGGER.debug(
                "Adding number entity %s (%s) for %s (current: %s, range: %s-%s)",
                param_name,
                param_code,
                device_name,
                numeric_value,
                param_config["min"],
                param_config["max"],
            )

    if entities:
        async_add_entities(entities)
        _LOGGER.info("Added %d number entities for editable parameters", len(entities))
    else:
        _LOGGER.debug("No editable number parameters found")


class CloudEdgeNumber(CoordinatorEntity, NumberEntity):
    """Representation of a CloudEdge numeric parameter."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        serial_number: str,
        device_name: str,
        param_code: str,
        param_name: str,
        param_config: dict,
        initial_value: float,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._device_name = device_name
        self._param_code = param_code
        self._param_name = param_name
        self._param_config = param_config
        self._attr_native_value = initial_value

        # Entity attributes - use parameter name directly
        display_name = param_name.replace("_", " ").title() if param_name else f"Parameter {param_code}"
        self._attr_name = display_name
        self._attr_unique_id = f"{serial_number}_{param_code}_number"
        
        # Number configuration
        self._attr_native_min_value = float(param_config["min"])
        self._attr_native_max_value = float(param_config["max"])
        self._attr_native_step = float(param_config["step"])
        self._attr_native_unit_of_measurement = param_config.get("unit")
        self._attr_icon = param_config.get("icon", "mdi:tune")
        self._attr_mode = NumberMode.SLIDER
        
        # Entity category - these are configuration entities
        self._attr_entity_category = EntityCategory.CONFIG
        
        # Entity is enabled by default (user can disable if unwanted)
        self._attr_entity_registry_enabled_default = True
        
        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, serial_number)},
            "name": device_name,
        }

    @property
    def native_value(self) -> float | None:
        """Return the current value."""
        device_data = self.coordinator.data.get(self._serial_number)
        if not device_data:
            return self._attr_native_value

        config = device_data.get("configuration") or {}
        value = config.get(self._param_code)
        
        if value is None:
            return self._attr_native_value
            
        if isinstance(value, dict):
            value = value.get("value", 0)
            
        try:
            return float(value)
        except (ValueError, TypeError):
            return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        int_value = int(value)
        _LOGGER.debug(
            "Setting %s to %s for %s",
            self._param_name,
            int_value,
            self._device_name,
        )

        try:
            success = await self.hass.async_add_executor_job(
                self.coordinator.client.set_device_parameter,
                self._device_name,
                self._param_name,
                int_value,
            )

            if success:
                self._attr_native_value = value
                # Refresh coordinator to get updated value
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(
                    "Failed to set %s for %s",
                    self._param_name,
                    self._device_name,
                )
        except Exception as e:
            _LOGGER.error(
                "Error setting %s for %s: %s",
                self._param_name,
                self._device_name,
                e,
            )
