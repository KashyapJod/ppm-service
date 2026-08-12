# YK-8000C Integration - Hardware Deployment & Raspberry Pi Gateway Guide

This document describes how to deploy the YK-8000C integration system in a physical clinical environment using a Raspberry Pi as a secure gateway bridge between the patient parameter monitors (PPM) and the hospital's Central Monitoring System (CMS) intranet.

---

## 1. Network Topology & Flow Diagram

In a clinical deployment, medical devices (YK-8000C patient monitors) are isolated on a dedicated Medical LAN (VLAN/subnet) for safety and security. The Raspberry Pi acts as a secure dual-homed gateway routing proxy:

```mermaid
graph TD
    subgraph Medical LAN (VLAN 10 - Isolated Subnet)
        PPM1[YK-8000C Monitor 1<br/>192.168.1.101] -- UDP 5000/5011 --> Switch[Medical Switch]
        PPM2[YK-8000C Monitor 2<br/>192.168.1.102] -- UDP 5000/5012 --> Switch
    end

    Switch -- VLAN Trunk/Port access --> PiEth0[Raspberry Pi Gateway<br/>eth0: 192.168.1.50]

    subgraph Raspberry Pi Gateway (Proxy Routing Bridge)
        PiEth0 <--> BridgeProc[pi_bridge.py Proxy service]
        BridgeProc <--> PiEth1[eth1: 10.0.0.100]
    end

    PiEth1 -- Intranet LAN --> CoreRouter[Hospital Core Router]

    subgraph Hospital Intranet (VLAN 20)
        CoreRouter <--> CMS[Vendor CMS Server<br/>10.0.0.50:5002]
        CoreRouter <--> APIServer[FastAPI Server & Ingestion<br/>10.0.0.80:8000]
        CoreRouter <--> Sniffer[Sniffer Logger / Tap<br/>10.0.0.90]
    end
```

---

## 2. Connecting to the YK-8000C Patient Monitor

The YK-8000C monitor features an RJ45 Ethernet port on its rear panel designed for Central Monitoring System (CMS) integration.

### Step 2.1: Physical Connection
1. Connect a Cat5e/Cat6 Ethernet cable from the YK-8000C RJ45 port to an interface port on your isolated Medical LAN switch.
2. Connect the Raspberry Pi's native Ethernet interface (`eth0`) to the same switch.

### Step 2.2: Patient Monitor Menu Configuration
To configure network telemetry transmission on the YK-8000C:
1. Turn on the monitor and press the **Menu** button.
2. Select **System Setup** -> **Net Setup**.
3. Configure the local network parameters:
   - **Local IP Address:** Allocate a static IP address in the `192.168.1.0/24` range (e.g., `192.168.1.101` for Bed 1, `192.168.1.102` for Bed 2).
   - **Net Mask:** `255.255.255.0`
   - **Gateway IP:** Set to the Raspberry Pi's Medical LAN IP address: `192.168.1.50`.
4. Configure the Central Station connection:
   - **Server IP Address (CMS):** Set to the Raspberry Pi's Medical LAN interface IP: `192.168.1.50` (The Pi will bridge the packets across networks).
   - **Server Port:** `5001` (pi_bridge ingress port).
   - **Local Socket Port:** Set to `5011` for Bed 1, `5012` for Bed 2.
5. Save settings and restart the monitor.

---

## 3. Raspberry Pi Gateway Configuration

The Raspberry Pi requires two network interfaces:
- **Interface 1 (`eth0`):** Built-in Ethernet, connected to the isolated Medical LAN switch (`192.168.1.0/24` network).
- **Interface 2 (`eth1` or `wlan0`):** USB-to-Ethernet adapter or Wi-Fi, connected to the Hospital Intranet LAN (`10.0.0.0/24` network).

### Step 3.1: Configure IP Addresses
Edit the networking configuration on the Raspberry Pi (typically `/etc/dhcpcd.conf` or `/etc/netplan/` depending on OS version). 

For `/etc/dhcpcd.conf`:
```ini
# Interface connected to isolated Medical Switch
interface eth0
static ip_address=192.168.1.50/24
nogateway

# Interface connected to Clinical Intranet LAN
interface eth1
static ip_address=10.0.0.100/24
static routers=10.0.0.1
static domain_name_servers=10.0.0.1 8.8.8.8
```
Apply changes: `sudo systemctl restart dhcpcd`.

### Step 3.2: Enable IP Forwarding & Security Rules
To allow network communication, configure IP forwarding in `/etc/sysctl.conf`:
```bash
# Uncomment or append this line:
net.ipv4.ip_forward=1
```
Apply changes: `sudo sysctl -p`.

Configure security rules to mirror telemetry traffic using `iptables`:
```bash
# Allow established and related traffic routing
sudo iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Route traffic from Medical LAN (eth0) out to Intranet (eth1)
sudo iptables -A FORWARD -i eth0 -o eth1 -j ACCEPT
```

---

## 4. Deploying the Proxy Bridge Service

To parse packets and fan them out to the database sniffer, ingestion parser, and websocket tap ports, run a python proxy daemon (`pi_bridge.py` equivalent) on the Raspberry Pi:

### Step 4.1: Transfer and Configure Proxy Script
1. Deploy `protocol_decoder.py` and `pi_bridge.py` to the `/opt/ppm_bridge` directory on the Raspberry Pi.
2. Edit `pi_bridge.py` config values to point to your intranet endpoints:
   - `CMS_IP`: Set to the physical Vendor CMS IP (e.g., `10.0.0.50`).
   - Fanout targets in `pi_bridge.py` should be updated to point to the FastAPI Ingestion server's IP address (e.g., `10.0.0.80`).

### Step 4.2: Create Systemd Startup Service
To ensure the proxy bridge starts automatically on boot, create a systemd configuration file `/etc/systemd/system/ppm-bridge.service`:

```ini
[Unit]
Description=YK-8000C Telemetry Proxy Gateway Service
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/ppm_bridge
ExecStart=/usr/bin/python3 /opt/ppm_bridge/pi_bridge.py
Restart=always
RestartSec=5
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=ppm-bridge

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable ppm-bridge.service
sudo systemctl start ppm-bridge.service
```

Verify service status:
```bash
sudo systemctl status ppm-bridge.service
```

---

## 5. Vendor CMS Server Mapping

The Vendor CMS server expects packets from the patient monitors to identify their beds and map ports correctly:
1. Ensure the Vendor CMS application is configured to listen for UDP packets on port `5002`.
2. The proxy bridge will automatically map outgoing packets. When the CMS responds with a heartbeat ACK, it sends the payload to `pi_bridge.py` on `10.0.0.100:5002`.
3. The proxy looks up the active routing table, maps the client back to its Medical LAN port (e.g. `192.168.1.101:5011`), and routes the response frame back to the monitor.
4. This ensures bi-directional connection handshakes remain intact even across isolated network subnets.
