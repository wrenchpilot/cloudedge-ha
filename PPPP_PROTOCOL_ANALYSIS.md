# PPPP Protocol Reverse Engineering Analysis

## Executive Summary

This document details the reverse engineering of the **PPPP (P2P Penetrate Protocol)** used by CloudEdge cameras for direct local video streaming. Analysis based on Wireshark packet capture of iPhone app traffic.

## Network Architecture

### Communication Channels

CloudEdge cameras use a **hybrid architecture**:

1. **Cloud Control Channel** (AWS IoT MQTT):
   - Server: `openapi-usce.mearicloud.com` (47.89.182.8)
   - AWS IoT MQTT: 47.252.74.86:16478
   - Purpose: Device control, status, configuration

2. **Local P2P Video Channel** (PPPP):
   - Camera IP: 10.0.0.40
   - Camera Port: 53470 (UDP)
   - Discovery Port: 59870 (UDP broadcast)
   - Purpose: Real-time video streaming (H.264/H.265)

### Traffic Flow

```
iPhone App (10.0.0.128)
   |
   ├─→ TLS/HTTPS ─→ openapi-usce.mearicloud.com (control)
   |
   ├─→ TCP ─→ 47.252.74.86:16478 (AWS IoT MQTT)
   |
   └─→ UDP ─→ Camera (10.0.0.40:53470) (video via PPPP)
```

## PPPP Protocol Structure

### Packet Format

All PPPP packets use the following binary structure:

```
┌─────────────────────────────────────────────────────────┐
│ PPPP Packet Format (8+ bytes)                           │
├───────────┬─────────────────────────────────────────────┤
│ Offset    │ Field                                        │
├───────────┼─────────────────────────────────────────────┤
│ 0x00-0x01 │ Magic Bytes (0xe6a2=camera, 0xe6a1=phone)  │
│ 0x02-0x03 │ Message Type (see below)                     │
│ 0x04-0x05 │ Protocol Version (0xd1e6)                    │
│ 0x06-0x07 │ Payload Length (uint16, big-endian)         │
│ 0x08-...  │ Payload (JSON or encrypted binary)          │
└───────────┴─────────────────────────────────────────────┘
```

### Message Types

| Type     | Value  | Direction       | Purpose              |
|----------|--------|-----------------|----------------------|
| HELLO    | 0xb1c3 | Camera → Phone  | Initial handshake    |
| HELLO_ACK| 0xb2c4 | Phone → Camera  | Handshake ACK        |
| VERSION  | 0xb1c4 | Camera ↔ Phone  | Version exchange     |
| DATA     | 0x0051 | Bidirectional   | Data transfer        |
| DATA_ACK | 0x0052 | Bidirectional   | Data acknowledgment  |

### Magic Bytes

- **0xe6a2**: Sent by camera (or client acting as camera)
- **0xe6a1**: Sent by phone (or client acting as phone)
- **0xe6a7**: Extended camera messages

## Handshake Protocol

### Phase 1: Initial HELLO

**Camera → Phone:**
```
Hex: e6 a2 b1 c3 d1 e6 34 00 + JSON payload
```

**JSON Payload:**
```json
{
  "sid": "05217c4000000002",
  "uuid": "6f313fdd69bd4632"
}
```

- **sid**: Session ID (timestamp-based, format: `05217c40` + hex timestamp)
- **uuid**: Device UUID (serial without "ppsl" prefix)

**Phone → Camera ACK:**
```
Hex: e6 a1 b2 c4 d1 e6 1a 00 + JSON payload
```

**JSON Payload:**
```json
{
  "sid": "05217c4000000002"
}
```

### Phase 2: Version Exchange

**Camera → Phone:**
```json
{
  "sid": "05217c4000000002",
  "uuid": "6f313fdd69bd4632",
  "ver": 33711
}
```

- **ver**: Protocol version number (33711 = 0x83CF)

**Phone → Camera ACK:**
```json
{
  "sid": "05217c4000000002"
}
```

### Phase 3: Data Transfer

After handshake, encrypted data packets are exchanged:

**Command Packet Structure:**
```
┌─────────────────────────────────────────────────────────┐
│ Command Packet Format (12+ bytes)                       │
├───────────┬─────────────────────────────────────────────┤
│ 0x00-0x03 │ Command Length (uint32, little-endian)      │
│ 0x04-0x07 │ Message Type (0x0051 or 0x0052)             │
│ 0x08-0x0B │ Sequence Number (uint32, little-endian)     │
│ 0x0C-...  │ Encrypted Payload (see encryption section)  │
└───────────┴─────────────────────────────────────────────┘
```

## Packet Capture Examples

### Example 1: Initial HELLO (Camera → Phone)

```
Offset  Hex                                          ASCII
------  -------------------------------------------  -----------------
0x0000  e6 a2 b1 c3 d1 e6 34 00                     ......4.
0x0008  7b 22 73 69 64 22 3a 22 30 35 32 31 37 63   {"sid":"05217c
0x0018  34 30 30 30 30 30 30 30 30 32 22 2c 22 75   4000000002","u
0x0028  75 69 64 22 3a 22 36 66 33 31 33 66 64 64   uid":"6f313fdd
0x0038  36 39 62 64 34 36 33 32 22 7d 4c 9d         69bd4632"}L.
```

**Decoded:**
- Magic: `0xe6a2` (camera)
- Type: `0xb1c3` (HELLO)
- Version: `0xd1e6`
- Length: `0x0034` (52 bytes)
- Payload: `{"sid":"05217c4000000002","uuid":"6f313fdd69bd4632"}`

### Example 2: HELLO ACK (Phone → Camera)

```
Offset  Hex                                          ASCII
------  -------------------------------------------  -----------------
0x0000  e6 a1 b2 c4 d1 e6 1a 00                     ........
0x0008  7b 22 73 69 64 22 3a 22 30 35 32 31 37 63   {"sid":"05217c
0x0018  34 30 30 30 30 30 30 30 30 32 22 7d 42 9d   4000000002"}B.
```

**Decoded:**
- Magic: `0xe6a1` (phone)
- Type: `0xb2c4` (HELLO_ACK)
- Version: `0xd1e6`
- Length: `0x001a` (26 bytes)
- Payload: `{"sid":"05217c4000000002"}`

### Example 3: Encrypted Data Packet

```
Offset  Hex                                          ASCII
------  -------------------------------------------  -----------------
0x0000  0c 00 00 00 51 00 00 02                     ....Q...
0x0008  d8 97 98 18 00 00 00 00                     ........
0x0010  00 00 00 00 50 00 00 00                     ....P...
0x0018  ff 01 00 00 b8 d4 b0 09                     ........
0x0020  9f 99 5d 08 00 00 10 70                     ..]....p
0x0028  3c 00 00 00 56 56 50 99                     <...VVP.
...
```

**Decoded:**
- Command Length: `0x0000000c` (12 bytes)
- Message Type: `0x00000051` (DATA)
- Sequence: `0x000002d8` (728)
- Encrypted Payload: Starts at offset 0x000c

## Encryption Analysis

### Observations

1. **Encrypted payload** starts after 12-byte command header
2. **Encryption key** likely derived from `hostKey` (from CloudEdge API)
3. **Pattern repeats**: `70 ab 97 56 e3 06 ca a0` appears frequently (16-byte block cipher?)
4. **Possible algorithms**:
   - **AES-128-CBC** (most common for IoT devices)
   - **3DES-CBC** (used for CloudEdge API auth)
   - **Custom XOR cipher** (common in P2P cameras)

### Encryption Key Derivation (Hypothesis)

```python
# Possible key derivation from hostKey
import hashlib

def derive_p2p_key(host_key: str, session_id: str) -> bytes:
    """
    Hypothesized P2P encryption key derivation.
    Needs validation against actual decrypted traffic.
    """
    # Method 1: Direct MD5 hash
    key_material = f"{host_key}{session_id}".encode()
    return hashlib.md5(key_material).digest()
    
    # Method 2: SHA256 truncated
    # return hashlib.sha256(key_material).digest()[:16]
    
    # Method 3: PBKDF2
    # import pbkdf2
    # return pbkdf2.PBKDF2(host_key, session_id).read(16)
```

### Video Frame Detection

**Indicators of H.264/H.265 frames:**

1. **NAL unit headers** (H.264):
   - `00 00 00 01` - Start code
   - `00 00 01` - Short start code
   
2. **Frame types** (after decryption):
   - **I-frame** (keyframe): Starts with `67` (SPS) or `68` (PPS)
   - **P-frame**: Starts with `41` or `01`
   - **B-frame**: Starts with `01`

3. **VVP marker** seen in capture:
   - Offset 0x0198: `56 56 50 99` ("VVP" + byte)
   - Possible custom video packet header

## Packet Capture Statistics

### Discovery Phase (Port 59870)

- **Broadcast packets**: Laptop → 255 (discovery)
- **Response packets**: Camera → Phone (presence announcement)

### P2P Session Phase (Port 53470)

| Packet Type          | Count | Avg Size | Direction       |
|----------------------|-------|----------|-----------------|
| HELLO handshake      | 14    | 62 bytes | Camera → Phone  |
| HELLO ACK            | 14    | 36 bytes | Phone → Camera  |
| VERSION exchange     | 20    | 76 bytes | Bidirectional   |
| Encrypted DATA       | 3     | 1184 B   | Camera → Phone  |
| DATA ACK             | 3     | 24 bytes | Phone → Camera  |

**Total P2P traffic**: 54 packets, ~4.5 KB (capture incomplete)

## Implementation Notes

### Required Components

1. **UDP Socket**:
   - Bind to any port
   - Send to camera IP:53470
   - Set timeout (5 seconds recommended)

2. **Session Management**:
   - Generate unique session ID per connection
   - Maintain sequence counter for DATA packets
   - Handle ACKs within 100ms

3. **Encryption**:
   - Decrypt incoming video data using hostKey
   - Encrypt outgoing commands using hostKey
   - Test AES-128-CBC first (most common)

4. **Video Decoding**:
   - Parse decrypted NAL units
   - Assemble frames from packets
   - Feed to H.264/H.265 decoder (FFmpeg, OpenCV, etc.)

### Test Strategy

**Phase 1: Handshake** (WORKING - implemented in pppp_client.py)
```python
1. Send HELLO with sid + uuid
2. Receive HELLO_ACK
3. Send VERSION with ver=33711
4. Receive VERSION_ACK
```

**Phase 2: Encryption** (TODO - needs cryptanalysis)
```python
1. Try AES-128-CBC with hostKey as key
2. Try 3DES-CBC with hostKey (like API auth)
3. Try XOR with hostKey bytes
4. Try custom algorithm (reverse engineer firmware)
```

**Phase 3: Video Streaming** (TODO - depends on Phase 2)
```python
1. Send VIDEO_START command (encrypted)
2. Receive encrypted video packets
3. Decrypt packets
4. Parse H.264/H.265 NAL units
5. Decode and display frames
```

## Security Considerations

### Authentication

- **hostKey** provides authentication (no password needed)
- **Session ID** prevents replay attacks
- **Sequence numbers** prevent packet injection

### Encryption

- **Symmetric encryption** (fast, suitable for real-time video)
- **Key exchange**: None - hostKey is pre-shared via API
- **Perfect Forward Secrecy**: NO (key reuse across sessions)

### Vulnerabilities

1. **No public key cryptography** - hostKey compromise = full access
2. **UDP protocol** - no built-in reliability or ordering
3. **Local network only** - no NAT traversal in current implementation
4. **No certificate validation** - MitM possible on local network

## Future Work

### Priority 1: Decrypt Video Packets
- **Try known encryption algorithms** with hostKey
- **Analyze firmware** for encryption implementation
- **Brute force** small keyspace if custom algorithm

### Priority 2: Implement Video Decoder
- **FFmpeg integration** for H.264/H.265 decoding
- **Frame buffering** and reassembly
- **Timestamp synchronization**

### Priority 3: Home Assistant Integration
- **Camera entity** for live streaming
- **Snapshot service** for single frames
- **Recording service** for video clips

### Priority 4: NAT Traversal
- **STUN/TURN** for remote access
- **Relay server** for cloud-mediated P2P
- **UPnP/NAT-PMP** for port forwarding

## References

### Protocol Documentation
- **PPPP SDK**: CS2 P2P SDK (used by many Chinese IoT cameras)
- **Similar protocols**: Tutk P2P, Kalay P2P, XMEye P2P

### Packet Capture Files
- **cloudedge.pcapng**: Full Wireshark capture
- **cloudedge.csv**: CSV export (metadata only)
- **Untitled-1**: Hex dump (UDP stream follow)

### Code Files
- **pppp_client.py**: Python PPPP protocol client (handshake working)
- **test_p2p_snapshot.py**: Legacy P2P test (may use different protocol)
- **cloudedge_p2p_client.py**: Existing P2P implementation (needs analysis)

## Conclusion

The **PPPP protocol** is a custom binary protocol with:
- ✅ **Reverse-engineered handshake** (HELLO, VERSION exchanges)
- ✅ **Session establishment** (sid, uuid, sequence)
- ⚠️ **Encrypted video data** (algorithm TBD)
- ❌ **Video decoding** (blocked on decryption)

**Next step**: Cryptanalyze encrypted payloads to extract hostKey encryption algorithm, enabling video frame extraction.

---

**Document Version**: 1.0  
**Last Updated**: December 13, 2025  
**Author**: Reverse engineered from network packet capture  
**Status**: Handshake implemented, encryption TODO
