#!/usr/bin/env python3
"""
Debug script to show EXACTLY what the CloudEdge API returns for device status.
This will tell us if the problem is the API or the integration logic.
"""

import sys
import json
import argparse
import requests
import time
import hmac
import base64
import hashlib
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import padding as sym_padding


class SimpleCloudEdgeClient:
    """Minimal CloudEdge client for debugging."""
    
    BASE_URL = "https://apis.cloudedge360.com"
    DEFAULT_TIMEOUT = 10
    
    def __init__(self):
        self.session = requests.Session()
        self.session_data = None
    
    def _encrypt_password_3des(self, password):
        """Encrypt password using 3DES for authentication."""
        key = b"123456781234567812345678"
        iv = b"01234567"
        
        cipher = Cipher(
            algorithms.TripleDES(key),
            modes.CBC(iv),
            backend=default_backend()
        )
        encryptor = cipher.encryptor()
        
        padder = sym_padding.PKCS7(64).padder()
        padded_data = padder.update(password.encode()) + padder.finalize()
        
        encrypted = encryptor.update(padded_data) + encryptor.finalize()
        return base64.b64encode(encrypted).decode()
    
    def _generate_xca_headers(self, params_str, user_token):
        """Generate X-Ca headers for API requests."""
        ca_timestamp = str(int(time.time() * 1000))
        ca_nonce = str(int(time.time() * 1000000) % 100000000)
        ca_signature = base64.b64encode(
            hmac.new(user_token.encode(), params_str.encode(), hashlib.sha1).digest()
        ).decode()
        
        return {
            "X-Ca-Timestamp": ca_timestamp,
            "X-Ca-Signature": ca_signature,
            "X-Ca-Key": "meari",
            "X-Ca-Nonce": ca_nonce,
        }
    
    def authenticate(self, email, password):
        """Authenticate with CloudEdge."""
        encrypted_password = self._encrypt_password_3des(password)
        timestamp = str(int(time.time() * 1000))
        
        ca_key = "bc29be30292a4309877807e101afbd51"
        ca_nonce = str(int(time.time() * 1000000) % 100000000)
        
        # Create signature data string
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
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us) AppleWebKit/533.1",
            "X-Ca-Timestamp": timestamp,
            "X-Ca-Sign": ca_signature,
            "X-Ca-Key": ca_key,
            "X-Ca-Nonce": ca_nonce,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        
        response = self.session.post(
            f"{self.BASE_URL}/meari/app/login",
            headers=headers,
            data=login_data,
            timeout=self.DEFAULT_TIMEOUT
        )
        
        data = response.json()
        if data.get("resultCode") == "1001":
            result = data.get("result", {})
            self.session_data = {
                'userID': result.get('userID'),
                'userToken': result.get('userToken'),
                'nickName': result.get('nickName'),
            }
            print(f"   Session Data: userID={self.session_data['userID']}, token={self.session_data['userToken'][:20]}...")
            return True
        else:
            print(f"   Auth failed: {data.get('resultCode')} - {data.get('resultMsg')}")
            return False
    
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
    parser = argparse.ArgumentParser(description='Debug CloudEdge device status from API')
    parser.add_argument('email', help='CloudEdge account email')
    parser.add_argument('password', help='CloudEdge account password')
    parser.add_argument('--device-id', help='Specific device ID to check')
    args = parser.parse_args()
    
    print("=" * 80)
    print("CloudEdge API Status Debug Tool")
    print("=" * 80)
    print("\nThis will show EXACTLY what the API returns (no processing)")
    print("")
    
    # Create client
    client = SimpleCloudEdgeClient()
    
    # Authenticate
    print("[1/3] Authenticating...")
    try:
        success = client.authenticate(args.email, args.password)
        if not success:
            print("❌ Authentication failed - check credentials")
            return 1
        print("✅ Authentication successful\n")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    # Get raw device list response
    print("[2/3] Getting RAW device list from API...")
    print("-" * 80)
    
    try:
        # Make the raw API call
        device_body = client._generate_device_body()
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us; Android SDK built for arm64 Build/QSR1.211112.002) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1",
        }
        
        response = client.session.post(
            f"{client.BASE_URL}/ppstrongs/getDevice.action",
            headers=headers,
            data=device_body,
            timeout=client.DEFAULT_TIMEOUT
        )
        
        raw_response = response.json()
        
        print("\n📋 RAW API RESPONSE:")
        print("=" * 80)
        print(json.dumps(raw_response, indent=2))
        print("=" * 80)
        
        # Extract devices
        devices = []
        device_types = ['nvr', 'ipc', 'chime', 'doorbell', 'snap']
        for device_type in device_types:
            if device_type in raw_response and raw_response[device_type]:
                device_list = raw_response[device_type]
                if isinstance(device_list, list):
                    devices.extend(device_list)
        
        if not devices:
            print("\n❌ No devices found in response")
            return 1
        
        print(f"\n\n[3/3] Analyzing {len(devices)} device(s)...")
        print("=" * 80)
        
        for idx, device in enumerate(devices):
            device_id = device.get('deviceID', 'unknown')
            device_name = device.get('deviceName', 'Unknown')
            device_sn = device.get('snNum', 'unknown')
            
            # Skip if filtering by device ID
            if args.device_id and str(device_id) != str(args.device_id):
                continue
            
            print(f"\n🎥 DEVICE [{idx+1}]: {device_name}")
            print("-" * 80)
            print(f"   Device ID: {device_id}")
            print(f"   Serial: {device_sn}")
            print("")
            
            # Show ALL status-related fields
            print("   📊 STATUS FIELDS FROM API:")
            status_fields = ['onLine', 'devStatus', 'online', 'status', 'deviceStatus', 
                           'sleep', 'isOnline', 'connectStatus']
            for field in status_fields:
                if field in device:
                    value = device.get(field)
                    print(f"      {field}: {value} (type: {type(value).__name__})")
            
            print("")
            print("   🔧 DEVICE CHARACTERISTICS:")
            print(f"      iotType: {device.get('iotType')}")
            print(f"      awsCloudCompat: {device.get('awsCloudCompat', 0)}")
            print(f"      cloudSupport: {device.get('cloudSupport', 0)}")
            print(f"      p2p version: {device.get('p2p')}")
            
            # Determine what status means
            print("")
            print("   🧮 STATUS INTERPRETATION:")
            
            is_cloud_only = (device.get('iotType') == 3 and device.get('awsCloudCompat', 0) == 1)
            has_cloud_sub = (device.get('cloudSupport', 0) == 1)
            
            if is_cloud_only:
                print("      ⚠️  CLOUD-ONLY CAMERA (AWS IoT)")
                print(f"      Cloud Subscription: {'✅ ACTIVE' if has_cloud_sub else '❌ INACTIVE'}")
            
            # Check each status field
            if 'onLine' in device:
                status = "ONLINE" if device.get('onLine') == 1 else "OFFLINE"
                print(f"      onLine={device.get('onLine')} → {status}")
            
            if 'devStatus' in device:
                status = "ONLINE" if device.get('devStatus') == 1 else "OFFLINE"
                print(f"      devStatus={device.get('devStatus')} → {status}")
            
            if 'online' in device:
                val = device.get('online')
                if isinstance(val, bool):
                    status = "ONLINE" if val else "OFFLINE"
                else:
                    status = "ONLINE" if val == 1 else "OFFLINE"
                print(f"      online={val} → {status}")
            
            if 'sleep' in device:
                status = "SLEEPING" if device.get('sleep') == 1 else "AWAKE"
                print(f"      sleep={device.get('sleep')} → {status}")
            
            print("")
            print("   🌐 NETWORK INFO:")
            ip_fields = ['lanIP', 'deviceIP', 'ip', 'ipAddress']
            for field in ip_fields:
                if device.get(field):
                    print(f"      {field}: {device.get(field)}")
            
            print("")
            print("-" * 80)
        
        # Now call get_device_status API to see what IT says
        print("\n\n[BONUS] Calling get_device_status API...")
        print("=" * 80)
        
        for device in devices:
            device_id = device.get('deviceID')
            if args.device_id and str(device_id) != str(args.device_id):
                continue
                
            try:
                print(f"\nCalling get_device_status for device {device_id}...")
                
                # Make get_device_status call
                device_body = client._generate_device_body({'deviceID': device_id})
                headers = {
                    "Accept": "*/*",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": "Mozilla/5.0 (Linux; U; Android 10; en-us) AppleWebKit/533.1",
                }
                
                response = client.session.post(
                    f"{client.BASE_URL}/ppstrongs/getDeviceOnLine.action",
                    headers=headers,
                    data=device_body,
                    timeout=client.DEFAULT_TIMEOUT
                )
                
                status_result = response.json()
                print(f"\n📋 get_device_status RESPONSE:")
                print(json.dumps(status_result, indent=2))
            except Exception as e:
                print(f"❌ get_device_status failed: {e}")
        
        print("\n" + "=" * 80)
        print("✅ DEBUG COMPLETE")
        print("=" * 80)
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
