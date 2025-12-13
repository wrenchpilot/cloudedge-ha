#!/usr/bin/env python3
"""
Test script to probe CloudEdge camera's P2P protocol.

This script attempts to communicate with the camera using the PPPP P2P protocol
on UDP port 32100/32108 to determine if we can establish a connection and
retrieve video/snapshots.

Reference implementations:
- https://github.com/fbertone/lib32100 (JavaScript)
- https://github.com/devbis/aiopppp (Python)
- https://github.com/indykoning/PyPI_p2pcam (Python)

Usage:
    python test_p2p_protocol.py <camera_ip> [device_uid]

Example:
    python test_p2p_protocol.py 10.0.0.40 ppsl6f313fdd69bd4632
"""

import socket
import struct
import sys
import time
import random

# PPPP Protocol constants (port 32100)
PPPP_MAGIC = b'\xf1\xd0\xd1\xd2'  # Common magic bytes for PPPP protocol
TUTK_MAGIC = b'\x01\x10'  # TUTK IOTC magic

# P2P Protocol message types based on lib32100
MSG_TYPE_STUN_REQ = 0x00
MSG_TYPE_STUN_RESP = 0x01
MSG_TYPE_HELLO = 0x10
MSG_TYPE_HELLO_ACK = 0x11
MSG_TYPE_PUNCH = 0x20
MSG_TYPE_PUNCH_ACK = 0x21
MSG_TYPE_P2P_RDY = 0x30
MSG_TYPE_P2P_RDY_ACK = 0x31
MSG_TYPE_CLOSE = 0x40
MSG_TYPE_CLOSE_ACK = 0x41
MSG_TYPE_DATA = 0xD0
MSG_TYPE_DATA_ACK = 0xD1
MSG_TYPE_ALIVE = 0xE0
MSG_TYPE_ALIVE_ACK = 0xE1


def build_pppp_hello(uid: str) -> bytes:
    """Build a PPPP HELLO message to initiate connection with camera."""
    # PPPP hello format (based on lib32100 reverse engineering)
    # Magic (4 bytes) + Type (1 byte) + Length (2 bytes) + UID
    uid_bytes = uid.encode('ascii')
    length = len(uid_bytes)
    
    # Try multiple magic patterns
    messages = []
    
    # Pattern 1: lib32100 style
    msg1 = struct.pack('>BBH', 0xf1, MSG_TYPE_HELLO, length) + uid_bytes
    messages.append(('lib32100', msg1))
    
    # Pattern 2: With 4-byte magic header
    msg2 = PPPP_MAGIC + struct.pack('>BH', MSG_TYPE_HELLO, length) + uid_bytes
    messages.append(('pppp_magic', msg2))
    
    # Pattern 3: Simple UID lookup
    msg3 = b'\xf1\x10' + struct.pack('>H', length) + uid_bytes
    messages.append(('f1_10', msg3))
    
    # Pattern 4: TUTK IOTC style
    msg4 = TUTK_MAGIC + struct.pack('<H', 0x0010) + struct.pack('<I', 0) + uid_bytes.ljust(20, b'\x00')
    messages.append(('tutk', msg4))
    
    return messages


def build_discovery_probe() -> bytes:
    """Build a generic discovery probe message."""
    probes = []
    
    # Pattern 1: Simple ping
    probes.append(('ping', b'\xf1\xe0\x00\x00'))
    
    # Pattern 2: STUN request
    probes.append(('stun', b'\xf1\x00\x00\x00'))
    
    # Pattern 3: Alive check
    probes.append(('alive', struct.pack('>BBHI', 0xf1, MSG_TYPE_ALIVE, 4, int(time.time()))))
    
    # Pattern 4: Common P2P camera discovery
    probes.append(('discover', b'\xf1\x30\x00\x00'))
    
    return probes


def probe_camera(camera_ip: str, port: int = 32100, device_uid: str = None):
    """Probe the camera to understand its P2P protocol."""
    print(f"\n{'='*60}")
    print(f"P2P Protocol Probe")
    print(f"Camera IP: {camera_ip}")
    print(f"Port: {port}")
    print(f"Device UID: {device_uid or 'Not specified'}")
    print(f"{'='*60}\n")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)  # Short timeout
    local_port = random.randint(30000, 35000)
    sock.bind(('0.0.0.0', local_port))
    print(f"Bound to local port: {local_port}\n")
    
    # Test 1: Send discovery probes
    print("Phase 1: Discovery Probes")
    print("-" * 40)
    
    probes = build_discovery_probe()
    for name, probe in probes:
        try:
            print(f"  Sending {name} probe: {probe.hex()}")
            sock.sendto(probe, (camera_ip, port))
            
            response, addr = sock.recvfrom(1024)
            print(f"    ✓ Response from {addr}: {response.hex()}")
            print(f"    ASCII: {response[:32]}")
        except socket.timeout:
            print(f"    ✗ No response (timeout)")
        except Exception as e:
            print(f"    ✗ Error: {e}")
    
    # Test 2: Send UID-based hello messages
    if device_uid:
        print(f"\nPhase 2: UID Hello Messages (UID: {device_uid})")
        print("-" * 40)
        
        hello_messages = build_pppp_hello(device_uid)
        for name, msg in hello_messages:
            try:
                print(f"  Sending {name} hello: {msg.hex()}")
                sock.sendto(msg, (camera_ip, port))
                
                response, addr = sock.recvfrom(1024)
                print(f"    ✓ Response from {addr}: {response.hex()}")
                print(f"    ASCII: {response[:32]}")
            except socket.timeout:
                print(f"    ✗ No response (timeout)")
            except Exception as e:
                print(f"    ✗ Error: {e}")
    
    # Test 3: Try sending raw data to see what the camera echoes back
    print(f"\nPhase 3: Raw Protocol Discovery")
    print("-" * 40)
    
    # Send increasing byte patterns to see what triggers a response
    patterns = [
        b'\xf1\x00',
        b'\xf1\x01',
        b'\xf1\x10',
        b'\xf1\x11',
        b'\xf1\x20',
        b'\xf1\x30',
        b'\xf1\xd0',
        b'\xf1\xe0',
        b'\x00\x00',
        b'\x01\x10',  # TUTK
    ]
    
    for pattern in patterns:
        padded = pattern + b'\x00' * 10
        try:
            sock.sendto(padded, (camera_ip, port))
            sock.settimeout(0.5)
            response, addr = sock.recvfrom(1024)
            print(f"  Pattern {pattern.hex()}: ✓ Response: {response.hex()}")
        except socket.timeout:
            pass  # Silent on timeout for this phase
        except Exception as e:
            print(f"  Pattern {pattern.hex()}: Error - {e}")
    
    sock.close()
    print(f"\n{'='*60}")
    print("Probe complete.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    camera_ip = sys.argv[1]
    device_uid = sys.argv[2] if len(sys.argv) > 2 else None
    
    # Try both common P2P ports
    for port in [32100, 32108]:
        probe_camera(camera_ip, port, device_uid)


if __name__ == "__main__":
    main()
