import socket
import time
from protocol_decoder import unpack_packet

# Network Configuration
BRIDGE_IP = "127.0.0.1"
BRIDGE_PORT = 5001
CMS_ADDR = ("127.0.0.1", 5002)
SNIFFER_TAP_ADDR = ("127.0.0.1", 5003)
INGESTION_TAP_ADDR = ("127.0.0.1", 5004)
API_TAP_ADDR = ("127.0.0.1", 5005)

# Virtual IP/Port mappings for simulated network logging
VIRTUAL_NETWORK = {
    "YK-8000C-001": {"ip": "192.168.1.101", "port": 5011},
    "YK-8000C-002": {"ip": "192.168.1.102", "port": 5012}
}
CMS_VIRTUAL_IP = "192.168.1.50"

def run_bridge():
    # Setup UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((BRIDGE_IP, BRIDGE_PORT))
    print(f"Raspberry Pi Bridge Simulator running on {BRIDGE_IP}:{BRIDGE_PORT}...")
    print(f"Relaying packets to Central Monitor at {CMS_ADDR[0]}:{CMS_ADDR[1]}")
    
    # Store device_id -> source_address mapping for routing ACKs back
    routing_table = {}
    
    # Log packet counters
    stats = {
        "ppm_packets": 0,
        "cms_acks": 0,
        "bytes_relayed": 0
    }
    
    last_stats_print = time.time()
    
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            stats["bytes_relayed"] += len(data)
            
            # Check source address to determine direction
            if addr == CMS_ADDR:
                # 1. Packet from Central Monitor (CMS) -> ACK to PPM
                stats["cms_acks"] += 1
                try:
                    # Unpack header to read device_id
                    parsed = unpack_packet(data)
                    device_id = parsed["device_id"]
                    
                    if device_id in routing_table:
                        dest_addr = routing_table[device_id]
                        sock.sendto(data, dest_addr)
                        
                        # Mirror to Packet Sniffer, Ingestion, and API Tap
                        net_info = VIRTUAL_NETWORK.get(device_id, {"ip": "192.168.1.100", "port": dest_addr[1]})
                        metadata = f"{CMS_VIRTUAL_IP},{net_info['ip']},{CMS_ADDR[1]},{net_info['port']}\n".encode('utf-8')
                        sock.sendto(metadata + data, SNIFFER_TAP_ADDR)
                        sock.sendto(metadata + data, INGESTION_TAP_ADDR)
                        sock.sendto(metadata + data, API_TAP_ADDR)
                    else:
                        print(f"[Pi Bridge] Warning: No routing entry for Device {device_id}")
                except Exception as e:
                    print(f"[Pi Bridge] Error routing ACK from CMS: {e}")
            else:
                # 2. Packet from Patient Monitor (PPM) -> Vitals or Heartbeat to CMS
                stats["ppm_packets"] += 1
                try:
                    # Parse package to extract device_id and record it
                    parsed = unpack_packet(data)
                    device_id = parsed["device_id"]
                    
                    # Update routing table
                    if device_id not in routing_table or routing_table[device_id] != addr:
                        routing_table[device_id] = addr
                        print(f"[Pi Bridge] Routing Registered: Device {device_id} is at {addr}")
                        
                    # Forward to Central Monitor
                    sock.sendto(data, CMS_ADDR)
                    
                    # Mirror to Packet Sniffer, Ingestion, and API Tap
                    net_info = VIRTUAL_NETWORK.get(device_id, {"ip": "192.168.1.100", "port": addr[1]})
                    metadata = f"{net_info['ip']},{CMS_VIRTUAL_IP},{net_info['port']},{BRIDGE_PORT}\n".encode('utf-8')
                    sock.sendto(metadata + data, SNIFFER_TAP_ADDR)
                    sock.sendto(metadata + data, INGESTION_TAP_ADDR)
                    sock.sendto(metadata + data, API_TAP_ADDR)
                    
                except Exception as e:
                    print(f"[Pi Bridge] Error routing packet from PPM: {e}")
                    
            # Print stats summary every 10 seconds
            now = time.time()
            if now - last_stats_print >= 10.0:
                print(f"[Pi Bridge Stats] Relayed from PPM: {stats['ppm_packets']} | ACKs from CMS: {stats['cms_acks']} | Total Traffic: {stats['bytes_relayed']} bytes")
                last_stats_print = now
                
        except Exception as e:
            print(f"[Pi Bridge] Socket error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    try:
        run_bridge()
    except KeyboardInterrupt:
        print("\nPi Bridge Simulator stopped.")
