# YK-8000C Central Clinical Workstation & Patient Monitor Integration

This project is a high-fidelity real-time clinical workstation dashboard and network integration system for the **YK-8000C Patient Parameter Monitor (PPM)**. It simulates the medical device network telemetry layer, sniffs network packets, decodes proprietary binary frames, ingests vitals data into a MySQL database with critical threshold alarm triggers, and visualizes live scrolling ECG sweeps and historical trends on a workstation interface.

---

## System Architecture Overview

The application features a modular multi-process architecture coordinating the following services:
- **PPM Simulators (`ppm_sim.py`):** Telemetry engines simulating YK-8000C patient monitors. They stream real-time patient vitals and 100Hz ECG waveforms packed in custom binary protocol payloads.
- **Central Monitoring System (`cms_sim.py`):** Simulates the vendor Central Monitoring System (CMS) listening for telemetry packets and returning Modbus CRC-16 check keepalive ACK responses.
- **Raspberry Pi Gateway Bridge (`pi_bridge.py`):** Acting as a routing proxy, it relays UDP packets between the medical device VLAN and the clinical intranet, logging routing associations and duplicating packets to sniffer, database, and live API tap ports.
- **Packet Sniffer (`packet_capture.py`):** Sniffs UDP traffic and outputs a standard Wireshark-compatible `capture.pcap` file.
- **Ingestion Daemon (`ingestion.py`):** Decodes raw byte packets, updates heartbeats, checks alarm thresholds, inserts records into MySQL, and logs transactions in `comm_logs`.
- **FastAPI Backend Server (`main.py`):** Manages MySQL connections, exposes REST APIs for history/alarms, and starts a WebSockets server (`/ws/live`) to stream live vital data.
- **Clinical Workstation UI:** Single-page surveillance dashboard served directly at `http://127.0.0.1:8000/static/index.html`.

---

## File Structure

```
PPM Software/
│
├── static/                         # Web Workstation Frontend Files
│   ├── index.html                  # Workstation layout template
│   ├── style.css                   # Glassmorphism dark theme styling
│   └── app.js                      # WebSockets link, canvas ECG drawing, and APIs polling
│
├── main.py                         # FastAPI Backend API & WebSocket router
├── ppm_sim.py                      # YK-8000C PPM hardware client simulator
├── cms_sim.py                      # Central Monitoring System simulator
├── pi_bridge.py                    # Raspberry Pi NAT/proxy network bridge
├── packet_capture.py               # Sniffer tap tool writing capture.pcap
├── ingestion.py                    # Database ingestion background service
│
├── protocol_decoder.py             # Binary packing/unpacking and Modbus CRC-16 helper
├── db_setup.py                     # Database setup and seeder script
├── run_all.py                      # Orchestrator launcher script
│
├── readme.md                       # Software setup guide (This file)
├── rasp.md                         # Physical Raspberry Pi deployment instructions
└── changelog.md                    # Historical record of completed steps
```

---

## Software Installation & Setup

### 1. Prerequisites
Ensure you have the following installed on your host system:
- **Python 3.10+** (Added to system PATH)
- **MySQL Server 8.0** running locally on port `3306`

### 2. Configure MySQL Server
The system expects MySQL to accept connections on port `3306` with the following credentials:
- **User:** `root`
- **Password:** `1234`

### 3. Initialize Virtual Environment & Install Dependencies
Open a terminal in the project root directory and run:

```bash
# 1. Create a python virtual environment
python -m venv .venv

# 2. Activate virtual environment
# On Windows PowerShell:
.venv\Scripts\Activate.ps1
# On Linux / macOS:
source .venv/bin/activate

# 3. Install packages
pip install mysql-connector-python scapy fastapi uvicorn[standard] websockets
```

### 4. Create Database Schema
Run the database setup script to configure the MySQL database schema and seed the initial dummy datasets:

```bash
.venv\Scripts\python db_setup.py
```
This script creates the `ppm_monitoring` database, tables (`patient_info`, `device_info`, `vital_params`, `alarm_events`, `comm_logs`), and seeds active patients `PT-1001` (John Doe) and `PT-1002` (Jane Smith).

---

## Running the Application

Start all simulators, proxy gateways, database ingestion loops, and the FastAPI backend using the orchestrator launcher:

```bash
.venv\Scripts\python run_all.py
```

The orchestrator will:
1. Initialize the database connection.
2. Launch background threads for the Pi Bridge, CMS simulator, Sniffer, Ingestion daemon, and FastAPI server.
3. Wait 3 seconds to let sockets bind.
4. Launch the PPM client monitor simulators.
5. Print unified prefix logs showing real-time network packets and database inserts.

---

## Using the Workstation UI

Open your browser and navigate to: **[http://127.0.0.1:8000/static/index.html](http://127.0.0.1:8000/static/index.html)**

### Workstation Controls
- **Bed Selector Navigation (Left Sidebar):** Lists active clinical beds. Click a bed card (ICU-01 or ICU-02) to load that patient's vitals, scrolling ECG, and historical trends into focus.
- **Top Toolbar controls:**
  - **🔊 Audio Alarm:** Click to mute/unmute alarm sounds.
  - **⏸️ Silence (60s):** Temporarily silence alert sound beeps for 60 seconds (useful during patient triage).
  - **Inject Condition:** Select a patient state (Normal, Tachycardia, Bradycardia, Hypoxia, Fever) and click **Inject** to send command bytes back down the UDP link to the simulator. Watch the vital metrics shift and trigger alerts!
  - **ECG Customization:** Scale wave height/gain (`x0.5`, `x1.0`, `x2.0`) and adjust sweep scanning speed (`12.5`, `25`, `50` mm/s).
  - **Grid Toggle:** Enable/disable pink background grid lines.
  - **📥 Export CSV:** Compile the patient's vitals trends log database records into a CSV spreadsheet.
- **Focused Panel (Center):** Shows digital parameter meters and a large rolling canvas ECG. Inline trend charts render real-time history lines for HR, SpO2, NIBP, and Temperature.
- **Alert Notifications:** Flashing red banners indicate critical states, playing synchronized alert beeps. Click **Acknowledge Alarm** on the banner to clear the active alarm in the database.
- **System Monitoring (Right Sidebar):** View database records count, active devices status, and live hexadecimal UDP packet streams.

---

## Deployment

### Render backend
Use the included [render.yaml](render.yaml) Blueprint to deploy the FastAPI backend on Render. The service expects MySQL connection values in environment variables:
- `DB_HOST`
- `DB_USER`
- `DB_PASSWORD`
- `DB_NAME`
- `DB_PORT`

After deployment, the Render root URL redirects to the dashboard at `/static/index.html`, while the API remains available under `/api/*` and `/ws/live`.

### Netlify frontend
Use the included [netlify.toml](netlify.toml) to publish the `static/` folder as a standalone site. The frontend defaults to `https://ppm-backend.onrender.com` for API and websocket traffic.

If your Render service URL is different, open the Netlify site once with `?backend=<your-render-host>` in the query string. The browser caches that host in local storage, so subsequent visits keep using the correct backend.

### Recommended split
- Render hosts the Python API and websocket service.
- Netlify hosts the static clinical dashboard.
- When the frontend is opened on the Render host itself, it uses same-origin requests automatically.
