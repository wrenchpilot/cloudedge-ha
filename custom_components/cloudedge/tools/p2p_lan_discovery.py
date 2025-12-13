#!/usr/bin/env python3
"""
P2P LAN Discovery for CloudEdge/Meari cameras

Based on reverse engineering of the PPPP protocol:
- https://palant.info/2025/11/05/an-overview-of-the-pppp-protocol-for-iot-cameras/
- https://github.com/fbertone/lib32100

CloudEdge uses:
- PPPP protocol on UDP port 32100/32108 for transport
- Meari SDK binary protocol for application layer

Message format:
- Header: 2 bytes message type, 2 bytes payload length
- Payload: varies by message type
"""

import socket
import struct
import sys
import time
from typing import Optional, Tuple

# PPPP Message Types (from lib32100 and palant.info)
MSG_HELLO = b'\xF1\x00'           # 0 byte payload
MSG_LAN_SEARCH = b'\xF1\x30'      # 0 byte payload - UDP broadcast discovery
MSG_LAN_SEARCH_EXT = b'\xF1\x32'  # 0 byte payload - Extended LAN search
MSG_PUNCH_PKT = b'\xF1\x41'       # 20 byte payload (device ID)
MSG_P2P_RDY = b'\xF1\x42'         # 20 byte payload (device ID) - Camera confirms session
MSG_P2P_ALIVE = b'\xF1\xE0'       # 0 byte payload - Ping
MSG_P2P_ALIVE_ACK = b'\xF1\xE1'   # 0 byte payload - Pong
MSG_CLOSE = b'\xF1\xF0'           # 0 byte payload
MSG_DRW = b'\xF1\xD0'             # Variable - Data message
MSG_DRW_ACK = b'\xF1\xD1'         # Variable - Data acknowledgment

# Camera details from CloudEdge API
CAMERA_IP = "10.0.0.40"
CAMERA_UID = "ppsl6f313fdd69bd4632"  # snNum from API
CAMERA_HOST_KEY = "b9bf77590d4003865e5652bc64a89501"


def encode_uid_to_bytes(uid: str) -> bytes:
    """
    Encode camera UID to 20-byte buffer format.
    
    Format varies by UID length/prefix. For CloudEdge 'ppsl' prefix:
    - First 4 chars: literal ASCII
    - Next 6 chars: numeric, converted to 3 bytes (big endian)
    - Last 8 chars: checksum/random portion
    - Padded to 20 bytes total
    """
    if len(uid) < 14:
        raise ValueError(f"UID too short: {uid}")
    
    # Try to parse the ppsl format: ppsl + 6 hex chars + rest
    prefix = uid[:4].encode('ascii')  # 'ppsl'
    
    # The rest appears to be hex - try to decode it
    hex_part = uid[4:]
    
    # Build the 20-byte buffer
    # Format: 4 bytes prefix + 8 bytes from hex + 8 bytes padding
    try:
        hex_bytes = bytes.fromhex(hex_part)
        result = prefix + hex_bytes
        # Pad to 20 bytes
        result = result.ljust(20, b'\x00')
        return result[:20]
    except ValueError:
        # If not valid hex, just use ASCII
        result = uid.encode('ascii')
        return result.ljust(20, b'\x00')[:20]


def build_message(msg_type: bytes, payload: bytes = b'') -> bytes:
    """Build a PPPP message with header."""
    payload_len = struct.pack('>H', len(payload))
    return msg_type + payload_len + payload


def parse_message(data: bytes) -> Tuple[bytes, int, bytes]:
    """Parse a PPPP message. Returns (msg_type, payload_len, payload)."""
    if len(data) < 4:
        return b'', 0, b''
    msg_type = data[:2]
    payload_len = struct.unpack('>H', data[2:4])[0]
    payload = data[4:4+payload_len] if len(data) >= 4 + payload_len else data[4:]
    return msg_type, payload_len, payload


def discover_camera_lan():
    """
    Try to discover cameras on LAN using UDP broadcast.
    """
    print("\n=== LAN DISCOVERY ===")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(3.0)
    
    # Bind to any available port
    sock.bind(('', 0))
    local_port = sock.getsockname()[1]
    print(f"Bound to local port {local_port}")
    
    # Build LAN search messages
    lan_search = build_message(MSG_LAN_SEARCH)
    lan_search_ext = build_message(MSG_LAN_SEARCH_EXT)
    
    print(f"LAN_SEARCH: {lan_search.hex()}")
    print(f"LAN_SEARCH_EXT: {lan_search_ext.hex()}")
    
    # Send to broadcast on port 32108 (LAN discovery port)
    targets = [
        ('255.255.255.255', 32108),  # Broadcast
        ('10.0.0.255', 32108),        # Local subnet broadcast
        (CAMERA_IP, 32108),           # Direct to camera
        (CAMERA_IP, 32100),           # Direct to camera main port
    ]
    
    for target in targets:
        print(f"\nSending to {target}...")
        try:
            sock.sendto(lan_search, target)
            sock.sendto(lan_search_ext, target)
        except Exception as e:
            print(f"  Error sending: {e}")
    
    print("\nWaiting for responses...")
    start_time = time.time()
    while time.time() - start_time < 5:
        try:
            data, addr = sock.recvfrom(1024)
            print(f"\n  RESPONSE from {addr}:")
            print(f"  Raw: {data.hex()}")
            msg_type, payload_len, payload = parse_message(data)
            print(f"  Type: {msg_type.hex()}, Len: {payload_len}")
            if payload:
                print(f"  Payload: {payload.hex()}")
                # Try to decode as ASCII
                try:
                    print(f"  Payload (ASCII): {payload.decode('ascii', errors='replace')}")
                except:
                    pass
        except socket.timeout:
            break
        except Exception as e:
            print(f"  Error: {e}")
            break
    
    sock.close()


def direct_camera_probe():
    """
    Try to establish direct connection with camera using various message types.
    """
    print("\n=== DIRECT CAMERA PROBE ===")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    sock.bind(('', 0))
    local_port = sock.getsockname()[1]
    print(f"Local port: {local_port}")
    
    uid_bytes = encode_uid_to_bytes(CAMERA_UID)
    print(f"UID bytes: {uid_bytes.hex()}")
    
    # Messages to try
    messages = [
        ("HELLO", build_message(MSG_HELLO)),
        ("P2P_ALIVE (ping)", build_message(MSG_P2P_ALIVE)),
        ("PUNCH_PKT (with UID)", build_message(MSG_PUNCH_PKT, uid_bytes)),
        ("LAN_SEARCH", build_message(MSG_LAN_SEARCH)),
        ("LAN_SEARCH_EXT", build_message(MSG_LAN_SEARCH_EXT)),
    ]
    
    for port in [32100, 32108]:
        print(f"\n--- Port {port} ---")
        for name, msg in messages:
            print(f"\nSending {name}: {msg.hex()}")
            try:
                sock.sendto(msg, (CAMERA_IP, port))
                time.sleep(0.1)
                
                # Try to receive response
                try:
                    data, addr = sock.recvfrom(1024)
                    print(f"  RESPONSE from {addr}:")
                    print(f"  Raw: {data.hex()}")
                    msg_type, payload_len, payload = parse_message(data)
                    print(f"  Type: {msg_type.hex()}, Len: {payload_len}")
                except socket.timeout:
                    print("  (no response)")
            except Exception as e:
                print(f"  Error: {e}")
    
    sock.close()


def meari_protocol_probe():
    """
    Try to send Meari SDK format message.
    
    Meari SDK header (52 bytes):
    - Magic: 56 56 50 99 (4 bytes)
    - Auth token: SHA1 hex digest (40 bytes)
    - Command ID, sequence, payload size encoded in remaining bytes
    """
    print("\n=== MEARI PROTOCOL PROBE ===")
    import hashlib
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    sock.bind(('', 0))
    
    # Meari magic bytes
    MEARI_MAGIC = bytes([0x56, 0x56, 0x50, 0x99])
    
    # Try some potential Meari commands
    # Format appears to be: magic(4) + auth_sha1(40 ascii hex) + seq(2) + cmd(2) + payload_len(4)
    
    # Build a simple "hello" style message
    username = "admin"
    password = ""  # Try empty password first
    seq = 1
    cmd = 0x0001  # Unknown command - try various
    payload = b''
    
    # Create auth hash: SHA1(username + password + seq + cmd + payload_len)
    auth_string = f"{username}{password}{seq}{cmd}{len(payload)}"
    auth_hash = hashlib.sha1(auth_string.encode()).hexdigest()
    
    # Build header
    header = MEARI_MAGIC + auth_hash.encode('ascii')
    header += struct.pack('>HHI', seq, cmd, len(payload))
    
    print(f"Meari header ({len(header)} bytes): {header.hex()}")
    
    message = header + payload
    
    try:
        sock.sendto(message, (CAMERA_IP, 32100))
        print("Sent Meari message, waiting for response...")
        
        data, addr = sock.recvfrom(1024)
        print(f"RESPONSE from {addr}:")
        print(f"Raw: {data.hex()}")
    except socket.timeout:
        print("No response to Meari message")
    except Exception as e:
        print(f"Error: {e}")
    
    sock.close()


def scan_camera_ports():
    """
    Quick scan of camera to see what ports are open and responding.
    """
    print("\n=== PORT SCAN ===")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    
    # Test payload
    test_msg = build_message(MSG_HELLO)
    
    open_ports = []
    for port in [32100, 32101, 32102, 32103, 32104, 32105, 32106, 32107, 32108, 32109, 32110]:
        try:
            sock.sendto(test_msg, (CAMERA_IP, port))
            try:
                data, addr = sock.recvfrom(1024)
                open_ports.append((port, data))
                print(f"Port {port}: RESPONSE - {data.hex()}")
            except socket.timeout:
                # No response but ICMP would tell us if unreachable
                print(f"Port {port}: no response (may still be open)")
        except Exception as e:
            print(f"Port {port}: error - {e}")
    
    sock.close()
    return open_ports


if __name__ == "__main__":
    print("=" * 60)
    print("CloudEdge P2P Protocol Discovery")
    print("=" * 60)
    print(f"Camera IP: {CAMERA_IP}")
    print(f"Camera UID: {CAMERA_UID}")
    print(f"Host Key: {CAMERA_HOST_KEY}")
    
    # Run probes
    scan_camera_ports()
    discover_camera_lan()
    direct_camera_probe()
    meari_protocol_probe()
    
    print("\n" + "=" * 60)
    print("Discovery complete")
    print("=" * 60)
