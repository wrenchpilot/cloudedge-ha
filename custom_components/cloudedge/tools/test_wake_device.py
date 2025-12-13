#!/usr/bin/env python3
"""
Test script for CloudEdge wake_device API.

This script tests the removeWake.action endpoint that wakes a sleeping camera
and returns P2P connection parameters for direct streaming.

Usage (run from HA container or with proper Python path):
    # From HA container:
    python /config/custom_components/cloudedge/tools/test_wake_device.py <email> <password>
    
    # Standalone test (from project root):
    PYTHONPATH=custom_components/cloudedge python tools/test_wake_device.py <email> <password>
"""

import sys
import os
import argparse
import json
import hashlib
import base64
import hmac
import time
import random
import string
from urllib.parse import quote

# Always use standalone client for this test tool (avoids import conflicts)
import requests

# Minimal standalone CloudEdge client for testing wake_device
class CloudEdgeClient:
        """Minimal CloudEdge client for standalone testing."""
        
        # Use the correct US region endpoint
        BASE_URL = "https://apis.cloudedge360.com"
        DEFAULT_TIMEOUT = 30
        
        def __init__(self, debug=True):
            self.debug = debug
            self.session_data = None
            self.session = requests.Session()
            
        def _log(self, msg):
            if self.debug:
                print(f"[DEBUG] {msg}")
                
        def _generate_random_string(self, length=16):
            return ''.join(random.choices(string.ascii_letters + string.digits, k=length))
            
        def _generate_url_timestamp(self):
            return int(time.time() * 1000)
            
        def _generate_api_signature(self, params_str, key=None):
            """Generate API signature using HMAC-SHA1."""
            secret_key = key or "cloudedge_app_key"
            signature = hmac.new(
                secret_key.encode('utf-8'),
                params_str.encode('utf-8'),
                hashlib.sha1
            ).digest()
            return base64.b64encode(signature).decode('utf-8')
            
        def authenticate(self, email, password):
            """Authenticate using the same method as the main CloudEdge client."""
            self._log(f"Authenticating as {email}...")
            
            # Encrypt credentials using 3DES (matching main client)
            timestamp = int(time.time() * 1000)
            
            try:
                from cryptography.hazmat.primitives.ciphers import Cipher, modes
                from cryptography.hazmat.primitives import padding
                from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
                
                key = "123456781234567812345678".encode('utf-8')
                iv = "01234567".encode('utf-8')
                
                algorithm = TripleDES(key)
                cipher = Cipher(algorithm, modes.CBC(iv))
                encryptor = cipher.encryptor()
                
                padder = padding.PKCS7(64).padder()
                padded_data = padder.update(password.encode('utf-8')) + padder.finalize()
                
                encrypted = encryptor.update(padded_data) + encryptor.finalize()
                encrypted_password = base64.b64encode(encrypted).decode('utf-8')
            except Exception as e:
                raise Exception(f"Password encryption failed: {e}")
            
            # Generate headers matching main client
            ca_timestamp = str(timestamp)
            ca_nonce = str(int(time.time() * 1000000) % 100000000)
            ca_key = "bc29be30292a4309877807e101afbd51"
            
            # Create signature
            ca_sign_data = (
                f"phoneType=a&sourceApp=8&appVer=5.5.1&iotType=4&equipmentNo=&"
                f"appVerCode=551&localTime={timestamp}&password={encrypted_password}&"
                f"t={timestamp}&lngType=en&countryCode=US&"
                f"userAccount={email}&phoneCode=+1"
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
                "countryCode": "US",
                "userAccount": email,
                "phoneCode": "+1"
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
            
            url = f"{self.BASE_URL}/meari/app/login"
            self._log(f"Request URL: {url}")
            
            response = self.session.post(url, headers=headers, data=login_data, timeout=self.DEFAULT_TIMEOUT)
            self._log(f"Response status: {response.status_code}")
            if response.status_code != 200:
                self._log(f"Response text: {response.text[:500]}")
            
            data = response.json()
            
            if data.get("resultCode") == "1001":
                result = data.get("result", {})
                user_token = result.get("userToken")
                user_id = result.get("userID")
                
                if not user_token or not user_id:
                    raise Exception("Missing user token or ID in response")
                
                self.session_data = {
                    "userToken": user_token,
                    "userID": user_id,
                }
                self._log("Authentication successful!")
                return self.session_data
            else:
                error_msg = data.get('resultMsg', 'Unknown error')
                error_code = data.get('resultCode', 'unknown')
                raise Exception(f"Auth failed: {error_msg} (Code: {error_code})")
                
        def get_devices(self):
            """Get device list."""
            if not self.session_data:
                raise Exception("Not authenticated")
                
            device_body = self._generate_device_body()
            
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us) AppleWebKit/533.1",
            }
            
            response = self.session.post(
                f"{self.BASE_URL}/ppstrongs/getDeviceList.action",
                headers=headers,
                data=device_body,
                timeout=self.DEFAULT_TIMEOUT
            )
            data = response.json()
            
            if data.get("resultCode") == "1001":
                return data.get("result", [])
            else:
                raise Exception(f"Get devices failed: {data.get('resultMsg')}")
                
        def wake_device(self, device_id):
            """Wake device and get P2P connection parameters."""
            if not self.session_data:
                raise Exception("Not authenticated")
                
            self._log(f"Waking device {device_id}...")
            
            device_body = self._generate_device_body({'deviceID': device_id})
            
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us) AppleWebKit/533.1",
            }
            
            response = self.session.post(
                f"{self.BASE_URL}/ppstrongs/removeWake.action",
                headers=headers,
                data=device_body,
                timeout=self.DEFAULT_TIMEOUT
            )
            data = response.json()
            
            self._log(f"Wake response: {json.dumps(data, indent=2)}")
            
            if data.get("resultCode") == "1001":
                result = data.get("result", {})
                connect_string_raw = result.get("getConnectString", "")
                
                if connect_string_raw:
                    try:
                        connect_params = json.loads(connect_string_raw)
                        return {
                            'success': True,
                            'connect_string': connect_params,
                            'raw_result': result
                        }
                    except json.JSONDecodeError:
                        return {
                            'success': True,
                            'connect_string_raw': connect_string_raw,
                            'raw_result': result
                        }
                else:
                    return {
                        'success': True,
                        'connect_string': None,
                        'raw_result': result
                    }
            else:
                raise Exception(f"Wake failed: {data.get('resultMsg')}")
                
        def _generate_device_body(self, extra_params=None):
            """Generate device request body."""
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


def main():
    parser = argparse.ArgumentParser(description='Test CloudEdge wake_device API')
    parser.add_argument('email', help='CloudEdge account email')
    parser.add_argument('password', help='CloudEdge account password')
    parser.add_argument('--device-id', help='Specific device ID to wake (optional)')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()
    
    print("=" * 60)
    print("CloudEdge Wake Device API Test")
    print("=" * 60)
    
    # Create client
    client = CloudEdgeClient(debug=args.debug)
    
    # Authenticate
    print(f"\n[1/3] Authenticating as {args.email}...")
    try:
        client.authenticate(args.email, args.password)
        print("✅ Authentication successful!")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        return 1
    
    # Get device list
    print("\n[2/3] Getting device list...")
    try:
        devices = client.get_devices()
        if not devices:
            print("❌ No devices found in account!")
            return 1
            
        print(f"✅ Found {len(devices)} device(s):")
        for i, device in enumerate(devices):
            device_id = device.get('deviceID', 'unknown')
            device_name = device.get('deviceName', 'Unknown')
            device_sn = device.get('snNum') or device.get('deviceUUID', 'unknown')
            print(f"   [{i+1}] {device_name} (ID: {device_id}, SN: {device_sn})")
        
        # Select device to wake
        if args.device_id:
            target_device = args.device_id
        else:
            # Use first device
            target_device = devices[0].get('deviceID')
            
        print(f"\n[3/3] Waking device {target_device}...")
        
    except Exception as e:
        print(f"❌ Failed to get devices: {e}")
        return 1
    
    # Wake device
    try:
        result = client.wake_device(target_device)
        
        print("\n" + "=" * 60)
        print("WAKE DEVICE RESPONSE")
        print("=" * 60)
        
        if result.get('success'):
            print("✅ Wake request successful!")
            
            connect_string = result.get('connect_string')
            if connect_string:
                print("\n📡 P2P Connection Parameters:")
                print("-" * 40)
                
                # Display key P2P parameters
                params_to_show = [
                    ('udpport', 'UDP Port'),
                    ('did', 'Device ID (P2P)'),
                    ('initstring', 'Init String'),
                    ('licenceid', 'License ID'),
                    ('protocolv', 'Protocol Version'),
                    ('username', 'Username'),
                    ('password', 'Password (MD5)'),
                    ('mode', 'Connection Mode'),
                    ('trytimes', 'Try Times'),
                    ('delaysec', 'Delay Seconds'),
                    ('factory', 'Factory'),
                ]
                
                for key, label in params_to_show:
                    value = connect_string.get(key, 'N/A')
                    if key == 'initstring' and value != 'N/A' and len(str(value)) > 50:
                        value = str(value)[:50] + '...'
                    if key == 'password' and value != 'N/A':
                        print(f"   {label}: {value}")  # Show MD5 hash
                    else:
                        print(f"   {label}: {value}")
                
                # Full JSON for debugging
                print("\n📋 Full Connect String JSON:")
                print("-" * 40)
                print(json.dumps(connect_string, indent=2))
                
            else:
                print("\n⚠️  No connect string returned - device may already be awake")
                print("   Try direct P2P connection or check device status")
                
            # Raw result
            print("\n📋 Raw API Result:")
            print("-" * 40)
            raw = result.get('raw_result', {})
            print(json.dumps(raw, indent=2, default=str))
            
        else:
            print("❌ Wake request failed")
            print(f"   Result: {result}")
            
    except Exception as e:
        print(f"\n❌ Wake device failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    print("\n" + "=" * 60)
    print("Next Steps:")
    print("=" * 60)
    print("1. Use the P2P connection parameters to connect to the camera")
    print("2. The UDP port should now be open on the camera for P2P traffic")
    print("3. Run cloudedge_p2p_client.py with the connection parameters")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
