# RPi connection runbook and checkpoint log

This file is the step-by-step implementation guide for the Raspberry Pi deployment. Use it as a live field checklist while connecting the PPM, CMS, bridge, gateway, and dashboard.

## Goal

Build and validate a working Pi-based telemetry path where:
- the PPM is connected over Ethernet
- the Pi hosts the RaspAP Wi‑Fi network
- the CMS connects over Wi‑Fi at 192.168.2.200
- the Pi can capture the real traffic and show telemetry in the dashboard
- no assumptions are made about UDP/TCP or a port until packet capture proves them

## Core rule

The capture is the source of truth. Do not guess transport, destination, or port. Keep looping the following checks until the real packet flow is understood.

## Required setup

### 1. Confirm the Wi‑Fi AP is already active

- RaspAP is already managing the AP.
- Do not recreate the AP in this project.
- Confirm the SSID is RaspAP and the password is ChangeMe.

### 2. Confirm the Pi network state

Run:

```bash
ip -br link
ip addr show br0
ip addr show eth0
ip route show
```

Check:
- the Pi has a bridge or shared interface available
- the Pi has an Ethernet link to the PPM
- the Pi is reachable on 192.168.2.1 from the CMS side where applicable

### 3. Configure the CMS Wi‑Fi

Connect the CMS to:

```text
SSID: RaspAP
Password: ChangeMe
```

Set:

```text
IP: 192.168.2.200
Subnet: 255.255.255.0
```

Then test:

```bash
ping 192.168.2.1
```

If ping fails, fix the Wi‑Fi or IP state before touching the PPM traffic.

## Capture loop

### 4. Start packet capture first

On the Pi, start a broad capture:

```bash
sudo tcpdump -i br0 -nn -s 0 -w /home/pi/ppm_capture.pcap
```

If the bridge is unknown or there are multiple interfaces, try:

```bash
sudo tcpdump -i any host 192.168.2.200 -w /home/pi/ppm_capture.pcap
```

Then inspect:

```bash
tcpdump -r /home/pi/ppm_capture.pcap -nn -X
```

Look for:
- whether packets are UDP, TCP or another protocol
- the actual source and destination addresses
- the real destination port
- whether the traffic is going to the CMS, the Pi, or elsewhere on the shared LAN

### 5. Repeat until the real path is known

Loop this process until both of these are true:
- the traffic source and destination are clear
- the protocol and port are confirmed

Do not move to runtime configuration until this is known.

## Dashboard loop

### 6. Start the dashboard

```bash
cd "/home/kk/ppm project"
HTTP_HOST=0.0.0.0 HTTP_PORT=3000 python3 scripts/laptop_server.py
```

Open:

```text
http://192.168.2.1:3000
```

If data never appears, confirm:
- the dashboard is running
- the gateway is sending telemetry to the API
- the API endpoint is reachable
- the captured traffic is being parsed successfully

## Gateway loop

### 7. Launch the gateway only after packet capture is understood

Use capture-only by default:

```bash
cd "/home/kk/ppm project"
FORWARD_TO_CMS=false \
PPM_PROTOCOL=udp \
PPM_LISTEN_PORT=5005 \
CMS_IP=192.168.2.200 \
DASHBOARD_API_IP=127.0.0.1 \
DASHBOARD_API_PORT=3000 \
CAPTURE_INTERFACE=br0 \
python3 scripts/rpi_gateway.py
```

Then adjust only if the capture proves the data path needs forwarding.

### 8. If the gateway does not work, debug in this order

1. Check the captured pcap for real protocol and port.
2. Confirm the interface name is correct.
3. Confirm the PPM traffic is actually reaching the Pi side.
4. Confirm the CMS address and dashboard address are correct.
5. Check whether `FORWARD_TO_CMS` must be true or false.
6. Restart the gateway with the corrected settings.

Repeat until the gateway logs show received data and the dashboard updates.

## Final validation steps

### 9. Confirm end-to-end success

The setup is considered successful only when all are true:

```text
[ ] CMS connects to RaspAP successfully
[ ] Pi sees traffic on the Ethernet/bridge interface
[ ] tcpdump reveals the actual protocol and port
[ ] gateway uses the real values
[ ] dashboard receives telemetry from the API
[ ] packet flow matches the captured traffic, not assumptions
```

### 10. Keep looping until final result

If anything is missing, repeat the sequence:

1. capture traffic
2. inspect packets
3. adjust protocol/port/interface values
4. restart gateway
5. confirm dashboard receives data
6. stop only when the flow is proven end-to-end

## Commands to keep handy

Capture:

```bash
sudo tcpdump -i br0 -nn -s 0 -w /home/pi/ppm_capture.pcap
```

Inspect:

```bash
tcpdump -r /home/pi/ppm_capture.pcap -nn -X
```

Validate network:

```bash
ip -br link
ip addr show br0
ip addr show eth0
ip route show
```

Run dashboard:

```bash
cd "/home/kk/ppm project"
HTTP_HOST=0.0.0.0 HTTP_PORT=3000 python3 scripts/laptop_server.py
```

Run gateway:

```bash
cd "/home/kk/ppm project"
FORWARD_TO_CMS=false PPM_PROTOCOL=udp PPM_LISTEN_PORT=5005 CMS_IP=192.168.2.200 CAPTURE_INTERFACE=br0 python3 scripts/rpi_gateway.py
```

Validate syntax:

```bash
cd "/home/kk/ppm project" && python3 -m py_compile scripts/*.py
```

## Final note

This project is never finished until the real monitor traffic is captured, identified, and validated on the Pi. Every connection step must be proven by live evidence, not by assumptions.
