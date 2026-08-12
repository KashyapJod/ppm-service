import socket
import time
from protocol_decoder import unpack_packet, build_packet, MSG_HANDSHAKE, MSG_DATA, MSG_HEARTBEAT, MSG_ACK

# Network Configuration
CMS_IP = "127.0.0.1"
CMS_PORT = 5002

def run_cms():
    # Setup UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((CMS_IP, CMS_PORT))
    print(f"Vendor CMS Simulator listening on {CMS_IP}:{CMS_PORT}...")

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            # print(f"Received {len(data)} bytes from {addr}")
            
            try:
                # Unpack and validate the binary packet
                parsed = unpack_packet(data)
                msg_type = parsed["msg_type"]
                device_id = parsed["device_id"]
                patient_id = parsed["patient_id"]
                
                # Determine log display based on type
                if msg_type == MSG_HANDSHAKE:
                    print(f"[CMS] New Connection Handshake: Device={device_id}, Patient={patient_id}")
                elif msg_type == MSG_HEARTBEAT:
                    print(f"[CMS] Keepalive Heartbeat: Device={device_id}")
                elif msg_type == MSG_DATA:
                    vitals = parsed["vitals"]
                    print(f"[CMS] Vitals Data: Device={device_id}, HR={vitals['hr']} BPM, SpO2={vitals['spo2']:.1f}%, Temp={vitals['temp']:.1f}C, NIBP={vitals['nibp_sys']}/{vitals['nibp_dia']}, ECG Samples={len(vitals['ecg_points'])}")
                else:
                    print(f"[CMS] Unknown message type {msg_type} from Device={device_id}")
                
                # Send Heartbeat ACK back to the sender (Pi Bridge)
                ack = build_packet(MSG_ACK, device_id, patient_id)
                sock.sendto(ack, addr)
                # print(f"Sent ACK to {addr}")
                
            except ValueError as val_err:
                print(f"[CMS] Invalid packet received from {addr}: {val_err}")
                
        except Exception as e:
            print(f"[CMS] Socket error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    try:
        run_cms()
    except KeyboardInterrupt:
        print("\nCMS Simulator stopped.")
