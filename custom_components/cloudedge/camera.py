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
        """Return bytes of camera image.
        
        CloudEdge/Meari cameras use TUTK P2P protocol and do NOT have HTTP snapshot endpoints.
        We prioritize alarm event thumbnails from the cloud API, which are the most reliable source.
        """
        _LOGGER.debug("Camera image requested for %s", self._attr_name)

        device_data = self.coordinator.data.get(self._serial_number)
        if not device_data:
            _LOGGER.debug("No device data for %s", self._attr_name)
            return None

        # Helper: fetch URL with short timeout (cloud URLs should be fast)
        def _do_get(url: str, timeout: int = 5):
            try:
                session = getattr(self.coordinator.client, '_session', None)
                if session:
                    resp = session.get(url, timeout=timeout, verify=False)
                else:
                    import requests
                    resp = requests.get(url, timeout=timeout, verify=False)
                return resp
            except Exception as e:
                _LOGGER.debug("HTTP request to %s failed: %s", url, e)
                return None

        async def _try_url(url: str, timeout: int = 5) -> bytes | None:
            """Try to fetch an image from a URL."""
            try:
                resp = await self.hass.async_add_executor_job(_do_get, url, timeout)
                if not resp:
                    return None
                _LOGGER.debug("GET %s -> %s", url, getattr(resp, 'status_code', None))
                if getattr(resp, 'status_code', None) == 200:
                    content = resp.content
                    if content and len(content) > 100:  # Sanity check for actual image data
                        ct = resp.headers.get('Content-Type', '')
                        if 'image' in ct or content[:3] in (b'\xff\xd8\xff', b'\x89PN', b'GIF'):
                            _LOGGER.debug("Got valid image (%d bytes) from %s", len(content), url)
                            return content
                return None
            except Exception as e:
                _LOGGER.debug("Error fetching %s: %s", url, e)
                return None

        # === PRIORITY 1: Latest alarm event image from cloud API ===
        # This is the MOST RELIABLE source - fetched directly from Meari cloud alarm API
        device_id = device_data.get('device_id')
        if device_id:
            try:
                _LOGGER.debug("Fetching latest alarm image for device %s (ID: %s)", self._attr_name, device_id)
                alarm_image = await self.hass.async_add_executor_job(
                    self.coordinator.client.get_latest_alarm_image,
                    device_id,
                    self._serial_number
                )
                if alarm_image and len(alarm_image) > 100:
                    # Validate it's an image (JPEG starts with FFD8, PNG with 89 50 4E 47)
                    if alarm_image[:2] == b'\xff\xd8' or alarm_image[:4] == b'\x89PNG':
                        _LOGGER.debug("Got valid alarm image (%d bytes) for %s", len(alarm_image), self._attr_name)
                        return alarm_image
                    _LOGGER.debug("Alarm image data is not valid JPEG/PNG format")
            except Exception as e:
                _LOGGER.debug("Failed to get alarm image for %s: %s", self._attr_name, e)

        # === PRIORITY 2: Cloud-stored thumbnail URL from device data ===
        # Fallback to stored thumbnail URL from API response
        thumbnail_url = device_data.get('thumbnail_url')
        if thumbnail_url and isinstance(thumbnail_url, str) and thumbnail_url.startswith(('http://', 'https://')):
            _LOGGER.debug("Trying cloud thumbnail URL: %s", thumbnail_url[:80])
            result = await _try_url(thumbnail_url)
            if result:
                return result

        # === PRIORITY 3: Check device_info for any image URLs ===
        # Some API responses include image URLs under various field names
        for key in ['deviceImg', 'coverImgUrl', 'thumbUrl', 'imageUrl', 'alarmImgUrl', 'lastAlarmUrl']:
            url = device_data.get(key)
            if url and isinstance(url, str) and url.startswith(('http://', 'https://')):
                _LOGGER.debug("Trying device info URL (%s): %s", key, url[:80])
                result = await _try_url(url)
                if result:
                    return result

        # === PRIORITY 4: Check configuration for ONVIF/RTSP URLs ===
        # Some devices may expose ONVIF or RTSP URLs that could have snapshot endpoints
        config = device_data.get("configuration") or {}
        
        # Look for ONVIF URL (parameter code 123)
        onvif_url = None
        for code, info in config.items():
            pname = get_parameter_name(code)
            if pname == 'ONVIF_URL':
                onvif_url = info.get('value') if isinstance(info, dict) else info
                break
        if not onvif_url:
            onvif_url = config.get('123')
        
        if onvif_url and isinstance(onvif_url, str) and onvif_url.startswith(('http://', 'https://')):
            _LOGGER.debug("Trying ONVIF URL: %s", onvif_url)
            result = await _try_url(onvif_url)
            if result:
                return result

        # === NO LOCAL IP ATTEMPTS ===
        # CloudEdge/Meari cameras use TUTK P2P protocol and do NOT serve HTTP on their IP.
        # Attempting local IPs just wastes time with connection timeouts.
        # If you need live streaming, you would need to use RTSP with go2rtc or similar.

        _LOGGER.debug("No snapshot available for %s - device uses P2P protocol without HTTP endpoints", self._attr_name)
        return None