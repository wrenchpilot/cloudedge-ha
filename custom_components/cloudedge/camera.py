"""Camera platform for CloudEdge integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CloudEdgeCoordinator
from .const import DOMAIN
from .cloudedge.iot_parameters import get_parameter_name

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up CloudEdge camera platform."""
    coordinator: CloudEdgeCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Handle case where coordinator.data might be None
    if not coordinator.data:
        _LOGGER.warning("No device data available yet, camera entities will be added when data is available")
        
        # Add a listener to create entities when data becomes available
        async def _handle_coordinator_update():
            if coordinator.data and not getattr(coordinator, '_cameras_added', False):
                _LOGGER.info("Device data is now available, adding camera entities")
                cameras = []
                for serial_number, device_info in coordinator.data.items():
                    # Only add camera devices
                    if device_info.get("type_id") in [1, 2, 3, 4, 5]:  # Common camera type IDs
                        camera = CloudEdgeCamera(coordinator, serial_number, device_info)
                        cameras.append(camera)
                        _LOGGER.debug("Added camera: %s", device_info.get("name"))
                if cameras:
                    async_add_entities(cameras)
                    coordinator._cameras_added = True
        
        coordinator.async_add_listener(_handle_coordinator_update)
        async_add_entities([])
        return

        coordinator._cameras_added = True

    cameras = []
    for serial_number, device_info in coordinator.data.items():
        # Only add camera devices
        if device_info.get("type_id") in [1, 2, 3, 4, 5]:  # Common camera type IDs
            camera = CloudEdgeCamera(coordinator, serial_number, device_info)
            cameras.append(camera)
            _LOGGER.debug("Added camera: %s", device_info.get("name"))

    async_add_entities(cameras)


class CloudEdgeCamera(CoordinatorEntity[CloudEdgeCoordinator], Camera):
    """Representation of a CloudEdge camera."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.ON_OFF

    def __init__(
        self,
        coordinator: CloudEdgeCoordinator,
        serial_number: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the camera."""
        super().__init__(coordinator)
        Camera.__init__(self)
        
        self._serial_number = serial_number
        self._device_info = device_info
        self._attr_unique_id = f"{DOMAIN}_{serial_number}_camera"
        self._attr_name = device_info.get("name", f"Camera {serial_number}")

    @property
    def device_info(self) -> dict[str, Any]:
        """Return device information."""
        return {
            "identifiers": {(DOMAIN, self._serial_number)},
            "name": self._device_info.get("name", f"Camera {self._serial_number}"),
            "manufacturer": "CloudEdge",
            "model": self._device_info.get("type", "SmartEye Camera"),
            "serial_number": self._serial_number,
            "sw_version": self._device_info.get("firmware_version"),
        }

    @property
    def available(self) -> bool:
        """Return if camera is available."""
        # Always available if coordinator has data, don't check device online status
        return self.coordinator.last_update_success and bool(self.coordinator.data)

    @property
    def is_on(self) -> bool:
        """Return true if camera is on."""
        device_data = self.coordinator.data.get(self._serial_number)
        if not device_data:
            return False
            
        return device_data.get("online", False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        device_data = self.coordinator.data.get(self._serial_number)
        if not device_data:
            return {}

        attributes = {
            "serial_number": self._serial_number,
            "device_type": device_data.get("type"),
            "host_key": device_data.get("host_key"),
            "last_seen": device_data.get("last_seen"),
        }

        # Add configuration parameters if available
        if config := device_data.get("configuration"):
            # Add some key configuration parameters
            for param_name, param_info in config.items():
                if param_name in [
                    "DEVICE_RESOLUTION",
                    "WIFI_STRENGTH", 
                    "BATTERY_PERCENT",
                    "MOTION_DET_ENABLE",
                    "FRONT_LIGHT_SWITCH",
                    "LED_ENABLE",
                ]:
                    attributes[param_name.lower()] = param_info.get("formatted", param_info.get("value"))

        return attributes

    async def async_turn_on(self) -> None:
        """Turn on camera."""
        # For CloudEdge cameras, "turning on" might mean enabling motion detection
        # or turning on the front light, depending on the device capabilities
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.client.set_device_parameter,
                self._device_info.get("name"),
                "MOTION_DET_ENABLE",
                1,
            )
            await self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error("Failed to turn on camera %s: %s", self._attr_name, e)

    async def async_turn_off(self) -> None:
        """Turn off camera."""
        # For CloudEdge cameras, "turning off" might mean disabling motion detection
        try:
            await self.hass.async_add_executor_job(
                self.coordinator.client.set_device_parameter,
                self._device_info.get("name"),
                "MOTION_DET_ENABLE",
                0,
            )
            await self.coordinator.async_request_refresh()
        except Exception as e:
            _LOGGER.error("Failed to turn off camera %s: %s", self._attr_name, e)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return bytes of camera image."""
        _LOGGER.debug("Camera image requested for %s - attempting to fetch snapshot", self._attr_name)

        # Attempt to fetch snapshot via multiple hints from device configuration
        device_data = self.coordinator.data.get(self._serial_number)
        if not device_data:
            _LOGGER.debug("No device data for %s", self._attr_name)
            return None

        config = device_data.get("configuration") or {}

        # Helper: attempt url fetch using client's session to reuse cookies/headers
        def _try_url(url: str) -> bytes | None:
            try:
                # Use coordinator client session if available
                session = getattr(self.coordinator.client, '_session', None)
                if session:
                    resp = session.get(url, timeout=10, verify=False)
                else:
                    import requests
                    resp = requests.get(url, timeout=10, verify=False)

                _LOGGER.debug("Snapshot request %s returned %d", url, getattr(resp, 'status_code', None))
                if getattr(resp, 'status_code', None) == 200:
                    # Ensure content is image/jpeg or at least non-empty
                    ct = resp.headers.get('Content-Type', '')
                    if 'image' in ct or resp.content:
                        return resp.content
                return None
            except Exception as e:
                _LOGGER.debug("Snapshot request failed for %s: %s", url, e)
                return None

        # Candidate: ONVIF URL (if present)
        onvif_url = None
        rtmp_url = None
        ip_address = None
        for code, info in config.items():
            pname = get_parameter_name(code)
            if pname == 'ONVIF_URL' and info and isinstance(info, dict):
                # Some config variants may have nested dicts
                onvif_url = info.get('value') if isinstance(info, dict) else info
            if pname == 'RTMP_STREAM' and info and isinstance(info, dict):
                rtmp_url = info.get('value') if isinstance(info, dict) else info
            if pname == 'IP_ADDRESS' and info and isinstance(info, dict):
                ip_address = info.get('value') if isinstance(info, dict) else info

        # If config holds simple string values instead of dicts
        if not onvif_url and config.get('123'):
            onvif_url = config.get('123')
        if not rtmp_url and config.get('130'):
            rtmp_url = config.get('130')
        if not ip_address and config.get('126'):
            ip_address = config.get('126')

        # Try ONVIF URL first if it looks like an HTTP endpoint
        if onvif_url and isinstance(onvif_url, str) and onvif_url.lower().startswith(('http://', 'https://')):
            _LOGGER.debug("Attempting ONVIF URL snapshot: %s", onvif_url)
            result = _try_url(onvif_url)
            if result:
                return result

        # Try RTMP/HTTP streams if they contain http(s) for snapshot thumbnail
        if rtmp_url and isinstance(rtmp_url, str) and rtmp_url.lower().startswith(('http://', 'https://')):
            _LOGGER.debug("Attempting RTMP/HTTP snapshot: %s", rtmp_url)
            result = _try_url(rtmp_url)
            if result:
                return result

        # Try common snapshot CGI endpoints on the device's IP address
        if ip_address:
            candidates = [
                f"http://{ip_address}/cgi-bin/snapshot.jpg",
                f"http://{ip_address}/snapshot.jpg",
                f"http://{ip_address}/capture.jpg",
                f"http://{ip_address}/cgi-bin/snapshot.cgi",
                f"http://{ip_address}/cgi-bin/jpg/image.cgi",
            ]
            for url in candidates:
                _LOGGER.debug("Trying candidate snapshot URL: %s", url)
                result = _try_url(url)
                if result:
                    return result

        # Fallback: if the device_info contains a deviceImg or deviceTypeName with an HTTP URL, try that
        # Some backends incorrectly return an image URL under deviceTypeName or deviceImg
        device_img = device_data.get('deviceImg') or device_data.get('deviceImg')
        device_type_name = device_data.get('deviceTypeName')
        for candidate in (device_img, device_type_name):
            if isinstance(candidate, str) and candidate.lower().startswith(('http://', 'https://')):
                _LOGGER.debug("Attempting fallback device image URL: %s", candidate)
                result = _try_url(candidate)
                if result:
                    return result

        _LOGGER.debug("No snapshot available for %s", self._attr_name)
        return None