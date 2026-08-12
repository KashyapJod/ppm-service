import socket
import threading
import json
import asyncio
import time
import mysql.connector
from mysql.connector import Error, pooling
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from datetime import datetime
from protocol_decoder import unpack_packet, MSG_DATA, MSG_SIM_CONTROL, build_packet

app = FastAPI(title="YK-8000C Central Monitoring Dashboard API")

# Allow CORS for development ease
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import os
# MySQL Connection Pool
db_config = {
    "pool_name": "ppm_pool",
    "pool_size": 5,
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "1234"),
    "database": os.getenv("DB_NAME", "ppm_monitoring"),
    "port": int(os.getenv("DB_PORT", "3306"))
}

try:
    connection_pool = pooling.MySQLConnectionPool(**db_config)
    print("[API Backend] MySQL connection pool initialized successfully.")
except Error as err:
    print(f"[API Backend] Failed to initialize connection pool: {err}")
    connection_pool = None

def get_db_connection():
    if connection_pool:
        return connection_pool.get_connection()
    # Fallback to direct connection if pool fails
    return mysql.connector.connect(
        host="127.0.0.1",
        user="root",
        password="1234",
        database="ppm_monitoring"
    )

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"[WebSocket] Client connected. Total active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            print(f"[WebSocket] Client disconnected. Total active: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        # Gather all broadcast coroutines
        if not self.active_connections:
            return
        
        # Serialize once
        message_str = json.dumps(message)
        
        # We broadcast concurrently to all open sockets
        disconnected_sockets = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message_str)
            except Exception:
                disconnected_sockets.append(connection)
                
        # Clean up failed connections
        for conn in disconnected_sockets:
            self.disconnect(conn)

manager = ConnectionManager()

# Global Event Loop reference for the background listener thread to schedule broadcasts
main_loop = None

# UDP Live Data Tap Receiver (port 5005)
API_TAP_IP = "127.0.0.1"
API_TAP_PORT = 5005

def run_udp_tap_listener():
    """Background thread listening for telemetry tap and broadcasting it to web clients."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((API_TAP_IP, API_TAP_PORT))
    print(f"[API Backend] UDP telemetry tap listener running on {API_TAP_IP}:{API_TAP_PORT}...")
    
    while True:
        try:
            data, addr = sock.recvfrom(65535)
            # Parse metadata header
            metadata_header, raw_packet = data.split(b"\n", 1)
            src_ip, dst_ip, sport, dport = metadata_header.decode('utf-8').split(",")
            
            try:
                parsed = unpack_packet(raw_packet)
                if parsed["msg_type"] == MSG_DATA:
                    vitals = parsed["vitals"]
                    
                    # Construct message to broadcast
                    broadcast_msg = {
                        "type": "telemetry",
                        "device_id": parsed["device_id"],
                        "patient_id": parsed["patient_id"],
                        "timestamp": parsed["timestamp"],
                        "vitals": {
                            "hr": vitals["hr"],
                            "spo2": vitals["spo2"],
                            "nibp_sys": vitals["nibp_sys"],
                            "nibp_dia": vitals["nibp_dia"],
                            "temp": vitals["temp"],
                            "resp": vitals["resp"],
                            "ecg_points": vitals["ecg_points"]
                        }
                    }
                    
                    # Schedule broadcast on the main event loop
                    if main_loop:
                        asyncio.run_coroutine_threadsafe(manager.broadcast(broadcast_msg), main_loop)
                        
            except Exception as e:
                # Silently ignore parsing errors in live stream to keep performance high
                pass
        except Exception as e:
            print(f"[API Backend] Live listener thread error: {e}")
            time.sleep(1)

# API Endpoints
class AcknowledgeRequest(BaseModel):
    acknowledged_by: str = "Admin"

@app.get("/api/patients")
def get_patients():
    """Retrieve all patients and their latest vitals."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        # Fetch patients
        cursor.execute("SELECT * FROM patient_info")
        patients = cursor.fetchall()
        
        # Combine with latest vital parameters and online status
        for p in patients:
            pid = p["patient_id"]
            
            # Get latest vitals
            cursor.execute("""
                SELECT heart_rate, spo2, nibp_sys, nibp_dia, temperature, respiration_rate, timestamp
                FROM vital_params 
                WHERE patient_id = %s 
                ORDER BY record_id DESC LIMIT 1
            """, (pid,))
            vitals = cursor.fetchone()
            p["vitals"] = vitals
            
            # Get device mapping
            cursor.execute("""
                SELECT d.device_id, d.status, d.last_heartbeat 
                FROM device_info d
                JOIN vital_params v ON v.device_id = d.device_id
                WHERE v.patient_id = %s
                ORDER BY v.record_id DESC LIMIT 1
            """, (pid,))
            device = cursor.fetchone()
            if device:
                # Check if device timed out (> 8 seconds since heartbeat)
                last_hb = device["last_heartbeat"]
                time_diff = (datetime.now() - last_hb).total_seconds() if last_hb else 999
                p["device"] = {
                    "device_id": device["device_id"],
                    "status": "ONLINE" if time_diff <= 8.0 else "OFFLINE",
                    "last_heartbeat": last_hb.isoformat() if last_hb else None
                }
            else:
                p["device"] = {"status": "OFFLINE", "device_id": "None", "last_heartbeat": None}
                
            # Count active alerts
            cursor.execute("""
                SELECT COUNT(*) as active_count 
                FROM alarm_events 
                WHERE patient_id = %s AND is_acknowledged = FALSE
            """, (pid,))
            p["active_alarms_count"] = cursor.fetchone()["active_count"]
            
        return patients
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/history/{patient_id}")
def get_patient_history(patient_id: str):
    """Fetch recent vital trends for a specific patient."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT timestamp, heart_rate, spo2, nibp_sys, nibp_dia, temperature, respiration_rate
            FROM vital_params
            WHERE patient_id = %s
            ORDER BY record_id DESC LIMIT 50
        """, (patient_id,))
        rows = cursor.fetchall()
        # Return in ascending order for trends
        return list(reversed(rows))
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/alarms")
def get_alarms():
    """Fetch unacknowledged alarms."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT a.*, p.name as patient_name, p.bed_number 
            FROM alarm_events a
            JOIN patient_info p ON p.patient_id = a.patient_id
            WHERE a.is_acknowledged = FALSE
            ORDER BY a.alarm_id DESC
        """)
        return cursor.fetchall()
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/alarms/{alarm_id}/acknowledge")
def acknowledge_alarm(alarm_id: int):
    """Acknowledge an active alarm."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE alarm_events SET is_acknowledged = TRUE WHERE alarm_id = %s", (alarm_id,))
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Alarm not found or already acknowledged.")
        
        # Broadcast alarm update
        asyncio.run_coroutine_threadsafe(
            manager.broadcast({"type": "alarm_acknowledged", "alarm_id": alarm_id}),
            main_loop
        )
        return {"status": "success", "message": f"Alarm {alarm_id} acknowledged."}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

class SimulateConditionRequest(BaseModel):
    device_id: str
    condition: str

DEVICE_PORT_MAP = {
    "YK-8000C-001": 5011,
    "YK-8000C-002": 5012
}

@app.post("/api/simulate_condition")
def simulate_condition(req: SimulateConditionRequest):
    """Forward simulation command to the active PPM simulator UDP socket."""
    port = DEVICE_PORT_MAP.get(req.device_id)
    if not port:
        raise HTTPException(status_code=400, detail=f"No local port configured for device {req.device_id}")
        
    try:
        # Build the command packet (use "SYSTEM" as sender patient_id)
        packet = build_packet(MSG_SIM_CONTROL, req.device_id, "SYSTEM", req.condition.encode('utf-8'))
        
        # Send to the PPM simulator
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(packet, ("127.0.0.1", port))
        sock.close()
        
        print(f"[API Backend] Relayed simulation command '{req.condition}' to device {req.device_id} on port {port}")
        return {"status": "success", "message": f"Condition command '{req.condition}' forwarded to device {req.device_id}."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send simulator command: {e}")

# Models for custom thresholds and notes
class ThresholdsRequest(BaseModel):
    hr_low: int
    hr_high: int
    spo2_low: float
    temp_high: float

class BedsideNoteRequest(BaseModel):
    author: str
    note: str

@app.get("/api/patients/{patient_id}/thresholds")
def get_patient_thresholds(patient_id: str):
    """Retrieve patient specific alarm thresholds."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT hr_low, hr_high, spo2_low, temp_high FROM patient_info WHERE patient_id = %s", (patient_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Patient not found")
        return {
            "hr_low": row["hr_low"] if row["hr_low"] is not None else 60,
            "hr_high": row["hr_high"] if row["hr_high"] is not None else 100,
            "spo2_low": float(row["spo2_low"]) if row["spo2_low"] is not None else 90.0,
            "temp_high": float(row["temp_high"]) if row["temp_high"] is not None else 38.0
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/patients/{patient_id}/thresholds")
def update_patient_thresholds(patient_id: str, req: ThresholdsRequest):
    """Update patient specific alarm thresholds."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE patient_info 
            SET hr_low = %s, hr_high = %s, spo2_low = %s, temp_high = %s
            WHERE patient_id = %s
        """, (req.hr_low, req.hr_high, req.spo2_low, req.temp_high, patient_id))
        conn.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Patient not found or no changes made")
        return {"status": "success", "message": f"Alarm thresholds updated for patient {patient_id}."}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/patients/{patient_id}/alarms/history")
def get_patient_alarms_history(patient_id: str):
    """Retrieve historical alarm events for a patient."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT alarm_id, timestamp, alarm_type, severity, message, is_acknowledged 
            FROM alarm_events 
            WHERE patient_id = %s 
            ORDER BY timestamp DESC LIMIT 20
        """, (patient_id,))
        alarms = cursor.fetchall()
        for a in alarms:
            if a["timestamp"]:
                a["timestamp"] = a["timestamp"].isoformat()
        return alarms
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/patients/{patient_id}/notes")
def get_patient_notes(patient_id: str):
    """Retrieve bedside care notes for a patient."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT note_id, timestamp, author, note 
            FROM patient_notes 
            WHERE patient_id = %s 
            ORDER BY timestamp DESC
        """, (patient_id,))
        notes = cursor.fetchall()
        for n in notes:
            if n["timestamp"]:
                n["timestamp"] = n["timestamp"].isoformat()
        return notes
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/patients/{patient_id}/notes")
def add_patient_note(patient_id: str, req: BedsideNoteRequest):
    """Add a new bedside note for a patient."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO patient_notes (patient_id, timestamp, author, note)
            VALUES (%s, NOW(3), %s, %s)
        """, (patient_id, req.author, req.note))
        conn.commit()
        return {"status": "success", "message": "Note added successfully."}
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/system_health")
def get_system_health():
    """Retrieve statistics and connectivity status of the system."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT COUNT(*) as count FROM comm_logs")
        total_logs = cursor.fetchone()["count"]
        
        cursor.execute("SELECT COUNT(*) as count FROM vital_params")
        total_vitals = cursor.fetchone()["count"]
        
        cursor.execute("SELECT COUNT(*) as count FROM alarm_events WHERE is_acknowledged = FALSE")
        active_alarms = cursor.fetchone()["count"]
        
        cursor.execute("SELECT MAX(timestamp) as last_time FROM comm_logs")
        last_packet_time = cursor.fetchone()["last_time"]
        
        # Query active devices
        cursor.execute("SELECT device_id, mac_address, ip_address, status, last_heartbeat FROM device_info")
        devices = cursor.fetchall()
        
        for d in devices:
            # Check timeout
            last_hb = d["last_heartbeat"]
            time_diff = (datetime.now() - last_hb).total_seconds() if last_hb else 999
            d["status"] = "ONLINE" if time_diff <= 8.0 else "OFFLINE"
            
        return {
            "total_communication_logs": total_logs,
            "total_vital_records": total_vitals,
            "active_unacknowledged_alarms": active_alarms,
            "last_captured_packet": last_packet_time.isoformat() if last_packet_time else None,
            "devices": devices
        }
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/comm_logs")
def get_comm_logs():
    """Fetch latest communication logs."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT timestamp, source_ip, dest_ip, protocol, packet_size, log_type, raw_hex
            FROM comm_logs
            ORDER BY log_id DESC LIMIT 15
        """)
        logs = cursor.fetchall()
        # Convert timestamp to ISO string format for JSON compatibility
        for l in logs:
            if l["timestamp"]:
                l["timestamp"] = l["timestamp"].isoformat()
        return logs
    except Error as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# WebSocket Route
@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep socket open and receive dummy heartbeats if sent by client
            data = await websocket.receive_text()
            # Send keepalive reply
            await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# Start background listener thread on startup
@app.on_event("startup")
def startup_event():
    global main_loop
    main_loop = asyncio.get_event_loop()
    
    # Run the listener socket in a background daemon thread
    t = threading.Thread(target=run_udp_tap_listener, daemon=True)
    t.start()

# Mount frontend folder
app.mount("/static", StaticFiles(directory="static"), name="static")

# Root redirect to the static frontend so Render can serve the UI from the same app.
@app.get("/")
def read_root():
    return RedirectResponse(url="/static/index.html")
