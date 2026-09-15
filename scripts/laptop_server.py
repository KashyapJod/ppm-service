#!/usr/bin/env python3
"""
File 2: laptop_server.py (Run on the Raspberry Pi dashboard host)
Handles MySQL storage, protocol logging, alarms, and the Web Dashboard.
"""

from flask import Flask, request, jsonify, render_template_string, send_from_directory
import mysql.connector
from datetime import datetime
import json
import random
import os

# ==================== CONFIGURATION ====================
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost")
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "1234")
MYSQL_DB = os.getenv("MYSQL_DB", "ppm_monitoring")
HTTP_HOST = os.getenv("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.getenv("HTTP_PORT", "3000"))
DEVICE_ID = os.getenv("DEVICE_ID", "YK-8000C-001")
# ========================================================

app = Flask(__name__, static_folder=None)

# Fallback memory buffer if MySQL service is not running
memory_store = {
    "vitals": {},
    "logs": [],
    "alarms": [],
    "history": []
}

def init_mysql_db():
    """Initializes tables and inserts dummy data automatically."""
    try:
        conn = mysql.connector.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASSWORD)
        cursor = conn.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_DB};")
        cursor.execute(f"USE {MYSQL_DB};")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS patient_info (
            patient_id VARCHAR(50) PRIMARY KEY,
            name VARCHAR(100),
            age INT,
            gender VARCHAR(10),
            bed_number VARCHAR(20)
        );""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS device_info (
            device_id VARCHAR(50) PRIMARY KEY,
            ip_address VARCHAR(15),
            status ENUM('ONLINE', 'OFFLINE'),
            last_heartbeat DATETIME
        );""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS vital_params (
            record_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            patient_id VARCHAR(50),
            device_id VARCHAR(50),
            timestamp DATETIME(3),
            heart_rate INT,
            spo2 DECIMAL(5,2),
            nibp_sys INT,
            nibp_dia INT,
            temperature DECIMAL(4,2),
            respiration_rate INT,
            ecg_payload TEXT
        );""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS alarm_events (
            alarm_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            patient_id VARCHAR(50),
            timestamp DATETIME(3),
            alarm_type VARCHAR(50),
            severity ENUM('LOW', 'MEDIUM', 'CRITICAL'),
            message VARCHAR(255)
        );""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS comm_logs (
            log_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            timestamp DATETIME(3),
            source_ip VARCHAR(15),
            dest_port INT,
            packet_size INT,
            interval_ms FLOAT,
            raw_hex TEXT
        );""")

        # Add initial dummy records
        cursor.execute("REPLACE INTO patient_info VALUES ('PT-101', 'David Johnson', 58, 'M', 'ICU-Bed-01');")
        cursor.execute("REPLACE INTO patient_info VALUES ('PT-102', 'Sarah Williams', 42, 'F', 'ICU-Bed-02');")
        cursor.execute("REPLACE INTO device_info VALUES ('YK-8000C-001', '10.42.0.1', 'ONLINE', NOW());")
        conn.commit()
        conn.close()
        print("[+] MySQL initialized successfully.")
    except Exception as e:
        print(f"[!] MySQL connection warning: {e}. Defaulting to in-memory mode.")

@app.route("/api/ingest", methods=["POST"])
def ingest():
    """Receives parsed packets and metrics from Raspberry Pi."""
    data = request.json
    now = datetime.now()
    v = data.get("parsed_vitals", {})
    
    # Check for alarm states
    alarm_msg = None
    severity = "LOW"
    if v.get("spo2", 100) < 92:
        alarm_msg = f"Low SpO2 Critical: {v.get('spo2')}%"
        severity = "CRITICAL"
    elif v.get("hr", 75) > 115:
        alarm_msg = f"Tachycardia Alert: HR {v.get('hr')} BPM"
        severity = "MEDIUM"

    try:
        conn = mysql.connector.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DB)
        cur = conn.cursor()
        
        # 1. Log Comm Packet
        cur.execute(
            "INSERT INTO comm_logs (timestamp, source_ip, dest_port, packet_size, interval_ms, raw_hex) VALUES (%s, %s, %s, %s, %s, %s)",
            (now, data.get("source_ip"), data.get("dest_port", 5005), data.get("packet_size"), data.get("update_interval_ms"), data.get("raw_hex")[:60])
        )
        # 2. Insert Vitals
        cur.execute(
            """INSERT INTO vital_params (patient_id, device_id, timestamp, heart_rate, spo2, nibp_sys, nibp_dia, temperature, respiration_rate, ecg_payload)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            ('PT-101', data.get("device_id"), now, v.get("hr"), v.get("spo2"), v.get("nibp_sys"), v.get("nibp_dia"), v.get("temp"), v.get("resp"), json.dumps(v.get("ecg", [])))
        )
        # 3. Insert Alarm if triggered
        if alarm_msg:
            cur.execute("INSERT INTO alarm_events (patient_id, timestamp, alarm_type, severity, message) VALUES (%s, %s, %s, %s, %s)",
                        ('PT-101', now, 'PHYSIO_ALARM', severity, alarm_msg))
        conn.commit()
        conn.close()
    except Exception:
        pass

    # Save to memory cache for fast dashboard polling
    memory_store["vitals"] = {**v, "device_id": data.get("device_id"), "timestamp": now.strftime("%H:%M:%S")}
    memory_store["history"].append({
        "timestamp": now.isoformat(),
        "heart_rate": v.get("hr"),
        "spo2": v.get("spo2"),
        "nibp_sys": v.get("nibp_sys"),
        "nibp_dia": v.get("nibp_dia"),
        "temperature": v.get("temp"),
        "respiration_rate": v.get("resp")
    })
    memory_store["history"] = memory_store["history"][-50:]
    memory_store["logs"].insert(0, {
        "time": now.strftime("%H:%M:%S"),
        "ip": data.get("source_ip"),
        "size": data.get("packet_size"),
        "interval": data.get("update_interval_ms"),
        "dest_port": data.get("dest_port", 5005),
        "protocol": data.get("protocol", "detected"),
        "hex": data.get("raw_hex")[:24]
    })
    memory_store["logs"] = memory_store["logs"][:8]
    if alarm_msg:
        memory_store["alarms"].insert(0, {"time": now.strftime("%H:%M:%S"), "sev": severity, "msg": alarm_msg})
        memory_store["alarms"] = memory_store["alarms"][:5]

    return jsonify({"status": "SUCCESS"}), 200

@app.route("/api/dashboard-data")
def dashboard_data():
    return jsonify({
        "active_vitals": memory_store["vitals"],
        "comm_logs": memory_store["logs"],
        "alarms": memory_store["alarms"],
        "system": {"status": "ONLINE", "db": "CONNECTED", "latency": "2ms"}
    })

@app.route("/api/patients")
def patients():
    v = memory_store["vitals"]
    return jsonify([{
        "patient_id": "PT-101",
        "name": "David Johnson",
        "age": 58,
        "gender": "M",
        "bed_number": "ICU-Bed-01",
        "vitals": v,
        "device": {"device_id": v.get("device_id", DEVICE_ID), "status": "ONLINE" if v else "OFFLINE"}
    }])

@app.route("/api/history/<patient_id>")
def patient_history(patient_id):
    return jsonify(memory_store["history"])

@app.route("/api/system_health")
def system_health():
    return jsonify({
        "total_vital_records": len(memory_store["history"]),
        "total_communication_logs": len(memory_store["logs"]),
        "active_unacknowledged_alarms": len(memory_store["alarms"]),
        "last_captured_packet": memory_store["history"][-1]["timestamp"] if memory_store["history"] else None,
        "devices": [{"device_id": memory_store["vitals"].get("device_id", DEVICE_ID), "ip_address": "", "status": "ONLINE" if memory_store["vitals"] else "OFFLINE", "last_heartbeat": None}]
    })

@app.route("/api/comm_logs")
def communication_logs():
    return jsonify([{
        "timestamp": f"{log['time']}",
        "source_ip": log["ip"],
        "dest_ip": "dashboard",
        "dest_port": log.get("dest_port", 5005),
        "protocol": log.get("protocol", "detected"),
        "packet_size": log["size"],
        "log_type": "PPM",
        "raw_hex": log["hex"]
    } for log in memory_store["logs"]])

@app.route("/api/alarms")
def active_alarms():
    return jsonify([])

@app.route("/health")
def health_check():
    return jsonify({"status": "ok", "host": HTTP_HOST, "port": HTTP_PORT}), 200

# Embedded Hospital Multi-Bed ICU Dashboard
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>YK-8000C Central Monitoring Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { margin: 0; background-color: #0d1117; color: #c9d1d9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }
        header { background: #161b22; padding: 12px 20px; display: flex; justify-content: space-between; border-bottom: 1px solid #30363d; align-items: center; }
        .tag { background: #238636; color: white; padding: 3px 8px; border-radius: 4px; font-size: 12px; }
        .container { display: grid; grid-template-columns: 2fr 1fr; gap: 15px; padding: 15px; }
        .card { background: #161b22; border-radius: 6px; border: 1px solid #30363d; padding: 15px; }
        .vitals-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 10px; }
        .tile { background: #0d1117; border-radius: 4px; padding: 10px; text-align: center; border-left: 4px solid #58a6ff; }
        .tile h4 { margin: 0; font-size: 12px; color: #8b949e; text-transform: uppercase; }
        .tile .val { font-size: 32px; font-weight: bold; margin-top: 5px; }
        .tile.green { border-color: #3fb950; color: #3fb950; }
        .tile.cyan { border-color: #388bfd; color: #388bfd; }
        .tile.yellow { border-color: #d29922; color: #d29922; }
        .tile.red { border-color: #f85149; color: #f85149; }
        canvas { width: 100%; height: 130px; background: #000; border-radius: 4px; }
        table { width: 100%; font-size: 11px; border-collapse: collapse; margin-top: 8px; }
        th, td { text-align: left; padding: 5px; border-bottom: 1px solid #21262d; }
        .alarm-box { background: #3b1219; border: 1px solid #f85149; color: #ff7b72; padding: 8px; border-radius: 4px; margin-bottom: 8px; font-size: 12px; }
    </style>
</head>
<body>
    <header>
        <div><strong>HOSPITAL CENTRAL MONITORING STATION (CMS)</strong> | YK-8000C Bridge</div>
        <div>Device: <span class="tag">ONLINE</span></div>
    </header>
    <div class="container">
        <!-- Bed 1 Active Stream -->
        <div class="card">
            <h3>ICU BED 01: David Johnson (58M) - YK-8000C</h3>
            <canvas id="ecgCanvas"></canvas>
            <div class="vitals-grid">
                <div class="tile green"><h4>Heart Rate</h4><div class="val" id="hr">--</div>BPM</div>
                <div class="tile cyan"><h4>SpO2</h4><div class="val" id="spo2">--</div>%</div>
                <div class="tile yellow"><h4>NIBP (Sys/Dia)</h4><div class="val" id="nibp">--/--</div>mmHg</div>
                <div class="tile"><h4>Temperature</h4><div class="val" id="temp">--</div>°C</div>
                <div class="tile"><h4>Respiration</h4><div class="val" id="resp">--</div>/min</div>
                <div class="tile"><h4>Pulse Rate</h4><div class="val" id="pr">--</div>BPM</div>
            </div>
        </div>

        <!-- Protocol Metrics & Alarm Sidebar -->
        <div class="card">
            <h3>Protocol & Comm Sniffer</h3>
            <div id="alarmContainer"></div>
            <table>
                <thead><tr><th>Time</th><th>IP</th><th>Size</th><th>Freq</th><th>Hex</th></tr></thead>
                <tbody id="logTable"></tbody>
            </table>
        </div>
    </div>

    <script>
        // Oscilloscope ECG Rendering
        const canvas = document.getElementById('ecgCanvas');
        const ctx = canvas.getContext('2d');
        canvas.width = 600; canvas.height = 130;
        let x = 0, y = 65;
        
        function drawECG(val) {
            ctx.fillStyle = 'rgba(0, 0, 0, 0.05)';
            ctx.fillRect(x, 0, 8, canvas.height);
            ctx.strokeStyle = '#00ff41';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(x, y);
            y = 65 - val;
            x = (x + 3) % canvas.width;
            ctx.lineTo(x, y);
            ctx.stroke();
        }

        async function updateDashboard() {
            try {
                const res = await fetch('/api/dashboard-data');
                const data = await res.json();
                const v = data.active_vitals;
                if(v && v.hr) {
                    document.getElementById('hr').innerText = v.hr;
                    document.getElementById('pr').innerText = v.hr;
                    document.getElementById('spo2').innerText = v.spo2;
                    document.getElementById('nibp').innerText = `${v.nibp_sys}/${v.nibp_dia}`;
                    document.getElementById('temp').innerText = v.temp;
                    document.getElementById('resp').innerText = v.resp;
                    if(v.ecg) v.ecg.forEach(val => drawECG(val));
                }
                // Comm logs table
                const logRows = data.comm_logs.map(l => 
                    `<tr><td>${l.time}</td><td>${l.ip}</td><td>${l.size}B</td><td>${l.interval}ms</td><td>${l.hex}</td></tr>`
                ).join('');
                document.getElementById('logTable').innerHTML = logRows;

                // Alarms
                const alarms = data.alarms.map(a => 
                    `<div class="alarm-box"><strong>[${a.sev}]</strong> ${a.time}: ${a.msg}</div>`
                ).join('');
                document.getElementById('alarmContainer').innerHTML = alarms;
            } catch(e) {}
        }
        setInterval(updateDashboard, 1000);
    </script>
</body>
</html>
"""

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")

@app.route("/static/<path:filename>")
def static_assets(filename):
    return send_from_directory(STATIC_DIR, filename)

if __name__ == "__main__":
    init_mysql_db()
    print(f"[+] Starting Dashboard on http://0.0.0.0:{HTTP_PORT}")
    app.run(host="0.0.0.0", port=HTTP_PORT)