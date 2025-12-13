#!/usr/bin/env python3
"""
CloudEdge P2P Client
====================

A Python implementation of the PPPP (Peer-to-Peer Protocol) for CloudEdge/Meari cameras.
Based on research from:
- https://github.com/elastic/camera-hacks/blob/main/p2p/p2p_client.py (AJCloud)
- https://github.com/youribonnaffe/iegeek-security-camera (CloudEdge reverse engineering)
- https://github.com/devbis/aiopppp (Python PPPP implementation)

Key differences from AJCloud cameras:
- CloudEdge uses "ppsl" prefix instead of "ajy"
- Uses different P2P relay servers
- Requires removeWake.action API to wake camera before P2P connection

Usage:
    python cloudedge_p2p_client.py --serial <camera_serial> --ip <camera_ip>
    
Example:
    python cloudedge_p2p_client.py --serial ppsl6f313fdd69bd4632 --ip 10.0.0.40
"""

import os
import io
import sys
import socket
import struct
import time
import random
import argparse
import threading
import logging
from typing import Optional, Tuple, Dict, Any

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================================================================
# PPPP Protocol Constants
# ============================================================================

# CloudEdge P2P servers - these may need to be discovered from API
# Based on iegeek research, the initstring contains server info
CLOUDEDGE_P2P_SERVERS = [
    # European servers (likely - need to verify)
    ('47.91.22.246', 32100),   # AWS Frankfurt region
    ('52.58.28.83', 32100),    # AWS Frankfurt region
    ('35.156.177.76', 32100),  # AWS Frankfurt region
    # US servers (likely - need to verify)  
    ('18.214.0.243', 32100),   # AWS US region
]

# UDP port for P2P communication
P2P_PORT = 32100
P2P_DISCOVERY_PORT = 32108

# Magic byte for PPPP protocol
PPPP_MAGIC = 0xF1

# PPPP Message Types (opcodes)
MSG_HELLO = 0x00           # Initial hello to P2P server
MSG_HELLO_ACK = 0x01       # Hello acknowledgment
MSG_P2P_REQ = 0x20         # P2P connection request
MSG_P2P_REQ_ACK = 0x21     # P2P request acknowledgment
MSG_INTRO = 0x25           # Connection introduction (punch info)
MSG_LAN_SEARCH = 0x30      # LAN broadcast discovery
MSG_LAN_SEARCH_ACK = 0x31  # LAN discovery response
MSG_PUNCH_TO = 0x40        # Punch to address
MSG_PUNCH_PKT = 0x41       # Punch packet
MSG_P2P_RDY = 0x42         # P2P ready
MSG_DRW = 0xD0             # Data read/write (command)
MSG_DRW_ACK = 0xD1         # Data acknowledgment
MSG_ALIVE = 0xE0           # Keepalive
MSG_ALIVE_ACK = 0xE1       # Keepalive acknowledgment
MSG_CLOSE = 0xF0           # Close connection

# Device command prefixes (HH01 format for Meari/CloudEdge)
# Based on iegeek reverse engineering, Meari uses different command structure
MEARI_CMD_PREFIX = b'HH01'  # 0x48483031

# ============================================================================
# CloudEdge P2P Client
# ============================================================================

class CloudEdgeP2PClient:
    """
    P2P client for CloudEdge/Meari cameras.
    
    Implements the PPPP protocol to establish direct P2P connections
    with cameras for video streaming and snapshot capture.
    """
    
    def __init__(
        self,
        serial: str,
        camera_ip: Optional[str] = None,
        host_key: Optional[str] = None,
        p2p_password: Optional[str] = None,
        init_string: Optional[str] = None,
        debug: bool = True
    ):
        """
        Initialize the P2P client.
        
        Args:
            serial: Camera serial number (e.g., "ppsl6f313fdd69bd4632")
            camera_ip: Camera's LAN IP address (optional, for direct connection)
            host_key: Camera's host key for authentication
            p2p_password: P2P password (MD5 hash from getConnectString)
            init_string: Initialization string from getConnectString
            debug: Enable debug logging
        """
        self.serial = serial
        self.camera_ip = camera_ip
        self.host_key = host_key
        self.p2p_password = p2p_password
        self.init_string = init_string
        self.debug = debug
        
        # Connection state
        self.sock: Optional[socket.socket] = None
        self.relay_ip: Optional[str] = None
        self.relay_port: Optional[int] = None
        self.connected = False
        self.msg_drw_index = 0
        
        # Extract prefix from serial (e.g., "ppsl" from "ppsl6f313fdd69bd4632")
        self.prefix = self._extract_prefix(serial)
        logger.info(f"Initialized P2P client for {serial} (prefix: {self.prefix})")
        
    def _extract_prefix(self, serial: str) -> str:
        """Extract the device prefix from serial number."""
        # Common prefixes: ppsl (CloudEdge), ajy (AJCloud), DGOK, PTZA, etc.
        prefixes = ['ppsl', 'ajy', 'DGOK', 'PTZA', 'FTYC', 'BATE', 'DGB', 'ACCQ']
        for prefix in prefixes:
            if serial.lower().startswith(prefix.lower()):
                return prefix.lower()
        # Default: first 4 characters
        return serial[:4].lower() if len(serial) >= 4 else serial.lower()
    
    def _generate_uuid(self, serial: str) -> bytes:
        """
        Generate UUID for P2P messages.
        
        Format: <prefix_hex>000000000000000000<serial_hex>
        For CloudEdge (ppsl): ppsl -> 7070736c
        For AJCloud (ajy): ajy -> 616a79
        """
        prefix_hex = self.prefix.encode('utf-8').hex()
        serial_hex = serial.encode('utf-8').hex()
        
        # Pad prefix to match expected format
        # AJCloud uses 6 chars prefix + 18 zeros + 32 chars serial = 56 chars
        # CloudEdge likely similar
        padding = '00' * 18  # 18 zero bytes
        uuid_hex = prefix_hex + padding[:36 - len(prefix_hex)] + serial_hex
        
        return bytes.fromhex(uuid_hex)
    
    def _create_socket(self, local_port: Optional[int] = None) -> socket.socket:
        """Create and bind a UDP socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        if local_port is None:
            local_port = random.randint(30000, 40000)
            
        sock.bind(('0.0.0.0', local_port))
        sock.settimeout(2.0)
        
        logger.debug(f"Socket bound to port {local_port}")
        return sock
    
    def _send_packet(
        self,
        data: bytes,
        target_ip: Optional[str] = None,
        target_port: Optional[int] = None,
        recv_response: bool = False
    ) -> Optional[bytes]:
        """
        Send a UDP packet and optionally receive response.
        
        Args:
            data: Raw packet data to send
            target_ip: Target IP (uses relay_ip if not specified)
            target_port: Target port (uses relay_port if not specified)
            recv_response: Whether to wait for a response
            
        Returns:
            Response bytes if recv_response is True, else None
        """
        if target_ip is None:
            target_ip = self.relay_ip
        if target_port is None:
            target_port = self.relay_port
            
        if target_ip is None or target_port is None:
            logger.error("No target address specified")
            return None
            
        try:
            logger.debug(f"SEND to {target_ip}:{target_port} ({len(data)} bytes): {data[:32].hex()}...")
            self.sock.sendto(data, (target_ip, target_port))
            
            if recv_response:
                response, addr = self.sock.recvfrom(4096)
                logger.debug(f"RECV from {addr}: {response[:32].hex()}...")
                return response
                
        except socket.timeout:
            logger.debug("No response (timeout)")
        except Exception as e:
            logger.error(f"Send error: {e}")
            
        return None
    
    def _build_msg_hello(self) -> bytes:
        """Build MSG_HELLO packet."""
        # Format: f1 00 00 00
        return bytes([PPPP_MAGIC, MSG_HELLO, 0x00, 0x00])
    
    def _build_msg_lan_search(self) -> bytes:
        """Build MSG_LAN_SEARCH packet for local discovery."""
        # Format: f1 30 00 00
        return bytes([PPPP_MAGIC, MSG_LAN_SEARCH, 0x00, 0x00])
    
    def _build_msg_p2p_req(self, source_ip: str, source_port: int) -> bytes:
        """
        Build MSG_P2P_REQ packet.
        
        Args:
            source_ip: Local source IP address
            source_port: Local source port
            
        Returns:
            Packet bytes
        """
        uuid = self._generate_uuid(self.serial)
        
        # Convert source IP to network byte order (reversed)
        ip_bytes = socket.inet_aton(source_ip)[::-1]
        
        # Convert port to network byte order
        port_bytes = struct.pack('!I', socket.htons(source_port))[2:]
        
        # Build packet: magic + opcode + length + uuid + ... + port + ip + padding
        # Length is 0x5C (92) for the payload
        header = bytes([PPPP_MAGIC, MSG_P2P_REQ, 0x00, 0x5C])
        
        # Payload: uuid + zeros + 0x02 + port + ip + zeros
        padding1 = b'\x00' * (56 - len(uuid))  # Pad UUID to 56 bytes
        mode = b'\x02'
        padding2 = b'\x00' * 8
        
        payload = uuid + padding1 + mode + port_bytes + ip_bytes + padding2
        
        return header + payload
    
    def _build_msg_punch_pkt(self) -> bytes:
        """Build MSG_PUNCH_PKT packet."""
        uuid = self._generate_uuid(self.serial)
        
        # Format: f1 41 00 58 + uuid + zeros + 01 + zeros
        header = bytes([PPPP_MAGIC, MSG_PUNCH_PKT, 0x00, 0x58])
        padding = b'\x00' * (88 - len(uuid) - 1)
        tail = b'\x01' + b'\x00' * 11
        
        return header + uuid + padding + tail
    
    def _build_msg_p2p_rdy(self) -> bytes:
        """Build MSG_P2P_RDY packet."""
        uuid = self._generate_uuid(self.serial)
        
        # Format: f1 42 00 58 + uuid + zeros + 01 + zeros
        header = bytes([PPPP_MAGIC, MSG_P2P_RDY, 0x00, 0x58])
        padding = b'\x00' * (88 - len(uuid) - 1)
        tail = b'\x01' + b'\x00' * 11
        
        return header + uuid + padding + tail
    
    def _build_msg_alive(self) -> bytes:
        """Build MSG_ALIVE packet."""
        return bytes([PPPP_MAGIC, MSG_ALIVE, 0x00, 0x00])
    
    def _build_msg_drw(self, command_data: bytes) -> bytes:
        """
        Build MSG_DRW (data read/write) packet.
        
        Args:
            command_data: Command payload to send
            
        Returns:
            Complete DRW packet with index
        """
        # Format: f1 d0 <len_hi> <len_lo> d1 00 <index_hi> <index_lo> + command_data
        length = len(command_data) + 4  # Include d1 00 xx xx header
        
        header = bytes([
            PPPP_MAGIC, MSG_DRW,
            (length >> 8) & 0xFF, length & 0xFF,
            0xD1, 0x00,
            (self.msg_drw_index >> 8) & 0xFF, self.msg_drw_index & 0xFF
        ])
        
        self.msg_drw_index += 1
        
        return header + command_data
    
    def _parse_ip_port(self, data: bytes, offset: int = 0) -> Tuple[str, int]:
        """
        Parse IP and port from packet data.
        
        Args:
            data: Raw packet data
            offset: Offset to start parsing
            
        Returns:
            Tuple of (ip_address, port)
        """
        # Port is in network byte order (big endian), 2 bytes
        port_raw = data[offset:offset+2]
        port = int.from_bytes(port_raw, byteorder='big')
        
        # IP is in network byte order (big endian), 4 bytes
        ip_raw = data[offset+2:offset+6]
        ip = socket.inet_ntoa(ip_raw)
        
        return ip, port
    
    def discover_lan(self, timeout: float = 3.0) -> Optional[Tuple[str, int]]:
        """
        Attempt to discover camera on LAN via broadcast.
        
        Args:
            timeout: Discovery timeout in seconds
            
        Returns:
            Tuple of (camera_ip, camera_port) if found, else None
        """
        logger.info("Starting LAN discovery...")
        
        self.sock = self._create_socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.settimeout(timeout)
        
        # Send LAN search on broadcast
        search_pkt = self._build_msg_lan_search()
        
        for port in [P2P_PORT, P2P_DISCOVERY_PORT]:
            try:
                logger.debug(f"Broadcasting LAN search on port {port}")
                self.sock.sendto(search_pkt, ('255.255.255.255', port))
                
                response, addr = self.sock.recvfrom(1024)
                logger.info(f"LAN discovery response from {addr}: {response.hex()}")
                
                # Check if response is LAN_SEARCH_ACK
                if len(response) >= 2 and response[0] == PPPP_MAGIC and response[1] == MSG_LAN_SEARCH_ACK:
                    self.sock.close()
                    return addr
                    
            except socket.timeout:
                continue
            except Exception as e:
                logger.warning(f"LAN discovery error: {e}")
                
        self.sock.close()
        logger.info("No cameras found via LAN discovery")
        return None
    
    def probe_direct(self, timeout: float = 2.0) -> bool:
        """
        Probe camera directly on LAN.
        
        Args:
            timeout: Probe timeout
            
        Returns:
            True if camera responds, False otherwise
        """
        if not self.camera_ip:
            logger.error("No camera IP specified for direct probe")
            return False
            
        logger.info(f"Probing camera at {self.camera_ip}...")
        
        self.sock = self._create_socket()
        self.sock.settimeout(timeout)
        
        # Try various message types
        probes = [
            ('LAN_SEARCH', self._build_msg_lan_search()),
            ('HELLO', self._build_msg_hello()),
            ('ALIVE', self._build_msg_alive()),
        ]
        
        for port in [P2P_PORT, P2P_DISCOVERY_PORT]:
            for name, probe in probes:
                try:
                    logger.debug(f"Sending {name} to {self.camera_ip}:{port}")
                    self.sock.sendto(probe, (self.camera_ip, port))
                    
                    response, addr = self.sock.recvfrom(1024)
                    logger.info(f"Camera responded to {name}: {response.hex()}")
                    
                    self.relay_ip = addr[0]
                    self.relay_port = addr[1]
                    self.connected = True
                    return True
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.warning(f"Probe error: {e}")
                    
        self.sock.close()
        logger.warning("Camera did not respond to direct probes")
        return False
    
    def connect_via_relay(
        self,
        p2p_servers: Optional[list] = None,
        timeout: float = 5.0
    ) -> bool:
        """
        Connect to camera via P2P relay servers.
        
        This is the standard connection method for cameras that are
        not directly accessible on LAN.
        
        Args:
            p2p_servers: List of (ip, port) tuples for P2P servers
            timeout: Connection timeout
            
        Returns:
            True if connected, False otherwise
        """
        if p2p_servers is None:
            p2p_servers = CLOUDEDGE_P2P_SERVERS
            
        logger.info(f"Connecting via P2P relay servers...")
        
        self.sock = self._create_socket()
        self.sock.settimeout(timeout)
        
        # Get local address
        local_ip = socket.gethostbyname(socket.gethostname())
        local_port = self.sock.getsockname()[1]
        
        logger.info(f"Local address: {local_ip}:{local_port}")
        
        for server_ip, server_port in p2p_servers:
            logger.info(f"Trying P2P server {server_ip}:{server_port}")
            
            # Step 1: Send HELLO
            hello = self._build_msg_hello()
            response = self._send_packet(hello, server_ip, server_port, recv_response=True)
            
            if not response or len(response) < 4:
                logger.debug(f"No response from {server_ip}")
                continue
                
            # Check for HELLO_ACK (f1 01 ...)
            if response[0] != PPPP_MAGIC or response[1] != MSG_HELLO_ACK:
                logger.debug(f"Unexpected response: {response[:4].hex()}")
                continue
                
            logger.info(f"P2P server {server_ip} is online!")
            
            # Step 2: Send P2P_REQ
            p2p_req = self._build_msg_p2p_req(local_ip, local_port)
            response = self._send_packet(p2p_req, server_ip, server_port, recv_response=True)
            
            if not response:
                logger.debug("No response to P2P_REQ")
                continue
                
            # Parse response - expecting MSG_INTRO with punch addresses
            if len(response) >= 4 and response[1] == MSG_INTRO:
                logger.info(f"Received MSG_INTRO: {response.hex()}")
                
                # Extract punch addresses and continue handshake
                # This is complex and depends on the specific protocol variant
                
            # TODO: Complete the P2P handshake
            # - Parse MSG_PUNCH_TO addresses
            # - Send MSG_PUNCH_PKT to those addresses
            # - Receive MSG_P2P_RDY
            # - Start keepalive thread
                
        self.sock.close()
        return False
    
    def send_command(self, command: bytes) -> Optional[bytes]:
        """
        Send a command to the camera.
        
        Args:
            command: Raw command bytes (Meari/HH01 format)
            
        Returns:
            Response bytes if successful, None otherwise
        """
        if not self.connected:
            logger.error("Not connected")
            return None
            
        packet = self._build_msg_drw(command)
        return self._send_packet(packet, recv_response=True)
    
    def request_snapshot(self) -> Optional[bytes]:
        """
        Request a snapshot from the camera.
        
        This is a synchronous wrapper around request_snapshot_async().
        For better performance, use the async version directly.
        
        Returns:
            JPEG image bytes if successful, None otherwise
        """
        import asyncio
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.request_snapshot_async())
    
    async def request_snapshot_async(self) -> Optional[bytes]:
        """
        Request a snapshot from the camera asynchronously.
        
        Uses the aiopppp library approach:
        1. Connect via P2P
        2. Start video stream
        3. Capture first complete frame
        4. Stop video stream
        5. Convert frame to JPEG
        
        Returns:
            JPEG image bytes if successful, None otherwise
        """
        try:
            # Try using aiopppp library if available
            from aiopppp import Device
            
            logger.info(f"Using aiopppp to capture snapshot from {self.camera_ip}")
            
            async with Device(
                self.camera_ip,
                username=getattr(self, 'p2p_username', 'admin'),
                password=self.p2p_password or '6666'
            ) as device:
                logger.info(f"Connected to camera: {device.properties}")
                
                # Start video stream
                await device.start_video()
                logger.info("Video stream started, waiting for frame...")
                
                # Wait for first frame with timeout
                import asyncio
                try:
                    frame = await asyncio.wait_for(
                        device.get_video_frame(),
                        timeout=10.0
                    )
                    logger.info(f"Got video frame: {len(frame.data)} bytes")
                    
                    # Convert H.264 frame to JPEG
                    jpeg_data = self._h264_frame_to_jpeg(frame.data)
                    return jpeg_data
                    
                except asyncio.TimeoutError:
                    logger.error("Timeout waiting for video frame")
                    return None
                finally:
                    await device.stop_video()
                    
        except ImportError:
            logger.info("aiopppp not available, using fallback method")
            return await self._request_snapshot_fallback()
        except Exception as e:
            logger.error(f"Snapshot error: {e}")
            return await self._request_snapshot_fallback()
    
    async def _request_snapshot_fallback(self) -> Optional[bytes]:
        """
        Fallback snapshot method using commondeviceparams2 pattern.
        
        Based on Meari SDK, sends a JSON command to the camera's internal
        HTTP API via P2P tunnel.
        """
        if not self.connected:
            logger.error("Not connected for fallback snapshot")
            return None
        
        # Try the Meari commondeviceparams2 pattern
        # deviceurl format: http://127.0.0.1/devices/snapshot
        command = {
            "action": "GET",
            "deviceurl": "http://127.0.0.1/devices/snapshot"
        }
        
        import json
        command_bytes = json.dumps(command).encode('utf-8')
        
        logger.info(f"Sending snapshot command: {command}")
        response = self.send_command(command_bytes)
        
        if response:
            # Check if response is JPEG data
            if response[:2] == b'\xff\xd8':  # JPEG magic
                logger.info(f"Got JPEG snapshot: {len(response)} bytes")
                return response
            else:
                logger.warning(f"Unexpected response format: {response[:20].hex()}")
        
        return None
    
    def _h264_frame_to_jpeg(self, h264_data: bytes) -> Optional[bytes]:
        """
        Convert H.264 video frame to JPEG image.
        
        Args:
            h264_data: Raw H.264 NAL unit data
            
        Returns:
            JPEG bytes if successful, None otherwise
        """
        try:
            import av
            
            # Create in-memory container
            container = av.open(io.BytesIO(h264_data), format='h264')
            
            for frame in container.decode(video=0):
                # Convert to RGB
                img = frame.to_image()
                
                # Save as JPEG
                output = io.BytesIO()
                img.save(output, format='JPEG', quality=85)
                return output.getvalue()
                
        except ImportError:
            logger.warning("PyAV not installed, trying ffmpeg")
            return self._h264_to_jpeg_ffmpeg(h264_data)
        except Exception as e:
            logger.error(f"H.264 to JPEG conversion failed: {e}")
            return None
    
    def _h264_to_jpeg_ffmpeg(self, h264_data: bytes) -> Optional[bytes]:
        """
        Convert H.264 to JPEG using ffmpeg subprocess.
        """
        import subprocess
        import tempfile
        
        try:
            with tempfile.NamedTemporaryFile(suffix='.h264', delete=False) as f:
                f.write(h264_data)
                h264_path = f.name
            
            jpeg_path = h264_path.replace('.h264', '.jpg')
            
            result = subprocess.run([
                'ffmpeg', '-y', '-i', h264_path,
                '-frames:v', '1', '-q:v', '2',
                jpeg_path
            ], capture_output=True, timeout=10)
            
            if result.returncode == 0 and os.path.exists(jpeg_path):
                with open(jpeg_path, 'rb') as f:
                    jpeg_data = f.read()
                os.unlink(jpeg_path)
                os.unlink(h264_path)
                return jpeg_data
                
        except Exception as e:
            logger.error(f"ffmpeg conversion failed: {e}")
        
        return None
        
    @classmethod
    def from_connect_string(
        cls,
        connect_params: Dict,
        camera_ip: Optional[str] = None,
        debug: bool = True
    ) -> 'CloudEdgeP2PClient':
        """
        Create a P2P client from getConnectString parameters.
        
        Args:
            connect_params: Parsed JSON from getConnectString field
                - licenceid: Camera serial (e.g., "ppsl6f313fdd69bd4632")
                - password: MD5 hash for P2P auth
                - initstring: P2P initialization string
                - udpport: UDP port on camera (after wake)
                - did: Device ID for P2P
                - mode: Connection mode
            camera_ip: Optional LAN IP for direct connection
            debug: Enable debug logging
            
        Returns:
            Configured CloudEdgeP2PClient instance
        """
        serial = connect_params.get('licenceid', '')
        p2p_password = connect_params.get('password', '')
        init_string = connect_params.get('initstring', '')
        
        logger.info(f"Creating P2P client from connect string for {serial}")
        
        client = cls(
            serial=serial,
            camera_ip=camera_ip,
            p2p_password=p2p_password,
            init_string=init_string,
            debug=debug
        )
        
        # Store additional connection params
        client.p2p_udp_port = connect_params.get('udpport')
        client.p2p_did = connect_params.get('did')
        client.p2p_mode = connect_params.get('mode')
        client.p2p_protocol_version = connect_params.get('protocolv')
        client.p2p_username = connect_params.get('username', 'admin')
        client.p2p_trytimes = connect_params.get('trytimes', 3)
        client.p2p_delaysec = connect_params.get('delaysec', 5)
        
        return client
        
    def connect_after_wake(self, timeout: float = 10.0) -> bool:
        """
        Connect to camera after wake_device API was called.
        
        This method attempts:
        1. Direct LAN connection if camera_ip is set
        2. P2P relay connection using the init_string
        
        Args:
            timeout: Connection timeout in seconds
            
        Returns:
            True if connected, False otherwise
        """
        logger.info(f"Connecting to camera {self.serial} after wake...")
        
        # First, try direct LAN connection if we have IP
        if self.camera_ip:
            logger.info(f"Attempting direct LAN connection to {self.camera_ip}")
            
            # Use the P2P UDP port from getConnectString if available
            target_port = getattr(self, 'p2p_udp_port', None) or P2P_PORT
            
            self.sock = self._create_socket()
            self.sock.settimeout(timeout / 3)  # Give 1/3 time for LAN
            
            for port in [target_port, P2P_PORT, P2P_DISCOVERY_PORT]:
                try:
                    # Send HELLO with our serial
                    hello_pkt = self._build_msg_hello()
                    logger.debug(f"Sending HELLO to {self.camera_ip}:{port}")
                    self.sock.sendto(hello_pkt, (self.camera_ip, port))
                    
                    # Wait for response
                    response, addr = self.sock.recvfrom(1024)
                    logger.info(f"Camera responded from {addr}: {response[:20].hex()}...")
                    
                    # Parse response - check if it's valid PPPP packet
                    if len(response) >= 2 and response[0] == PPPP_MAGIC:
                        msg_type = response[1]
                        logger.info(f"Got PPPP response type: 0x{msg_type:02x}")
                        
                        self.relay_ip = addr[0]
                        self.relay_port = addr[1]
                        self.connected = True
                        logger.info(f"✅ Direct LAN connection established to {addr}")
                        return True
                        
                except socket.timeout:
                    logger.debug(f"Timeout on port {port}")
                    continue
                except Exception as e:
                    logger.warning(f"LAN connection error on port {port}: {e}")
                    
            self.sock.close()
            logger.info("Direct LAN connection failed, trying relay...")
            
        # Try P2P relay connection
        if self.init_string:
            logger.info("Attempting P2P relay connection...")
            return self.connect_via_relay()
            
        logger.error("No connection method available - need camera_ip or init_string")
        return False
    
    def close(self):
        """Close the connection."""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.connected = False
        logger.info("Connection closed")


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='CloudEdge P2P Client - Connect to cameras via P2P protocol'
    )
    parser.add_argument(
        '--serial', '-s',
        help='Camera serial number (e.g., ppsl6f313fdd69bd4632)'
    )
    parser.add_argument(
        '--ip', '-i',
        help='Camera LAN IP address for direct connection'
    )
    parser.add_argument(
        '--host-key', '-k',
        help='Camera host key'
    )
    parser.add_argument(
        '--discovery',
        action='store_true',
        help='Attempt LAN discovery'
    )
    parser.add_argument(
        '--relay',
        action='store_true', 
        help='Connect via P2P relay servers'
    )
    parser.add_argument(
        '--connect-string',
        help='JSON string from wake_device API getConnectString response'
    )
    parser.add_argument(
        '--connect-file',
        help='Path to JSON file containing getConnectString response'
    )
    parser.add_argument(
        '--snapshot',
        metavar='OUTFILE',
        help='Capture a snapshot and save to file (e.g., --snapshot snapshot.jpg)'
    )
    
    args = parser.parse_args()
    
    client = None
    
    # Create client from connect string if provided
    if args.connect_string or args.connect_file:
        import json
        
        try:
            if args.connect_file:
                with open(args.connect_file, 'r') as f:
                    connect_params = json.load(f)
            else:
                connect_params = json.loads(args.connect_string)
                
            print(f"\n📡 Using P2P connection parameters from getConnectString:")
            print(f"   License ID: {connect_params.get('licenceid')}")
            print(f"   UDP Port: {connect_params.get('udpport')}")
            print(f"   Protocol: v{connect_params.get('protocolv')}")
            print(f"   Mode: {connect_params.get('mode')}")
            
            client = CloudEdgeP2PClient.from_connect_string(
                connect_params=connect_params,
                camera_ip=args.ip,
                debug=True
            )
            
            # Try to connect after wake
            print(f"\n🔌 Attempting connection after wake...")
            if client.connect_after_wake():
                print(f"\n✅ CONNECTED to camera!")
                print(f"   Target: {client.relay_ip}:{client.relay_port}")
                
                # Capture snapshot if requested
                if args.snapshot:
                    print(f"\n📷 Capturing snapshot...")
                    snapshot_data = client.request_snapshot()
                    if snapshot_data:
                        with open(args.snapshot, 'wb') as f:
                            f.write(snapshot_data)
                        print(f"✅ Snapshot saved to {args.snapshot} ({len(snapshot_data)} bytes)")
                    else:
                        print("❌ Failed to capture snapshot")
            else:
                print(f"\n❌ Connection failed")
                
        except json.JSONDecodeError as e:
            print(f"Error parsing connect string: {e}")
            return 1
        except FileNotFoundError:
            print(f"File not found: {args.connect_file}")
            return 1
            
    else:
        # Use traditional parameters
        if not args.serial:
            print("Error: --serial is required when not using --connect-string")
            parser.print_help()
            return 1
            
        client = CloudEdgeP2PClient(
            serial=args.serial,
            camera_ip=args.ip,
            host_key=args.host_key,
            debug=True
        )
        
        try:
            if args.discovery:
                result = client.discover_lan()
                if result:
                    print(f"\n✓ Camera found at {result[0]}:{result[1]}")
                else:
                    print("\n✗ No cameras found via LAN discovery")
                    
            if args.ip:
                if client.probe_direct():
                    print(f"\n✓ Camera at {args.ip} is responsive!")
                else:
                    print(f"\n✗ Camera at {args.ip} did not respond")
                    print("  Note: CloudEdge cameras may require wake-up via removeWake.action API")
                    print("  Run: python test_wake_device.py <email> <password>")
                    
            if args.relay:
                if client.connect_via_relay():
                    print("\n✓ Connected via P2P relay!")
                else:
                    print("\n✗ Failed to connect via P2P relay")
                    
        finally:
            if client:
                client.close()
    
    if client:
        client.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
