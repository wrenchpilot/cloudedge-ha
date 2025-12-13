#!/usr/bin/env python3
"""
Test script to discover CloudEdge alarm API endpoints.

This script tests various potential API endpoint paths to find the one
that returns alarm/motion event data with image URLs.

Usage:
    python test_alarm_api.py <username> <password> <country_code> <phone_code>

Example:
    python test_alarm_api.py user@example.com mypassword US +1
"""

import sys
import os
import json
import datetime
import traceback

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cloudedge.client import CloudEdgeClient


def test_alarm_endpoints(client: CloudEdgeClient, device_id: int, device_serial: str):
    """Test alarm API endpoints and report results."""
    print(f"\n{'='*60}")
    print(f"Testing Alarm API Endpoints")
    print(f"Device ID: {device_id}")
    print(f"Device Serial: {device_serial}")
    print(f"Date: {datetime.datetime.now().strftime('%Y%m%d')}")
    print(f"{'='*60}\n")
    
    # Show iotPlatformKeys for debugging
    iot_keys = client.session_data.get('iotPlatformKeys', {})
    if iot_keys:
        print(f"OpenAPI domain: {iot_keys.get('openapidomain', 'NOT SET')}")
        print(f"Platform domain: {iot_keys.get('platformdomain', 'NOT SET')}")
        print(f"Access ID: {'***' if iot_keys.get('accessid') else 'NOT SET'}")
        print()
    
    # Test the main method
    print("Testing get_alarm_events()...")
    print("(This will try multiple endpoints and report results)\n")
    
    try:
        events = client.get_alarm_events(device_id, limit=5)
        if events:
            print(f"\n✓ SUCCESS! Found {len(events)} alarm events!\n")
            for i, event in enumerate(events):
                print(f"  Event {i+1}:")
                print(f"    ID: {event.get('event_id')}")
                print(f"    Type: {event.get('event_type')}")
                print(f"    Time: {event.get('event_time')}")
                img_url = event.get('image_url', 'N/A')
                print(f"    Image URL: {img_url[:100]}..." if len(str(img_url)) > 100 else f"    Image URL: {img_url}")
                print(f"    Encrypted: {event.get('is_encrypted')}")
        else:
            print("✗ No alarm events returned (API may have worked but no events available)")
            print("  This could mean:")
            print("    - No motion/alarm events have occurred recently")
            print("    - The endpoint worked but returned empty list")
            print("    - All endpoint attempts failed (check debug output above)")
    except Exception as e:
        print(f"✗ get_alarm_events() failed: {e}")
        traceback.print_exc()
    
    # Test latest alarm image
    print("\n" + "-"*60)
    print("Testing get_latest_alarm_image()...")
    try:
        image = client.get_latest_alarm_image(device_id, device_serial)
        if image:
            print(f"✓ SUCCESS! Got image data ({len(image)} bytes)")
            # Check if it's a valid JPEG
            if image[:2] == b'\xff\xd8':
                print("  → Valid JPEG header detected!")
                # Save to file for inspection
                output_path = f"/tmp/cloudedge_alarm_test_{device_id}.jpg"
                with open(output_path, 'wb') as f:
                    f.write(image)
                print(f"  → Image saved to: {output_path}")
            elif image[:4] == b'\x89PNG':
                print("  → Valid PNG header detected!")
                output_path = f"/tmp/cloudedge_alarm_test_{device_id}.png"
                with open(output_path, 'wb') as f:
                    f.write(image)
                print(f"  → Image saved to: {output_path}")
            else:
                print(f"  → Unknown format, first bytes: {image[:10].hex()}")
                output_path = f"/tmp/cloudedge_alarm_test_{device_id}.bin"
                with open(output_path, 'wb') as f:
                    f.write(image)
                print(f"  → Raw data saved to: {output_path}")
        else:
            print("✗ No image data returned")
            print("  This could mean:")
            print("    - No alarm events with images available")
            print("    - Image download failed")
            print("    - All endpoints failed to return events")
    except Exception as e:
        print(f"✗ get_latest_alarm_image() failed: {e}")
        traceback.print_exc()
    
    print("\n" + "="*60)
    print("\nSUMMARY:")
    print("If you see 'No alarm events' but the device does have motion events,")
    print("the endpoint may not be correct. Check the CloudEdge mobile app to")
    print("confirm there are actual alarm/motion events for this device.")
    print("="*60)


def main():
    if len(sys.argv) < 5:
        print("Usage: python test_alarm_api.py <username> <password> <country_code> <phone_code>")
        print("Example: python test_alarm_api.py user@example.com mypassword US +1")
        sys.exit(1)
    
    username = sys.argv[1]
    password = sys.argv[2]
    country_code = sys.argv[3]
    phone_code = sys.argv[4]
    
    print(f"CloudEdge Alarm API Endpoint Discovery Tool")
    print(f"User: {username}")
    print(f"Region: {country_code}")
    
    # Create client with debug enabled
    client = CloudEdgeClient(
        username=username,
        password=password,
        country_code=country_code,
        phone_code=phone_code,
        debug=True
    )
    
    print("\nAuthenticating...")
    try:
        if client.authenticate():
            print("✓ Authentication successful!")
        else:
            print("✗ Authentication failed!")
            sys.exit(1)
    except Exception as e:
        print(f"✗ Authentication error: {e}")
        sys.exit(1)
    
    # Get devices
    print("\nFetching devices...")
    try:
        devices = client.get_devices()
        if not devices:
            print("✗ No devices found!")
            sys.exit(1)
        
        print(f"Found {len(devices)} device(s):")
        for i, device in enumerate(devices):
            print(f"  {i+1}. {device.get('name')} (ID: {device.get('device_id')}, SN: {device.get('serial_number')})")
        
        # Test with first device
        device = devices[0]
        test_alarm_endpoints(
            client, 
            device.get('device_id'),
            device.get('serial_number')
        )
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
