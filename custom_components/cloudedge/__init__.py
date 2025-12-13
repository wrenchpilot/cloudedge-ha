"""
CloudEdgeintegration for Home Assistant.

This integration provides support forCloudEdge cameras and IoT devices,
allowing you to monitor and control your devices through Home Assistant.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import timedelta
from typing import Dict, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_COUNTRY_CODE,
    CONF_PHONE_CODE,
    CONF_REFRESH_INTERVAL,
    CONF_REGION,
    CONF_BASE_URL,
    CONF_OPENAPI_BASE_URL,
    CONF_DISABLE_P2P,
    DEFAULT_REFRESH_INTERVAL,
    DEFAULT_REGION,
)
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CloudEdge from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Get configuration
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    country_code = entry.data[CONF_COUNTRY_CODE]
    phone_code = entry.data[CONF_PHONE_CODE]
    refresh_interval = entry.data.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)
    region = entry.data.get(CONF_REGION, DEFAULT_REGION)
    base_url = entry.data.get(CONF_BASE_URL)
    openapi_base_url = entry.data.get(CONF_OPENAPI_BASE_URL)

    # Create coordinator
    coordinator = CloudEdgeCoordinator(
        hass, username, password, country_code, phone_code, refresh_interval, entry
    )

    # Validate authentication before first refresh
    await coordinator.async_validate_authentication()

    # Fetch initial data with error handling
    success = await coordinator.async_safe_first_refresh()
    if not success:
        _LOGGER.warning("Initial setup had issues, integration will retry on next update")

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms even if initial refresh failed
    # This allows the integration to load and retry later
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Set up services
    await async_setup_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload services
    await async_unload_services(hass)
    
    # Clean up cache when unloading (disable or remove integration)
    if entry.entry_id in hass.data[DOMAIN]:
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await hass.async_add_executor_job(coordinator.cleanup_cache)
    
    # Also clean up old cache file variants
    for cache_name in [".cloudedge_session_cache", "cloudedge_session_cache"]:
        cache_path = os.path.join(hass.config.config_dir, cache_name)
        if os.path.exists(cache_path):
            try:
                await hass.async_add_executor_job(os.remove, cache_path)
                _LOGGER.debug("Removed cache file: %s", cache_path)
            except OSError as e:
                _LOGGER.debug("Could not remove cache file %s: %s", cache_path, e)
    
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry and clean up associated files."""
    _LOGGER.info("Removing CloudEdge integration and cleaning up cache files")
    
    # Clean up both cache file variants (old and new)
    for cache_name in [".cloudedge_session_cache", "cloudedge_session_cache"]:
        cache_path = os.path.join(hass.config.config_dir, cache_name)
        if os.path.exists(cache_path):
            try:
                await hass.async_add_executor_job(os.remove, cache_path)
                _LOGGER.info("Removed CloudEdge session cache: %s", cache_path)
            except OSError as e:
                _LOGGER.warning("Failed to remove cache file %s: %s", cache_path, e)



class CloudEdgeCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from CloudEdge API."""

    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
        country_code: str,
        phone_code: str,
        refresh_interval: int,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        self.username = username
        self.password = password
        self.country_code = country_code
        self.phone_code = phone_code
        self.config_entry = config_entry
        self.client = None
        self._authenticated = False
        self._setup_complete = False
        self._last_updated_device = None  # Track which device was last updated
        
        # Initialize data as empty dict to prevent None errors
        self.data: Dict[str, Any] = {}

        _LOGGER.info(
            "Initializing CloudEdge coordinator with %d minute refresh interval",
            refresh_interval,
        )
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=refresh_interval),
        )

    async def async_validate_authentication(self):
        """Validate authentication during coordinator setup."""
        _LOGGER.debug("Validating CloudEdge authentication")
        try:
            # Import here to avoid import issues during startup
            from .cloudedge import CloudEdgeClient
            from .cloudedge.exceptions import AuthenticationError

            if self.client is None:
                _LOGGER.debug("Initializing CloudEdge client for validation")
                # Use config directory for session cache
                cache_path = os.path.join(self.hass.config.config_dir, "cloudedge_session_cache")
                # Sanity check credentials are present - fail early if not
                if not self.username or not self.password:
                    _LOGGER.error("CloudEdge credentials missing: username or password not set in config entry")
                    raise AuthenticationError("Missing credentials in config entry")
                self.client = CloudEdgeClient(
                    username=self.username,
                    password=self.password,
                    country_code=self.country_code,
                    phone_code=self.phone_code,
                    debug=False,  # Enable debug logging
                    session_cache_file=cache_path,
                    region=(self.config_entry.data.get(CONF_REGION) if self.config_entry.data.get(CONF_REGION) != "AUTO" else None),
                    base_url=self.config_entry.data.get(CONF_BASE_URL),
                    openapi_base_url=self.config_entry.data.get(CONF_OPENAPI_BASE_URL),
                    disable_p2p=self.config_entry.data.get(CONF_DISABLE_P2P, True),
                )
                _LOGGER.debug(
                    "CloudEdge client initialized for validation for user %s (region=%s, base_url=%s)",
                    self.username,
                    self.config_entry.data.get(CONF_REGION),
                    self.config_entry.data.get(CONF_BASE_URL),
                )

            # Check if we have valid session data
            if self.client.session_data:
                session_time = self.client.session_data.get('loginTime', 0)
                current_time = int(time.time())
                session_age = current_time - session_time
                
                if session_age < 86400:  # Less than 24 hours
                    _LOGGER.debug("Found valid cached session (%d hours old)", session_age // 3600)
                    self._authenticated = True
                    return
                else:
                    _LOGGER.debug("Cached session expired (%d hours old), will re-authenticate", session_age // 3600)

            # Perform initial authentication
            _LOGGER.debug("Performing initial authentication")
            success = await self.hass.async_add_executor_job(self.client.authenticate)
            if success:
                self._authenticated = True
                _LOGGER.info("Initial CloudEdge authentication successful")
            else:
                self._authenticated = False
                raise AuthenticationError("Initial authentication failed")
                
        except Exception as auth_error:
            _LOGGER.error("Initial authentication validation failed: %s", auth_error)
            self._authenticated = False
            raise AuthenticationError(f"Authentication validation failed: {auth_error}")

    async def _async_update_data(self) -> Dict[str, Any]:
        """Update data via library."""
        _LOGGER.debug("Starting data update cycle")
        try:
            # Add timeout to prevent hanging during Home Assistant startup/shutdown
            result = await asyncio.wait_for(
                self.hass.async_add_executor_job(self._fetch_data),
                timeout=60.0  # 60 second timeout
            )
            _LOGGER.debug("Data update cycle completed successfully")
            return result
        except asyncio.CancelledError:
            _LOGGER.warning("Data update was cancelled (Home Assistant shutting down?)")
            # Re-raise CancelledError to properly handle shutdown
            raise
        except asyncio.TimeoutError:
            _LOGGER.error("Data update timed out after 60 seconds")
            raise UpdateFailed("CloudEdge API timeout - check network connectivity")
        except Exception as exception:
            _LOGGER.error("Data update failed: %s", exception)
            # Reset authentication on certain errors to force re-auth on next update
            if "authentication" in str(exception).lower():
                self._authenticated = False
            raise UpdateFailed(f"Error communicating with API: {exception}") from exception

    def _fetch_data(self) -> Dict[str, Any]:
        """Fetch data from CloudEdge API."""
        start_time = time.time()
        _LOGGER.debug("Starting CloudEdge API data fetch")
        
        # Clear last updated device flag on full refresh
        self._last_updated_device = None
        
        # Import here to avoid import issues during startup
        from .cloudedge import CloudEdgeClient
        from .cloudedge.exceptions import AuthenticationError, CloudEdgeError

        try:
            if self.client is None:
                _LOGGER.debug("Initializing CloudEdge client")
                # Use config directory for session cache
                cache_path = os.path.join(self.hass.config.config_dir, "cloudedge_session_cache")
                # Sanity check credentials are present - fail early if not
                if not self.username or not self.password:
                    _LOGGER.error("CloudEdge credentials missing: username or password not set in config entry")
                    raise AuthenticationError("Missing credentials in config entry")
                self.client = CloudEdgeClient(
                    username=self.username,
                    password=self.password,
                    country_code=self.country_code,
                    phone_code=self.phone_code,
                    debug=False,  # Enable debug logging
                    session_cache_file=cache_path,
                    # Pass region and URL overrides from the config entry to ensure consistency
                    region=(self.config_entry.data.get(CONF_REGION) if self.config_entry.data.get(CONF_REGION) != "AUTO" else None),
                    base_url=self.config_entry.data.get(CONF_BASE_URL),
                    openapi_base_url=self.config_entry.data.get(CONF_OPENAPI_BASE_URL),
                    disable_p2p=self.config_entry.data.get(CONF_DISABLE_P2P, True),
                )
                _LOGGER.debug(
                    "CloudEdge client initialized for user %s (region=%s, base_url=%s)",
                    self.username,
                    self.config_entry.data.get(CONF_REGION),
                    self.config_entry.data.get(CONF_BASE_URL),
                )

            # Always validate and authenticate before making API calls
            auth_needed = False
            
            if not self._authenticated:
                _LOGGER.debug("No existing authentication, will authenticate")
                auth_needed = True
            else:
                # Check if existing session is still valid
                _LOGGER.debug("Checking existing session validity")
                if not self.client.session_data:
                    _LOGGER.debug("No session data found, re-authentication needed")
                    auth_needed = True
                    self._authenticated = False
                else:
                    # Check session expiration (24 hours)
                    session_time = self.client.session_data.get('loginTime', 0)
                    current_time = int(time.time())
                    session_age = current_time - session_time
                    
                    if session_age > 86400:  # 24 hours
                        _LOGGER.debug("Session expired (%d hours old), re-authentication needed", session_age // 3600)
                        auth_needed = True
                        self._authenticated = False
                    else:
                        _LOGGER.debug("Session is valid (%d hours old)", session_age // 3600)

            # Authenticate if needed
            if auth_needed:
                _LOGGER.debug("Authenticating with CloudEdge API")
                try:
                    success = self.client.authenticate()
                    if not success:
                        raise AuthenticationError("Authentication failed")
                    self._authenticated = True
                    _LOGGER.info("Successfully authenticated with CloudEdge API")
                except Exception as auth_error:
                    _LOGGER.error("Authentication failed: %s", auth_error)
                    # Reset authentication state
                    self._authenticated = False
                    self.client.session_data = None
                    raise AuthenticationError(f"Authentication failed: {auth_error}")

            # Get all devices from all homes
            _LOGGER.debug("Fetching device data from CloudEdge API")
            try:
                devices = self.client.get_all_devices()
                _LOGGER.debug("Raw devices from API: %s", devices)
            except AuthenticationError as auth_error:
                _LOGGER.warning("Authentication error during device fetch, retrying with fresh auth: %s", auth_error)
                # Reset authentication and retry once
                self._authenticated = False
                self.client.session_data = None
                
                success = self.client.authenticate()
                if not success:
                    raise AuthenticationError("Re-authentication failed")
                self._authenticated = True
                _LOGGER.info("Re-authenticated successfully, retrying device fetch")
                
                # Retry device fetch
                devices = self.client.get_all_devices()
            except Exception as device_error:
                _LOGGER.error("Failed to get device list: %s", device_error)
                raise CloudEdgeError(f"Failed to get device list: {device_error}")
            
            # Get detailed information for each device
            device_data = {}
            for device in devices:
                try:
                    _LOGGER.debug("Fetching detailed info for device: %s (SN: %s)", 
                                device.get("name"), device.get("serial_number"))
                    
                    # Get basic device info without config
                    device_info = self.client.get_device_info(
                        device["name"], include_config=False
                    )
                    
                    if device_info:
                        serial_number = device["serial_number"]
                        
                        # Now get configuration separately with better error handling
                        try:
                            config_response = self.client.get_device_config(serial_number)
                            if config_response:
                                # Try different response structures
                                iot_data = None
                                
                                if 'result' in config_response and 'iot' in config_response['result']:
                                    iot_data = config_response['result']['iot']
                                elif 'iot' in config_response:
                                    iot_data = config_response['iot']
                                elif isinstance(config_response, dict) and any(key.isdigit() for key in config_response.keys()):
                                    iot_data = config_response
                                
                                if iot_data and isinstance(iot_data, dict):
                                    # Process IoT parameters
                                    from .cloudedge.iot_parameters import get_parameter_name, format_parameter_value
                                    
                                    processed_config = {}
                                    for param_code, value in iot_data.items():
                                        param_name = get_parameter_name(param_code)
                                        formatted_value = format_parameter_value(param_name, value)
                                        processed_config[param_code] = {
                                            'name': param_name,
                                            'code': param_code,
                                            'value': value,
                                            'formatted': formatted_value
                                        }
                                    
                                    device_info['configuration'] = processed_config
                                    _LOGGER.info("Device %s has %d parameters", device["name"], len(processed_config))
                                else:
                                    device_info['configuration'] = {}
                                    _LOGGER.debug("Device %s has no IoT data in config response", device["name"])
                            else:
                                device_info['configuration'] = {}
                                _LOGGER.debug("Device %s config response was empty", device["name"])
                        except Exception as config_error:
                            _LOGGER.debug("Could not get config for device %s: %s", device["name"], config_error)
                            device_info['configuration'] = {}
                        
                        device_data[serial_number] = device_info
                        
                        # Debug: Log configuration data structure
                        config = device_info.get("configuration", {})
                        if config:
                            _LOGGER.debug(
                                "Device %s has %d configuration parameters: %s",
                                device["name"],
                                len(config),
                                list(config.keys())[:10] if config else "None"  # Show first 10 parameter codes
                            )
                        else:
                            # Some devices (like chimes, doorbells) don't have configuration parameters
                            # This is normal, so just log at debug level
                            device_type = device_info.get("type", "Unknown")
                            _LOGGER.debug(
                                "Device %s (%s) has no configuration parameters (this is normal for some device types)",
                                device["name"],
                                device_type
                            )
                        
                        _LOGGER.debug(
                            "Retrieved data for device %s (%s)",
                            device["name"],
                            device["serial_number"],
                        )
                except Exception as e:
                    _LOGGER.warning(
                        "Failed to get detailed info for device %s: %s",
                        device["name"],
                        e,
                    )
                    # Add basic device info even if detailed info fails
                    device_data[device["serial_number"]] = device

            fetch_time = time.time() - start_time
            _LOGGER.info(
                "Successfully fetched data for %d devices in %.2f seconds",
                len(device_data),
                fetch_time,
            )
            return device_data

        except AuthenticationError as e:
            _LOGGER.error("Authentication failed: %s", e)
            self._authenticated = False
            raise UpdateFailed(f"Authentication failed: {e}")
        except CloudEdgeError as e:
            _LOGGER.error("CloudEdge API error: %s", e)
            raise UpdateFailed(f"CloudEdge API error: {e}")
        except Exception as e:
            _LOGGER.error("Unexpected error: %s", e)
            raise UpdateFailed(f"Unexpected error: {e}")

    @property
    def refresh_interval_minutes(self) -> int:
        """Return the refresh interval in minutes."""
        return int(self.update_interval.total_seconds() / 60) if self.update_interval else 0

    async def async_refresh_device_config(self, device_name: str) -> bool:
        """
        Refresh configuration for a specific device without affecting other devices.
        
        Args:
            device_name (str): Name of the device to refresh
            
        Returns:
            bool: True if successful, False otherwise
        """
        _LOGGER.debug("Refreshing configuration for device: %s", device_name)
        
        try:
            # Find the device in coordinator data
            device = None
            device_sn = None
            
            for sn, device_data in self.data.items():
                if device_data.get('name') == device_name:
                    device = device_data
                    device_sn = sn
                    break
            
            if not device or not device_sn:
                _LOGGER.error("Device %s not found in coordinator data", device_name)
                return False
            
            # Get fresh device configuration from API
            try:
                config = await self.hass.async_add_executor_job(
                    self.client.get_device_config, device_sn
                )
                _LOGGER.info("get_device_config returned for %s: %s", device_name, "data" if config else "None")
                if config and isinstance(config, dict):
                        # Log the keys we received for debug
                        try:
                            if 'result' in config and isinstance(config['result'], dict) and 'iot' in config['result']:
                                _LOGGER.debug("Config keys for %s: %s", device_name, list(config['result']['iot'].keys()))
                            elif 'iot' in config and isinstance(config['iot'], dict):
                                _LOGGER.debug("Config keys for %s: %s", device_name, list(config['iot'].keys()))
                            else:
                                _LOGGER.debug("Config top-level keys for %s: %s", device_name, list(config.keys()))
                                if 'result' in config and not config.get('result'):
                                    _LOGGER.debug("config.result is empty; top-level keys: %s", list(config.keys()))
                        except Exception:
                            pass
            except Exception as e:
                _LOGGER.error("get_device_config failed for %s: %s (type: %s)", 
                            device_name, str(e), type(e).__name__)
                config = None
            
            if config:
                # Try different response structures
                iot_data = None
                
                if 'result' in config and 'iot' in config['result']:
                    iot_data = config['result']['iot']
                elif 'iot' in config:
                    iot_data = config['iot']
                elif isinstance(config, dict) and any(key.isdigit() for key in config.keys()):
                    iot_data = config
                
                if iot_data and isinstance(iot_data, dict):
                    # Process IoT parameters for display
                    from .cloudedge.iot_parameters import get_parameter_name, format_parameter_value
                    
                    processed_config = {}
                    for param_code, value in iot_data.items():
                        param_name = get_parameter_name(param_code)
                        formatted_value = format_parameter_value(param_name, value)
                        # Use parameter CODE as key, not parameter name
                        processed_config[param_code] = {
                            'name': param_name,
                            'code': param_code,
                            'value': value,
                            'formatted': formatted_value
                        }
                    
                    # Check if this device previously had no configuration
                    had_no_config = not self.data[device_sn].get('configuration')
                    
                    # Update only this device's configuration
                    self.data[device_sn]['configuration'] = processed_config
                    
                    _LOGGER.info("Successfully refreshed %d parameters for device %s", 
                               len(processed_config), device_name)
                    
                    # Notify existing entities about the update
                    self.async_update_listeners()
                    
                    # If device previously had no configuration, user needs to reload manually
                    if had_no_config and processed_config:
                        _LOGGER.warning(
                            "Device %s now has %d parameters (previously had none). "
                            "Please RELOAD the integration from the UI to see all sensor entities.",
                            device_name, len(processed_config)
                        )
                    
                    return True
                else:
                    _LOGGER.warning("No valid IoT data found for device %s", device_name)
                    return False
            else:
                _LOGGER.warning("No configuration data received for device %s", device_name)
                return False
                
        except Exception as e:
            _LOGGER.error("Error refreshing device %s configuration: %s", device_name, e)
            return False

    def get_coordinator_info(self) -> Dict[str, Any]:
        """Get coordinator diagnostic information."""
        return {
            "refresh_interval_minutes": self.refresh_interval_minutes,
            "last_update_success": self.last_update_success,
            "last_update_time": self.last_update_success_time,
            "authenticated": self._authenticated,
            "device_count": len(self.data) if self.data else 0,
            "update_interval_seconds": self.update_interval.total_seconds() if self.update_interval else None,
        }
    
    def cleanup_cache(self) -> None:
        """Clean up session cache file."""
        if self.client and hasattr(self.client, 'session_cache_file'):
            cache_file = self.client.session_cache_file
            if os.path.exists(cache_file):
                try:
                    os.remove(cache_file)
                    _LOGGER.info("Cleaned up CloudEdge session cache: %s", cache_file)
                except OSError as e:
                    _LOGGER.warning("Failed to remove cache file %s: %s", cache_file, e)

    async def async_safe_first_refresh(self) -> bool:
        """Perform first refresh with safety handling."""
        try:
            # Give a bit more time for initial setup
            _LOGGER.info("Performing initial CloudEdge data fetch...")
            await asyncio.wait_for(
                self.async_config_entry_first_refresh(),
                timeout=90.0  # 90 seconds for initial setup
            )
            _LOGGER.info("Initial CloudEdge setup completed successfully")
            return True
        except asyncio.TimeoutError:
            _LOGGER.warning("Initial refresh timed out - will retry on next cycle")
            return False
        except asyncio.CancelledError:
            _LOGGER.warning("First refresh cancelled - will retry on next cycle")
            return False
        except Exception as e:
            _LOGGER.error("First refresh failed: %s - will retry on next cycle", e)
            return False