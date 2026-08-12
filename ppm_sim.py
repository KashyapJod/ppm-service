import socket
import time
import random
import threading
import math
import struct
from protocol_decoder import (
    build_packet, pack_vitals_payload, MSG_HANDSHAKE, MSG_DATA, MSG_HEARTBEAT, MSG_ACK, MSG_SIM_CONTROL, unpack_packet
)

# Network Configuration
BRIDGE_IP = "127.0.0.1"
BRIDGE_PORT = 5001

class PatientMonitorSimulator:
    def __init__(self, device_id: str, patient_id: str, local_port: int, normal_vitals: bool = True):
        self.device_id = device_id
        self.patient_id = patient_id
        self.local_port = local_port
        self.normal_vitals = normal_vitals
        self.running = False
        
        # Socket setup
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", self.local_port))
        self.sock.settimeout(0.5)
        
        # Initialize default vital signs
        self.set_sim_condition("NORMAL" if self.normal_vitals else "HYPOXIA")
        
        # ECG parameters
        self.ecg_points_to_send = []
        self.step = 0
        self.current_lead = 'II'
        
    def set_sim_condition(self, condition: str):
        """Set patient vitals baseline based on simulated condition command."""
        cond = condition.upper().strip()
        if cond == "NORMAL":
            self.hr = 72
            self.spo2 = 98.5
            self.sys = 118
            self.dia = 76
            self.temp = 36.9
            self.resp = 14
            print(f"[{self.device_id}] Condition state -> NORMAL")
        elif cond == "TACHYCARDIA":
            self.hr = 114
            self.spo2 = 96.2
            self.sys = 135
            self.dia = 85
            self.temp = 37.2
            self.resp = 19
            print(f"[{self.device_id}] Condition state -> TACHYCARDIA (High HR)")
        elif cond == "BRADYCARDIA":
            self.hr = 47
            self.spo2 = 95.8
            self.sys = 102
            self.dia = 62
            self.temp = 36.5
            self.resp = 10
            print(f"[{self.device_id}] Condition state -> BRADYCARDIA (Low HR)")
        elif cond == "HYPOXIA":
            self.hr = 88
            self.spo2 = 89.2
            self.sys = 122
            self.dia = 78
            self.temp = 37.0
            self.resp = 24
            print(f"[{self.device_id}] Condition state -> HYPOXIA (Low SpO2)")
        elif cond == "FEVER":
            self.hr = 96
            self.spo2 = 95.5
            self.sys = 125
            self.dia = 80
            self.temp = 39.2
            self.resp = 18
            print(f"[{self.device_id}] Condition state -> FEVER (High Temp)")
        else:
            print(f"[{self.device_id}] Warning: Unknown simulation condition received: {cond}")

    def generate_ecg_point(self, hr: int, step: int) -> int:
        """
        Generate a single ECG point at 100 Hz matching the cardiac cycle.
        Returns scaled millivolts as integer (range roughly -50 to 300) depending on active lead.
        """
        samples_per_beat = int((60.0 / hr) * 100)
        phase = step % samples_per_beat
        pct = phase / samples_per_beat
        
        # Lead II Generator (Normal ECG profile)
        val_ii = 0.0
        val_ii += random.uniform(-1.0, 1.0)
        
        # P wave (sine-like, 0% to 12%)
        if 0.0 <= pct < 0.12:
            p_pct = pct / 0.12
            val_ii += 15.0 * math.sin(p_pct * math.pi)
        # QRS complex (16% to 24%)
        elif 0.16 <= pct < 0.24:
            qrs_pct = (pct - 0.16) / 0.08
            if qrs_pct < 0.25: # Q wave
                val_ii -= 15.0 * (qrs_pct / 0.25)
            elif qrs_pct < 0.75: # R wave
                r_pct = (qrs_pct - 0.25) / 0.50
                val_ii += 220.0 * (1.0 - abs(2.0 * r_pct - 1.0))
            else: # S wave
                s_pct = (qrs_pct - 0.75) / 0.25
                val_ii -= 45.0 * (1.0 - s_pct)
        # T wave (broader, 32% to 55%)
        elif 0.32 <= pct < 0.55:
            t_pct = (pct - 0.32) / 0.23
            val_ii += 35.0 * math.sin(t_pct * math.pi)
            
        if self.current_lead == 'II':
            return int(round(val_ii))
            
        # Lead I: smaller amplitude (0.6x) and slight phase shift (advance cycle by 5%)
        phase_i = (step + int(samples_per_beat * 0.05)) % samples_per_beat
        pct_i = phase_i / samples_per_beat
        
        val_i = 0.0
        val_i += random.uniform(-1.0, 1.0)
        if 0.0 <= pct_i < 0.12:
            p_pct = pct_i / 0.12
            val_i += 10.0 * math.sin(p_pct * math.pi)
        elif 0.16 <= pct_i < 0.24:
            qrs_pct = (pct_i - 0.16) / 0.08
            if qrs_pct < 0.25: # Q wave
                val_i -= 10.0 * (qrs_pct / 0.25)
            elif qrs_pct < 0.75: # R wave
                r_pct = (qrs_pct - 0.25) / 0.50
                val_i += 135.0 * (1.0 - abs(2.0 * r_pct - 1.0))
            else: # S wave
                s_pct = (qrs_pct - 0.75) / 0.25
                val_i -= 30.0 * (1.0 - s_pct)
        elif 0.32 <= pct_i < 0.55:
            t_pct = (pct_i - 0.32) / 0.23
            val_i += 22.0 * math.sin(t_pct * math.pi)
            
        if self.current_lead == 'I':
            return int(round(val_i))
            
        # Lead III: Lead II - Lead I
        val_iii = val_ii - val_i
        if self.current_lead == 'III':
            return int(round(val_iii))
            
        return int(round(val_ii))

    def update_vitals(self):
        """Random walk vitals centered around current baselines."""
        self.hr = max(40, min(180, self.hr + random.choice([-1, 0, 1])))
        self.spo2 = max(75.0, min(100.0, self.spo2 + random.choice([-0.1, 0.0, 0.1])))
        self.sys = max(80, min(200, self.sys + random.choice([-1, 0, 1])))
        self.dia = max(50, min(120, self.dia + random.choice([-1, 0, 1])))
        self.temp = max(35.0, min(42.0, self.temp + random.choice([-0.05, 0.0, 0.05])))
        self.resp = max(8, min(40, self.resp + random.choice([-1, 0, 1])))

    def start(self):
        self.running = True
        self.listener_thread = threading.Thread(target=self.receive_loop, daemon=True)
        self.sender_thread = threading.Thread(target=self.send_loop, daemon=True)
        
        self.listener_thread.start()
        self.sender_thread.start()
        print(f"Device {self.device_id} started (sending from port {self.local_port})")

    def stop(self):
        self.running = False
        self.sock.close()
        print(f"Device {self.device_id} stopped.")

    def receive_loop(self):
        """Listen for Heartbeat ACKs from CMS & Simulation commands from API Backend."""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                try:
                    parsed = unpack_packet(data)
                    msg_type = parsed["msg_type"]
                    
                    if msg_type == MSG_ACK:
                        pass # Silently receive ACK keepalives
                    elif msg_type == MSG_SIM_CONTROL:
                        condition = parsed["payload_bytes"].decode('utf-8').strip().upper()
                        if condition.startswith("LEAD_"):
                            lead_name = condition.split("_")[1]
                            self.current_lead = lead_name
                            print(f"[{self.device_id}] Switched active ECG lead to: {lead_name}")
                        else:
                            print(f"[{self.device_id}] Ingesting simulation condition command: '{condition}'")
                            self.set_sim_condition(condition)
                except ValueError as e:
                    print(f"[{self.device_id}] Error decoding received command: {e}")
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[{self.device_id}] Command listener error: {e}")
                break

    def send_loop(self):
        """Regularly send Handshake, Vitals, and Heartbeat data."""
        # 1. Send initial handshake packet
        try:
            handshake = build_packet(MSG_HANDSHAKE, self.device_id, self.patient_id)
            self.sock.sendto(handshake, (BRIDGE_IP, BRIDGE_PORT))
        except Exception as e:
            print(f"[{self.device_id}] Handshake failed: {e}")

        last_heartbeat = time.time()
        last_data_send = time.time()
        
        while self.running:
            now = time.time()
            
            # Generate ECG points continuously at 100 Hz
            pt = self.generate_ecg_point(self.hr, self.step)
            self.ecg_points_to_send.append(pt)
            self.step += 1
            
            # Every 1 second: send vitals + ECG block
            if now - last_data_send >= 1.0:
                self.update_vitals()
                
                # Take last 100 ECG points
                ecg_block = self.ecg_points_to_send[-100:]
                self.ecg_points_to_send = ecg_block.copy()
                
                payload = pack_vitals_payload(
                    self.hr, self.spo2, self.sys, self.dia, self.temp, self.resp, ecg_block
                )
                packet = build_packet(MSG_DATA, self.device_id, self.patient_id, payload)
                
                try:
                    self.sock.sendto(packet, (BRIDGE_IP, BRIDGE_PORT))
                except Exception as e:
                    print(f"[{self.device_id}] Data send failed: {e}")
                
                last_data_send = now

            # Every 5 seconds: send heartbeat keepalive
            if now - last_heartbeat >= 5.0:
                hb = build_packet(MSG_HEARTBEAT, self.device_id, self.patient_id)
                try:
                    self.sock.sendto(hb, (BRIDGE_IP, BRIDGE_PORT))
                except Exception as e:
                    print(f"[{self.device_id}] Heartbeat send failed: {e}")
                last_heartbeat = now
                
            time.sleep(0.01) # 100 Hz sampling interval

if __name__ == "__main__":
    print("Starting YK-8000C PPM Hardware Simulator (Press Ctrl+C to stop)...")
    
    sim1 = PatientMonitorSimulator(
        device_id="YK-8000C-001", 
        patient_id="PT-1001", 
        local_port=5011, 
        normal_vitals=True
    )
    
    sim2 = PatientMonitorSimulator(
        device_id="YK-8000C-002", 
        patient_id="PT-1002", 
        local_port=5012, 
        normal_vitals=False
    )
    
    try:
        sim1.start()
        sim2.start()
        
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping simulators...")
        sim1.stop()
        sim2.stop()
        print("Simulators stopped.")