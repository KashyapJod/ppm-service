#!/usr/bin/env python3
"""
Dummy PPM Packet Sender
Simulates a YK-8000C patient monitor sending packets to the gateway for testing.
"""

import socket
import struct
import time
import random
import sys
import os

# ==================== CONFIGURATION ====================
# For a Raspberry Pi deployment, the PPM usually sends directly to the CMS device
# on 192.168.2.200. If you are testing through the Pi AP/gateway, override the
# IP with GATEWAY_IP=192.168.2.1 and set the matching protocol/port.
GATEWAY_IP = os.getenv("GATEWAY_IP", "192.168.2.200")
GATEWAY_PROTOCOL = os.getenv("GATEWAY_PROTOCOL", "udp").lower()
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "5005"))
SEND_INTERVAL = float(os.getenv("SEND_INTERVAL", "1.0"))
# ========================================================

def calculate_checksum(data_bytes):
    """Calculates 8-bit checksum matching common patient monitor framing."""
    return sum(data_bytes) & 0xFF

def generate_yk8000c_packet():
    """
    Generates a valid YK-8000C binary telemetry frame.
    Frame Format: [SYNC_0xAA, SYNC_0x55, MSG_TYPE, LEN, HR, SPO2, SYS, DIA, TEMP_H, TEMP_L, RESP, CHK]
    """
    hr = random.randint(60, 100)              # Heart rate: 60-100 bpm
    spo2 = random.randint(95, 100)            # SpO2: 95-100%
    sys_bp = random.randint(110, 130)         # Systolic: 110-130 mmHg
    dia_bp = random.randint(70, 85)           # Diastolic: 70-85 mmHg
    temp_val = int((36.5 + random.uniform(-0.5, 0.5)) * 10)  # Temp: 36-37°C
    resp = random.randint(12, 20)             # Respiratory rate: 12-20
    
    # Build frame: [SYNC_0xAA, SYNC_0x55, MSG_TYPE, LEN, HR, SPO2, SYS, DIA, TEMP_H, TEMP_L, RESP]
    payload = bytearray([
        0xAA, 0x55,           # Sync bytes
        0x02,                 # Message type: VITALS
        0x08,                 # Data length
        hr,                   # Heart rate
        spo2,                 # SpO2
        sys_bp,               # Systolic BP
        dia_bp,               # Diastolic BP
        (temp_val >> 8) & 0xFF,  # Temperature high byte
        temp_val & 0xFF,         # Temperature low byte
        resp                  # Respiratory rate
    ])
    
    # Calculate and append checksum
    chk = calculate_checksum(payload)
    payload.append(chk)
    
    return bytes(payload)

def send_dummy_packets():
    """Sends continuous dummy PPM packets to the gateway."""
    sock_type = socket.SOCK_DGRAM if GATEWAY_PROTOCOL == "udp" else socket.SOCK_STREAM
    sock = socket.socket(socket.AF_INET, sock_type)
    
    print(f"[*] Dummy PPM Sender starting...")
    print(f"[*] Target: {GATEWAY_PROTOCOL.upper()} {GATEWAY_IP}:{GATEWAY_PORT}")
    print(f"[*] Sending packets every {SEND_INTERVAL}s...\n")
    
    packet_count = 0
    try:
        while True:
            packet = generate_yk8000c_packet()
            if GATEWAY_PROTOCOL == "udp":
                sock.sendto(packet, (GATEWAY_IP, GATEWAY_PORT))
            else:
                if not hasattr(sock, "connected") or not sock.connected:
                    sock.connect((GATEWAY_IP, GATEWAY_PORT))
                    sock.connected = True
                sock.sendall(packet)
            
            packet_count += 1
            hr = packet[4]
            spo2 = packet[5]
            sys_bp = packet[6]
            dia_bp = packet[7]
            temp_raw = (packet[8] << 8) | packet[9]
            temp = temp_raw / 10.0
            resp = packet[10]
            
            print(f"[{packet_count:04d}] HR:{hr:3d} SpO2:{spo2:3d}% BP:{sys_bp:3d}/{dia_bp:3d} Temp:{temp:5.1f}°C RR:{resp:2d}")
            
            time.sleep(SEND_INTERVAL)
    
    except KeyboardInterrupt:
        print(f"\n[+] Stopped. Sent {packet_count} packets.")
        sock.close()
    except Exception as e:
        print(f"[!] Error: {e}")
        sock.close()

if __name__ == "__main__":
    send_dummy_packets()
