"""
CloudEdge API Client
============================

Main client class for interacting with CloudEdge cameras.
Provides authentication, device management, and configuration capabilities.
"""

import os
import json
import time
import base64
import hmac
import hashlib
import datetime
import subprocess
import socket
import ipaddress
import logging
from typing import Dict, List, Optional, Union, Any
from urllib.parse import quote, urlencode

import requests

from .exceptions import (
    CloudEdgeError, 
    AuthenticationError, 
    DeviceNotFoundError, 
    ConfigurationError,
    NetworkError,
    ValidationError
)
from .iot_parameters import (
    get_parameter_name, 
    get_parameter_code_by_name, 
    format_parameter_value
)
from .logging_config import get_logger
from .validators import validate_email, validate_country_code, validate_phone_code
from .constants import (
    CA_KEY,
    DEFAULT_HEADERS,
    DEFAULT_TIMEOUT,
    TYPE_REGION_EU,
    get_urls_for_region,
)
from ..const import (
    DEFAULT_P2P_MODE,
    P2P_MODE_DISABLED,
    P2P_MODE_FORCE_LOCAL,
)
from .utils import retry_on_failure


class CloudEdgeClient:
    """
    CloudEdge API Client
    
    A client for interacting with CloudEdge cameras.
    Handles authentication, device discovery, status monitoring, and configuration.
    
    Args:
        username (str): CloudEdge account username
        password (str): CloudEdge account password  
        country_code (str): Country code (e.g., "US", "IT")
        phone_code (str): Phone country code (e.g., "+1", "+39")
        debug (bool): Enable debug logging
        session_cache_file (str): Path to session cache file
        
    Example:
        >>> client = CloudEdgeClient("user@example.com", "password", "US", "+1")
        >>> client.authenticate()
        >>> devices = client.get_devices()
        >>> for device in devices:
        ...     print(f"Device: {device['name']} - Status: {device['online']}")
    """

    BASE_URL: str = ""
    OPENAPI_BASE_URL: str = ""
    
    def __init__(
        self, 
        username: str, 
        password: str, 
        country_code: str, 
        phone_code: str,
        debug: bool = False,
        session_cache_file: str = ".cloudedge_session_cache",
        enable_network_ping: bool = True,
        ping_timeout: float = 2.0,
        region: Optional[str] = None,
        base_url: Optional[str] = None,
        openapi_base_url: Optional[str] = None,
        p2p_mode: Optional[str] = None,
        log_signature_debug: bool = False,
        use_epoch_timestamp: bool = False,
        probe_allow_insecure: bool = False,
    ):
        """
        Initialize CloudEdge API client.
        
        Args:
            username (str): CloudEdge account username
            password (str): CloudEdge account password
            country_code (str): Country code (e.g., "US", "IT")
        force_local_p2p: bool = False,
            debug (bool): Enable debug logging
            session_cache_file (str): Path to session cache file
            enable_network_ping (bool): Enable ping-based online status when on same network
            ping_timeout (float): Ping timeout in seconds
            region (Optional[str]): Region code ("US" or "EU"). If not provided, inferred from country_code.
            base_url (Optional[str]): Override base URL for API requests
            openapi_base_url (Optional[str]): Override OpenAPI base URL
            log_signature_debug (bool): Log signature debug information (masked)
            use_epoch_timestamp (bool): Use epoch milliseconds for timestamp fields
            
        Raises:
            ValidationError: If input validation fails
        """
        # Validate inputs
        if not validate_email(username):
            raise ValidationError(
                f"Invalid email format: {username}",
                details={"field": "username", "value": username}
            )
        
        if not validate_country_code(country_code):
            raise ValidationError(
                f"Invalid country code (use 2-letter code like 'US'): {country_code}",
                details={"field": "country_code", "value": country_code}
            )
        
        if not validate_phone_code(phone_code):
            raise ValidationError(
                f"Invalid phone code (use format like '+1'): {phone_code}",
                details={"field": "phone_code", "value": phone_code}
            )
        
        self.username = username
        self.password = password
        self.country_code = country_code.upper()
        self.phone_code = phone_code if phone_code.startswith('+') else f'+{phone_code}'
        
        # Setup proper logging
        self.logger = get_logger("client")
        if debug:
            self.logger.setLevel(logging.DEBUG)
        self.debug = debug
        
        # Signature logging flag (masked output)
        self.log_signature_debug = log_signature_debug
        # Use epoch milliseconds for 'timestamp' fields on v1 endpoints
        self.use_epoch_timestamp = use_epoch_timestamp

        # Allow disabling TLS verification for snapshot probing (debug only)
        # WARNING: This should be False in production; only enable when diagnosing
        # certificate / hostname issues.
        self.probe_allow_insecure = bool(probe_allow_insecure)
        
        self.session_cache_file = session_cache_file
        self.enable_network_ping = enable_network_ping
        self.ping_timeout = ping_timeout
        
        self.session_data: Optional[Dict] = None
        self._session = requests.Session()
        self._session.headers.update({'User-Agent': DEFAULT_HEADERS['User-Agent']})
        
        # Network detection cache
        self._local_network = None
        self._network_detected = False
        # p2p_mode influences whether P2P is disabled or forced local; default to disabled if not set
        if p2p_mode is None:
            p2p_mode = DEFAULT_P2P_MODE
        self.force_local_p2p = (p2p_mode == P2P_MODE_FORCE_LOCAL)
        # Cache wake_device results to avoid frequent wake calls
        self._last_wake_times: Dict[str, float] = {}
        self._last_wake_results: Dict[str, Dict] = {}
        
        # Resolve base URLs for region (priority: explicit args -> region mapping -> defaults)
        resolved_region = region or ("US" if self.country_code == "US" else TYPE_REGION_EU)
        urls = get_urls_for_region(resolved_region)
        if base_url:
            self.BASE_URL = base_url
        else:
            self.BASE_URL = urls.get("BASE_URL") or ""

        if openapi_base_url:
            self.OPENAPI_BASE_URL = openapi_base_url
        else:
            self.OPENAPI_BASE_URL = urls.get("OPENAPI_BASE_URL") or ""

        if self.debug:
            self._log(f"Using BASE_URL={self.BASE_URL} OPENAPI_BASE_URL={self.OPENAPI_BASE_URL}")
        if self.probe_allow_insecure:
            self._log("Probe TLS verification is DISABLED (insecure) - only enable for debugging")
        # Per-account and device cached endpoints for snapshot retrieval
        # Structure: {'snapshotEndpointCache': {userID: {serial: endpoint_info}}}
        self.disable_p2p = (p2p_mode == P2P_MODE_DISABLED)
        
    def _detect_local_network(self) -> Optional[str]:
        """Detect the local network subnet."""
        if self._network_detected:
            return self._local_network
            
        try:
            # Get local IP address by connecting to a remote address
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                
            # Assume /24 subnet (common for home networks)
            network = ipaddress.IPv4Network(f"{local_ip}/24", strict=False)
            self._local_network = str(network)
            self._network_detected = True
            
            if self.debug:
                self._log(f"Detected local network: {self._local_network}")
                
            return self._local_network
            
        except Exception as e:
            if self.debug:
                self._log(f"Failed to detect local network: {e}")
            self._network_detected = True  # Don't retry
            return None
    
    def _is_device_on_local_network(self, device_ip: str) -> bool:
        """Check if device IP is on the same local network."""
        if not self.enable_network_ping:
            return False
            
        local_network = self._detect_local_network()
        if not local_network:
            return False
            
        try:
            device_addr = ipaddress.IPv4Address(device_ip)
            network = ipaddress.IPv4Network(local_network)
            is_local = device_addr in network
            
            if self.debug:
                self._log(f"Device {device_ip} on local network {local_network}: {is_local}")
                
            return is_local
            
        except Exception as e:
            if self.debug:
                self._log(f"Error checking if {device_ip} is local: {e}")
            return False
    
    def _check_ping_availability(self) -> bool:
        """Check if ping command is available on the system."""
        try:
            # Just try to run ping command with --help to check if it exists
            result = subprocess.run(["ping", "--help"], capture_output=True, text=True, timeout=5)
            return True
            
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
        except Exception:
            return False

    def _ping_device(self, ip_address: str) -> Optional[bool]:
        """
        Ping a device to check if it's online.
        
        Returns:
            True if device responds to ping
            False if device doesn't respond to ping
            None if ping command is not available (status unknown)
        """
        if not ip_address or not self.enable_network_ping:
            return False
            
        # Check if ping command is available
        if not self._check_ping_availability():
            if self.debug:
                self._log(f"Ping command not available on this system - cannot determine status for {ip_address}")
            return None
            
        try:
            # Use platform-appropriate ping command
            import platform
            if platform.system().lower() == "windows":
                cmd = ["ping", "-n", "1", "-w", str(int(self.ping_timeout * 1000)), ip_address]
            else:
                cmd = ["ping", "-c", "1", "-W", str(int(self.ping_timeout)), ip_address]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.ping_timeout + 1)
            is_online = result.returncode == 0
            
            if self.debug:
                self._log(f"Ping {ip_address}: {'success' if is_online else 'failed'}")
                
            return is_online
            
        except Exception as e:
            if self.debug:
                self._log(f"Ping error for {ip_address}: {e}")
            return False

    def _get_enhanced_device_status(self, device: Dict) -> bool:
        """Get enhanced device online status using ping when on local network."""
        # Start with API status as fallback
        api_status = device.get('online', False)
        
        # If ping is disabled, return API status
        if not self.enable_network_ping:
            if self.debug:
                self._log(f"Device {device.get('name', 'Unknown')}: Ping disabled, using API status={api_status}")
            return api_status
            
        # Try to get device IP from configuration or device info
        device_ip = None
        
        # First try to get IP from device configuration if available
        if 'ip_address' in device:
            device_ip = device['ip_address']
        else:
            # Try to get configuration to find IP
            try:
                config = self.get_device_config(device.get('serial_number', ''))
                if config and 'iot' in config:  # Fixed: use correct path
                    iot_data = config['iot']
                    if isinstance(iot_data, dict):
                        # Parameter 126 is IP_ADDRESS
                        device_ip = iot_data.get('126')
                        if self.debug:
                            self._log(f"Device {device.get('name', 'Unknown')}: Found IP {device_ip} in config")
            except Exception as e:
                if self.debug:
                    self._log(f"Device {device.get('name', 'Unknown')}: Failed to get config: {e}")
                pass
        
        # If we have an IP and it's on local network, use ping
        if device_ip and self._is_device_on_local_network(device_ip):
            ping_result = self._ping_device(device_ip)
            if ping_result is None:
                # Ping command not available - status unknown, fall back to API
                if self.debug:
                    self._log(f"Device {device.get('name', 'Unknown')} ({device_ip}): Ping unavailable, using API status={api_status}")
                return api_status
            elif ping_result is not None:
                if self.debug:
                    self._log(f"Device {device.get('name', 'Unknown')} ({device_ip}): API={api_status}, Ping={ping_result}, Using=Ping")
                return ping_result
        elif device_ip:
            if self.debug:
                self._log(f"Device {device.get('name', 'Unknown')} ({device_ip}): Not on local network, using API status={api_status}")
        else:
            if self.debug:
                self._log(f"Device {device.get('name', 'Unknown')}: No IP found, using API status={api_status}")
        
        # Fallback to API status
        return api_status
    
    def _log(self, message: str) -> None:
        """Log debug messages."""
        self.logger.debug(message)
            
    def _error(self, message: str) -> None:
        """Log error messages."""
        self.logger.error(message)
    
    def _generate_xca_headers(self, params_str: str = "", user_token: Optional[str] = None) -> Dict[str, str]:
        """Generate X-Ca headers for authenticated API requests.
        
        The CA key used will prefer the per-session caKey if it was provided by
        the login response (session_data['caKey']), otherwise it falls back to
        the default CA_KEY constant from the library.
        """
        if user_token is None:
            user_token = self.session_data.get('userToken', '') if self.session_data else ''
        
        ca_timestamp = str(int(time.time() * 1000))
        ca_nonce = str(int(time.time() * 1000000) % 100000000)
        ca_signature = base64.b64encode(
            hmac.new(
                user_token.encode(), 
                params_str.encode(), 
                hashlib.sha1
            ).digest()
        ).decode()
        
        # Prefer server-provided caKey if the server returned one at login
        ca_key = None
        if self.session_data and isinstance(self.session_data, dict):
            ca_key = self.session_data.get('caKey')
        if not ca_key:
            ca_key = CA_KEY
        
        # Debug logs: masked signature and CA key
        if (self.debug or getattr(self, 'log_signature_debug', False)) and params_str:
            try:
                masked = ca_signature[:8] + "..."
                self._log(f"X-Ca-Sign (masked): {masked}")
                self._log(f"X-Ca-Key used: {ca_key}")
            except Exception:
                pass
        
        return {
            "X-Ca-Timestamp": ca_timestamp,
            "X-Ca-Sign": ca_signature,
            "X-Ca-Key": ca_key,
            "X-Ca-Nonce": ca_nonce
        }
    
    @retry_on_failure(max_attempts=3, delay=1.0)
    def _make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make HTTP request with retry logic and error handling."""
        try:
            response = self._session.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except requests.exceptions.Timeout as e:
            self.logger.error(f"Request timeout: {url}")
            raise  # Let retry decorator handle it
        except requests.exceptions.ConnectionError as e:
            self.logger.error(f"Connection error: {url}")
            raise  # Let retry decorator handle it
        except requests.exceptions.HTTPError as e:
            self.logger.error(f"HTTP error {e.response.status_code}: {url}")
            raise  # HTTP errors shouldn't be retried
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Request failed: {url}")
            raise  # Let retry decorator handle it
        
    def _generate_timestamp(self) -> str:
        """Generate timestamp in CloudEdge format."""
        return datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        
    def _generate_url_timestamp(self) -> str:
        """Generate URL-encoded timestamp."""
        if getattr(self, 'use_epoch_timestamp', False):
            # Return epoch ms as string (not quoted)
            return str(int(time.time() * 1000))
        return quote(self._generate_timestamp())
        
    def _get_timeout(self) -> str:
        """Get timeout timestamp for API requests (60 seconds from now)."""
        return str(int(time.time()) + 60)
        
    def _format_sn(self, sn: str) -> str:
        """Format serial number according to CloudEdge requirements."""
        if not sn:
            return ""
        if len(sn) == 9:
            return "0000000" + sn
        return sn[4:] if len(sn) > 4 else sn
        
    def _des_encode(self, password: str) -> str:
        """Encrypt password using 3DES.
        
        Note: TripleDES is required for CloudEdge protocol compatibility.
        Using the new decrepit module path to avoid deprecation warnings.
        """
        key = "123456781234567812345678".encode('utf-8')
        iv = "01234567".encode('utf-8')
        
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, modes
            from cryptography.hazmat.primitives import padding
            # Import from the new decrepit module location
            from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES

            algorithm = TripleDES(key)
            cipher = Cipher(algorithm, modes.CBC(iv))
            encryptor = cipher.encryptor()
            
            padder = padding.PKCS7(64).padder()
            padded_data = padder.update(password.encode('utf-8')) + padder.finalize()
            
            encrypted = encryptor.update(padded_data) + encryptor.finalize()
            return base64.b64encode(encrypted).decode('utf-8')
            
        except Exception as e:
            raise CloudEdgeError(f"Error during password encryption: {e}")
            
    def _aes_encode_param(self, user_account: str, api_endpoint: str, partner_id: int = 8, 
                         ttid: str = "", timestamp: Optional[int] = None) -> str:
        """Encrypt userAccount using AES encryption."""
        if timestamp is None:
            timestamp = int(time.time() * 1000)
        
        key_material = f"{api_endpoint}{partner_id}{ttid}{timestamp}"
        key_b64 = base64.b64encode(key_material.encode('utf-8')).decode('utf-8')
        aes_key = key_b64[:16].encode('utf-8')
        
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding

            algorithm = algorithms.AES(aes_key)
            cipher = Cipher(algorithm, modes.CBC(aes_key))
            encryptor = cipher.encryptor()
            
            padder = padding.PKCS7(128).padder()
            padded_data = padder.update(user_account.encode('utf-8')) + padder.finalize()
            
            encrypted = encryptor.update(padded_data) + encryptor.finalize()
            return base64.b64encode(encrypted).decode('utf-8')
            
        except Exception as e:
            raise CloudEdgeError(f"Error during username encryption: {e}")
            
    def _generate_api_signature(self, params_str: str, secret: str) -> str:
        """Generate HMAC-SHA1 signature."""
        from urllib.parse import unquote
        
        params = {}
        if params_str:
            for pair in params_str.split('&'):
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    params[key] = unquote(value)
        
        sorted_keys = sorted(params.keys())
        parts = [f"{key}={params[key]}" for key in sorted_keys]
        string_to_sign = "&".join(parts)
        
        signature_bytes = hmac.new(
            secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha1
        ).digest()
        
        signature = base64.b64encode(signature_bytes).decode('utf-8')
        
        # Masked logging of signature and partial string-to-sign for debugging
        if (self.debug or getattr(self, 'log_signature_debug', False)) and string_to_sign:
            try:
                masked_sig = signature[:8] + '...'
                masked_str = string_to_sign[:80] + '...'
                self._log(f"Generated API signature (masked): {masked_sig}")
                self._log(f"String to sign (partial): {masked_str}")
            except Exception:
                pass
        
        return signature
        
    def _get_signature_for_openapi(self, url_path: str, action_type: str, secret: str) -> tuple:
        """Generate signature for OpenAPI requests."""
        timeout = self._get_timeout()
        params = [
            "GET",
            "",
            "",
            timeout,
            url_path,
            action_type
        ]
        string_to_sign = "\n".join(params)
        
        signature = base64.b64encode(
            hmac.new(secret.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha1).digest()
        ).decode('utf-8')
        
        return signature, timeout
        
    def _load_session_cache(self) -> Optional[Dict]:
        """Load session data from cache file."""
        if not os.path.exists(self.session_cache_file):
            return None
            
        try:
            with open(self.session_cache_file, 'r') as f:
                session_data = json.load(f)
                
            # Check if session is still valid (not older than 24 hours)
            login_time = session_data.get('loginTime', 0)
            if time.time() - login_time > 86400:  # 24 hours
                self._log("Session cache expired")
                return None
                
            return session_data
        except (json.JSONDecodeError, KeyError):
            self._log("Invalid session cache file")
            return None
            
    def _save_session_cache(self, session_data: Dict) -> None:
        """Save session data to cache file."""
        try:
            with open(self.session_cache_file, 'w') as f:
                json.dump(session_data, f)
        except Exception as e:
            self._log(f"Failed to save session cache: {e}")
            
    def authenticate(self) -> bool:
        """
        Authenticate with CloudEdge API.
        
        Returns:
            bool: True if authentication successful, False otherwise
            
        Raises:
            AuthenticationError: If authentication fails
            NetworkError: If network request fails
        """
        # Try to load cached session first
        self.session_data = self._load_session_cache()
        if self.session_data:
            self._log("Using cached session")
            # Update OPENAPI_BASE_URL from cached iotPlatformKeys if available
            iot_keys = self.session_data.get('iotPlatformKeys', {})
            if iot_keys and iot_keys.get('openapidomain'):
                self.OPENAPI_BASE_URL = iot_keys.get('openapidomain')
                self._log(f"Updated OPENAPI_BASE_URL from cached iotPlatformKeys: {self.OPENAPI_BASE_URL}")
            return True
            
        self._log("Performing CloudEdge login...")
        
        # Encrypt credentials
        timestamp = int(time.time() * 1000)
        try:
            from .constants import API_ENDPOINTS
            encrypted_username = self._aes_encode_param(
                self.username, API_ENDPOINTS["AUTH_LOGIN"], timestamp=timestamp
            )
            encrypted_password = self._des_encode(self.password)
        except Exception as e:
            raise AuthenticationError(f"Failed to encrypt credentials: {e}")
            
        # Generate headers
        ca_timestamp = str(timestamp)
        ca_nonce = str(int(time.time() * 1000000) % 100000000)
        ca_key = "bc29be30292a4309877807e101afbd51"
        
        # Create signature
        ca_sign_data = (
            f"phoneType=a&sourceApp=8&appVer=5.5.1&iotType=4&equipmentNo=&"
            f"appVerCode=551&localTime={timestamp}&password={encrypted_password}&"
            f"t={timestamp}&lngType=en&countryCode={self.country_code}&"
            f"userAccount={encrypted_username}&phoneCode={self.phone_code}"
        )
        ca_signature = base64.b64encode(
            hmac.new(ca_key.encode(), ca_sign_data.encode(), hashlib.sha1).digest()
        ).decode()
        
        login_data = {
            "phoneType": "a",
            "sourceApp": "8",
            "appVer": "5.5.1",
            "iotType": "4",
            "equipmentNo": "",
            "appVerCode": "551",
            "localTime": timestamp,
            "password": encrypted_password,
            "t": timestamp,
            "lngType": "en",
            "countryCode": self.country_code,
            "userAccount": encrypted_username,
            "phoneCode": self.phone_code
        }
        
        headers = {
            "Accept-Language": "en-US,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "X-Ca-Timestamp": ca_timestamp,
            "X-Ca-Sign": ca_signature,
            "X-Ca-Key": ca_key,
            "X-Ca-Nonce": ca_nonce,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip, deflate, br"
        }
        
        try:
            from .constants import API_ENDPOINTS
            response = self._session.post(
                f"{self.BASE_URL}{API_ENDPOINTS['AUTH_LOGIN']}", 
                headers=headers, 
                data=login_data,
                timeout=30
            )
            response.raise_for_status()
            response_data = response.json()
            
            if response_data.get("resultCode") == "1001":
                self._log("Authentication successful!")
                
                result = response_data.get("result", {})
                user_token = result.get("userToken")
                user_id = result.get("userID")
                
                if not user_token or not user_id:
                    raise AuthenticationError(
                        "Missing user token or ID in response",
                        details={"response": response_data}
                    )
                
                # Extract IoT platform keys if available using multiple possible structures
                iot_platform_keys = {}
                try:
                    iot = result.get('iot')
                    if isinstance(iot, dict):
                        # Common case: pfKey container
                        if 'pfKey' in iot and isinstance(iot['pfKey'], dict):
                            iot_platform_keys = iot['pfKey']
                        else:
                            # Search for nested dict containing accessid/accesskey
                            for v in iot.values():
                                if isinstance(v, dict) and (
                                    any(k.lower() == 'accessid' for k in v.keys())
                                    and any(k.lower() == 'accesskey' for k in v.keys())
                                ):
                                    iot_platform_keys = v
                                    break
                    # Final fallback: scan result dict for any nested dict with accessid/accesskey
                    if not iot_platform_keys:
                        for v in result.values():
                            if isinstance(v, dict) and (
                                any(k.lower() == 'accessid' for k in v.keys())
                                and any(k.lower() == 'accesskey' for k in v.keys())
                            ):
                                iot_platform_keys = v
                                break
                except Exception:
                    iot_platform_keys = {}
                
                # Normalize known nested key names to 'accessid'/'accesskey' to avoid case issues
                def _normalize_iot_keys(keys: dict[str, Any]) -> dict[str, Any]:
                    if not keys or not isinstance(keys, dict):
                        return {}
                    nk = {}
                    for k, v in keys.items():
                        kn = k.lower()
                        if kn in ('accessid', 'access_id'):
                            nk['accessid'] = v
                        elif kn in ('accesskey', 'access_key'):
                            nk['accesskey'] = v
                        else:
                            nk[kn] = v
                    return nk

                normalized_iot_keys = _normalize_iot_keys(iot_platform_keys)

                self.session_data = {
                    "userToken": user_token,
                    "userID": user_id,
                    "caKey": ca_key,
                    "loginTime": int(time.time()),
                    "apiServer": self.BASE_URL,
                    "iotPlatformKeys": normalized_iot_keys
                }
                # Ensure snapshot cache structure exists
                if 'snapshotEndpointCache' not in self.session_data:
                    self.session_data['snapshotEndpointCache'] = {}
                # Debug log presence of OpenAPI keys
                try:
                    if normalized_iot_keys:
                        self._log(f"OpenAPI keys found (masked): accessid={normalized_iot_keys.get('accessid') and '***'}, accesskey={normalized_iot_keys.get('accesskey') and '***'}")
                        # Update OPENAPI_BASE_URL from iotPlatformKeys if available
                        openapi_domain = normalized_iot_keys.get('openapidomain')
                        if openapi_domain:
                            self.OPENAPI_BASE_URL = openapi_domain
                            self._log(f"Updated OPENAPI_BASE_URL from iotPlatformKeys: {openapi_domain}")
                    else:
                        self._log("No OpenAPI keys found in login response; remote configuration endpoints may be unavailable")
                except Exception:
                    pass
                
                self._save_session_cache(self.session_data)
                return True
            else:
                error_msg = response_data.get('resultMsg', 'Unknown error')
                error_code = response_data.get('resultCode', 'unknown')
                raise AuthenticationError(
                    f"Login failed: {error_msg}",
                    details={"error_code": error_code, "message": error_msg}
                )
                
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Login request failed: {e}")
        except json.JSONDecodeError:
            raise AuthenticationError("Failed to parse login response")
            
    def _generate_device_body(self, extra_params: Optional[Dict] = None) -> Dict:
        """Generate device body for API requests."""
        if not self.session_data:
            raise AuthenticationError("Not authenticated")
            
        body = {
            'appVer': '5.5.1',
            'appVerCode': '551',
            'lngType': 'en',
            'phoneType': 'a',
            'sdkVer': '1.0.0',
            'sourceApp': '8',
            'userID': self.session_data['userID'],
            'userToken': self.session_data['userToken']
        }
        
        if extra_params:
            body.update(extra_params)
            
        return body
    
    def get_homes(self) -> List[Dict]:
        """
        Get list of homes associated with the account.
        
        Returns:
            List[Dict]: List of home information dictionaries
            
        Raises:
            AuthenticationError: If not authenticated
            NetworkError: If network request fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log("Getting homes from API...")
        
        timestamp = self._generate_url_timestamp()
        nonce = int(time.time())
        
        # Include userToken in params_str - this is required for the simple signature to work
        params_str = (
            f"appVer=5.5.1&appVerCode=551&lngType=en&phoneType=a&"
            f"signatureMethod=HMAC-SHA1&signatureNonce={nonce}&"
            f"signatureVersion=1.0&sourceApp=8&timestamp={timestamp}&"
            f"userID={self.session_data['userID']}&userToken={self.session_data['userToken']}"
        )
        
        # Generate X-Ca headers for authenticated requests
        xca_headers = self._generate_xca_headers(params_str, self.session_data['userToken'])
        
        headers = {
            "Accept-Language": "en-US,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "Accept-Encoding": "gzip, deflate, br"
        }
        headers.update(xca_headers)
        
        # Use the simple signature with userToken
        from .constants import API_ENDPOINTS
        signature = self._generate_api_signature(params_str, self.session_data.get('userToken'))
        signature_encoded = quote(signature)
        url = f"{self.BASE_URL}{API_ENDPOINTS['HOME_LIST']}?{params_str}&signature={signature_encoded}"
        
        try:
            response = self._make_request('GET', url, headers=headers, timeout=DEFAULT_TIMEOUT)
            response_data = response.json()
            
            if response_data.get("resultCode") == "1001":
                self._log("Homes retrieved successfully!")
                
                homes = []
                home_list = response_data.get('result', {}).get('homes', [])
                
                for home in home_list:
                    rooms = home.get('rooms', [])
                    device_count = sum(len(room.get('devices', [])) for room in rooms)
                    # Normalize home name: some backends return empty string for homeName
                    home_name = home.get('homeName') or home.get('name') or 'Unnamed'
                    homes.append({
                        'home_id': home.get('homeID'),
                        'name': home_name,
                        'owner': home.get('owner'),
                        'rooms': len(rooms),
                        'device_count': device_count
                    })
                    
                return homes
            else:
                error_msg = response_data.get('resultMsg', 'Unknown error')
                error_code = response_data.get('resultCode', 'unknown')
                self._log(f"API error - Code: {error_code}, Message: {error_msg}")
                raise CloudEdgeError(
                    f"Failed to retrieve homes: {error_msg} (Code: {error_code})",
                    details={"error_code": error_code, "message": error_msg}
                )
                
        except requests.exceptions.RequestException as e:
            # Fallback to EU endpoint if request failed
            try:
                from .constants import API_ENDPOINTS
                eu_urls = get_urls_for_region(TYPE_REGION_EU)
                eu_url = f"{eu_urls['BASE_URL']}{API_ENDPOINTS['HOME_LIST']}?{params_str}&signature={signature_encoded}"
                self._log(f"Home list request failed on {self.BASE_URL}: {e}; retrying on EU URL")
                response = self._make_request('GET', eu_url, headers=headers, timeout=DEFAULT_TIMEOUT)
                response_data = response.json()
                if response_data.get("resultCode") == "1001":
                    homes = []
                    home_list = response_data.get('result', {}).get('homes', [])
                    for home in home_list:
                        rooms = home.get('rooms', [])
                        device_count = sum(len(room.get('devices', [])) for room in rooms)
                        home_name = home.get('homeName') or home.get('name') or 'Unnamed'
                        homes.append({
                            'home_id': home.get('homeID'),
                            'name': home_name,
                            'owner': home.get('owner'),
                            'rooms': len(rooms),
                            'device_count': device_count
                        })
                    return homes
            except Exception:
                pass
            raise NetworkError(f"Home list request failed: {e}")
        except json.JSONDecodeError:
            raise CloudEdgeError("Failed to parse home list response")
            
    def get_devices_by_home(self, home_id: str) -> List[Dict]:
        """
        Get list of devices in a specific home.
        
        Args:
            home_id (str): Home ID to get devices from
            
        Returns:
            List[Dict]: List of device information dictionaries
            
        Raises:
            AuthenticationError: If not authenticated
            NetworkError: If network request fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log(f"Getting devices from home {home_id}...")
        
        timestamp = self._generate_url_timestamp()
        nonce = int(time.time())
        
        # Include userToken in params_str - required for the simple signature to work
        params_str = (
            f"appVer=5.5.1&appVerCode=551&homeID={home_id}&lngType=en&phoneType=a&"
            f"signatureMethod=HMAC-SHA1&signatureNonce={nonce}&"
            f"signatureVersion=1.0&sourceApp=8&timestamp={timestamp}&"
            f"userID={self.session_data['userID']}&userToken={self.session_data['userToken']}"
        )
        
        # Generate X-Ca headers for authenticated requests
        xca_headers = self._generate_xca_headers(params_str, self.session_data['userToken'])
        
        headers = {
            "Accept-Language": "en-US,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "Accept-Encoding": "gzip, deflate, br"
        }
        headers.update(xca_headers)
        
        # Use the simple signature with userToken
        from .constants import API_ENDPOINTS
        signature = self._generate_api_signature(params_str, self.session_data.get('userToken'))
        signature_encoded = quote(signature)
        url = f"{self.BASE_URL}{API_ENDPOINTS['HOME_DEVICE_LIST']}?{params_str}&signature={signature_encoded}"
        
        def _parse_devices(response_data: Dict) -> List[Dict]:
            """Parse devices from response data."""
            devices = []
            device_types = ['snap', 'nvr', 'ipc', 'chime', 'doorbell']
            
            # Check both top-level and result for device lists
            sources = [response_data]
            if isinstance(response_data.get('result'), dict):
                sources.append(response_data.get('result'))
            
            raw_devices = []
            for src in sources:
                for device_type in device_types:
                    if device_type in src and isinstance(src[device_type], list):
                        raw_devices.extend(src[device_type])
            
            # Also check for deviceList format
            if not raw_devices:
                device_list = response_data.get('result', {}).get('deviceList', [])
                if isinstance(device_list, list) and device_list:
                    raw_devices.extend(device_list)
            
            # Normalize and deduplicate
            seen = set()
            for device in raw_devices:
                serial = device.get('snNum') or device.get('devUid') or device.get('productSN') or str(device.get('deviceID'))
                if not serial or serial in seen:
                    continue
                seen.add(serial)
                device_name = device.get('deviceName') or 'Unnamed'
                
                # Extract any potential thumbnail/image URLs from the device response
                thumbnail_url = None
                for key in ['alarmImgUrl', 'imgUrl', 'thumbUrl', 'coverImgUrl', 'snapshotUrl', 
                           'lastAlarmUrl', 'deviceImg', 'coverUrl', 'picUrl', 'imageUrl']:
                    if device.get(key) and isinstance(device.get(key), str):
                        thumbnail_url = device.get(key)
                        break
                
                device_dict = {
                    'device_id': device.get('deviceID'),
                    'serial_number': serial,
                    'name': device_name,
                    'type': device.get('deviceTypeName', 'Unknown'),
                    'type_id': device.get('devTypeID'),
                    'host_key': device.get('hostKey'),
                    'online': (
                        (device.get('onLine') == 1) if 'onLine' in device else
                        (device.get('devStatus') == 1) if 'devStatus' in device else
                        (device.get('online') is True) if 'online' in device else False
                    ),
                    'home_id': home_id,
                    'thumbnail_url': thumbnail_url,  # Cloud-stored thumbnail if available
                    # P2P and Cloud capability indicators (for snapshot support detection)
                    'iot_type': device.get('iotType'),
                    'aws_cloud_compat': device.get('awsCloudCompat', 0),
                    'cloud_support': device.get('cloudSupport', 0),
                    'p2p_version': device.get('p2p'),
                    'sleep': device.get('sleep'),
                    'dev_status': device.get('devStatus'),
                    'device_ip': device.get('lanIP') or device.get('deviceIP') or device.get('ip'),
                    'firmware': device.get('firmwareVersion') or device.get('firmware'),
                }
                device_dict['online'] = self._get_enhanced_device_status(device_dict)
                devices.append(device_dict)
            
            return devices
        
        try:
            response = self._make_request('GET', url, headers=headers, timeout=DEFAULT_TIMEOUT)
            response_data = response.json()
            
            if response_data.get("resultCode") in ["1001", "1107"]:
                self._log("Home devices retrieved successfully!")
                return _parse_devices(response_data)
            else:
                error_msg = response_data.get('resultMsg', 'Unknown error')
                error_code = response_data.get('resultCode', 'unknown')
                raise CloudEdgeError(
                    f"Failed to retrieve home devices: {error_msg}",
                    details={"error_code": error_code, "message": error_msg, "home_id": home_id}
                )
                
        except requests.exceptions.RequestException as e:
            # Fallback to EU endpoint if request failed
            try:
                from .constants import API_ENDPOINTS
                eu_urls = get_urls_for_region(TYPE_REGION_EU)
                eu_url = f"{eu_urls['BASE_URL']}{API_ENDPOINTS['HOME_DEVICE_LIST']}?{params_str}&signature={signature_encoded}"
                self._log(f"Home device list request failed on {self.BASE_URL}: {e}; retrying on EU URL")
                response = self._make_request('GET', eu_url, headers=headers, timeout=DEFAULT_TIMEOUT)
                response_data = response.json()
                if response_data.get("resultCode") in ["1001", "1107"]:
                    return _parse_devices(response_data)
            except Exception:
                pass
            raise NetworkError(f"Home device list request failed: {e}")
        except json.JSONDecodeError:
            raise CloudEdgeError("Failed to parse home device list response")
            
    def get_all_devices(self) -> List[Dict]:
        """
        Get all devices from all homes associated with the account.
        
        Returns:
            List[Dict]: List of all device information dictionaries
            
        Raises:
            AuthenticationError: If not authenticated
            NetworkError: If network request fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log("Getting all devices from all homes...")
        
        all_devices = []
        
        # First try the default home API (works for device owners)
        try:
            default_devices = self.get_devices()
            if default_devices:
                self._log(f"Found {len(default_devices)} devices via default home API")
                all_devices.extend(default_devices)
                return all_devices
        except Exception as e:
            self._log(f"Default home API failed: {e}, trying home-based approach...")
        
        # Fallback to home-based approach
        try:
            homes = self.get_homes()
            self._log(f"Found {len(homes)} homes")
            
            for home in homes:
                home_id = home['home_id']
                home_name = home['name']
                self._log(f"Getting devices from home '{home_name}' ({home_id})")
                
                try:
                    home_devices = self.get_devices_by_home(home_id)
                    self._log(f"Found {len(home_devices)} devices in home '{home_name}'")
                    all_devices.extend(home_devices)
                except Exception as e:
                    self._log(f"Failed to get devices from home '{home_name}': {e}")
                    
            return all_devices
            
        except Exception as e:
            raise CloudEdgeError(f"Failed to get devices from homes: {e}")
            
    def get_devices(self, home_id: Optional[str] = None) -> List[Dict]:
        """
        Get list of devices associated with the account.
        
        Args:
            home_id (Optional[str]): Specific home ID to get devices from. 
                                   If None, uses default home API.
        
        Returns:
            List[Dict]: List of device information dictionaries
            
        Raises:
            AuthenticationError: If not authenticated
            NetworkError: If network request fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        # If home_id is specified, use home-based API
        if home_id:
            return self.get_devices_by_home(home_id)
            
        # Otherwise use default home API (default behavior)
        self._log("Getting devices from API...")
        
        device_body = self._generate_device_body()
        
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "Accept-Language": "en-US,en;q=1"
        }
        
        try:
            from .constants import API_ENDPOINTS
            response = self._make_request(
                'POST',
                f"{self.BASE_URL}{API_ENDPOINTS['DEVICE_LIST']}",
                headers=headers, 
                data=device_body,
                timeout=DEFAULT_TIMEOUT
            )
            response_data = response.json()
            
            # Debug: Always log the response code and structure for troubleshooting
            if self.debug:
                self._log(f"get_devices response code: {response_data.get('resultCode')}, msg: {response_data.get('resultMsg')}")
                self._log(f"get_devices response keys: {list(response_data.keys())}")
            
            if response_data.get("resultCode") == "1001":
                self._log("Devices retrieved successfully!")
                
                # Debug: Log the actual response structure
                if self.debug:
                    self._log(f"API Response structure: {json.dumps(response_data, indent=2)}")
                
                devices = []
                
                # Check for devices in different device type keys (working format)
                device_types = ['nvr', 'ipc', 'chime', 'doorbell', 'snap']
                
                for device_type in device_types:
                    if device_type in response_data and response_data[device_type]:
                        device_list = response_data[device_type]
                        if isinstance(device_list, list):
                            self._log(f"Found {len(device_list)} devices under '{device_type}' key")
                            devices.extend(device_list)
                
                # Fallback: check for devices in result.deviceList (older format)
                if not devices:
                    device_list = response_data.get("result", {}).get("deviceList", [])
                    if isinstance(device_list, list) and device_list:
                        self._log(f"Found {len(device_list)} devices under 'result.deviceList' key")
                        devices.extend(device_list)
                
                # Convert to standardized format
                standardized_devices = []
                for device in devices:
                    # Debug: log ALL raw device fields to discover available data
                    if self.debug:
                        self._log(f"Device '{device.get('deviceName')}' RAW fields: {list(device.keys())}")
                        # Log any potential image/thumbnail URLs
                        image_fields = {k: v for k, v in device.items() 
                                       if any(x in k.lower() for x in ['img', 'image', 'thumb', 'url', 'cover', 'alarm', 'snapshot', 'pic'])}
                        if image_fields:
                            self._log(f"Device '{device.get('deviceName')}' IMAGE fields: {image_fields}")
                        status_fields = {k: v for k, v in device.items() if 'status' in k.lower() or 'online' in k.lower() or k in ['onLine', 'devStatus']}
                        self._log(f"Device '{device.get('deviceName')}' status fields: {status_fields}")
                    
                    # Determine online status from multiple possible fields
                    online_status = False
                    if 'onLine' in device:
                        online_status = device.get('onLine') == 1
                        if self.debug:
                            self._log(f"  Using onLine={device.get('onLine')} -> {online_status}")
                    elif 'devStatus' in device:
                        online_status = device.get('devStatus') == 1
                        if self.debug:
                            self._log(f"  Using devStatus={device.get('devStatus')} -> {online_status}")
                    elif 'online' in device:
                        online_status = device.get('online') is True or device.get('online') == 1
                        if self.debug:
                            self._log(f"  Using online={device.get('online')} -> {online_status}")
                    
                    # Extract any potential thumbnail/image URLs from the device response
                    # CloudEdge cameras may include cloud-stored thumbnails for alarms/events
                    thumbnail_url = None
                    for key in ['alarmImgUrl', 'imgUrl', 'thumbUrl', 'coverImgUrl', 'snapshotUrl', 
                               'lastAlarmUrl', 'deviceImg', 'coverUrl', 'picUrl', 'imageUrl']:
                        if device.get(key) and isinstance(device.get(key), str):
                            thumbnail_url = device.get(key)
                            if self.debug:
                                self._log(f"  Found thumbnail URL in '{key}': {thumbnail_url[:60]}...")
                            break
                    
                    device_dict = {
                        'device_id': device.get('deviceID'),
                        'serial_number': device.get('snNum'),
                        'name': device.get('deviceName', 'Unnamed'),
                        'type': device.get('deviceTypeName', 'Unknown'),
                        'type_id': device.get('devTypeID'),
                        'host_key': device.get('hostKey'),
                        'online': online_status,
                        'thumbnail_url': thumbnail_url,  # Cloud-stored thumbnail if available
                        # P2P and Cloud capability indicators (for snapshot support detection)
                        'iot_type': device.get('iotType'),
                        'aws_cloud_compat': device.get('awsCloudCompat', 0),
                        'cloud_support': device.get('cloudSupport', 0),
                        'p2p_version': device.get('p2p'),
                        'sleep': device.get('sleep'),
                        'dev_status': device.get('devStatus'),
                        'device_ip': device.get('lanIP') or device.get('deviceIP') or device.get('ip'),
                        'firmware': device.get('firmwareVersion') or device.get('firmware'),
                    }
                    
                    # Get enhanced online status (may override with ping result)
                    device_dict['online'] = self._get_enhanced_device_status(device_dict)
                    
                    standardized_devices.append(device_dict)
                    
                return standardized_devices
            else:
                error_msg = response_data.get('resultMsg', 'Unknown error')
                error_code = response_data.get('resultCode', 'unknown')
                raise CloudEdgeError(
                    f"Failed to retrieve devices: {error_msg}",
                    details={"error_code": error_code, "message": error_msg}
                )
                
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Device list request failed: {e}")
        except json.JSONDecodeError:
            raise CloudEdgeError("Failed to parse device list response")
            
    def get_device_status(self, device_id: str) -> Optional[Dict]:
        """
        Get online status for a specific device.
        
        Args:
            device_id (str): Device ID
            
        Returns:
            Optional[Dict]: Device status information or None if failed
            
        Raises:
            AuthenticationError: If not authenticated
            NetworkError: If network request fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log(f"Getting device status for ID: {device_id}")
        
        device_body = self._generate_device_body({'deviceID': device_id})
        
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
        }
        
        try:
            from .constants import API_ENDPOINTS
            response = self._make_request(
                'POST',
                f"{self.BASE_URL}{API_ENDPOINTS['DEVICE_STATUS']}",
                headers=headers, 
                data=device_body,
                timeout=DEFAULT_TIMEOUT
            )
            response_data = response.json()
            
            if response_data.get("resultCode") == "1001":
                result = response_data.get("result", {})
                
                # Debug: log the raw result for status debugging
                if self.debug:
                    self._log(f"Device status API response result: {result}")
                
                # Check multiple possible status fields
                online_status = False
                if 'onLine' in result:
                    online_status = result.get('onLine') == 1
                elif 'devStatus' in result:
                    online_status = result.get('devStatus') == 1
                elif 'online' in result:
                    online_status = result.get('online') is True or result.get('online') == 1
                
                if self.debug:
                    self._log(f"Device status determined: {online_status}")
                
                return {
                    'online': online_status,
                    'last_seen': result.get('lastOnLineTime')
                }
            else:
                error_msg = response_data.get('resultMsg', 'Unknown error')
                error_code = response_data.get('resultCode', 'unknown')
                self._log(f"Failed to get device status: {error_msg}")
                raise CloudEdgeError(
                    f"Failed to get device status: {error_msg}",
                    details={"error_code": error_code, "device_id": device_id}
                )
                
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Device status request failed: {e}")
        except json.JSONDecodeError:
            raise CloudEdgeError("Failed to parse device status response")
            
    def wake_device(self, device_id: str) -> Dict:
        """
        Wake a sleeping device and get P2P connection parameters.
        
        This calls the removeWake.action API which:
        1. Wakes the camera from sleep mode
        2. Opens a UDP port on the camera for P2P connection
        3. Returns P2P connection string (getConnectString) with parameters
        
        Args:
            device_id (str): Device ID
            
        Returns:
            Dict: P2P connection parameters including:
                - udpport: UDP port for P2P connection
                - did: Device identifier for P2P
                - initstring: Initialization string for P2P handshake
                - licenceid: Camera serial/license ID
                - protocolv: Protocol version
                - username: P2P username (usually 'admin')
                - password: P2P password (MD5 hash)
                - mode: Connection mode
                - trytimes: Retry count
                - delaysec: Delay between retries
            
        Raises:
            AuthenticationError: If not authenticated
            NetworkError: If network request fails
            CloudEdgeError: If wake request fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log(f"Waking device ID: {device_id}")
        # Avoid repeated wake calls - cache for a short period
        try:
            now = time.time()
            last_ts = self._last_wake_times.get(device_id)
            if last_ts and now - last_ts < 15:
                if self.debug:
                    self._log(f"Using cached wake result for device {device_id} (age {now-last_ts:.1f}s)")
                return self._last_wake_results.get(device_id, {})
        except Exception:
            pass
        
        device_body = self._generate_device_body({'deviceID': device_id})
        
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
        }
        
        # Try multiple potential wake endpoints - the endpoint varies by region/server
        from .constants import API_ENDPOINTS
        wake_endpoints = [
            f"{self.BASE_URL}{API_ENDPOINTS['DEVICE_WAKE']}",
            f"{self.BASE_URL}{API_ENDPOINTS['DEVICE_WAKE_V1']}",
            f"{self.BASE_URL}/app/device/wake.action",
        ]
        
        # Also try OpenAPI endpoint if available
        iot_keys = self.session_data.get('iotPlatformKeys', {})
        if iot_keys.get('openapidomain'):
            openapi_domain = iot_keys['openapidomain']
            if not openapi_domain.startswith('http'):
                openapi_domain = f"https://{openapi_domain}"
            wake_endpoints.insert(1, f"{openapi_domain}{API_ENDPOINTS['DEVICE_WAKE_V1']}")
        
        last_error = None
        # Try a cached endpoint first (per-account/device) to avoid probing
        cached = self._get_cached_snapshot_endpoint(device_serial)
        if cached:
            try:
                self._log(f"Trying cached snapshot endpoint for device {device_serial}: {cached}")
                if cached.get('type') == 'openapi':
                    # cached contains path and params
                    url = f"{cached.get('base')}{cached.get('path')}"
                    resp = self._session.get(url, headers=headers, params=cached.get('params', {}), timeout=DEFAULT_TIMEOUT)
                else:
                    # v1 endpoints - send as full URL
                    url = cached.get('url')
                    req_headers = headers.copy()
                    req_headers.update(self._generate_xca_headers(params_str, self.session_data.get('userToken')))
                    resp = self._session.get(url, headers=req_headers, timeout=DEFAULT_TIMEOUT)

                if resp and resp.status_code == 200 and 'image' in (resp.headers.get('Content-Type', '')):
                    self._log(f"Cached snapshot endpoint succeeded for device {device_serial}: {cached}")
                    return resp.content
            except Exception as e:
                # Remove cache if cached endpoint fails
                self._log(f"Cached snapshot endpoint failed for device {device_serial}: {e}")
                try:
                    # Remove cached entry
                    cache = self.session_data.get('snapshotEndpointCache', {})
                    user_cache = cache.get(str(self.session_data.get('userID', '')), {})
                    if device_serial in user_cache:
                        del user_cache[device_serial]
                        self._save_session_cache(self.session_data)
                except Exception:
                    pass
        for endpoint in wake_endpoints:
            try:
                self._log(f"Trying wake endpoint: {endpoint}")
                response = self._session.post(
                    endpoint,
                    headers=headers, 
                    data=device_body,
                    timeout=DEFAULT_TIMEOUT
                )
                
                # Skip 404s silently and try next endpoint
                if response.status_code == 404:
                    self._log(f"  -> 404 Not Found, trying next endpoint")
                    continue
                    
                response.raise_for_status()
                response_data = response.json()
                
                if self.debug:
                    self._log(f"Wake device response: {response_data}")
                
                if response_data.get("resultCode") == "1001":
                    result = response_data.get("result", {}) or response_data.get("resultData", {})
                    
                    # Parse getConnectString if present
                    connect_string_raw = result.get("getConnectString", "")
                    if connect_string_raw:
                        try:
                            connect_params = json.loads(connect_string_raw)
                            self._log(f"P2P connection params obtained for device {device_id}")
                            result_dict = {
                                'success': True,
                                'connect_string': connect_params,
                                'raw_result': result
                            }
                            # Cache result
                            self._last_wake_times[device_id] = time.time()
                            self._last_wake_results[device_id] = result_dict
                            return result_dict
                        except json.JSONDecodeError:
                            self._log(f"Warning: Failed to parse getConnectString: {connect_string_raw}")
                            result_dict = {
                                'success': True,
                                'connect_string_raw': connect_string_raw,
                                'raw_result': result
                            }
                            # Cache result
                            self._last_wake_times[device_id] = time.time()
                            self._last_wake_results[device_id] = result_dict
                            return result_dict
                    else:
                        # No connect string - device may be online and ready
                        self._log(f"Device {device_id} woken but no connect string returned")
                        result_dict = {
                            'success': True,
                            'connect_string': None,
                            'raw_result': result
                        }
                        self._last_wake_times[device_id] = time.time()
                        self._last_wake_results[device_id] = result_dict
                        return result_dict
                elif response_data.get("resultCode") not in [None, "1003", "1023"]:
                    # Got a real error response, not just endpoint not found
                    error_msg = response_data.get('resultMsg', 'Unknown error')
                    error_code = response_data.get('resultCode', 'unknown')
                    self._log(f"Wake endpoint returned error: {error_code} - {error_msg}")
                    last_error = CloudEdgeError(
                        f"Failed to wake device: {error_msg}",
                        details={"error_code": error_code, "device_id": device_id}
                    )
                    
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue  # Try next endpoint
                last_error = e
            except requests.exceptions.RequestException as e:
                last_error = e
            except json.JSONDecodeError:
                continue  # Try next endpoint
                
        # All endpoints failed
        if last_error:
            raise NetworkError(f"Device wake request failed on all endpoints: {last_error}")
        else:
            raise CloudEdgeError("Wake endpoint not found on any known server")
            
    def get_device_config(self, device_serial: str, 
                          parameter_codes: Optional[List[str]] = None) -> Optional[Dict]:
        """
        Get device configuration parameters.
        
        Args:
            device_serial (str): Device serial number
            parameter_codes (Optional[List[str]]): Specific parameter codes to retrieve
            
        Returns:
            Optional[Dict]: Device configuration data or None if failed
            
        Raises:
            AuthenticationError: If not authenticated
            ConfigurationError: If configuration retrieval fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log(f"Getting device configuration for SN: {device_serial}")
        
        # Check if we have OpenAPI credentials
        iot_keys = self.session_data.get('iotPlatformKeys', {})
        if not iot_keys or 'accessid' not in iot_keys or 'accesskey' not in iot_keys:
            raise ConfigurationError(
                "No OpenAPI credentials available. Device may not support remote configuration.",
                details={"device_serial": device_serial}
            )
            
        access_id = iot_keys['accessid']
        access_key = iot_keys['accesskey']
        openapi_base = iot_keys.get('openapidomain') or iot_keys.get('platformdomain') or self.OPENAPI_BASE_URL
        # Prefer explicit openapi domain returned by the IoT platform keys
        openapi_base = iot_keys.get('openapidomain') or iot_keys.get('platformdomain') or self.OPENAPI_BASE_URL
        
        # Generate signature for OpenAPI
        from .constants import API_ENDPOINTS
        signature, timeout = self._get_signature_for_openapi(API_ENDPOINTS['OPENAPI_DEVICE_CONFIG'], 'get', access_key)
        
        # Format device SN
        formatted_sn = self._format_sn(device_serial)
        
        # Prepare IoT parameters
        iot_params = {
            'code': 100001,
            'action': 'get',
            'name': 'iot'
        }
        
        if parameter_codes:
            iot_params['iot'] = parameter_codes
        
        # Build request parameters
        params = {
            'accessid': access_id,
            'expires': timeout,
            'signature': signature,
            'action': 'get',
            'deviceid': formatted_sn,
            'target': 'server',
            'params': base64.b64encode(json.dumps(iot_params).encode()).decode()
        }
        
        headers = {
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "Accept-Language": "en-US,en;q=1",
            "X-Ca-Key": CA_KEY
        }
        
        try:
            from .constants import API_ENDPOINTS
            response = self._make_request(
                'GET',
                f"{openapi_base}{API_ENDPOINTS['OPENAPI_DEVICE_CONFIG']}",
                headers=headers, 
                params=params, 
                timeout=DEFAULT_TIMEOUT
            )
            
            if response.status_code == 200:
                response_data = response.json()
                self._log("Device configuration retrieved successfully")
                return response_data
            else:
                raise ConfigurationError(f"Config request failed: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Config request failed: {e}")
        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Failed to parse config response: {e}")
            
    def set_device_config(self, device_serial: str, parameters: Dict[str, Any]) -> bool:
        """
        Set device configuration parameters.
        
        Args:
            device_serial (str): Device serial number
            parameters (Dict[str, Any]): Parameter codes and values to set
            
        Returns:
            bool: True if successful, False otherwise
            
        Raises:
            AuthenticationError: If not authenticated
            ConfigurationError: If configuration setting fails
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        self._log(f"Setting device configuration for SN: {device_serial}")
        
        # Check if we have OpenAPI credentials
        iot_keys = self.session_data.get('iotPlatformKeys', {})
        if not iot_keys or 'accessid' not in iot_keys or 'accesskey' not in iot_keys:
            raise ConfigurationError(
                "No OpenAPI credentials available. Device may not support remote configuration.",
                details={"device_serial": device_serial, "parameters": list(parameters.keys())}
            )
            
        access_id = iot_keys['accessid']
        access_key = iot_keys['accesskey']
        openapi_base = iot_keys.get('openapidomain') or iot_keys.get('platformdomain') or self.OPENAPI_BASE_URL
        
        # Generate signature for OpenAPI
        from .constants import API_ENDPOINTS
        signature, timeout = self._get_signature_for_openapi(API_ENDPOINTS['OPENAPI_DEVICE_CONFIG'], 'set', access_key)
        
        # Format device SN
        formatted_sn = self._format_sn(device_serial)
        
        # Prepare IoT parameters for SET operation
        iot_params = {
            'code': 100001,
            'action': 'set',
            'name': 'iot',
            'iot': parameters
        }
        
        # Encode parameters to base64
        params_json = json.dumps(iot_params)
        params_b64 = base64.b64encode(params_json.encode()).decode()
        
        # Build request parameters
        params = {
            'accessid': access_id,
            'expires': timeout,
            'signature': signature,
            'action': 'set',
            'deviceid': formatted_sn,
            'params': params_b64
        }
        
        headers = {
            "Accept": "*/*",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
            "Accept-Language": "en-US,en;q=1"
        }
        
        try:
            from .constants import API_ENDPOINTS
            response = self._make_request(
                'GET',
                f"{openapi_base}{API_ENDPOINTS['OPENAPI_DEVICE_CONFIG']}",
                headers=headers, 
                params=params, 
                timeout=DEFAULT_TIMEOUT
            )
            
            if response.status_code == 200:
                response_data = response.json()
                
                # Check if the operation was successful
                if response_data.get('code') == 100001 or response_data.get('resultCode') == '1001':
                    self._log("Device configuration set successfully")
                    return True
                else:
                    raise ConfigurationError(f"Set config failed: {response_data}")
            else:
                raise ConfigurationError(f"Set config request failed: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Set config request failed: {e}")
        except json.JSONDecodeError as e:
            raise ConfigurationError(f"Failed to parse set config response: {e}")
            
    def find_device_by_name(self, device_name: str) -> Optional[Dict]:
        """
        Find a device by its name.
        
        Args:
            device_name (str): Device name to search for
            
        Returns:
            Optional[Dict]: Device information or None if not found
        """
        # First try the get_all_devices method for comprehensive search
        try:
            devices = self.get_all_devices()
        except AuthenticationError:
            # If we're not authenticated, return None to indicate no device
            # found (caller can decide to authenticate first)
            return None
        
        # Search for exact match first
        for device in devices:
            if device.get('name', '').lower() == device_name.lower():
                return device
                
        # If exact match not found, try partial match
        for device in devices:
            if device_name.lower() in device.get('name', '').lower():
                return device
                
        return None

    def _get_cached_snapshot_endpoint(self, device_serial: str) -> Optional[Dict[str, Any]]:
        """Get cached snapshot endpoint info for a device serial for the current user."""
        if not self.session_data:
            return None
        user_id = str(self.session_data.get('userID', ''))
        cache = self.session_data.setdefault('snapshotEndpointCache', {})
        user_cache = cache.get(user_id, {}) if isinstance(cache, dict) else {}
        return user_cache.get(device_serial)

    def _set_cached_snapshot_endpoint(self, device_serial: str, endpoint_info: Dict[str, Any]) -> None:
        """Set a cached snapshot endpoint for a device serial, persistent in session_data."""
        if not self.session_data:
            return
        user_id = str(self.session_data.get('userID', ''))
        cache = self.session_data.setdefault('snapshotEndpointCache', {})
        user_cache = cache.setdefault(user_id, {})
        user_cache[device_serial] = endpoint_info
        # Persist the updated cache to disk
        self._save_session_cache(self.session_data)

    def clear_cached_snapshot_endpoint(self, device_serial: str) -> None:
        """Remove the cached snapshot endpoint for a device to force re-probe."""
        if not device_serial or not self.session_data:
            return
        try:
            cache = self.session_data.setdefault('snapshotEndpointCache', {})
            user_cache = cache.setdefault(str(self.session_data.get('userID')), {})
            if device_serial in user_cache:
                user_cache.pop(device_serial, None)
                if self.debug:
                    self._log(f"Cleared cached snapshot endpoint for {device_serial}")
            self._save_session_cache(self.session_data)
        except Exception:
            pass

    def probe_snapshot_endpoint(self, device_id: int, device_serial: str, force_reprobe: bool = False) -> Optional[Dict[str, Any]]:
        """Probe snapshot endpoints for a device and return endpoint info (and optionally image via get_device_snapshot).

        Returns a dict like { 'endpoint_info': {...}, 'image': b'...' } or None if not authenticated.
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
        if force_reprobe:
            self.clear_cached_snapshot_endpoint(device_serial)

        # Note: No early-exit checks here — always attempt probing.

        # Snapshot probing is intentionally disabled for now to avoid
        # aggressive scanning of snapshot/preview endpoints that can
        # cause undesirable network activity or mark devices offline.
        if force_reprobe:
            # Clearing cache is allowed but we won't actively reprobe here
            self.clear_cached_snapshot_endpoint(device_serial)

        endpoint_info = self._get_cached_snapshot_endpoint(device_serial)
        if self.debug:
            self._log(f"Snapshot probing is disabled; returning cached endpoint for {device_serial}: {endpoint_info}")
        return { 'endpoint_info': endpoint_info, 'image': None, 'probe_disabled': True }

    def _resolve_full_url(self, url: str) -> str:
        """
        Convert a possibly relative URL returned by API to a full absolute URL.
        Tries openapi domain first, then BASE_URL as fallback.
        """
        if not url:
            return url
        if url.startswith('http'):
            return url
        # Try OpenAPI domain from iotPlatformKeys when available
        iot_keys = self.session_data.get('iotPlatformKeys', {}) if self.session_data else {}
        openapi_domain = (iot_keys.get('openapidomain') or iot_keys.get('platformdomain')) if iot_keys else None
        if openapi_domain:
            if not openapi_domain.startswith('http'):
                openapi_domain = f"https://{openapi_domain}"
            return f"{openapi_domain.rstrip('/')}/{url.lstrip('/')}"
        # Fallback to BASE_URL
        return f"{self.BASE_URL.rstrip('/')}/{url.lstrip('/')}"
        
    def set_device_parameter(self, device_name: str, parameter_name: str, 
                             value: Union[int, str, float]) -> bool:
        """
        Set a single device parameter by name.
        
        Args:
            device_name (str): Device name
            parameter_name (str): Parameter name (e.g., "FRONT_LIGHT_SWITCH")
            value (Union[int, str, float]): Parameter value
            
        Returns:
            bool: True if successful, False otherwise
            
        Raises:
            DeviceNotFoundError: If device not found
            ConfigurationError: If parameter is invalid or setting fails
        """
        # Find device
        device = self.find_device_by_name(device_name)
        if not device:
            raise DeviceNotFoundError(f"Device '{device_name}' not found")
            
        # Get parameter code
        parameter_code = get_parameter_code_by_name(parameter_name)
        if not parameter_code:
            raise ConfigurationError(
                f"Unknown parameter: {parameter_name}",
                details={"parameter_name": parameter_name, "device": device_name}
            )
            
        # Set parameter
        parameters = {parameter_code: value}
        success = self.set_device_config(device['serial_number'], parameters)
        
        if success:
            param_display = get_parameter_name(parameter_code)
            formatted_value = format_parameter_value(param_display, value)
            self._log(f"Set {param_display} = {formatted_value} on device '{device_name}'")
            
        return success
        
    def get_device_info(self, device_name: str, include_config: bool = True) -> Optional[Dict]:
        """
        Get comprehensive device information including status and configuration.
        
        Args:
            device_name (str): Device name
            include_config (bool): Whether to include device configuration
            
        Returns:
            Optional[Dict]: Complete device information or None if not found
            
        Raises:
            DeviceNotFoundError: If device not found
        """
        # Find device
        device = self.find_device_by_name(device_name)
        if not device:
            raise DeviceNotFoundError(f"Device '{device_name}' not found")
            
        # Try to get updated device status, but don't overwrite if API returns empty
        try:
            status = self.get_device_status(device['device_id'])
            if status:
                # Only update if we got a valid status response
                # Some regions/APIs return empty result - preserve existing status in that case
                prev_online = device.get('online', False)
                new_online = status.get('online', False)
                
                # Prefer the more positive status (if either shows online, use online)
                # This handles cases where one API call succeeds and another fails
                if new_online or prev_online:
                    device['online'] = True
                else:
                    device['online'] = False
                    
                if status.get('last_seen'):
                    device['last_seen'] = status['last_seen']
                    
                if self.debug:
                    self._log(f"Updated device status: prev={prev_online}, new={new_online}, final={device['online']}")
        except Exception as e:
            if self.debug:
                self._log(f"Failed to get updated device status (using existing): {e}")
            # Keep existing status from device list API
            pass
            
        # Get device configuration if requested
        if include_config:
            try:
                config = self.get_device_config(device['serial_number'])
                if config and 'result' in config and 'iot' in config['result']:
                    # Process IoT parameters for display
                    iot_data = config['result']['iot']
                    processed_config = {}
                    
                    for param_code, value in iot_data.items():
                        param_name = get_parameter_name(param_code)
                        formatted_value = format_parameter_value(param_name, value)
                        # Use parameter CODE as key (not name) for consistent lookups
                        processed_config[param_code] = {
                            'name': param_name,
                            'code': param_code,
                            'value': value,
                            'formatted': formatted_value
                        }
                        
                    device['configuration'] = processed_config
            except Exception as e:
                self._log(f"Failed to get device configuration: {e}")
                device['configuration'] = None
                
        return device
    
    def refresh_device_status(self, device: Dict) -> bool:
        """
        Refresh the online status of a device using enhanced checking.
        
        Args:
            device (Dict): Device dictionary to refresh
            
        Returns:
            bool: Updated online status
        """
        updated_status = self._get_enhanced_device_status(device)
        device['online'] = updated_status
        return updated_status
    
    def get_network_info(self) -> Dict:
        """
        Get network configuration information.
        
        Returns:
            Dict: Network configuration info
        """
        return {
            'ping_enabled': self.enable_network_ping,
            'ping_timeout': self.ping_timeout,
            'local_network': self._detect_local_network(),
            'network_detected': self._network_detected
        }

    def get_alarm_events(
        self, 
        device_id: int, 
        day: Optional[str] = None,
        index: str = "0",
        direction: int = 1,
        event_type: int = 0,
        ai_types: Optional[List[int]] = None,
        limit: int = 10
    ) -> List[Dict]:
        """
        Get alarm/motion events for a device with associated images.
        
        IMPORTANT: This requires an active cloud subscription (cloudSupport=1).
        Cameras without cloud subscription will return empty results.
        
        Args:
            device_id (int): Device ID (numeric ID, not serial number)
            day (str): Date in format 'YYYYMMDD'. Defaults to today.
            index (str): Pagination index. "0" for latest (when direction=1).
            direction (int): 1 = refresh/latest (default), 0 = load more/older
            event_type (int): Event type filter (0 = all)
            ai_types (List[int]): AI detection types (None = no filter)
            limit (int): Maximum number of events to return
            
        Returns:
            List[Dict]: List of alarm events with image URLs, or empty list
        """
        if not self.session_data:
            raise AuthenticationError("Not authenticated - call authenticate() first")
            
        # Default to today's date
        if not day:
            day = datetime.datetime.now().strftime('%Y%m%d')
        
        timestamp = self._generate_url_timestamp()
        nonce = int(time.time())
        
        # Build request parameters
        base_params = {
            'appVer': '5.5.1',
            'appVerCode': '551',
            'deviceID': str(device_id),
            'day': day,
            'index': index,
            'direction': str(direction),
            'eventType': str(event_type),
            'lngType': 'en',
            'phoneType': 'a',
            'signatureMethod': 'HMAC-SHA1',
            'signatureNonce': str(nonce),
            'signatureVersion': '1.0',
            'sourceApp': '8',
            'timestamp': timestamp,
            'userID': str(self.session_data['userID']),
            'userToken': self.session_data['userToken']
        }
        
        if ai_types is not None:
            base_params['aiType'] = ",".join(str(t) for t in ai_types)
        
        # Generate headers and signature
        params_str = "&".join(f"{k}={v}" for k, v in sorted(base_params.items()))
        xca_headers = self._generate_xca_headers(params_str, self.session_data['userToken'])
        
        headers = {
            "Accept-Language": "en-US,en;q=0.8",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us) AppleWebKit/533.1",
            "Accept-Encoding": "gzip, deflate, br"
        }
        headers.update(xca_headers)
        
        signature = self._generate_api_signature(params_str, self.session_data.get('userToken'))
        signature_encoded = quote(signature)
        
        # Try the known working endpoint: /v1/app/msg/alert/list
        # This endpoint returns:
        # - 1001: Success with events
        # - 1003: No permission (no cloud subscription) 
        # - 1023: Invalid parameters or no events
        from .constants import API_ENDPOINTS
        endpoint = API_ENDPOINTS['ALERT_LIST']
        url = f"{self.BASE_URL}{endpoint}?{params_str}&signature={signature_encoded}"
        
        try:
            response = self._make_request('GET', url, headers=headers, timeout=DEFAULT_TIMEOUT)
            response_data = response.json()
            result_code = response_data.get('resultCode')
            
            if result_code in (1001, '1001'):
                events = self._parse_alarm_events(response_data.get('result', {}))
                if events:
                    self._log(f"Found {len(events)} alarm events")
                return events[:limit]
            elif result_code in (1003, '1003'):
                # No cloud subscription - don't spam logs, just return empty
                if self.debug:
                    self._log("Alarm API requires cloud subscription (cloudSupport=1)")
                return []
            elif result_code in (1023, '1023'):
                # No events or invalid params
                return []
            else:
                if self.debug:
                    self._log(f"Alarm API returned code {result_code}")
                return []
                
        except requests.exceptions.RequestException as e:
            if self.debug:
                self._log(f"Alarm API request failed: {e}")
            return []
        except json.JSONDecodeError:
            return []
        
    def _parse_alarm_events(self, result: Union[Dict, List]) -> List[Dict]:
        """
        Parse alarm events from API response.
        
        Handles two response formats based on Meari SDK:
        - evt < 1 (old API): imageUrl is a string URL, tumbnailPic is thumbnail
        - evt >= 1 (Cloud 2.0): imageUrl is a long (numeric), eventTime format
        
        Args:
            result: API result data (dict or list)
            
        Returns:
            List[Dict]: Parsed alarm events
        """
        events = []
        
        # Handle different response formats
        if isinstance(result, list):
            raw_events = result
        elif isinstance(result, dict):
            # Try common keys for event lists from SDK
            raw_events = (
                result.get('msgs', []) or
                result.get('deviceAlarmMessages', []) or  # SDK class name
                result.get('alarmList', []) or
                result.get('events', []) or
                result.get('list', []) or
                result.get('data', [])
            )
        else:
            return []
            
        for event in raw_events:
            if not isinstance(event, dict):
                continue
                
            # Extract image URL - handle both string URLs and numeric IDs
            # SDK: evt < 1 has 'imgUrl' (string), evt >= 1 has 'imageUrl' (long)
            image_url = None
            image_id = None
            
            for key in ['imgUrl', 'imageUrl', 'tumbnailPic', 'thumbUrl', 'picUrl', 'alarmImgUrl']:
                val = event.get(key)
                if val:
                    if isinstance(val, str) and (val.startswith('http') or val.startswith('/')):
                        image_url = val
                        break
                    elif isinstance(val, (int, float)) or (isinstance(val, str) and val.isdigit()):
                        # Cloud 2.0 format - imageUrl is numeric ID
                        image_id = str(val)
                        # For Cloud 2.0, the imageUrl is often in aiVideoInfo or needs construction
                        break
                    elif isinstance(val, str):
                        # String but not URL - could be relative path
                        image_url = val
                        break
            
            # Check for video info (Cloud 2.0 may have URLs in videoUrl list)
            video_info = event.get('videoUrl') or event.get('aiVideoInfo') or []
            if isinstance(video_info, list) and video_info:
                first_video = video_info[0] if video_info else {}
                if isinstance(first_video, dict):
                    # VideoInfo has 'url' and 'duration' per SDK
                    if not image_url and first_video.get('url'):
                        # Use video thumbnail if no image
                        pass
                    
            # Extract event time (SDK: eventTime for evt>=1, createDate for old)
            event_time = (
                event.get('eventTime') or 
                event.get('createDate') or 
                event.get('time') or 
                event.get('createTime')
            )
            
            parsed_event = {
                'event_id': event.get('msgID') or event.get('id') or event.get('eventId'),
                'device_id': event.get('deviceID') or event.get('deviceId'),
                'event_type': event.get('imageAlertType') or event.get('eventType') or event.get('msgTypeID'),
                'event_time': event_time,
                'image_url': image_url,
                'image_id': image_id,  # For Cloud 2.0 numeric IDs
                'video_info': video_info if isinstance(video_info, list) else [],
                'is_encrypted': self._is_encrypted_image(image_url) if image_url else False,
                'raw': event  # Keep raw data for debugging
            }
            
            # Include events with either image URL or image ID
            if image_url or image_id:
                events.append(parsed_event)
            elif self.debug:
                self._log(f"Skipping event without image: {event.get('msgID') or event.get('eventTime')}")
                
        return events
        
    def _is_encrypted_image(self, url: str) -> bool:
        """
        Check if image URL points to an encrypted image.
        
        Based on Meari SDK documentation:
        - jpgx3 suffix: encrypted with device SN (default for Cloud 2.0)
        - jpgx2 suffix: encrypted with user password
        - jepx1/jepx2/jepx3: alternative naming (older format)
        """
        if not url:
            return False
        url_lower = url.lower()
        # Check for both jpgx and jepx naming conventions
        encrypted_extensions = [
            '.jpgx1', '.jpgx2', '.jpgx3',  # Meari SDK naming
            '.jepx1', '.jepx2', '.jepx3',  # Alternative naming
            'jpgx1', 'jpgx2', 'jpgx3',      # Without dot
            'jepx1', 'jepx2', 'jepx3'       # Without dot
        ]
        return any(ext in url_lower for ext in encrypted_extensions)
        
    def decrypt_alarm_image(self, image_data: bytes, device_serial: str, url: str = "") -> bytes:
        """
        Decrypt an encrypted alarm image (jpgx3/jpgx2 format).
        
        Based on Meari SDK: SdkUtils.handleEncodedImage(url, img, sn, allPwd)
        The SDK uses XOR-based decryption with the device serial number.
        
        For Cloud 2.0 (jpgx3): encrypted with device SN (default)
        For jpgx2: encrypted with user password
        
        Args:
            image_data (bytes): Encrypted image data
            device_serial (str): Device serial number (used as decryption key)
            url (str): Original URL (used to determine encryption version)
            
        Returns:
            bytes: Decrypted JPEG image data
        """
        if not image_data or not device_serial:
            return image_data
        
        # Check encryption version from URL or file suffix
        url_lower = url.lower() if url else ""
        
        try:
            # Based on SDK: SdkUtils.formatLicenceId(sn) is used as key
            # The license ID is typically the device serial formatted
            key = device_serial.encode('utf-8')
            key_len = len(key)
            
            # JPGX3 / JEPX3: Encrypted with device SN (most common for Cloud 2.0)
            if any(ext in url_lower for ext in ['.jpgx3', 'jpgx3', '.jepx3', 'jepx3']):
                # SDK uses device SN as key - simple XOR decryption
                decrypted = bytearray(len(image_data))
                for i, byte in enumerate(image_data):
                    decrypted[i] = byte ^ key[i % key_len]
                
                # Check if result is valid JPEG
                if decrypted[:2] == b'\xff\xd8':
                    return bytes(decrypted)
                    
                # Try alternative XOR pattern if simple doesn't work
                decrypted = bytearray(len(image_data))
                for i, byte in enumerate(image_data):
                    key_byte = key[i % key_len]
                    decrypted[i] = (byte ^ key_byte) & 0xFF
                
                if decrypted[:2] == b'\xff\xd8':
                    return bytes(decrypted)
                    
                # Return original if neither works
                self._log("JPGX3 decryption didn't produce valid JPEG")
                return image_data
                
            # JPGX2 / JEPX2: Encrypted with user password  
            elif any(ext in url_lower for ext in ['.jpgx2', 'jpgx2', '.jepx2', 'jepx2']):
                # Would need user password - try SN as fallback
                decrypted = bytearray(len(image_data))
                for i, byte in enumerate(image_data):
                    decrypted[i] = byte ^ key[i % key_len]
                return bytes(decrypted)
                
            # JPGX1 / JEPX1: Simple XOR
            elif any(ext in url_lower for ext in ['.jpgx1', 'jpgx1', '.jepx1', 'jepx1']):
                decrypted = bytearray(len(image_data))
                for i, byte in enumerate(image_data):
                    decrypted[i] = byte ^ key[i % key_len]
                return bytes(decrypted)
                
            else:
                # Not encrypted or unknown format
                return image_data
                
        except Exception as e:
            self._log(f"Image decryption failed: {e}")
            return image_data
            
    def get_latest_alarm_image(self, device_id: int, device_serial: str) -> Optional[bytes]:
        """
        Get the latest alarm event image for a device.
        
        This is a convenience method that fetches the most recent alarm event
        and downloads/decrypts its associated image.
        
        Note: Requires active cloud subscription (cloudSupport=1).
        Returns None silently if no subscription.
        
        Args:
            device_id (int): Device ID (numeric)
            device_serial (str): Device serial number (for decryption)
            
        Returns:
            Optional[bytes]: JPEG image data or None if not available
        """
        # Alarm image retrieval has been disabled to avoid network activity
        # related to downloading snapshots or alarm images. This method is a
        # no-op and will always return None. Enable fetching only if you
        # explicitly re-enable snapshot probing in a controlled manner.
        if self.debug:
            self._log(f"get_latest_alarm_image called for {device_serial}/{device_id} but alarm image retrieval is disabled")
        return None

    def get_device_snapshot(self, device_id: int, device_serial: str) -> Optional[bytes]:

        """
        Try to fetch a snapshot image for a device using Cloud API endpoints.

        This method probes several possible server endpoints and attempts to
        return raw JPEG/PNG bytes. Returns None if no snapshot is available
        or if the account/device lacks permission (no cloud subscription).
        """
        # Snapshot retrieval has been disabled to avoid scanning snapshot/preview endpoints.
        # This method intentionally performs no network activity and returns None.
        if self.debug:
            self._log(f"get_device_snapshot called for {device_serial}/{device_id} but snapshots have been disabled")
        return None

    def _get_image_by_id(self, image_id: str, device_serial: str, formatted_sn: Optional[str] = None) -> Optional[bytes]:
        """
        Attempt to download an image referenced by a cloud image/file ID using the OpenAPI file endpoints.

        This method will try several likely OpenAPI paths and query parameter names to
        accommodate differences across CloudEdge/Meari server regions and API versions.
        """
        # Image-by-ID retrieval has been disabled to prevent any network
        # activity for fetching alarm/snapshot images. This function is a
        # no-op and will always return None. Re-enable only when snapshot
        # probing is required and under control.
        if self.debug:
            self._log(f"_get_image_by_id called for id {image_id} but image retrieval is disabled")
        return None