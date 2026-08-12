import socket
import time
import json
import mysql.connector
from mysql.connector import Error
from datetime import datetime
from protocol_decoder import (
    unpack_packet, MSG_HANDSHAKE, MSG_DATA, MSG_HEARTBEAT, MSG_ACK
)

# Network configuration
INGESTION_IP = "127.0.0.1"
INGESTION_PORT = 5004

class IngestionService:
    def __init__(self):
        import os
        self.db_config = {
            "host": os.getenv("DB_HOST", "127.0.0.1"),
            "user": os.getenv("DB_USER", "root"),
            "password": os.getenv("DB_PASSWORD", "1234"),
            "database": os.getenv("DB_NAME", "ppm_monitoring"),
            "port": int(os.getenv("DB_PORT", "3306"))
        }
        self.conn = None
        self.cursor = None
        self.patient_thresholds = {}
        self.last_thresholds_refresh = 0
        self.connect_db()

    def connect_db(self):
        """Establish connection to MySQL database, retrying on failure."""
        while True:
            try:
                if self.conn and self.conn.is_connected():
                    return
                print("[Ingestion] Connecting to MySQL database...")
                self.conn = mysql.connector.connect(**self.db_config)
                self.cursor = self.conn.cursor()
                print("[Ingestion] MySQL connection established successfully.")
                return
            except Error as e:
                print(f"[Ingestion] Database connection failed: {e}. Retrying in 3 seconds...")
                time.sleep(3)

    def execute_query(self, query, params=()):
        """Execute a query, handle reconnection if connection fails."""
        try:
            self.connect_db()
            self.cursor.execute(query, params)
            self.conn.commit()
            return True
        except Error as e:
            print(f"[Ingestion] Query execution error: {e}. Reconnecting...")
            try:
                if self.conn:
                    self.conn.close()
            except:
                pass
            self.conn = None
            # Retry once after reconnecting
            try:
                self.connect_db()
                self.cursor.execute(query, params)
                self.conn.commit()
                return True
            except Error as retry_err:
                print(f"[Ingestion] Query retry failed: {retry_err}")
                return False

    def log_comm_packet(self, timestamp, src_ip, dst_ip, size, log_type_name, hex_data):
        """Insert communication packet metadata into comm_logs."""
        query = """
        INSERT INTO comm_logs (timestamp, source_ip, dest_ip, protocol, packet_size, log_type, raw_hex)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        # Map protocol_decoder log type to database enum
        db_type = 'ERROR'
        if log_type_name == 'HANDSHAKE':
            db_type = 'HANDSHAKE'
        elif log_type_name == 'DATA':
            db_type = 'DATA'
        elif log_type_name in ('HEARTBEAT', 'ACK'):
            db_type = 'HEARTBEAT'
            
        dt = datetime.fromtimestamp(timestamp)
        self.execute_query(query, (dt, src_ip, dst_ip, "UDP", size, db_type, hex_data))

    def update_device_heartbeat(self, device_id, ip_address):
        """Update device connection status and heartbeat time."""
        query = """
        UPDATE device_info 
        SET status = 'ONLINE', last_heartbeat = NOW(), ip_address = %s
        WHERE device_id = %s
        """
        self.execute_query(query, (ip_address, device_id))

    def flag_alarm(self, patient_id, device_id, timestamp, alarm_type, severity, message):
        """Log alarm events, preventing duplicates within a 10 second window."""
        dt = datetime.fromtimestamp(timestamp)
        
        # Check for recent unacknowledged alarms of same type for patient
        check_query = """
        SELECT COUNT(*) FROM alarm_events
        WHERE patient_id = %s AND alarm_type = %s AND is_acknowledged = FALSE 
        AND timestamp > %s - INTERVAL 10 SECOND
        """
        self.connect_db()
        self.cursor.execute(check_query, (patient_id, alarm_type, dt))
        exists = self.cursor.fetchone()[0] > 0
        
        if not exists:
            insert_query = """
            INSERT INTO alarm_events (patient_id, device_id, timestamp, alarm_type, severity, message, is_acknowledged)
            VALUES (%s, %s, %s, %s, %s, %s, FALSE)
            """
            self.execute_query(insert_query, (patient_id, device_id, dt, alarm_type, severity, message))
            print(f"[Ingestion ALERT] Created alarm: {alarm_type} ({severity}) - {message} for Patient {patient_id}")

    def refresh_patient_thresholds(self):
        """Fetch patient specific alarm thresholds from database."""
        now = time.time()
        # Refresh every 5 seconds
        if now - self.last_thresholds_refresh < 5:
            return
            
        try:
            self.connect_db()
            # Disable dictionary cursor mapping if it was set globally, or just fetch as indices
            self.cursor.execute("SELECT patient_id, hr_low, hr_high, spo2_low, temp_high FROM patient_info")
            rows = self.cursor.fetchall()
            for r in rows:
                self.patient_thresholds[r[0]] = {
                    "hr_low": r[1] if r[1] is not None else 60,
                    "hr_high": r[2] if r[2] is not None else 100,
                    "spo2_low": float(r[3]) if r[3] is not None else 90.0,
                    "temp_high": float(r[4]) if r[4] is not None else 38.0
                }
            self.last_thresholds_refresh = now
        except Exception as e:
            print(f"[Ingestion] Failed to refresh patient thresholds: {e}")

    def process_data_packet(self, device_id, patient_id, timestamp, vitals):
        """Parse vital signs and store them, flagging alarms as needed."""
        self.refresh_patient_thresholds()
        
        dt = datetime.fromtimestamp(timestamp)
        ecg_json = json.dumps(vitals["ecg_points"])
        
        # 1. Insert into vital_params
        query = """
        INSERT INTO vital_params (patient_id, device_id, timestamp, heart_rate, spo2, nibp_sys, nibp_dia, temperature, respiration_rate, ecg_payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        self.execute_query(query, (
            patient_id, device_id, dt, vitals["hr"], vitals["spo2"],
            vitals["nibp_sys"], vitals["nibp_dia"], vitals["temp"],
            vitals["resp"], ecg_json
        ))
        
        # 2. Get patient thresholds (default fallbacks if not cached)
        thresh = self.patient_thresholds.get(patient_id, {
            "hr_low": 60,
            "hr_high": 100,
            "spo2_low": 90.0,
            "temp_high": 38.0
        })
        
        # 3. Evaluate custom Alarm thresholds
        # SpO2 low alarm
        spo2 = vitals["spo2"]
        if spo2 < thresh["spo2_low"]:
            self.flag_alarm(patient_id, device_id, timestamp, "SPO2_LOW", "CRITICAL", f"Critical Low SpO2: {spo2:.1f}% (Limit: <{thresh['spo2_low']:.1f}%)")
        elif spo2 < (thresh["spo2_low"] + 3.0):
            self.flag_alarm(patient_id, device_id, timestamp, "SPO2_LOW", "MEDIUM", f"Low SpO2: {spo2:.1f}% (Limit: <{thresh['spo2_low'] + 3.0:.1f}%)")
            
        # Heart Rate low/high alarms
        hr = vitals["hr"]
        if hr > (thresh["hr_high"] + 5):
            self.flag_alarm(patient_id, device_id, timestamp, "HR_HIGH", "CRITICAL", f"Critical High Heart Rate: {hr} BPM (Limit: >{thresh['hr_high'] + 5} BPM)")
        elif hr > thresh["hr_high"]:
            self.flag_alarm(patient_id, device_id, timestamp, "HR_HIGH", "MEDIUM", f"High Heart Rate (Tachycardia): {hr} BPM (Limit: >{thresh['hr_high']} BPM)")
        elif hr < (thresh["hr_low"] - 5):
            self.flag_alarm(patient_id, device_id, timestamp, "HR_LOW", "CRITICAL", f"Critical Low Heart Rate: {hr} BPM (Limit: <{thresh['hr_low'] - 5} BPM)")
        elif hr < thresh["hr_low"]:
            self.flag_alarm(patient_id, device_id, timestamp, "HR_LOW", "MEDIUM", f"Low Heart Rate (Bradycardia): {hr} BPM (Limit: <{thresh['hr_low']} BPM)")
            
        # Temperature high/low alarms
        temp = vitals["temp"]
        if temp > (thresh["temp_high"] + 0.5):
            self.flag_alarm(patient_id, device_id, timestamp, "TEMP_HIGH", "MEDIUM", f"High Fever Temperature: {temp:.1f}C (Limit: >{thresh['temp_high'] + 0.5:.1f}C)")
        elif temp > thresh["temp_high"]:
            self.flag_alarm(patient_id, device_id, timestamp, "TEMP_HIGH", "LOW", f"Elevated Temperature: {temp:.1f}C (Limit: >{thresh['temp_high']:.1f}C)")

    def run(self):
        # Setup UDP Receiver Socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((INGESTION_IP, INGESTION_PORT))
        print(f"Database Ingestion Service running on {INGESTION_IP}:{INGESTION_PORT}...")
        
        while True:
            try:
                data, addr = sock.recvfrom(65535)
                # Split metadata header
                metadata_header, raw_packet = data.split(b"\n", 1)
                src_ip, dst_ip, sport, dport = metadata_header.decode('utf-8').split(",")
                sport, dport = int(sport), int(dport)
                
                timestamp = time.time()
                
                try:
                    # Decode custom binary protocol
                    parsed = unpack_packet(raw_packet)
                    msg_type = parsed["msg_type"]
                    device_id = parsed["device_id"]
                    patient_id = parsed["patient_id"]
                    
                    # 1. Update device status
                    self.update_device_heartbeat(device_id, src_ip)
                    
                    # 2. Process based on packet type
                    log_type_name = "ERROR"
                    if msg_type == MSG_HANDSHAKE:
                        log_type_name = "HANDSHAKE"
                        print(f"[Ingestion] Session Handshake registered for {device_id}")
                    elif msg_type == MSG_HEARTBEAT:
                        log_type_name = "HEARTBEAT"
                    elif msg_type == MSG_DATA:
                        log_type_name = "DATA"
                        self.process_data_packet(device_id, patient_id, timestamp, parsed["vitals"])
                    elif msg_type == MSG_ACK:
                        log_type_name = "ACK"
                        
                    # 3. Log packet communication
                    self.log_comm_packet(timestamp, src_ip, dst_ip, len(raw_packet), log_type_name, raw_packet.hex().upper())
                    
                except ValueError as parse_err:
                    print(f"[Ingestion] Error parsing binary packet: {parse_err}")
                    # Log decoding error packet
                    self.log_comm_packet(timestamp, src_ip, dst_ip, len(raw_packet), "ERROR", raw_packet.hex().upper())
                    
            except Exception as e:
                print(f"[Ingestion] Service loop error: {e}")
                time.sleep(1)

if __name__ == "__main__":
    service = IngestionService()
    try:
        service.run()
    except KeyboardInterrupt:
        print("\nIngestion Service stopped.")
