# YK-8000C Patient Monitor Integration & Dashboard Project

This document outlines the step-by-step development plan using a modular "Agent" architecture. Each agent is responsible for a specific domain of the system. 

## Agent 1: Hardware Simulator (The Mock Layer)
**Goal:** Simulate the YK-8000C PPM, the Vendor CMS, and the network bridge (Raspberry Pi) so downstream development can happen immediately.

*   **Step 1.1: Develop the PPM Simulator (`ppm_sim.py`)**
    *   Create a Python script that generates mock vitals (ECG waveforms, SpO2: 95-100%, NIBP: 120/80, Temp: 36.5-37.5°C, Resp, Pulse: 60-100).
    *   Package these vitals into a structured binary or JSON payload (mocking a proprietary protocol).
    *   Broadcast/Send this data over a local UDP/TCP port (e.g., `127.0.0.1:5000`).
*   **Step 1.2: Develop the Vendor CMS Simulator (`cms_sim.py`)**
    *   Create a script that binds to a port, listens for the PPM's data, and sends back heartbeat ACKs every 5 seconds.
*   **Step 1.3: Develop the Raspberry Pi Bridge Simulator (`pi_bridge.py`)**
    *   Act as a proxy passing data between `ppm_sim` and `cms_sim`. 

## Agent 2: Packet Capture & Protocol Analyzer
**Goal:** Intercept communications, log network metrics, and reverse-engineer the protocol structure.

*   **Step 2.1: Packet Sniffer (`packet_capture.py`)**
    *   Use Python's `scapy` library (or C++ `libpcap`) to sniff traffic on the simulated ports.
    *   Log metadata: Source/Dest IPs, TCP/UDP ports, packet size, and frequency (update intervals).
    *   Save raw streams to `.pcap` files for offline Wireshark analysis.
*   **Step 2.2: Protocol Decoder Logic**
    *   Write a parser that extracts:
        *   **Session Establishment:** Look for SYN/ACK or specific handshake bytes.
        *   **Message Headers:** Identify static magic bytes indicating start-of-frame.
        *   **Payload Format & Encoding:** Map byte offsets to patient parameters.
        *   **Checksum/CRC:** Implement CRC-16 or checksum validation to ensure data integrity.
        *   **Timestamps:** Extract or append UNIX timestamps.

## Agent 3: Backend Ingestion & MySQL Database
**Goal:** Receive decoded messages, validate them, and store them in the local MySQL database.

*   **Step 3.1: Database Schema Creation**
    *   Create normalized tables for the system.
*   **Step 3.2: Ingestion Service**
    *   Create a background service that takes parsed data from Agent 2, batches it, and runs `INSERT`/`UPDATE` queries. 
    *   Implement logic to flag Alarm Events (e.g., SpO2 < 90) and log them to the `alarm_events` table.

## Agent 4: Live Dashboard & Frontend
**Goal:** Visualize the data in a multi-bed, real-time UI.

*   **Step 4.1: API Backend (Node.js/Express or Python/FastAPI)**
    *   Expose REST endpoints for historical data (`/api/patients`, `/api/history`).
    *   Implement WebSockets (`Socket.io`) to stream live vital updates and connection status to the frontend.
*   **Step 4.2: Frontend UI (React.js / Vue.js)**
    *   **Multi-Bed View:** A grid showing cards for active patients with current vitals and device status (Online/Offline).
    *   **Patient Detail View:** Real-time ECG charting (using libraries like Chart.js or CanvasJS), vital sign trend graphs, and historical tables.
    *   **Alarm Display:** A global notification banner for critical events.
    *   **System Health:** Visual indicators for packet capture frequency, database size, and comm logs.

---

## Appendix A: MySQL Schema & Dummy Data

Execute the following SQL script to set up your environment and populate it with simulated data.

```sql
-- 1. Create Database
CREATE DATABASE IF NOT EXISTS ppm_monitoring;
USE ppm_monitoring;

-- 2. Patient Info Table
CREATE TABLE patient_info (
    patient_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100),
    age INT,
    gender VARCHAR(10),
    bed_number VARCHAR(20),
    admission_date DATETIME
);

-- 3. Device Info Table
CREATE TABLE device_info (
    device_id VARCHAR(50) PRIMARY KEY,
    mac_address VARCHAR(17),
    ip_address VARCHAR(15),
    firmware_version VARCHAR(20),
    status ENUM('ONLINE', 'OFFLINE', 'MAINTENANCE'),
    last_heartbeat DATETIME
);

-- 4. Vital Params Table (Optimized for time-series)
CREATE TABLE vital_params (
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
    ecg_payload TEXT, -- Base64 encoded array for waveform rendering
    FOREIGN KEY (patient_id) REFERENCES patient_info(patient_id),
    FOREIGN KEY (device_id) REFERENCES device_info(device_id)
);

-- 5. Alarm Events Table
CREATE TABLE alarm_events (
    alarm_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    patient_id VARCHAR(50),
    device_id VARCHAR(50),
    timestamp DATETIME(3),
    alarm_type VARCHAR(50), -- e.g., 'SPO2_LOW', 'HR_HIGH'
    severity ENUM('LOW', 'MEDIUM', 'CRITICAL'),
    message VARCHAR(255),
    is_acknowledged BOOLEAN DEFAULT FALSE,
    FOREIGN KEY (patient_id) REFERENCES patient_info(patient_id)
);

-- 6. Comm & System Logs
CREATE TABLE comm_logs (
    log_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME(3),
    source_ip VARCHAR(15),
    dest_ip VARCHAR(15),
    protocol VARCHAR(10),
    packet_size INT,
    log_type ENUM('HEARTBEAT', 'DATA', 'HANDSHAKE', 'ERROR'),
    raw_hex TEXT
);

-- ==========================================
-- DUMMY DATA INSERTION
-- ==========================================

INSERT INTO patient_info (patient_id, name, age, gender, bed_number, admission_date) VALUES 
('PT-1001', 'John Doe', 45, 'M', 'ICU-Bed-01', NOW() - INTERVAL 2 DAY),
('PT-1002', 'Jane Smith', 62, 'F', 'ICU-Bed-02', NOW() - INTERVAL 5 HOUR);

INSERT INTO device_info (device_id, mac_address, ip_address, firmware_version, status, last_heartbeat) VALUES 
('YK-8000C-001', '00:1A:2B:3C:4D:5E', '192.168.1.101', 'v2.4.1', 'ONLINE', NOW()),
('YK-8000C-002', '00:1A:2B:3C:4D:5F', '192.168.1.102', 'v2.4.1', 'ONLINE', NOW());

INSERT INTO vital_params (patient_id, device_id, timestamp, heart_rate, spo2, nibp_sys, nibp_dia, temperature, respiration_rate, ecg_payload) VALUES 
('PT-1001', 'YK-8000C-001', NOW() - INTERVAL 10 SECOND, 78, 98.5, 120, 80, 37.1, 16, '[12,15,18,50,-20,10,12]'),
('PT-1001', 'YK-8000C-001', NOW() - INTERVAL 5 SECOND, 79, 98.0, 120, 80, 37.1, 16, '[13,14,19,55,-22,11,12]'),
('PT-1002', 'YK-8000C-002', NOW() - INTERVAL 10 SECOND, 110, 92.0, 145, 90, 38.5, 22, '[10,12,14,40,-10,8,10]');

INSERT INTO alarm_events (patient_id, device_id, timestamp, alarm_type, severity, message) VALUES 
('PT-1002', 'YK-8000C-002', NOW() - INTERVAL 10 SECOND, 'HR_HIGH', 'MEDIUM', 'Heart rate exceeded 100 BPM'),
('PT-1002', 'YK-8000C-002', NOW() - INTERVAL 5 SECOND, 'TEMP_HIGH', 'LOW', 'Patient temperature at 38.5C');

INSERT INTO comm_logs (timestamp, source_ip, dest_ip, protocol, packet_size, log_type, raw_hex) VALUES 
(NOW(), '192.168.1.101', '192.168.1.50', 'TCP', 256, 'DATA', 'AA 55 01 02 ... FF'),
(NOW(), '192.168.1.102', '192.168.1.50', 'TCP', 64, 'HEARTBEAT', 'AA 55 00 00 ... FF');