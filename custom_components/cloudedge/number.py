"""Number platform for CloudEdge integration.

Creates Number entities for known editable numeric parameters.
Uses an ALLOWLIST approach - only parameters we KNOW are adjustable.
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
from .cloudedge.iot_parameters import get_parameter_name

_LOGGER = logging.getLogger(__name__)

# ALLOWLIST of numeric parameters that are KNOWN to be editable
# Format: param_code -> {min, max, step, unit, icon, name}
# ONLY these parameters will be exposed as Number entities
EDITABLE_NUMERIC_PARAMETERS = {
    # Recording settings
    "105": {
        "min": 5, "max": 300, "step": 5,
        "unit": "s", "icon": "mdi:record-circle",
        "name": "SD Recording Duration"
    },

    # Detection sensitivity settings
    "107": {
        "min": 0, "max": 100, "step": 1,
        "unit": "%", "icon": "mdi:motion-sensor",
        "name": "PIR Detection Sensitivity"
    },
    "110": {
        "min": 0, "max": 100, "step": 1,
        "unit": "%", "icon": "mdi:ear-hearing",
        "name": "Sound Detection Sensitivity"
    },
    "143": {
        "min": 0, "max": 100, "step": 1,
        "unit": "%", "icon": "mdi:brain",
        "name": "Smart Detection Sensitivity"
    },
    "151": {
        "min": 0, "max": 100, "step": 1,
        "unit": "%", "icon": "mdi:motion-sensor",
        "name": "Motion Detection Sensitivity"
    },
    "223": {
        "min": 0, "max": 100, "step": 1,
        "unit": "%", "icon": "mdi:human",
        "name": "Human Detection Sensitivity"
    },
    "224": {
        "min": 1, "max": 5, "step": 1,
        "unit": None, "icon": "mdi:human",
        "name": "Human Sensitivity Level"
    },

    # Volume settings
    "152": {
        "min": 0, "max": 100, "step": 10,
        "unit": "%", "icon": "mdi:volume-high",
        "name": "Speaker Volume"
    },
    "158": {
        "min": 0, "max": 100, "step": 10,
        "unit": "%", "icon": "mdi:bell-ring",
        "name": "Wireless Chime Volume"
    },

    # Light settings
    "168": {
        "min": 0, "max": 100, "step": 10,
        "unit": "%", "icon": "mdi:brightness-6",
        "name": "Front Light Brightness"
    },
    "171": {
        "min": 5, "max": 300, "step": 5,
        "unit": "s", "icon": "mdi:timer",
        "name": "Front Light PIR Duration"
    },

    # Timing settings
    "136": {
        "min": 0, "max": 60, "step": 5,
        "unit": "min", "icon": "mdi:timer-sand",
        "name": "Chime Snooze Interval"
    },
    "163": {
        "min": 5, "max": 300, "step": 5,
        "unit": "s", "icon": "mdi:timer-sand",
        "name": "Doorbell Sleep Delay"
    },
    "164": {
        "min": 5, "max": 60, "step": 5,
        "unit": "s", "icon": "mdi:timer",
        "name": "Doorbell Enter Message Time"
    },
    "165": {
        "min": 10, "max": 120, "step": 5,
        "unit": "s", "icon": "mdi:timer",
        "name": "Doorbell Max Message Time"
    },
    "218": {
        "min": 1, "max": 60, "step": 1,
        "unit": "s", "icon": "mdi:whistle",
        "name": "Whistle Time"
    },

    # Video settings
    "228": {
        "min": 1, "max": 30, "step": 1,
        "unit": "fps", "icon": "mdi:video",
        "name": "Frame Rate"
    },
}


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

    for serial_number, device_data in coordinator.data.items():
        config = device_data.get("configuration") or {}
        device_name = device_data.get("name", serial_number)

        # Only create Number entities for parameters in our allowlist
        for param_code, param_config in EDITABLE_NUMERIC_PARAMETERS.items():
            if param_code not in config:
                continue

            current_value = config[param_code]

            # Handle dict format {value: x}
            if isinstance(current_value, dict):
                current_value = current_value.get("value", 0)

            # Get numeric value
            try:
                numeric_value = float(current_value)
            except (ValueError, TypeError):
                _LOGGER.debug(
                    "Skipping %s for %s - value %s is not numeric",
                    param_config["name"], device_name, current_value
                )
                continue

            entities.append(
                CloudEdgeNumber(
                    coordinator=coordinator,
                    serial_number=serial_number,
                    device_name=device_name,
                    param_code=param_code,
                    param_config=param_config,
                    initial_value=numeric_value,
                )
            )

            _LOGGER.debug(
                "Adding number entity %s for %s (current: %s)",
                param_config["name"],
                device_name,
                numeric_value,
            )

    if entities:
        async_add_entities(entities)
        _LOGGER.info("Added %d number entities", len(entities))


class CloudEdgeNumber(CoordinatorEntity, NumberEntity):
    """Representation of a CloudEdge numeric parameter."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        serial_number: str,
        device_name: str,
        param_code: str,
        param_config: dict,
        initial_value: float,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._device_name = device_name
        self._param_code = param_code
        self._param_config = param_config
        self._attr_native_value = initial_value

        # Entity attributes
        self._attr_name = param_config["name"]
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

        # Entity is enabled by default
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
        param_name = get_parameter_name(self._param_code)

        _LOGGER.debug(
            "Setting %s to %s for %s",
            self._param_config["name"],
            int_value,
            self._device_name,
        )

        try:
            success = await self.hass.async_add_executor_job(
                self.coordinator.client.set_device_parameter,
                self._device_name,
                param_name,
                int_value,
            )

            if success:
                self._attr_native_value = value
                # Refresh coordinator to get updated value
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(
                    "Failed to set %s for %s",
                    self._param_config["name"],
                    self._device_name,
                )
        except Exception as e:
            _LOGGER.error(
                "Error setting %s for %s: %s",
                self._param_config["name"],
                self._device_name,
                e,
            )
