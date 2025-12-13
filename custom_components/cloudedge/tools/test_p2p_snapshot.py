#!/usr/bin/env python3
"""
CloudEdge P2P Snapshot Test
============================

End-to-end test that:
1. Authenticates with CloudEdge API (using pycloudedge)
2. Gets device list and analyzes P2P capability
3. Tests P2P connectivity
4. Attempts to capture a snapshot if P2P is available

Usage:
    python test_p2p_snapshot.py <email> <password> <country_code> <phone_code> [options]
    
Example:
    python test_p2p_snapshot.py user@email.com mypassword US +1 --camera-ip 10.0.0.40 --output snapshot.jpg
"""

import argparse
import asyncio
import json
import os
import socket
import struct
import sys
import time

# Force LOCAL cloudedge module (not pycloudedge from site-packages)
# This ensures we use the HA integration's version with capability fields
_tools_dir = os.path.dirname(os.path.abspath(__file__))
_cloudedge_dir = os.path.dirname(_tools_dir)
sys.path.insert(0, _cloudedge_dir)

# Prefer package-qualified import when running as module; fall back to local package import
try:
    # When running with -m from repo root this resolves correctly
    from custom_components.cloudedge.cloudedge.client import CloudEdgeClient
    _client_module = __import__('custom_components.cloudedge.cloudedge.client', fromlist=['dummy'])
except Exception:
    # Fallback: adjust sys.path (already done above) and import local cloudedge package
    from cloudedge.client import CloudEdgeClient
    import cloudedge.client as _client_module

# Warn if we're not using the local integration module
try:
    if 'cloudedge-ha' not in getattr(_client_module, '__file__', ''):
        print(f"WARNING: Using pycloudedge from {_client_module.__file__}")
        print(f"         Expected: cloudedge-ha local module")
except Exception:
    pass


def check_p2p_reachable(device_ip: str, timeout: float = 3.0) -> bool:
    """
    Quick check if camera responds to P2P discovery protocol.
    
    Sends a PPPP LanSearch packet and waits for response.
    """
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
    except Exception as e:
        print(f"   P2P probe error: {e}")
        return False


async def capture_snapshot_async(camera_ip: str, connect_params: dict, output_file: str) -> bool:
    """
    Capture snapshot using aiopppp library.
    """
    try:
        from aiopppp import Device
        
        print(f"\n📷 Connecting to camera at {camera_ip}...")
        
        username = connect_params.get('username', 'admin')
        password = connect_params.get('password', '6666')
        
        print(f"   Using credentials: {username} / {'*' * len(str(password))}")
        
        async with Device(camera_ip, username=username, password=password) as device:
            print(f"   ✅ Connected! Device properties: {device.properties}")
            
            print("   Starting video stream...")
            await device.start_video()
            
            print("   Waiting for video frame...")
            try:
                frame = await asyncio.wait_for(device.get_video_frame(), timeout=15.0)
                print(f"   ✅ Got frame: {len(frame.data)} bytes")
                
                # Save raw frame data
                with open(output_file, 'wb') as f:
                    f.write(frame.data)
                print(f"   Saved raw frame to {output_file}")
                
                return True
                
            except asyncio.TimeoutError:
                print("   ❌ Timeout waiting for video frame")
                return False
            finally:
                await device.stop_video()
                
    except ImportError as e:
        print(f"\n⚠️  aiopppp library not installed: {e}")
        print("   Install with: pip install aiopppp")
        return False
    except Exception as e:
        print(f"\n❌ Snapshot error: {e}")
        import traceback
        traceback.print_exc()
        return False


def capture_snapshot_sync(camera_ip: str, connect_params: dict, output_file: str) -> bool:
    """
    Synchronous wrapper for snapshot capture.
    """
    return asyncio.run(capture_snapshot_async(camera_ip, connect_params, output_file))


def main():
    parser = argparse.ArgumentParser(
        description='CloudEdge P2P Snapshot Test - Wake, connect, and capture',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic test with auto-discovered camera IP
  python test_p2p_snapshot.py user@email.com password US +1

  # Specify camera IP and output file
  python test_p2p_snapshot.py user@email.com password US +1 --camera-ip 10.0.0.40 --output snapshot.jpg

  # Specify a particular device by ID
  python test_p2p_snapshot.py user@email.com password US +1 --device-id 1009985222
        """
    )
    
    parser.add_argument('email', help='CloudEdge account email')
    parser.add_argument('password', help='CloudEdge account password')
    parser.add_argument('country_code', help='Country code (e.g., US, IT, CN)')
    parser.add_argument('phone_code', help='Phone code (e.g., +1, +39, +86)')
    parser.add_argument('--device-id', '-d', help='Device ID to use (uses first device if not specified)')
    parser.add_argument('--camera-ip', '-i', help='Camera LAN IP address')
    parser.add_argument('--output', '-o', default='snapshot.jpg', help='Output file for snapshot')
    parser.add_argument('--skip-wake', action='store_true', help='Skip wake step (camera already awake)')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    
    args = parser.parse_args()
    
    print("\n" + "="*60)
    print("🎥 CloudEdge P2P Snapshot Test")
    print("="*60)
    
    # Step 1: Create client and login using pycloudedge
    print(f"\n🔐 Logging in as {args.email}...")
    client = CloudEdgeClient(
        username=args.email,
        password=args.password,
        country_code=args.country_code,
        phone_code=args.phone_code,
        debug=args.debug
    )
    
    try:
        if not client.authenticate():
            print("❌ Authentication failed!")
            return 1
        print("   ✅ Login successful!")
    except Exception as e:
        print(f"❌ Authentication error: {e}")
        return 1
    
    # Step 2: Get devices
    print("\n📱 Fetching device list...")
    try:
        devices = client.get_all_devices()
        if not devices:
            print("❌ No devices found")
            return 1
        print(f"   Found {len(devices)} device(s)")
    except Exception as e:
        print(f"❌ Failed to get devices: {e}")
        return 1
    
    # Find target device
    device = None
    if args.device_id:
        for d in devices:
            if str(d.get('device_id')) == args.device_id:
                device = d
                break
        if not device:
            print(f"❌ Device {args.device_id} not found")
            print("Available devices:")
            for d in devices:
                print(f"   - {d.get('name')} (ID: {d.get('device_id')})")
            return 1
    else:
        device = devices[0]
    
    device_id = str(device.get('device_id'))
    device_name = device.get('name', 'Unknown')
    device_sn = device.get('serial_number', 'Unknown')
    device_ip = device.get('device_ip')
    
    print(f"\n🎯 Target device: {device_name}")
    print(f"   Device ID: {device_id}")
    print(f"   Serial: {device_sn}")
    print(f"   Known IP: {device_ip}")
    
    # Check device sleep status from raw data
    is_sleeping = device.get('sleep') == 'on'
    is_online = device.get('online', False) or device.get('devStatus') == 1
    print(f"   Sleep mode: {'on' if is_sleeping else 'off'}")
    print(f"   Online: {is_online}")
    
    # ==========================================
    # CRITICAL: Camera Capability Analysis
    # ==========================================
    print(f"\n📊 Camera Capability Analysis:")
    print("-" * 40)
    
    # Extract key capability indicators
    iot_type = device.get('iot_type') or device.get('iotType')
    aws_cloud_compat = device.get('aws_cloud_compat') or device.get('awsCloudCompat', 0)
    cloud_support = device.get('cloud_support') or device.get('cloudSupport', 0)
    p2p_version = device.get('p2p') or device.get('p2pVersion')
    firmware = device.get('firmware', 'Unknown')
    
    print(f"   iotType: {iot_type}")
    print(f"   awsCloudCompat: {aws_cloud_compat}")
    print(f"   cloudSupport: {cloud_support}")
    print(f"   P2P version: {p2p_version}")
    print(f"   Firmware: {firmware}")
    
    # Determine if this is a cloud-only camera
    is_cloud_only = (iot_type == 3 and aws_cloud_compat == 1)
    has_cloud_subscription = cloud_support == 1
    
    if is_cloud_only:
        print(f"\n⚠️  CLOUD-ONLY CAMERA DETECTED!")
        print("-" * 40)
        print("   This camera uses AWS IoT-based P2P (iotType=3, awsCloudCompat=1).")
        print("   P2P connections are brokered through cloud servers, not direct LAN.")
        print("   Direct P2P protocol (PPPP) is NOT supported by this camera model.")
        
        if has_cloud_subscription:
            print(f"\n   ✅ Cloud subscription IS active (cloudSupport=1)")
            print("   You can use cloud-based snapshot APIs.")
        else:
            print(f"\n   ❌ Cloud subscription NOT active (cloudSupport=0)")
            print("   Cloud alarm APIs will also fail without subscription.")
        
        print("\n   📋 Your options:")
        print("      1. Subscribe to cloud storage (enables cloudSupport)")
        print("      2. Replace with ONVIF/RTSP compatible camera")
        print("      3. Try third-party firmware (RISKY - may brick device)")
        print("")
        
        # Still try P2P to confirm - user might want to see the failure
        print("   Continuing with P2P test to confirm (expected to fail)...")
    else:
        print(f"\n   ✅ Camera may support direct P2P")
    
    # Override IP if specified
    camera_ip = args.camera_ip or device_ip
    if not camera_ip:
        print("❌ No camera IP available. Specify with --camera-ip")
        return 1
    
    print(f"   Using IP: {camera_ip}")
    
    # ==========================================
    # P2P Reachability Test
    # ==========================================
    print(f"\n🔌 Testing P2P Reachability...")
    print("-" * 40)
    p2p_reachable = check_p2p_reachable(camera_ip, timeout=3.0)
    
    if not p2p_reachable:
        print(f"\n❌ P2P UNREACHABLE")
        print("-" * 40)
        print(f"   Camera at {camera_ip} does not respond to PPPP protocol (UDP:32108).")
        
        if is_cloud_only:
            print("\n   This confirms the camera is cloud-only.")
            print("   Direct P2P snapshot capture is NOT possible with this device.")
            print("\n" + "="*60)
            print("📋 SUMMARY: Camera requires cloud subscription for snapshots")
            print("="*60)
            return 1
        else:
            print("\n   Possible causes:")
            print("      - Camera is behind NAT/firewall blocking UDP")
            print("      - Camera is sleeping/offline")
            print("      - IP address is incorrect")
            print("      - Camera uses different P2P protocol")
            print("\n   Try:")
            print("      - Verify IP with: ping", camera_ip)
            print("      - Check camera is online in CloudEdge app")
            print("      - Try waking device first")
            
            # Don't give up - let it try anyway
            print("\n   Continuing to attempt P2P connection anyway...")
    else:
        print(f"   ✅ Camera responds to PPPP protocol!")
    
    # Get host key for P2P auth
    host_key = device.get('host_key') or device.get('hostKey')
    print(f"   Host Key: {host_key[:16]}..." if host_key else "   Host Key: None")
    
    # Step 3: Wake device only if sleeping
    connect_params = {}
    if not args.skip_wake and is_sleeping:
        print(f"\n📡 Waking device {device_id} (currently sleeping)...")
        try:
            wake_result = client.wake_device(device_id)
            if not wake_result.get('success'):
                print(f"⚠️  Wake returned: {wake_result}")
            else:
                connect_params = wake_result.get('connect_string', {}) or {}
                print(f"   ✅ Device woken!")
                if connect_params:
                    print(f"      UDP Port: {connect_params.get('udpport')}")
                    print(f"      License ID: {connect_params.get('licenceid')}")
                
            # Wait for camera to wake
            print("\n⏳ Waiting 2 seconds for camera to wake...")
            time.sleep(2)
            
        except Exception as e:
            print(f"⚠️  Wake failed (may already be awake): {e}")
    elif args.skip_wake:
        print("\n⏩ Skipping wake (--skip-wake)")
    else:
        print(f"\n✅ Device already online (sleep={device.get('sleep')}), skipping wake")
    
    # Add host_key to connect_params for P2P auth
    if host_key:
        connect_params['password'] = host_key
    
    # Step 4: Capture snapshot
    print(f"\n📷 Attempting to capture snapshot...")
    success = capture_snapshot_sync(camera_ip, connect_params, args.output)
    
    if success:
        print(f"\n" + "="*60)
        print("✅ SUCCESS!")
        print("="*60)
        print(f"   Snapshot saved to: {args.output}")
        return 0
    else:
        print(f"\n" + "="*60)
        print("❌ FAILED TO CAPTURE SNAPSHOT")
        print("="*60)
        
        # Provide context-aware troubleshooting
        if is_cloud_only:
            print(f"\n📋 DIAGNOSIS: Cloud-Only Camera")
            print("-" * 40)
            print("   Your camera (iotType=3, awsCloudCompat=1) requires")
            print("   AWS IoT cloud-mediated P2P connections.")
            print("")
            print("   Direct P2P snapshot is NOT possible with this device.")
            print("")
            print("   Options:")
            print("   1. Subscribe to CloudEdge cloud storage")
            print("      - Enables cloud API for snapshots/recordings")
            print("")
            print("   2. Replace camera with ONVIF/RTSP compatible device")
            print("      - Recommended: Reolink, Amcrest, Eufy (wired)")
            print("      - Full Home Assistant compatibility")
            print("")
            print("   3. Continue using camera for live view only")
            print("      - Live view works through CloudEdge app")
            print("      - No local snapshot capability")
        else:
            print(f"\n📋 Troubleshooting Steps:")
            print("-" * 40)
            print("   1. Verify aiopppp is installed:")
            print("      pip install aiopppp")
            print("")
            print("   2. Check camera IP is correct and reachable:")
            print(f"      ping {camera_ip}")
            print("")
            print("   3. Ensure camera is online and not sleeping:")
            print("      - Open CloudEdge app and check device status")
            print("")
            print("   4. Try running with --debug for more details:")
            print(f"      python {sys.argv[0]} ... --debug")
            print("")
            print("   5. Check firewall isn't blocking UDP port 32108")
        
        return 1


if __name__ == '__main__':
    sys.exit(main())
