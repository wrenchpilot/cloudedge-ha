"""Number platform for CloudEdge integration."""
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
from .cloudedge.iot_parameters import get_parameter_name, get_parameter_code_by_name

_LOGGER = logging.getLogger(__name__)

# Define numeric parameters that should be exposed as Number entities
# Format: parameter_code -> {min, max, step, unit, icon, name_override}
NUMERIC_PARAMETERS = {
    "105": {  # SD_RECORD_DURATION
        "min": 5,
        "max": 300,
        "step": 5,
        "unit": "s",
        "icon": "mdi:record-circle",
        "name": "SD Recording Duration",
    },
    "151": {  # MOTION_DET_SENSITIVITY
        "min": 0,
        "max": 100,
        "step": 1,
        "unit": "%",
        "icon": "mdi:motion-sensor",
        "name": "Motion Sensitivity",
    },
    "107": {  # PIR_DET_SENSITIVITY
        "min": 0,
        "max": 100,
        "step": 1,
        "unit": "%",
        "icon": "mdi:motion-sensor",
        "name": "PIR Sensitivity",
    },
    "110": {  # SOUND_DET_SENSITIVITY
        "min": 0,
        "max": 100,
        "step": 1,
        "unit": "%",
        "icon": "mdi:volume-high",
        "name": "Sound Sensitivity",
    },
    "143": {  # SMART_DET_SENSITIVITY
        "min": 0,
        "max": 100,
        "step": 1,
        "unit": "%",
        "icon": "mdi:brain",
        "name": "Smart Detection Sensitivity",
    },
    "152": {  # SPEAK_VOLUME
        "min": 0,
        "max": 100,
        "step": 10,
        "unit": "%",
        "icon": "mdi:volume-high",
        "name": "Speaker Volume",
    },
    "158": {  # WIRELESS_CHIME_VOLUME
        "min": 0,
        "max": 100,
        "step": 10,
        "unit": "%",
        "icon": "mdi:bell",
        "name": "Chime Volume",
    },
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CloudEdge number entities from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[NumberEntity] = []

    for serial_number, device_data in coordinator.data.items():
        config = device_data.get("configuration") or {}
        device_name = device_data.get("name", serial_number)

        # Check which numeric parameters this device supports
        for param_code, param_config in NUMERIC_PARAMETERS.items():
            if param_code in config:
                current_value = config[param_code]
                # Handle dict format {value: x} or direct value
                if isinstance(current_value, dict):
                    current_value = current_value.get("value", 0)
                
                entities.append(
                    CloudEdgeNumber(
                        coordinator=coordinator,
                        serial_number=serial_number,
                        device_name=device_name,
                        param_code=param_code,
                        param_config=param_config,
                        initial_value=current_value,
                    )
                )
                _LOGGER.debug(
                    "Adding number entity %s for %s (current: %s)",
                    param_config["name"],
                    device_name,
                    current_value,
                )

    if entities:
        async_add_entities(entities)
        _LOGGER.info("Added %d number entities", len(entities))


class CloudEdgeNumber(CoordinatorEntity, NumberEntity):
    """Representation of a CloudEdge numeric parameter."""

    def __init__(
        self,
        coordinator,
        serial_number: str,
        device_name: str,
        param_code: str,
        param_config: dict,
        initial_value: Any,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._device_name = device_name
        self._param_code = param_code
        self._param_config = param_config
        self._attr_native_value = float(initial_value) if initial_value is not None else 0

        # Entity attributes
        param_name = get_parameter_name(param_code) or param_code
        self._attr_name = f"{device_name} {param_config['name']}"
        self._attr_unique_id = f"{serial_number}_{param_code}_number"
        
        # Number configuration
        self._attr_native_min_value = param_config["min"]
        self._attr_native_max_value = param_config["max"]
        self._attr_native_step = param_config["step"]
        self._attr_native_unit_of_measurement = param_config.get("unit")
        self._attr_icon = param_config.get("icon")
        self._attr_mode = NumberMode.SLIDER
        
        # Entity category - these are configuration entities
        self._attr_entity_category = EntityCategory.CONFIG
        
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
            self._param_config["name"],
            int_value,
            self._device_name,
        )

        try:
            param_name = get_parameter_name(self._param_code)
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
