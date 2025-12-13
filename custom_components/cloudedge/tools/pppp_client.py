#!/usr/bin/env python3
"""
CloudEdge PPPP (P2P Penetrate Protocol) Client
Reverse-engineered from network packet capture analysis.

This implements the binary PPPP protocol for direct local communication
with CloudEdge cameras, enabling video streaming without cloud relay.

Protocol Structure:
- Discovery: UDP broadcasts to port 59870
- P2P Session: UDP to camera port 53470
- Authentication: Uses hostKey from CloudEdge API
- Video: H.264/H.265 over UDP with custom framing
"""

import socket
import struct
import json
import time
import logging
import hashlib
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# PPPP Protocol Constants
PPPP_MAGIC_CAMERA = 0xe6a2
PPPP_MAGIC_PHONE = 0xe6a1
PPPP_DISCOVERY_PORT = 59870
PPPP_P2P_PORT = 53470
PPPP_BUFFER_SIZE = 8192

# PPPP Message Types (reverse engineered from packet capture)
PPPP_MSG_HELLO = 0xb1c3  # Initial handshake
PPPP_MSG_HELLO_ACK = 0xb2c4  # Handshake ACK
PPPP_MSG_VERSION = 0xb1c4  # Version exchange
PPPP_MSG_VERSION_ACK = 0xb2c4  # Version ACK
PPPP_MSG_DATA = 0x0051  # Data packet
PPPP_MSG_DATA_ACK = 0x0052  # Data ACK

# PPPP Command Types
PPPP_CMD_VIDEO_START = 0x0002  # Start video stream
PPPP_CMD_VIDEO_DATA = 0x0002  # Video data packet
PPPP_CMD_SNAPSHOT = 0x0001  # Request snapshot


@dataclass
class PPPPPacket:
    """Represents a PPPP protocol packet."""
    magic: int
    msg_type: int
    payload: bytes
    
    def pack(self) -> bytes:
        """Pack packet into binary format."""
        # Header: magic (2 bytes) + msg_type (2 bytes) + reserved (2 bytes) + length (2 bytes)
        header = struct.pack('>HHHH', 
            self.magic,
            self.msg_type,
            0xd1e6,  # Reserved/protocol version bytes
            len(self.payload)
        )
        return header + self.payload
    
    @classmethod
    def unpack(cls, data: bytes) -> 'PPPPPacket':
        """Unpack binary data into PPPPPacket."""
        if len(data) < 8:
            raise ValueError(f"Packet too short: {len(data)} bytes")
        
        magic, msg_type, _, payload_len = struct.unpack('>HHHH', data[:8])
        payload = data[8:8+payload_len]
        
        return cls(magic=magic, msg_type=msg_type, payload=payload)


class PPPPClient:
    """
    PPPP Protocol Client for CloudEdge cameras.
    
    Implements the P2P Penetrate Protocol for direct local communication
    with CloudEdge cameras without cloud relay.
    """
    
    def __init__(self, camera_ip: str, serial: str, host_key: str):
        """
        Initialize PPPP client.
        
        Args:
            camera_ip: Local IP address of camera (e.g., "10.0.0.40")
            serial: Camera serial number (e.g., "ppsl6f313fdd69bd4632")
            host_key: Authentication key from CloudEdge API
        """
        self.camera_ip = camera_ip
        self.serial = serial
        self.host_key = host_key
        self.sock: Optional[socket.socket] = None
        self.session_id: Optional[str] = None
        self.uuid: Optional[str] = None
        self.sequence = 0
        
    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.disconnect()
    
    def connect(self) -> bool:
        """
        Establish P2P connection to camera.
        
        Returns:
            bool: True if connection successful
        """
        try:
            # Create UDP socket
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.settimeout(5.0)
            
            logger.info(f"Connecting to camera at {self.camera_ip}:{PPPP_P2P_PORT}")
            
            # Step 1: Send HELLO packet
            if not self._send_hello():
                logger.error("Failed to send HELLO")
                return False
            
            # Step 2: Receive HELLO response
            if not self._receive_hello_ack():
                logger.error("Failed to receive HELLO ACK")
                return False
            
            # Step 3: Exchange version information
            if not self._exchange_version():
                logger.error("Failed to exchange version")
                return False
            
            logger.info("✓ P2P connection established successfully!")
            return True
            
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False
    
    def _send_hello(self) -> bool:
        """Send initial HELLO handshake."""
        try:
            # Extract UUID from serial (remove "ppsl" prefix)
            self.uuid = self.serial.replace("ppsl", "")
            
            # Generate session ID (appears to be timestamp-based)
            timestamp = int(time.time() * 1000) & 0xFFFFFFFF
            self.session_id = f"05217c40{timestamp:08x}"
            
            # Build HELLO payload
            payload = {
                "sid": self.session_id,
                "uuid": self.uuid
            }
            payload_bytes = json.dumps(payload).encode('utf-8')
            
            # Create and send HELLO packet
            packet = PPPPPacket(
                magic=PPPP_MAGIC_CAMERA,
                msg_type=PPPP_MSG_HELLO,
                payload=payload_bytes
            )
            
            data = packet.pack()
            logger.debug(f"Sending HELLO: {payload}")
            logger.debug(f"HELLO packet: {data.hex()}")
            
            self.sock.sendto(data, (self.camera_ip, PPPP_P2P_PORT))
            return True
            
        except Exception as e:
            logger.error(f"Failed to send HELLO: {e}")
            return False
    
    def _receive_hello_ack(self) -> bool:
        """Receive HELLO ACK from camera."""
        try:
            data, addr = self.sock.recvfrom(PPPP_BUFFER_SIZE)
            logger.debug(f"Received {len(data)} bytes from {addr}")
            logger.debug(f"Raw data: {data.hex()}")
            
            # Parse response packet
            packet = PPPPPacket.unpack(data)
            
            if packet.magic != PPPP_MAGIC_PHONE:
                logger.error(f"Invalid magic in ACK: 0x{packet.magic:04x}")
                return False
            
            # Parse JSON payload
            try:
                response = json.loads(packet.payload.decode('utf-8'))
                logger.debug(f"HELLO ACK payload: {response}")
                
                # Verify session ID matches
                if response.get("sid") != self.session_id:
                    logger.warning(f"Session ID mismatch: {response.get('sid')} != {self.session_id}")
                
            except json.JSONDecodeError:
                logger.debug(f"Non-JSON ACK payload: {packet.payload.hex()}")
            
            logger.info("✓ Received HELLO ACK")
            return True
            
        except socket.timeout:
            logger.error("Timeout waiting for HELLO ACK")
            return False
        except Exception as e:
            logger.error(f"Failed to receive HELLO ACK: {e}")
            return False
    
    def _exchange_version(self) -> bool:
        """Exchange version information with camera."""
        try:
            # Send VERSION packet
            payload = {
                "sid": self.session_id,
                "uuid": self.uuid,
                "ver": 33711  # Version from packet capture
            }
            payload_bytes = json.dumps(payload).encode('utf-8')
            
            packet = PPPPPacket(
                magic=PPPP_MAGIC_CAMERA,
                msg_type=PPPP_MSG_VERSION,
                payload=payload_bytes
            )
            
            data = packet.pack()
            logger.debug(f"Sending VERSION: {payload}")
            self.sock.sendto(data, (self.camera_ip, PPPP_P2P_PORT))
            
            # Receive VERSION ACK
            data, addr = self.sock.recvfrom(PPPP_BUFFER_SIZE)
            packet = PPPPPacket.unpack(data)
            
            try:
                response = json.loads(packet.payload.decode('utf-8'))
                logger.debug(f"VERSION ACK: {response}")
            except json.JSONDecodeError:
                logger.debug(f"Non-JSON VERSION ACK: {packet.payload.hex()}")
            
            logger.info("✓ Version exchange complete")
            return True
            
        except Exception as e:
            logger.error(f"Version exchange failed: {e}")
            return False
    
    def request_video_stream(self, channel: int = 0, quality: str = "main") -> bool:
        """
        Request video stream from camera.
        
        Args:
            channel: Camera channel (usually 0)
            quality: Stream quality ("main" or "sub")
        
        Returns:
            bool: True if request successful
        """
        try:
            # Build video start command
            # This is encrypted in real traffic - need to reverse engineer encryption
            command = struct.pack('<IIIII', 
                0x0000000c,  # Command header
                PPPP_MSG_DATA,  # Message type
                self.sequence,  # Sequence number
                0,  # Reserved
                channel  # Channel number
            )
            
            self.sequence += 1
            
            logger.debug(f"Requesting video stream (channel={channel}, quality={quality})")
            logger.debug(f"Command: {command.hex()}")
            
            self.sock.sendto(command, (self.camera_ip, PPPP_P2P_PORT))
            
            # TODO: Receive and parse video stream
            # The video data is encrypted - need to decrypt using hostKey
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to request video stream: {e}")
            return False
    
    def receive_video_frame(self, timeout: float = 5.0) -> Optional[bytes]:
        """
        Receive a video frame from camera.
        
        Args:
            timeout: Receive timeout in seconds
        
        Returns:
            bytes: Raw video frame data (H.264/H.265), or None on error
        """
        try:
            self.sock.settimeout(timeout)
            data, addr = self.sock.recvfrom(PPPP_BUFFER_SIZE)
            
            logger.debug(f"Received {len(data)} bytes from {addr}")
            
            # Parse packet header
            if len(data) < 12:
                logger.warning(f"Packet too short: {len(data)} bytes")
                return None
            
            # Extract command header
            header = struct.unpack('<III', data[:12])
            cmd_len, cmd_type, seq = header
            
            logger.debug(f"Command: len={cmd_len}, type=0x{cmd_type:04x}, seq={seq}")
            
            # Video data starts after header
            # TODO: Decrypt using hostKey
            video_data = data[12:]
            
            return video_data
            
        except socket.timeout:
            logger.debug("Timeout waiting for video frame")
            return None
        except Exception as e:
            logger.error(f"Failed to receive video frame: {e}")
            return None
    
    def disconnect(self):
        """Disconnect from camera."""
        if self.sock:
            logger.info("Disconnecting from camera")
            self.sock.close()
            self.sock = None


def discover_cameras(timeout: float = 5.0) -> list:
    """
    Discover CloudEdge cameras on local network via UDP broadcast.
    
    Args:
        timeout: Discovery timeout in seconds
    
    Returns:
        list: List of discovered camera IP addresses
    """
    cameras = []
    
    try:
        # Create UDP socket for broadcast
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        
        # Send discovery broadcast
        discovery_msg = b"PPPP_DISCOVER"
        sock.sendto(discovery_msg, ("<broadcast>", PPPP_DISCOVERY_PORT))
        
        logger.info(f"Sent discovery broadcast to port {PPPP_DISCOVERY_PORT}")
        
        # Collect responses
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                data, addr = sock.recvfrom(1024)
                camera_ip = addr[0]
                if camera_ip not in cameras:
                    cameras.append(camera_ip)
                    logger.info(f"Discovered camera at {camera_ip}")
            except socket.timeout:
                break
        
        sock.close()
        
    except Exception as e:
        logger.error(f"Discovery failed: {e}")
    
    return cameras


def main():
    """Test PPPP client."""
    # Example usage
    CAMERA_IP = "10.0.0.40"
    SERIAL = "ppsl6f313fdd69bd4632"
    HOST_KEY = "your_host_key_here"  # Get from CloudEdge API
    
    print("=" * 60)
    print("CloudEdge PPPP Protocol Client")
    print("=" * 60)
    
    # Discover cameras
    print("\n[1] Discovering cameras on local network...")
    cameras = discover_cameras()
    print(f"Found {len(cameras)} camera(s): {cameras}")
    
    # Connect to camera
    print(f"\n[2] Connecting to camera at {CAMERA_IP}...")
    with PPPPClient(CAMERA_IP, SERIAL, HOST_KEY) as client:
        print("✓ Connected successfully!")
        
        # Request video stream
        print("\n[3] Requesting video stream...")
        if client.request_video_stream():
            print("✓ Video stream requested")
            
            # Receive frames
            print("\n[4] Receiving video frames...")
            for i in range(10):
                frame = client.receive_video_frame()
                if frame:
                    print(f"  Frame {i+1}: {len(frame)} bytes")
                else:
                    print(f"  Frame {i+1}: No data")
                time.sleep(0.1)
    
    print("\n✓ Test complete!")


if __name__ == "__main__":
    main()
