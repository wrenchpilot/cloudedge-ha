#!/usr/bin/env python3
"""
Test PPPP handshake with CloudEdge camera.

This script tests the PPPP protocol handshake implementation
against a real CloudEdge camera on the local network.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from cloudedge.client import CloudEdgeClient
from tools.pppp_client import PPPPClient, discover_cameras
import logging

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)

logger = logging.getLogger(__name__)


def get_device_info():
    """Get device information from CloudEdge API."""
    # CloudEdge API credentials
    EMAIL = "your_email@example.com"
    PASSWORD = "your_password"
    BASE_URL = "https://apis.cloudedge360.com"
    
    print("=" * 70)
    print("STEP 1: Authenticate with CloudEdge API")
    print("=" * 70)
    
    client = CloudEdgeClient(base_url=BASE_URL)
    
    print(f"\nAuthenticating as {EMAIL}...")
    if not client.authenticate(EMAIL, PASSWORD):
        logger.error("❌ Authentication failed!")
        return None
    
    print("✅ Authentication successful!")
    
    print("\n" + "=" * 70)
    print("STEP 2: Get Device Information")
    print("=" * 70)
    
    devices = client.get_devices()
    if not devices:
        logger.error("❌ No devices found!")
        return None
    
    print(f"\n✅ Found {len(devices)} device(s)")
    
    # Get first device
    device = devices[0]
    device_id = device.get('deviceID')
    serial = device.get('sn')
    
    print(f"\nDevice Information:")
    print(f"  Device ID: {device_id}")
    print(f"  Serial: {serial}")
    print(f"  Model: {device.get('deviceModel', 'Unknown')}")
    print(f"  Firmware: {device.get('firmwareVersion', 'Unknown')}")
    
    # Get detailed device info including hostKey
    print(f"\nGetting detailed device info...")
    device_status = client.get_device_status(device_id)
    
    if not device_status:
        logger.error("❌ Failed to get device status!")
        return None
    
    # Look for hostKey in device status
    host_key = None
    for key in ['hostKey', 'host_key', 'p2pKey', 'p2p_key']:
        if key in device_status:
            host_key = device_status[key]
            break
    
    # If not in status, check device info
    if not host_key:
        for key in ['hostKey', 'host_key', 'p2pKey', 'p2p_key']:
            if key in device:
                host_key = device[key]
                break
    
    if not host_key:
        logger.warning("⚠️  hostKey not found in API response!")
        logger.info("Available fields: " + ", ".join(device_status.keys()))
        host_key = "UNKNOWN"
    else:
        print(f"  Host Key: {host_key}")
    
    # Try to get local IP
    local_ip = device_status.get('localIP') or device.get('localIP')
    if not local_ip:
        logger.warning("⚠️  Local IP not found in API response!")
        local_ip = None
    else:
        print(f"  Local IP: {local_ip}")
    
    return {
        'device_id': device_id,
        'serial': serial,
        'host_key': host_key,
        'local_ip': local_ip,
        'device': device,
        'status': device_status
    }


def test_p2p_connection(device_info):
    """Test P2P connection to camera."""
    local_ip = device_info.get('local_ip')
    serial = device_info.get('serial')
    host_key = device_info.get('host_key')
    
    if not local_ip:
        print("\n" + "=" * 70)
        print("STEP 3: Discover Camera on Local Network")
        print("=" * 70)
        
        print("\nScanning local network for cameras...")
        cameras = discover_cameras(timeout=5.0)
        
        if not cameras:
            logger.error("❌ No cameras discovered on local network!")
            logger.info("Please manually specify camera IP address")
            return False
        
        print(f"\n✅ Discovered {len(cameras)} camera(s):")
        for i, ip in enumerate(cameras, 1):
            print(f"  {i}. {ip}")
        
        # Use first discovered camera
        local_ip = cameras[0]
        print(f"\nUsing camera at {local_ip}")
    
    print("\n" + "=" * 70)
    print("STEP 4: Test PPPP Handshake")
    print("=" * 70)
    
    print(f"\nConnecting to camera...")
    print(f"  IP: {local_ip}")
    print(f"  Serial: {serial}")
    print(f"  Host Key: {host_key[:20]}..." if len(host_key) > 20 else f"  Host Key: {host_key}")
    
    try:
        with PPPPClient(local_ip, serial, host_key) as p2p_client:
            print("\n✅ PPPP handshake successful!")
            
            print("\n" + "=" * 70)
            print("STEP 5: Request Video Stream")
            print("=" * 70)
            
            print("\nRequesting video stream...")
            if p2p_client.request_video_stream():
                print("✅ Video stream request sent")
                
                print("\nReceiving video frames (10 attempts)...")
                for i in range(10):
                    frame = p2p_client.receive_video_frame(timeout=2.0)
                    if frame:
                        print(f"  Frame {i+1}: {len(frame)} bytes ✅")
                        
                        # Try to detect H.264 NAL units
                        if b'\x00\x00\x00\x01' in frame[:20]:
                            print(f"    → H.264 start code detected!")
                        elif b'\x00\x00\x01' in frame[:20]:
                            print(f"    → H.264 short start code detected!")
                        else:
                            print(f"    → Encrypted or unknown format")
                            print(f"    → First 16 bytes: {frame[:16].hex()}")
                    else:
                        print(f"  Frame {i+1}: No data ⏳")
            else:
                logger.error("❌ Failed to request video stream")
                return False
            
            print("\n" + "=" * 70)
            print("SUMMARY")
            print("=" * 70)
            print("\n✅ PPPP Protocol Implementation Status:")
            print("  [✅] Handshake (HELLO, VERSION)")
            print("  [✅] Session establishment")
            print("  [⏳] Video stream request (sent, awaiting response)")
            print("  [❓] Video encryption (needs analysis)")
            print("  [❌] Video decoding (blocked on encryption)")
            
            return True
            
    except Exception as e:
        logger.error(f"❌ P2P connection failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main test function."""
    print("╔" + "═" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║" + "  CloudEdge PPPP Protocol Test".center(68) + "║")
    print("║" + " " * 68 + "║")
    print("╚" + "═" * 68 + "╝")
    
    # Get device info from API
    device_info = get_device_info()
    if not device_info:
        logger.error("Failed to get device information from API")
        return 1
    
    # Test P2P connection
    if not test_p2p_connection(device_info):
        logger.error("P2P connection test failed")
        return 1
    
    print("\n" + "=" * 70)
    print("TEST COMPLETE ✅")
    print("=" * 70)
    print("\nNext Steps:")
    print("  1. Analyze encrypted video packets")
    print("  2. Reverse engineer encryption algorithm")
    print("  3. Implement video decryption")
    print("  4. Decode H.264/H.265 frames")
    print("  5. Integrate with Home Assistant")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
