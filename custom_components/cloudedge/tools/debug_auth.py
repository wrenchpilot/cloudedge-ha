"""
Debug authentication helper for CloudEdge integration

Usage (from within HA container):

python /config/custom_components/cloudedge/tools/debug_auth.py \
  --username 'your-email' --password 'your-password' --country-code US --phone-code +1 --region US

The script will attempt to import the vendored package and run authenticate(), printing masked request info and full tracebacks if errors occur.
"""

import argparse
import sys
import traceback

# Ensure we're loading vendored integration first
sys.path.insert(0, "/config/custom_components/cloudedge")

from cloudedge import CloudEdgeClient
from cloudedge.exceptions import AuthenticationError, CloudEdgeError

MASK_LEN = 3


def mask_secret(s: str) -> str:
    if not s:
        return ""
    return s[:MASK_LEN] + "..." + s[-MASK_LEN:] if len(s) > MASK_LEN * 2 else "*" * len(s)


def run_debug(username, password, country_code, phone_code, region, base_url, openapi_base_url):
    try:
        print(f"Instantiating CloudEdge client for {mask_secret(username)} (region={region}, country={country_code}, phone={phone_code})")
        client = CloudEdgeClient(
            username=username,
            password=password,
            country_code=country_code,
            phone_code=phone_code,
            debug=True,
            session_cache_file="/tmp/cloudedge_debug_cache",
            region=(region if region and str(region).upper() != "AUTO" else None),
            base_url=(base_url or None),
            openapi_base_url=(openapi_base_url or None),
        )

        print("Attempting to authenticate... (this will raise on error)")
        success = client.authenticate()
        if success:
            print("Authentication OK")
            # Print a bit of session data without secrets
            session = client.session_data or {}
            print("Session data keys:", list(session.keys()))
            if session.get("userToken"):
                print("userToken:", mask_secret(session.get("userToken")))
            print("iotPlatformKeys:", session.get('iotPlatformKeys'))
        else:
            print("Authentication returned False (unexpected)")
        # After successful auth, list devices and optionally try snapshot tests
        try:
            devices = client.get_all_devices()
            print(f"Found {len(devices)} devices")
            for d in devices:
                print(f"  - {d.get('name')} (SN: {d.get('serial_number')})")
                # Show thumbnail URL if available
                if d.get('thumbnail_url'):
                    print(f"    thumbnail_url: {d.get('thumbnail_url')[:80]}...")
                else:
                    print(f"    thumbnail_url: NOT AVAILABLE (device uses P2P, no cloud thumbnail)")
        except Exception as e:
            print("Could not list devices:")
            traceback.print_exc()

    except AuthenticationError as e:
        print("AuthenticationError:")
        traceback.print_exc()
    except CloudEdgeError as e:
        print("CloudEdgeError:")
        traceback.print_exc()
    except Exception as e:
        print("Unexpected error:")
        traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Debug CloudEdge auth using the vendored integration")

    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--country-code", default="US")
    parser.add_argument("--phone-code", default="+1")
    parser.add_argument("--region", default="AUTO")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--openapi-base-url", default="")
    parser.add_argument("--list-devices", action="store_true", help="List devices after authentication")
    parser.add_argument("--snapshot", help="Attempt to retrieve a snapshot for the given serial number")
    parser.add_argument("--print-config", help="Print raw device configuration for given serial number")

    args = parser.parse_args()
    run_debug(args.username, args.password, args.country_code, args.phone_code, args.region, args.base_url, args.openapi_base_url)

    # If requested, attempt snapshot retrieval
    if getattr(args, 'snapshot'):
        try:
            client = CloudEdgeClient(
                username=args.username,
                password=args.password,
                country_code=args.country_code,
                phone_code=args.phone_code,
                debug=True,
                session_cache_file="/tmp/cloudedge_debug_cache",
                region=(args.region if args.region and str(args.region).upper() != "AUTO" else None),
                base_url=(args.base_url or None),
                openapi_base_url=(args.openapi_base_url or None),
            )
            client.authenticate()
            print("Trying snapshot for serial:", args.snapshot)
            # Try to fetch device config and find snapshotable info
            cfg = client.get_device_config(args.snapshot)
            if cfg and 'result' in cfg and 'iot' in cfg['result']:
                iot = cfg['result']['iot']
            elif cfg and isinstance(cfg, dict):
                iot = cfg
            else:
                iot = {}

            # Look for ONVIF_URL (123), RTMP (130), IP (126)
            for code, value in iot.items():
                print(f"Param {code} -> {value}")
            cand = []
            onvif = iot.get('123') or iot.get('ONVIF_URL')
            rtmp = iot.get('130') or iot.get('RTMP_STREAM')
            ipaddr = iot.get('126') or iot.get('IP_ADDRESS')
            if onvif and isinstance(onvif, str):
                cand.append(onvif)
            if rtmp and isinstance(rtmp, str):
                cand.append(rtmp)
            if ipaddr:
                cand.extend([
                    f"http://{ipaddr}/cgi-bin/snapshot.jpg",
                    f"http://{ipaddr}/snapshot.jpg",
                ])

            for url in cand:
                try:
                    print("Trying", url)
                    resp = client._session.get(url, timeout=10, verify=False)
                    print(url, resp.status_code, resp.headers.get('Content-Type'))
                    if resp.status_code == 200:
                        print("Got image bytes, length=", len(resp.content))
                        break
                except Exception as ex:
                    print("Error fetching", url, ex)
        except Exception:
            traceback.print_exc()
    if getattr(args, 'print_config'):
        try:
            client = CloudEdgeClient(
                username=args.username,
                password=args.password,
                country_code=args.country_code,
                phone_code=args.phone_code,
                debug=True,
                session_cache_file="/tmp/cloudedge_debug_cache",
                region=(args.region if args.region and str(args.region).upper() != "AUTO" else None),
                base_url=(args.base_url or None),
                openapi_base_url=(args.openapi_base_url or None),
            )
            client.authenticate()
            print("Fetching device config for:", args.print_config)
            result = client.get_device_config(args.print_config)
            import json
            print(json.dumps(result, indent=2) if result else "No config returned")
        except Exception:
            traceback.print_exc()
