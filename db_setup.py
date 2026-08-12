import mysql.connector
from mysql.connector import Error

def setup_database():
    try:
        import os
        # Connect to MySQL Server (utilizing env variables)
        db_host = os.getenv("DB_HOST", "127.0.0.1")
        db_user = os.getenv("DB_USER", "root")
        db_password = os.getenv("DB_PASSWORD", "1234")
        db_port = int(os.getenv("DB_PORT", "3306"))
        
        conn = mysql.connector.connect(
            host=db_host,
            user=db_user,
            password=db_password,
            port=db_port
        )
        cursor = conn.cursor()
        
        # 1. Create Database
        cursor.execute("CREATE DATABASE IF NOT EXISTS ppm_monitoring;")
        print("Database 'ppm_monitoring' ensured.")
        
        # Select database
        cursor.execute("USE ppm_monitoring;")
        
        # 2. Patient Info Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS patient_info (
            patient_id VARCHAR(50) PRIMARY KEY,
            name VARCHAR(100),
            age INT,
            gender VARCHAR(10),
            bed_number VARCHAR(20),
            admission_date DATETIME,
            hr_low INT DEFAULT 60,
            hr_high INT DEFAULT 100,
            spo2_low DECIMAL(5,2) DEFAULT 90.0,
            temp_high DECIMAL(4,2) DEFAULT 38.0
        );
        """)
        
        # Ensure custom threshold columns exist if table was already created
        for col, col_type in [("hr_low", "INT DEFAULT 60"), ("hr_high", "INT DEFAULT 100"), 
                              ("spo2_low", "DECIMAL(5,2) DEFAULT 90.0"), ("temp_high", "DECIMAL(4,2) DEFAULT 38.0")]:
            try:
                cursor.execute(f"ALTER TABLE patient_info ADD COLUMN {col} {col_type};")
            except Exception:
                pass # Already exists
                
        print("Table 'patient_info' ensured.")
        
        # 3. Device Info Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS device_info (
            device_id VARCHAR(50) PRIMARY KEY,
            mac_address VARCHAR(17),
            ip_address VARCHAR(15),
            firmware_version VARCHAR(20),
            status ENUM('ONLINE', 'OFFLINE', 'MAINTENANCE'),
            last_heartbeat DATETIME
        );
        """)
        print("Table 'device_info' ensured.")
        
        # 4. Vital Params Table
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
            ecg_payload TEXT, -- Base64 encoded array for waveform rendering
            FOREIGN KEY (patient_id) REFERENCES patient_info(patient_id),
            FOREIGN KEY (device_id) REFERENCES device_info(device_id)
        );
        """)
        print("Table 'vital_params' ensured.")
        
        # 5. Alarm Events Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS alarm_events (
            alarm_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            patient_id VARCHAR(50),
            device_id VARCHAR(50),
            timestamp DATETIME(3),
            alarm_type VARCHAR(50),
            severity ENUM('LOW', 'MEDIUM', 'CRITICAL'),
            message VARCHAR(255),
            is_acknowledged BOOLEAN DEFAULT FALSE,
            FOREIGN KEY (patient_id) REFERENCES patient_info(patient_id)
        );
        """)
        print("Table 'alarm_events' ensured.")
        
        # 6. Comm & System Logs
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS comm_logs (
            log_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            timestamp DATETIME(3),
            source_ip VARCHAR(15),
            dest_ip VARCHAR(15),
            protocol VARCHAR(10),
            packet_size INT,
            log_type ENUM('HEARTBEAT', 'DATA', 'HANDSHAKE', 'ERROR'),
            raw_hex TEXT
        );
        """)
        print("Table 'comm_logs' ensured.")
        
        # 7. Bedside Clinical Notes Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS patient_notes (
            note_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            patient_id VARCHAR(50),
            timestamp DATETIME(3),
            author VARCHAR(100),
            note TEXT,
            FOREIGN KEY (patient_id) REFERENCES patient_info(patient_id)
        );
        """)
        print("Table 'patient_notes' ensured.")
        
        # Insert Seed/Dummy Data
        # Patient Info
        patients = [
            ('PT-1001', 'John Doe', 45, 'M', 'ICU-Bed-01'),
            ('PT-1002', 'Jane Smith', 62, 'F', 'ICU-Bed-02')
        ]
        for p in patients:
            cursor.execute("SELECT COUNT(*) FROM patient_info WHERE patient_id = %s", (p[0],))
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                INSERT INTO patient_info (patient_id, name, age, gender, bed_number, admission_date)
                VALUES (%s, %s, %s, %s, %s, NOW() - INTERVAL 2 DAY)
                """, p)
                print(f"Inserted patient dummy data for {p[0]}")
                
        # Device Info
        devices = [
            ('YK-8000C-001', '00:1A:2B:3C:4D:5E', '192.168.1.101', 'v2.4.1', 'ONLINE'),
            ('YK-8000C-002', '00:1A:2B:3C:4D:5F', '192.168.1.102', 'v2.4.1', 'ONLINE')
        ]
        for d in devices:
            cursor.execute("SELECT COUNT(*) FROM device_info WHERE device_id = %s", (d[0],))
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                INSERT INTO device_info (device_id, mac_address, ip_address, firmware_version, status, last_heartbeat)
                VALUES (%s, %s, %s, %s, %s, NOW())
                """, d)
                print(f"Inserted device dummy data for {d[0]}")
                
        # Commit changes
        conn.commit()
        print("Database schema and seed data setup successfully completed.")
        
    except Error as e:
        print(f"Error setting up database: {e}")
        raise e
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    setup_database()
