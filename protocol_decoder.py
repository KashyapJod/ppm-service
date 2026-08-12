import struct
import time

# Protocol Constants
MAGIC_BYTES = b'\xaa\x55'

# Message Types
MSG_HANDSHAKE   = 0x01
MSG_DATA        = 0x02
MSG_HEARTBEAT   = 0x03
MSG_ACK         = 0x04
MSG_SIM_CONTROL = 0x05

# Base Header Format: Magic (2B), Type (1B), Length (2B), Device ID (16B), Patient ID (16B), Timestamp (8B double)
# Total Header Size = 2 + 1 + 2 + 16 + 16 + 8 = 45 bytes
HEADER_FORMAT = ">2sB H 16s 16s d"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

def crc16_modbus(data: bytes) -> int:
    """Calculate Modbus CRC-16 checksum."""
    crc = 0xFFFF
    for pos in data:
        crc ^= pos
        for _ in range(8):
            if (crc & 1) != 0:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return crc

def pack_header(msg_type: int, payload_len: int, device_id: str, patient_id: str, timestamp: float = None) -> bytes:
    """Pack the fixed-size message header."""
    if timestamp is None:
        timestamp = time.time()
    
    dev_bytes = device_id.encode('utf-8')[:16].ljust(16, b'\x00')
    pat_bytes = patient_id.encode('utf-8')[:16].ljust(16, b'\x00')
    
    return struct.pack(HEADER_FORMAT, MAGIC_BYTES, msg_type, payload_len, dev_bytes, pat_bytes, timestamp)

def pack_vitals_payload(hr: int, spo2: float, nibp_sys: int, nibp_dia: int, temp: float, resp: int, ecg_points: list) -> bytes:
    """
    Pack the vitals data payload.
    Format:
      - HR: 1B (uint8)
      - SpO2: 2B (uint16, scaled * 100)
      - NIBP Sys: 2B (uint16)
      - NIBP Dia: 2B (uint16)
      - Temp: 2B (uint16, scaled * 100)
      - Resp: 1B (uint8)
      - ECG Count: 2B (uint16)
      - ECG Points: Count * 2B (int16 array)
    """
    # Scale decimal values
    spo2_scaled = int(round(spo2 * 100))
    temp_scaled = int(round(temp * 100))
    
    ecg_count = len(ecg_points)
    
    # Static payload format: B H H H H B H
    static_fmt = ">B H H H H B H"
    static_payload = struct.pack(static_fmt, hr, spo2_scaled, nibp_sys, nibp_dia, temp_scaled, resp, ecg_count)
    
    # Dynamic ECG points format: <ecg_count>h
    ecg_fmt = f">{ecg_count}h"
    ecg_payload = struct.pack(ecg_fmt, *ecg_points)
    
    return static_payload + ecg_payload

def build_packet(msg_type: int, device_id: str, patient_id: str, payload: bytes = b"", timestamp: float = None) -> bytes:
    """Build a complete packet including header, payload, and CRC-16 checksum."""
    header = pack_header(msg_type, len(payload), device_id, patient_id, timestamp)
    packet_without_crc = header + payload
    crc = crc16_modbus(packet_without_crc)
    return packet_without_crc + struct.pack(">H", crc)

def unpack_packet(packet_bytes: bytes) -> dict:
    """
    Unpack a complete packet, validating its integrity.
    Returns a dictionary of parsed parameters or raises a ValueError.
    """
    if len(packet_bytes) < HEADER_SIZE + 2:
        raise ValueError("Packet is too short to contain a valid header and CRC.")
    
    # Extract CRC and verify
    received_crc = struct.unpack(">H", packet_bytes[-2:])[0]
    payload_and_header = packet_bytes[:-2]
    expected_crc = crc16_modbus(payload_and_header)
    
    if received_crc != expected_crc:
        raise ValueError(f"CRC Mismatch! Expected 0x{expected_crc:04X}, Got 0x{received_crc:04X}")
    
    # Unpack Header
    magic, msg_type, payload_len, dev_bytes, pat_bytes, timestamp = struct.unpack(
        HEADER_FORMAT, payload_and_header[:HEADER_SIZE]
    )
    
    if magic != MAGIC_BYTES:
        raise ValueError("Invalid Magic bytes.")
    
    device_id = dev_bytes.decode('utf-8').rstrip('\x00')
    patient_id = pat_bytes.decode('utf-8').rstrip('\x00')
    
    payload = payload_and_header[HEADER_SIZE:]
    if len(payload) != payload_len:
        raise ValueError(f"Payload length mismatch! Expected {payload_len} bytes, Got {len(payload)} bytes.")
    
    result = {
        "msg_type": msg_type,
        "device_id": device_id,
        "patient_id": patient_id,
        "timestamp": timestamp,
        "payload_bytes": payload
    }
    
    # Parse payload if MSG_DATA
    if msg_type == MSG_DATA:
        static_fmt = ">B H H H H B H"
        static_size = struct.calcsize(static_fmt)
        if len(payload) < static_size:
            raise ValueError("Vitals payload is too short.")
            
        hr, spo2_scaled, nibp_sys, nibp_dia, temp_scaled, resp, ecg_count = struct.unpack(
            static_fmt, payload[:static_size]
        )
        
        ecg_data_bytes = payload[static_size:]
        expected_ecg_bytes = ecg_count * 2
        if len(ecg_data_bytes) != expected_ecg_bytes:
            raise ValueError(f"ECG payload length mismatch! Expected {expected_ecg_bytes} bytes, Got {len(ecg_data_bytes)} bytes.")
            
        ecg_fmt = f">{ecg_count}h"
        ecg_points = list(struct.unpack(ecg_fmt, ecg_data_bytes))
        
        result["vitals"] = {
            "hr": hr,
            "spo2": spo2_scaled / 100.0,
            "nibp_sys": nibp_sys,
            "nibp_dia": nibp_dia,
            "temp": temp_scaled / 100.0,
            "resp": resp,
            "ecg_points": ecg_points
        }
        
    return result

if __name__ == "__main__":
    # Test encoding and decoding
    test_ecg = [10, 15, -5, 40, -20, 5, 12]
    payload = pack_vitals_payload(
        hr=78, spo2=98.5, nibp_sys=120, nibp_dia=80, temp=37.1, resp=16, ecg_points=test_ecg
    )
    packet = build_packet(MSG_DATA, "YK-8000C-001", "PT-1001", payload)
    
    print(f"Test Packet Hex: {packet.hex().upper()[:80]}...")
    print(f"Total Packet Length: {len(packet)} bytes")
    
    try:
        decoded = unpack_packet(packet)
        print("Successfully unpacked packet:")
        print(f"  Device: {decoded['device_id']}")
        print(f"  Patient: {decoded['patient_id']}")
        print(f"  Timestamp: {decoded['timestamp']}")
        print(f"  Vitals: {decoded['vitals']}")
        print("Self-test passed!")
    except Exception as e:
        print(f"Self-test failed: {e}")
