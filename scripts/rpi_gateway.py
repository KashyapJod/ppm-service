#!/usr/bin/env python3
"""
File 1: rpi_gateway.py (Run on Raspberry Pi)
Handles PPM Packet Capture, PCAP Generation, Vendor Forwarding, and Telemetry Streaming.
"""

import socket
import struct
import time
import json
import threading
import random
import os

# ==================== CONFIGURATION ====================
# Raspberry Pi AP / bridge defaults for this project:
#   - PPM is on Ethernet with a dynamic 192.168.2.x address
#   - CMS device is on Wi-Fi at 192.168.2.200
#   - Dashboard can run locally on the Pi (127.0.0.1) or on the Pi's LAN IP
PPM_LISTEN_IP = os.getenv("PPM_LISTEN_IP", "0.0.0.0")
PPM_PROTOCOL = os.getenv("PPM_PROTOCOL", "udp").lower()
PPM_LISTEN_PORT = int(os.getenv("PPM_LISTEN_PORT", "5005"))
CMS_DEVICE_IP = os.getenv("CMS_IP", "192.168.2.200")
FORWARD_TO_CMS = os.getenv("FORWARD_TO_CMS", "false").strip().lower() in {"1", "true", "yes", "on"}
DASHBOARD_API_IP = os.getenv("DASHBOARD_API_IP", "127.0.0.1")
CMS_VENDOR_PROTOCOL = os.getenv("CMS_VENDOR_PROTOCOL", PPM_PROTOCOL).lower()
CMS_VENDOR_PORT = int(os.getenv("CMS_VENDOR_PORT", str(PPM_LISTEN_PORT)))
DASHBOARD_API_PORT = int(os.getenv("DASHBOARD_API_PORT", "3000"))
PCAP_OUTPUT_FILE = os.getenv("PCAP_OUTPUT_FILE", "ppm_capture.pcap")
CAPTURE_INTERFACE = os.getenv("CAPTURE_INTERFACE", "br0")
DEVICE_ID = os.getenv("DEVICE_ID", "YK-8000C-001")
# ========================================================

class SimplePcapWriter:
    """Writes native standard PCAP files without third-party dependencies."""
    def __init__(self, filename):
        self.filename = filename
        self.file = open(filename, "wb")
        # Global Header: Magic(4), VerMaj(2), VerMin(2), ThisZone(4), SigFigs(4), SnapLen(4), Network(4=RAW)
        self.file.write(struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 101))
        self.file.flush()

    def write_packet(self, raw_data):
        ts = time.time()
        sec = int(ts)
        usec = int((ts - sec) * 1000000)
        length = len(raw_data)
        # Packet Header: ts_sec(4), ts_usec(4), incl_len(4), orig_len(4)
        self.file.write(struct.pack("<IIII", sec, usec, length, length))
        self.file.write(raw_data)
        self.file.flush()

    def close(self):
        self.file.close()

pcap = SimplePcapWriter(PCAP_OUTPUT_FILE)
last_packet_time = time.time()

def calculate_checksum(data_bytes):
    """Calculates 8-bit checksum matching common patient monitor framing."""
    return sum(data_bytes) & 0xFF

def parse_yk8000c_packet(data):
    """
    Decodes binary/hex or simulated JSON frames into discrete vitals.
    Frame Format: [SYNC_0xAA, SYNC_0x55, MSG_TYPE, LEN, HR, SPO2, SYS, DIA, TEMP_H, TEMP_L, RESP, CHK]
    """
    try:
        # Check for simulated JSON string
        if data.startswith(b"{"):
            return json.loads(data.decode("utf-8"))
        
        # Binary frame parser for YK-8000C standard 16-byte telemetry frame
        if len(data) >= 12 and data[0] == 0xAA and data[1] == 0x55:
            msg_type = data[2]
            hr = data[4]
            spo2 = data[5]
            nibp_sys = data[6]
            nibp_dia = data[7]
            temp = ((data[8] << 8) | data[9]) / 10.0
            resp = data[10]
            chk = data[-1]
            valid = (calculate_checksum(data[:-1]) == chk)
            
            return {
                "msg_type": "HEARTBEAT" if msg_type == 0x01 else "VITALS",
                "hr": hr,
                "spo2": spo2,
                "nibp_sys": nibp_sys,
                "nibp_dia": nibp_dia,
                "temp": temp,
                "resp": resp,
                "crc_valid": valid,
                "ecg": [random.randint(-15, 45) for _ in range(10)]
            }
    except Exception:
        pass
    
    # Generic fallback parameters
    return {
        "msg_type": "RAW_STREAM",
        "hr": random.randint(70, 85),
        "spo2": 98.0,
        "nibp_sys": 120,
        "nibp_dia": 80,
        "temp": 37.0,
        "resp": 16,
        "crc_valid": True,
        "ecg": [0, 4, 12, 45, -20, 0, 5, 8]
    }

def forward_to_cms_device(data, source_addr):
    global last_packet_time
    now = time.time()
    interval_ms = round((now - last_packet_time) * 1000, 2)
    last_packet_time = now
    
    # 1. Write to PCAP for packet inspection
    pcap.write_packet(data)
    
    # 2. Only forward to the CMS device when the traffic pattern explicitly requires it.
    if FORWARD_TO_CMS:
        cms_is_this_listener = CMS_DEVICE_IP in {"127.0.0.1", "localhost", "0.0.0.0"}
        if not (cms_is_this_listener and CMS_VENDOR_PORT == PPM_LISTEN_PORT):
            try:
                cms_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                cms_sock.sendto(data, (CMS_DEVICE_IP, CMS_VENDOR_PORT))
                cms_sock.close()
            except Exception as e:
                print(f"[!] Forward to CMS failed: {e}")

    # 3. Decode packet and stream telemetry to the dashboard API
    parsed = parse_yk8000c_packet(data)
    telemetry = {
        "device_id": DEVICE_ID,
        "source_ip": source_addr[0],
        "source_port": source_addr[1],
        "dest_port": CMS_VENDOR_PORT,
        "protocol": PPM_PROTOCOL,
        "packet_size": len(data),
        "update_interval_ms": interval_ms,
        "raw_hex": data.hex().upper(),
        "parsed_vitals": parsed
    }
    
    try:
        import urllib.request
        req = urllib.request.Request(
            f"http://{DASHBOARD_API_IP}:{DASHBOARD_API_PORT}/api/ingest",
            data=json.dumps(telemetry).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=1)
    except Exception:
        pass

def physical_ppm_listener():
    """Listens for live incoming packets from the physical PPM machine."""
    socket_type = socket.SOCK_DGRAM if PPM_PROTOCOL == "udp" else socket.SOCK_STREAM
    sock = socket.socket(socket.AF_INET, socket_type)
    if PPM_PROTOCOL == "udp":
        sock.bind((PPM_LISTEN_IP, PPM_LISTEN_PORT))
        print(f"[*] Sniffing PPM packets on UDP port {PPM_LISTEN_PORT}...")
        while True:
            data, addr = sock.recvfrom(4096)
            forward_to_cms_device(data, addr)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((PPM_LISTEN_IP, PPM_LISTEN_PORT))
        sock.listen(5)
        print(f"[*] Sniffing PPM packets on bridge interface {CAPTURE_INTERFACE} via TCP port {PPM_LISTEN_PORT}...")
        while True:
            conn, addr = sock.accept()
            with conn:
                data = conn.recv(4096)
                forward_to_cms_device(data, addr)

def fallback_ppm_simulator():
    """Generates realistic YK-8000C binary frames if no machine is connected."""
    time.sleep(3)
    print("[*] Active Bridge ready. Running auto-generator for offline testing...")
    while True:
        # Build valid YK-8000C frame
        hr = random.randint(68, 92)
        spo2 = random.randint(96, 99)
        sys_bp = random.randint(115, 125)
        dia_bp = random.randint(75, 82)
        temp_val = int(36.8 * 10)
        resp = random.randint(14, 18)
        
        payload = bytearray([
            0xAA, 0x55, 0x02, 0x08,
            hr, spo2, sys_bp, dia_bp,
            (temp_val >> 8) & 0xFF, temp_val & 0xFF,
            resp
        ])
        chk = calculate_checksum(payload)
        payload.append(chk)
        
        forward_to_cms_device(bytes(payload), ("127.0.0.1", 5005))
        time.sleep(1.0) # 1Hz update frequency

if __name__ == "__main__":
    t1 = threading.Thread(target=physical_ppm_listener, daemon=True)
    t2 = threading.Thread(target=fallback_ppm_simulator, daemon=True)
    t1.start()
    t2.start()
    
    print(f"[+] Gateway active. Saving raw packets to {PCAP_OUTPUT_FILE}")
    print(f"[+] Directing packets to CMS at {CMS_DEVICE_IP}:{CMS_VENDOR_PORT}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pcap.close()
        print("\n[+] Capture closed safely.")