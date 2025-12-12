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
        else:
            print("Authentication returned False (unexpected)")

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

    args = parser.parse_args()
    run_debug(args.username, args.password, args.country_code, args.phone_code, args.region, args.base_url, args.openapi_base_url)
