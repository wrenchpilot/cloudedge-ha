#!/usr/bin/env python3
"""
PPPP Encryption Cryptanalysis Tool

Attempts to decrypt PPPP video packets using various encryption algorithms
and key derivation methods with the hostKey from CloudEdge API.

Based on packet capture analysis showing encrypted video data after
successful PPPP handshake.
"""

import hashlib
import struct
from Crypto.Cipher import AES, DES3
from Crypto.Util.Padding import unpad
import logging

logger = logging.getLogger(__name__)


class PPPPCryptoAnalyzer:
    """
    Analyzes and attempts to decrypt PPPP encrypted packets.
    """
    
    def __init__(self, host_key: str):
        """
        Initialize crypto analyzer with hostKey.
        
        Args:
            host_key: Authentication key from CloudEdge API
        """
        self.host_key = host_key
        self.session_id = None
    
    def set_session_id(self, session_id: str):
        """Set session ID for key derivation."""
        self.session_id = session_id
    
    def derive_key_md5(self, salt: str = "") -> bytes:
        """
        Derive encryption key using MD5 hash.
        
        Args:
            salt: Optional salt to add to key material
        
        Returns:
            bytes: 16-byte key for AES-128
        """
        key_material = f"{self.host_key}{salt}".encode()
        return hashlib.md5(key_material).digest()
    
    def derive_key_sha256(self, salt: str = "", length: int = 16) -> bytes:
        """
        Derive encryption key using SHA256 hash.
        
        Args:
            salt: Optional salt to add to key material
            length: Key length in bytes (16 for AES-128, 24 for AES-192, 32 for AES-256)
        
        Returns:
            bytes: Truncated SHA256 hash
        """
        key_material = f"{self.host_key}{salt}".encode()
        return hashlib.sha256(key_material).digest()[:length]
    
    def derive_key_3des(self) -> bytes:
        """
        Derive 3DES key (same method as CloudEdge API auth).
        
        Returns:
            bytes: 24-byte key for 3DES
        """
        # 3DES requires 24 bytes (3 x 8-byte keys)
        key = b"123456781234567812345678"[:24]
        return key
    
    def try_decrypt_aes_cbc(self, ciphertext: bytes, iv: bytes = None) -> tuple:
        """
        Try decrypting with AES-128-CBC using various key derivations.
        
        Args:
            ciphertext: Encrypted data
            iv: Initialization vector (uses zero IV if None)
        
        Returns:
            tuple: (success, decrypted_data, method)
        """
        if iv is None:
            iv = b'\x00' * 16  # Zero IV
        
        # Try different key derivation methods
        methods = [
            ("MD5(hostKey)", self.derive_key_md5()),
            ("MD5(hostKey+sid)", self.derive_key_md5(self.session_id or "")),
            ("SHA256(hostKey)[:16]", self.derive_key_sha256()),
            ("SHA256(hostKey+sid)[:16]", self.derive_key_sha256(self.session_id or "")),
            ("hostKey[:16]", self.host_key.encode()[:16]),
        ]
        
        for method_name, key in methods:
            try:
                cipher = AES.new(key, AES.MODE_CBC, iv)
                decrypted = cipher.decrypt(ciphertext)
                
                # Check for valid decryption indicators
                if self._is_valid_decryption(decrypted):
                    logger.info(f"✅ AES-CBC decryption successful with {method_name}!")
                    return (True, decrypted, f"AES-CBC-{method_name}")
                
            except Exception as e:
                logger.debug(f"AES-CBC {method_name} failed: {e}")
                continue
        
        return (False, None, None)
    
    def try_decrypt_3des_cbc(self, ciphertext: bytes, iv: bytes = None) -> tuple:
        """
        Try decrypting with 3DES-CBC (same as CloudEdge API auth).
        
        Args:
            ciphertext: Encrypted data
            iv: Initialization vector (uses "01234567" if None)
        
        Returns:
            tuple: (success, decrypted_data, method)
        """
        if iv is None:
            iv = b"01234567"  # Same IV as API auth
        
        try:
            key = self.derive_key_3des()
            cipher = DES3.new(key, DES3.MODE_CBC, iv)
            decrypted = cipher.decrypt(ciphertext)
            
            if self._is_valid_decryption(decrypted):
                logger.info(f"✅ 3DES-CBC decryption successful!")
                return (True, decrypted, "3DES-CBC")
        
        except Exception as e:
            logger.debug(f"3DES-CBC failed: {e}")
        
        return (False, None, None)
    
    def try_decrypt_xor(self, ciphertext: bytes) -> tuple:
        """
        Try decrypting with simple XOR cipher.
        
        Args:
            ciphertext: Encrypted data
        
        Returns:
            tuple: (success, decrypted_data, method)
        """
        key_bytes = self.host_key.encode()
        
        # XOR with repeating key
        decrypted = bytearray()
        for i, byte in enumerate(ciphertext):
            decrypted.append(byte ^ key_bytes[i % len(key_bytes)])
        
        if self._is_valid_decryption(bytes(decrypted)):
            logger.info(f"✅ XOR decryption successful!")
            return (True, bytes(decrypted), "XOR")
        
        return (False, None, None)
    
    def _is_valid_decryption(self, data: bytes) -> bool:
        """
        Check if decrypted data looks valid (H.264/H.265 video).
        
        Args:
            data: Decrypted data
        
        Returns:
            bool: True if data appears to be valid video
        """
        if len(data) < 4:
            return False
        
        # Check for H.264/H.265 NAL unit start codes
        if data[:4] == b'\x00\x00\x00\x01':
            logger.debug("Found H.264 long start code!")
            return True
        
        if data[:3] == b'\x00\x00\x01':
            logger.debug("Found H.264 short start code!")
            return True
        
        # Check for NAL unit types (after start code)
        if data[:4] == b'\x00\x00\x00\x01':
            nal_type = data[4] & 0x1F
            if nal_type in [1, 5, 6, 7, 8]:  # Common NAL types
                logger.debug(f"Found H.264 NAL type {nal_type}")
                return True
        
        # Check for "VVP" marker (seen in packet capture)
        if b'VVP' in data[:20]:
            logger.debug("Found VVP marker!")
            return True
        
        # Check for JSON (unencrypted control messages)
        try:
            if data[0:1] == b'{' and data[-1:] == b'}':
                import json
                json.loads(data.decode())
                logger.debug("Found JSON data (unencrypted)")
                return True
        except:
            pass
        
        return False
    
    def analyze_packet(self, encrypted_data: bytes) -> dict:
        """
        Analyze and attempt to decrypt a PPPP encrypted packet.
        
        Args:
            encrypted_data: Raw encrypted packet data
        
        Returns:
            dict: Analysis results including decryption success
        """
        results = {
            'size': len(encrypted_data),
            'decrypted': False,
            'method': None,
            'data': None,
            'hex_preview': encrypted_data[:32].hex()
        }
        
        logger.info(f"Analyzing encrypted packet ({len(encrypted_data)} bytes)")
        logger.debug(f"First 32 bytes: {results['hex_preview']}")
        
        # Try AES-CBC
        success, data, method = self.try_decrypt_aes_cbc(encrypted_data)
        if success:
            results['decrypted'] = True
            results['method'] = method
            results['data'] = data
            return results
        
        # Try 3DES-CBC
        success, data, method = self.try_decrypt_3des_cbc(encrypted_data)
        if success:
            results['decrypted'] = True
            results['method'] = method
            results['data'] = data
            return results
        
        # Try XOR
        success, data, method = self.try_decrypt_xor(encrypted_data)
        if success:
            results['decrypted'] = True
            results['method'] = method
            results['data'] = data
            return results
        
        logger.warning("❌ All decryption methods failed!")
        logger.info("This may indicate:")
        logger.info("  1. Custom encryption algorithm (needs firmware analysis)")
        logger.info("  2. Additional key derivation step (needs more packet captures)")
        logger.info("  3. Different key material (not just hostKey)")
        
        return results
    
    def extract_video_frame(self, decrypted_data: bytes) -> bytes:
        """
        Extract video frame from decrypted packet.
        
        Args:
            decrypted_data: Decrypted packet data
        
        Returns:
            bytes: Raw video frame (H.264/H.265 NAL units)
        """
        # Look for H.264 start code
        start_idx = decrypted_data.find(b'\x00\x00\x00\x01')
        if start_idx == -1:
            start_idx = decrypted_data.find(b'\x00\x00\x01')
        
        if start_idx == -1:
            logger.warning("No H.264 start code found in decrypted data")
            return decrypted_data
        
        # Extract from start code to end
        frame = decrypted_data[start_idx:]
        logger.info(f"Extracted video frame: {len(frame)} bytes")
        
        return frame


def analyze_captured_packet():
    """
    Analyze the encrypted packet from our packet capture.
    
    This is the encrypted video data packet at offset 0x00000644 in the capture.
    """
    # Encrypted packet from packet capture (starting at 0x0644)
    encrypted_packet = bytes.fromhex("""
        0c 00 00 00 51 00 00 02 d8 97 98 18 00 00 00 00
        00 00 00 00 50 00 00 00 ff 01 00 00 b8 d4 b0 09
        9f 99 5d 08 00 00 10 70 3c 00 00 00 56 56 50 99
        00 00 00 01 00 00 00 30 00 00 11 ff 38 31 61 66
        30 39 65 39 36 61 39 34 64 36 66 30 64 64 61 35
        39 35 65 38 31 31 38 63 62 37 31 65 00 00 00 08
        00 00 00 00 65 01 00 00
    """.replace('\n', '').replace(' ', ''))
    
    # Extract encrypted payload (skip 12-byte command header)
    command_header = encrypted_packet[:12]
    encrypted_payload = encrypted_packet[12:]
    
    cmd_len, cmd_type, seq = struct.unpack('<III', command_header)
    
    print("=" * 70)
    print("PPPP Encrypted Packet Analysis")
    print("=" * 70)
    print(f"\nCommand Header:")
    print(f"  Length: 0x{cmd_len:08x} ({cmd_len})")
    print(f"  Type: 0x{cmd_type:08x}")
    print(f"  Sequence: 0x{seq:08x} ({seq})")
    print(f"\nEncrypted Payload:")
    print(f"  Size: {len(encrypted_payload)} bytes")
    print(f"  Hex: {encrypted_payload[:32].hex()}...")
    
    # Example hostKey (replace with actual from API)
    host_key = "your_host_key_here"
    
    analyzer = PPPPCryptoAnalyzer(host_key)
    analyzer.set_session_id("05217c4000000002")
    
    results = analyzer.analyze_packet(encrypted_payload)
    
    print(f"\n{'=' * 70}")
    print("Decryption Results")
    print("=" * 70)
    print(f"  Success: {results['decrypted']}")
    print(f"  Method: {results['method'] or 'None'}")
    
    if results['decrypted']:
        print(f"  Decrypted size: {len(results['data'])} bytes")
        print(f"  First 32 bytes: {results['data'][:32].hex()}")
        
        # Try to extract video frame
        frame = analyzer.extract_video_frame(results['data'])
        print(f"\n  Video Frame:")
        print(f"    Size: {len(frame)} bytes")
        print(f"    First 16 bytes: {frame[:16].hex()}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    analyze_captured_packet()
