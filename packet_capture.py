import socket
import time
import os
from protocol_decoder import unpack_packet, MSG_HANDSHAKE, MSG_DATA, MSG_HEARTBEAT, MSG_ACK

# Scapy imports
try:
    from scapy.all import IP, UDP, Raw, wrpcap, Ether
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False

TAP_IP = "127.0.0.1"
TAP_PORT = 5003
PCAP_FILE = "capture.pcap"

# Buffer for saving packets to PCAP
packet_buffer = []
buffer_lock = os.O_CREAT # simple lock or just standard append since it's UDP single thread

def get_msg_type_name(msg_type):
    if msg_type == MSG_HANDSHAKE:
        return "HANDSHAKE"
    elif msg_type == MSG_DATA:
        return "DATA"
    elif msg_type == MSG_HEARTBEAT:
        return "HEARTBEAT"
    elif msg_type == MSG_ACK:
        return "ACK"
    return "UNKNOWN"

def log_packet(timestamp, src_ip, dst_ip, sport, dport, payload_bytes):
    """Parse binary telemetry and log metadata."""
    try:
        parsed = unpack_packet(payload_bytes)
        msg_type_name = get_msg_type_name(parsed["msg_type"])
        device_id = parsed["device_id"]
        patient_id = parsed["patient_id"]
        
        log_line = (
            f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))}] "
            f"{src_ip}:{sport} -> {dst_ip}:{dport} | "
            f"Type: {msg_type_name} | Device: {device_id} | Patient: {patient_id} | "
            f"Size: {len(payload_bytes)} bytes"
        )
        print(log_line)
        
        # Write to local metadata log file
        with open("packet_metadata.log", "a") as f:
            f.write(log_line + "\n")
            
    except Exception as e:
        print(f"[Sniffer] Warning: Failed to parse packet from {src_ip}:{sport} ({e})")

def process_live_packet(pkt):
    """Scapy callback for live sniffing."""
    if pkt.haslayer(UDP) and pkt.haslayer(Raw):
        payload = pkt[Raw].load
        # Identify our custom protocol by length and potential structure (ends with CRC)
        if len(payload) >= 45: # HEADER_SIZE
            timestamp = pkt.time
            src_ip = pkt[IP].src
            dst_ip = pkt[IP].dst
            sport = pkt[UDP].sport
            dport = pkt[UDP].dport
            
            log_packet(timestamp, src_ip, dst_ip, sport, dport, payload)
            
            # Save packet to buffer and flush to pcap
            packet_buffer.append(pkt)
            if len(packet_buffer) >= 10:
                flush_pcap()

def flush_pcap():
    if not SCAPY_AVAILABLE or not packet_buffer:
        return
    try:
        # Check if PCAP file exists to append or write new
        append = os.path.exists(PCAP_FILE)
        wrpcap(PCAP_FILE, packet_buffer, append=append)
        packet_buffer.clear()
    except Exception as e:
        print(f"[Sniffer] PCAP Write Error: {e}")

def run_tap_sniffer():
    """Fallback sniffer that listens on UDP loopback port 5003."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((TAP_IP, TAP_PORT))
    print(f"[Sniffer] Fallback Tap Sniffer listening on UDP {TAP_IP}:{TAP_PORT}...")
    
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            # Packet metadata payload sent as: src_ip, dst_ip, sport, dport, followed by the raw packet data.
            # We structure it: IP/Port info is comma-separated text, then a newline, then raw bytes.
            metadata_header, raw_packet = data.split(b"\n", 1)
            src_ip, dst_ip, sport, dport = metadata_header.decode('utf-8').split(",")
            sport, dport = int(sport), int(dport)
            
            timestamp = time.time()
            log_packet(timestamp, src_ip, dst_ip, sport, dport, raw_packet)
            
            # Construct standard Scapy packet to save to PCAP
            if SCAPY_AVAILABLE:
                # Build mock Ethernet/IP/UDP frame
                scapy_pkt = (
                    Ether(src="00:11:22:33:44:55", dst="55:44:33:22:11:00") / 
                    IP(src=src_ip, dst=dst_ip) / 
                    UDP(sport=sport, dport=dport) / 
                    Raw(load=raw_packet)
                )
                packet_buffer.append(scapy_pkt)
                
                # Flush to PCAP regularly
                if len(packet_buffer) >= 5:
                    flush_pcap()
                    
        except Exception as e:
            print(f"[Sniffer] Tap Sniffer Error: {e}")
            time.sleep(1)

def run_live_sniffer():
    """Attempt live interface capture using Scapy."""
    print("[Sniffer] Attempting live network capture using Scapy...")
    # Scapy filter for our custom ports
    # Port 5001 (PPM -> Bridge) and 5002 (Bridge -> CMS)
    from scapy.all import sniff
    sniff(filter="udp port 5001 or udp port 5002", prn=process_live_packet, store=False)

if __name__ == "__main__":
    print("Starting YK-8000C Packet Sniffer & Protocol Analyzer (Press Ctrl+C to stop)...")
    if not SCAPY_AVAILABLE:
        print("[Sniffer] Scapy library is not installed. PCAP writing is disabled.")
    
    # Check if we can run live sniffing (Windows requires Npcap)
    try:
        # We try to import and call a quick test to see if sniffing works
        if SCAPY_AVAILABLE:
            from scapy.all import conf
            # A dummy call that will raise if WinPcap/Npcap is missing
            test_sock = conf.L2socket()
            # If we get here, live sniffing should work
            run_live_sniffer()
        else:
            run_tap_sniffer()
    except Exception as e:
        print(f"[Sniffer] Live capture interface failed to initialize: {e}")
        print("[Sniffer] Switching to Tap Mode (Pi Bridge will mirror network packets)...")
        try:
            run_tap_sniffer()
        except KeyboardInterrupt:
            flush_pcap()
            print("\nSniffer stopped.")
    except KeyboardInterrupt:
        flush_pcap()
        print("\nSniffer stopped.")
