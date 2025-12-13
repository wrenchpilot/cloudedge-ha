"""Camera platform for CloudEdge integration."""
from __future__ import annotations

import asyncio
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
        def _handle_coordinator_update():
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

        # === PRIORITY 3.5: Cloud snapshot API endpoint ===
        # Some regions/clouds provide a direct snapshot/preview API (non-P2P).
        # Try it here before attempting ONVIF or P2P.
        if device_id:
            try:
                _LOGGER.debug("Attempting cloud snapshot API for %s (ID: %s)", self._attr_name, device_id)
                cloud_snapshot = await self.hass.async_add_executor_job(
                    self.coordinator.client.get_device_snapshot,
                    device_id,
                    self._serial_number,
                )
                if cloud_snapshot and len(cloud_snapshot) > 100:
                    if cloud_snapshot[:2] == b'\xff\xd8' or cloud_snapshot[:4] == b'\x89PNG':
                        _LOGGER.debug("Got valid cloud snapshot (%d bytes) for %s", len(cloud_snapshot), self._attr_name)
                        return cloud_snapshot
            except Exception as e:
                _LOGGER.debug("Cloud snapshot API failed for %s: %s", self._attr_name, e)

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
        
        # === PRIORITY 5: P2P snapshot (wake device, connect via P2P, capture frame) ===
        # Only attempt P2P for cameras that support it (not cloud-only AWS IoT cameras)
        device_ip = device_data.get('device_ip')
        iot_type = device_data.get('iot_type') or device_data.get('iotType')
        cloud_support = device_data.get('cloud_support') or device_data.get('cloudSupport', 0)
        
        # Check if P2P is likely to work:
        # - iotType 3 with awsCloudCompat=1 are cloud-only (no direct P2P)
        # - Devices without cloud support AND iotType 3 have no snapshot capability
        aws_cloud_compat = device_data.get('aws_cloud_compat') or device_data.get('awsCloudCompat', 0)
        is_cloud_only = (iot_type == 3 and aws_cloud_compat == 1)
        
        if is_cloud_only and cloud_support == 0:
            _LOGGER.debug(
                "No snapshot available for %s - cloud-only camera (iotType=%s) without cloud subscription",
                self._attr_name, iot_type
            )
            return None
        elif is_cloud_only and cloud_support == 1:
            _LOGGER.debug(
                "Camera %s uses cloud-mediated P2P (iotType=%s) - attempting cloud snapshot APIs rather than P2P",
                self._attr_name, iot_type
            )

        
        # Attempt P2P snapshot (direct LAN or cloud-mediated wake) if we have a device ID
        p2p_tried = False
        if device_id and device_ip:
            p2p_tried = True
            _LOGGER.debug("Attempting P2P snapshot for %s (ID: %s, IP: %s)", 
                         self._attr_name, device_id, device_ip)
            try:
                p2p_snapshot = await self._get_p2p_snapshot(device_id, device_ip, device_data)
                if p2p_snapshot and len(p2p_snapshot) > 100:
                    if p2p_snapshot[:2] == b'\xff\xd8' or p2p_snapshot[:4] == b'\x89PNG':
                        _LOGGER.debug("Got valid P2P snapshot (%d bytes) for %s", 
                                     len(p2p_snapshot), self._attr_name)
                        return p2p_snapshot
            except Exception as e:
                _LOGGER.debug("P2P snapshot failed for %s: %s", self._attr_name, e)

        # If we haven't yet tried P2P (no LAN IP), attempt cloud-mediated P2P via wake/connect
        if device_id and not p2p_tried:
            try:
                _LOGGER.debug("Attempting cloud-mediated P2P snapshot for %s (ID: %s)", self._attr_name, device_id)
                p2p_snapshot = await self._get_p2p_snapshot(device_id, device_ip, device_data)
                if p2p_snapshot and len(p2p_snapshot) > 100:
                    if p2p_snapshot[:2] == b'\xff\xd8' or p2p_snapshot[:4] == b'\x89PNG':
                        _LOGGER.debug("Got valid cloud-mediated P2P snapshot (%d bytes) for %s", len(p2p_snapshot), self._attr_name)
                        return p2p_snapshot
            except Exception as e:
                _LOGGER.debug("Cloud-mediated P2P snapshot failed for %s: %s", self._attr_name, e)

        _LOGGER.debug("No snapshot available for %s - all methods exhausted", self._attr_name)
        return None

    async def _get_p2p_snapshot(
        self, 
        device_id: str, 
        device_ip: str,
        device_data: dict[str, Any]
    ) -> bytes | None:
        """
        Get snapshot via P2P connection.
        
        This attempts to connect via P2P protocol and capture a video frame.
        Note: Many CloudEdge cameras (especially iotType=3) don't support direct P2P
        and require cloud-mediated connections that we can't replicate.
        
        Returns:
            JPEG image bytes if successful, None otherwise.
        """
        # Quick connectivity check - if camera responds to UDP probe, prefer direct P2P
        p2p_reachable = await self._check_p2p_reachable(device_ip)
        
        try:
            # If direct P2P reachable, try aiopppp library for direct P2P connection
            if p2p_reachable:
                p2p_snapshot = await self._capture_via_aiopppp(device_ip, {}, device_data)
                if p2p_snapshot:
                    return p2p_snapshot
        except Exception as e:
            _LOGGER.debug("P2P snapshot error for %s: %s", self._attr_name, e)

        # If direct P2P failed or not reachable, try cloud-mediated P2P via wake_device
        try:
            wake_result = await self.hass.async_add_executor_job(
                self.coordinator.client.wake_device,
                device_id,
            )
        except Exception as e:
            _LOGGER.debug("Wake device call failed for %s: %s", self._attr_name, e)
            wake_result = None

        if not wake_result:
            _LOGGER.debug("No wake result for %s, cannot attempt cloud-mediated P2P", self._attr_name)
            return None

        connect_params = wake_result.get('connect_string') or wake_result.get('connect_string_raw')
        if isinstance(connect_params, str):
            try:
                import json
                connect_params = json.loads(connect_params)
            except Exception:
                connect_params = None

        if not connect_params:
            _LOGGER.debug("Wake result did not include connect_string for %s", self._attr_name)
            return None

        # Create a P2P client from connect params and attempt to capture snapshot via relay
        try:
            from .tools.cloudedge_p2p_client import CloudEdgeP2PClient

            # Instantiate client using parsed connect params
            p2p_client = CloudEdgeP2PClient.from_connect_string(connect_params, camera_ip=device_ip, debug=self.coordinator.client.debug)

            # Connect (this is blocking) via executor
            connected = await self.hass.async_add_executor_job(p2p_client.connect_after_wake, 15.0)
            if not connected:
                _LOGGER.debug("Cloud P2P client failed to connect for %s", self._attr_name)
                p2p_client.close()
                return None

            # Request snapshot synchronously via executor
            snapshot = await self.hass.async_add_executor_job(p2p_client.request_snapshot)
            p2p_client.close()

            if snapshot and len(snapshot) > 100:
                return snapshot
            return None
        except Exception as e:
            _LOGGER.debug("Cloud-mediated P2P snapshot failed for %s: %s", self._attr_name, e)
            return None

    async def _check_p2p_reachable(self, device_ip: str, timeout: float = 2.0) -> bool:
        """
        Quick check if camera responds to P2P discovery protocol.
        
        Sends a PPPP LanSearch packet and waits for response.
        Returns True if camera responds, False otherwise.
        """
        import socket
        import struct
        
        def _do_probe():
            try:
                # PPPP LanSearch packet (magic 0xF1, type 0x01)
                packet = struct.pack('>BBHH', 0xF1, 0x01, 0, 0)
                
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(timeout)
                sock.bind(('0.0.0.0', 0))
                
                try:
                    # Try standard P2P discovery port
                    sock.sendto(packet, (device_ip, 32108))
                    data, addr = sock.recvfrom(1024)
                    return len(data) > 0
                except socket.timeout:
                    return False
                finally:
                    sock.close()
            except Exception:
                return False
        
        return await self.hass.async_add_executor_job(_do_probe)

    async def _capture_via_aiopppp(
        self,
        device_ip: str,
        connect_params: dict[str, Any],
        device_data: dict[str, Any]
    ) -> bytes | None:
        """
        Capture snapshot using aiopppp library.
        
        aiopppp is an async Python implementation of the PPPP P2P protocol
        used by CloudEdge/Meari cameras.
        """
        try:
            from aiopppp import Device
        except ImportError:
            _LOGGER.debug("aiopppp library not available for P2P snapshot")
            return None
            
        try:
            # Get credentials from connect params or use defaults
            username = connect_params.get('username', 'admin')
            password = connect_params.get('password') or device_data.get('host_key', '6666')
            
            _LOGGER.debug("Connecting to %s via aiopppp (user: %s)", device_ip, username)
            
            async with Device(device_ip, username=username, password=password) as device:
                _LOGGER.debug("P2P connected, starting video stream")
                
                await device.start_video()
                
                try:
                    # Wait for a video frame (with timeout)
                    frame = await asyncio.wait_for(
                        device.get_video_frame(), 
                        timeout=10.0
                    )
                    
                    if frame and frame.data:
                        _LOGGER.debug("Got video frame: %d bytes", len(frame.data))
                        # Convert H.264 frame to JPEG
                        return await self._convert_h264_to_jpeg(frame.data)
                        
                except asyncio.TimeoutError:
                    _LOGGER.debug("Timeout waiting for video frame")
                    return None
                finally:
                    await device.stop_video()
                    
        except Exception as e:
            _LOGGER.debug("aiopppp capture error: %s", e)
            return None

    async def _convert_h264_to_jpeg(self, h264_data: bytes) -> bytes | None:
        """
        Convert H.264 frame data to JPEG image.
        
        Uses PyAV (av) library if available, falls back to ffmpeg subprocess.
        """
        # Try PyAV first
        try:
            import av
            import io
            
            def _decode_with_av():
                container = av.open(io.BytesIO(h264_data), format='h264')
                for frame in container.decode(video=0):
                    # Convert to JPEG
                    output = io.BytesIO()
                    frame.to_image().save(output, format='JPEG', quality=85)
                    return output.getvalue()
                return None
            
            result = await self.hass.async_add_executor_job(_decode_with_av)
            if result:
                return result
                
        except ImportError:
            _LOGGER.debug("PyAV not available, trying ffmpeg")
        except Exception as e:
            _LOGGER.debug("PyAV decode error: %s", e)
        
        # Fallback to ffmpeg subprocess
        try:
            import subprocess
            import tempfile
            import os
            
            def _decode_with_ffmpeg():
                with tempfile.NamedTemporaryFile(suffix='.h264', delete=False) as h264_file:
                    h264_file.write(h264_data)
                    h264_path = h264_file.name
                    
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as jpg_file:
                    jpg_path = jpg_file.name
                
                try:
                    subprocess.run([
                        'ffmpeg', '-y',
                        '-f', 'h264',
                        '-i', h264_path,
                        '-frames:v', '1',
                        '-q:v', '2',
                        jpg_path
                    ], capture_output=True, timeout=10)
                    
                    if os.path.exists(jpg_path) and os.path.getsize(jpg_path) > 0:
                        with open(jpg_path, 'rb') as f:
                            return f.read()
                finally:
                    for path in [h264_path, jpg_path]:
                        try:
                            os.unlink(path)
                        except:
                            pass
                return None
                
            return await self.hass.async_add_executor_job(_decode_with_ffmpeg)
            
        except Exception as e:
            _LOGGER.debug("ffmpeg decode error: %s", e)
            
        return None