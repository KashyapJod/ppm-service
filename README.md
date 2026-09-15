# Raspberry Pi AP + Bridge Setup for PPM and CMS

This project assumes the Raspberry Pi is the Wi‑Fi access point and the shared network bridge for the patient monitor (PPM) and the CMS system.

The design is intentionally simple and strict:

- PPM is connected over Ethernet
- the PPM address is discovered from live traffic and may not be in the 192.168.2.0/24 subnet
- Raspberry Pi owns 192.168.2.1 on the shared bridge
- CMS device connects over Wi‑Fi at 192.168.2.200
- the Pi and CMS share the same subnet, with no NAT between them
- packet capture decides the real transport and port; do not guess UDP/TCP or a port

## 1. Network layout

```text
PPM (Ethernet, exact address discovered from live traffic)
   | Ethernet
   v
Raspberry Pi bridge / AP
   - IP: 192.168.2.1/24
   - bridge interface: br0
   - Wi‑Fi AP uses the same subnet
   v
CMS device at 192.168.2.200/24

Dashboard URL:
  http://192.168.2.1:3000
```

Expected addresses:

```text
Raspberry Pi bridge: 192.168.2.1/24
CMS Wi‑Fi client:    192.168.2.200/24
PPM Ethernet client: discovered from traffic; not assumed to be 192.168.2.x
```

## 2. Identify the actual Pi interfaces

Run this on the Raspberry Pi before configuring anything:

```bash
ip -br link
nmcli device status
```

Typical names are:

```text
Wi‑Fi: wlan0
Ethernet: eth0
Bridge: br0
```

The important part is not the exact adapter label; it is that the Pi creates one shared 192.168.2.0/24 network and the bridge is used as the common layer-2 boundary.

## 3. Validate the shared bridge on the Pi

The Wi‑Fi AP is already implemented with RaspAP. Do not recreate it. Use the existing Pi and bridge interfaces to validate the shared network path, then capture traffic.

```bash
ip -br link
ip addr show br0
ip addr show eth0
ip route show
```

This validation step is designed for the Pi-first topology:

- leaves the existing RaspAP Wi‑Fi AP alone
- validates the Pi Ethernet side and bridge path
- does not assume the PPM IP range
- keeps CMS on Wi‑Fi at 192.168.2.200
- uses tcpdump as the source of truth for packet inspection

If the actual Pi bridge is called something else, use that interface name instead of br0.

## 4. Configure the CMS device

Connect the CMS PC to the Wi‑Fi SSID:

```text
RaspAP
```

Password:

```text
ChangeMe
```

Use these settings:

```text
IP address:  192.168.2.200
Subnet mask: 255.255.255.0
```

Test the link:

```bash
ping 192.168.2.1
```

This confirms the CMS client can reach the Pi. It does not confirm the monitor protocol yet.

## 5. Capture before guessing protocol or port

This is the key rule for the whole project:

- never assume UDP
- never assume TCP
- never assume port 5005
- never assume the gateway must forward packets to the CMS host
- always inspect traffic first

Start a broad capture on the bridge or the relevant interface:

```bash
sudo tcpdump -i br0 -nn -s 0 -w /home/pi/ppm_capture.pcap
```

If you want to capture all interfaces and filter later:

```bash
sudo tcpdump -i any host 192.168.2.200 -w /home/pi/ppm_capture.pcap
```

Inspect the capture:

```bash
tcpdump -r /home/pi/ppm_capture.pcap -nn -X
```

This reveals:

- whether the PPM traffic is UDP, TCP, or something custom
- the real destination port
- whether the traffic is sent directly to the CMS, to the Pi bridge, or elsewhere on the shared LAN

## 6. Start the dashboard

Open one terminal and run:

```bash
cd "/home/kk/ppm project"
HTTP_HOST=0.0.0.0 HTTP_PORT=3000 python3 scripts/laptop_server.py
```

The dashboard listens on port 3000 by default. On the Pi, use:

```text
http://192.168.2.1:3000
```

If you are testing locally from the Pi itself, this also works:

```text
http://127.0.0.1:3000
```

## 7. Start the gateway

Once you know the real protocol and port from packet capture, start the gateway with the correct values.

Example:

```bash
cd "/home/kk/ppm project"

PPM_PROTOCOL=udp \
PPM_LISTEN_PORT=5005 \
CMS_IP=192.168.2.200 \
FORWARD_TO_CMS=false \
DASHBOARD_API_IP=127.0.0.1 \
DASHBOARD_API_PORT=3000 \
CAPTURE_INTERFACE=br0 \
python3 scripts/rpi_gateway.py
```

The default is capture-only. Only set `FORWARD_TO_CMS=true` if packet inspection proves the real path requires forwarding to the CMS host.

## 8. Test with the dummy sender

Use the dummy sender only after the actual packet flow is understood. It is meant for validation, not for guessing the production setup.

Example:

```bash
cd "/home/kk/ppm project"

GATEWAY_IP=192.168.2.200 \
GATEWAY_PROTOCOL=udp \
GATEWAY_PORT=5005 \
python3 scripts/dummy_ppm_sender.py
```

If you want the dummy sender to target the Pi gateway instead of the CMS host directly, set:

```bash
GATEWAY_IP=192.168.2.1
```

## 9. Real PPM-to-CMS deployment mode

When the real device is connected:

- PPM is on Ethernet and its exact address is discovered from live traffic
- CMS is reachable at 192.168.2.200
- the Pi acts as the AP/bridge and packet capture point
- the final protocol and port must come from the actual capture
- the Pi-side gateway is not required to sit in front of the CMS or redirect traffic there unless the capture proves that path is required

Do not configure the real device assuming UDP/5005. Instead:

1. connect the PPM to the Pi Ethernet
2. start tcpdump on br0 or any
3. observe the actual traffic
4. keep the Pi as the bridge/capture point unless the capture shows a different path is required
5. validate the dashboard receives data

## 10. Quick verification checklist

```text
[ ] PPM is connected over Ethernet and its address is confirmed from live traffic
[ ] Raspberry Pi bridge IP is 192.168.2.1/24
[ ] CMS is at 192.168.2.200/24 on Wi‑Fi
[ ] Wi‑Fi AP is active and the Pi/CMS side share one subnet
[ ] ping 192.168.2.1 from CMS succeeds
[ ] tcpdump captures live packets on br0 or any
[ ] captured traffic reveals the actual protocol and port
[ ] dashboard is reachable on http://192.168.2.1:3000
[ ] gateway uses the detected protocol and port only if required
[ ] telemetry appears in the dashboard and API
```

## 11. Start the stack on the Pi

The Wi‑Fi access point is already configured by RaspAP. This project handles bridge/network validation, packet capture, the Pi-side gateway helper, and the dashboard flow.

```bash
cd "/home/kk/ppm project"
sudo ./scripts/start_pi_stack.sh all
```

## 12. Stop services

Stop each process with Ctrl+C in its terminal.

If needed, check for stuck services:

```bash
pgrep -af 'laptop_server.py|rpi_gateway.py|dummy_ppm_sender.py' || true
```

## 13. One rule to keep forever

The project is not allowed to guess transport or port. The capture is the truth source.

If the packet capture shows a different protocol or destination, change the environment variables and rerun the gateway. The Pi bridge design stays the same; only the traffic details change.

## Files

- [scripts/rpi_gateway.py](scripts/rpi_gateway.py) — bridge/capture helper for telemetry inspection and optional forwarding
- [scripts/laptop_server.py](scripts/laptop_server.py) — dashboard and API on the Pi host
- [scripts/dummy_ppm_sender.py](scripts/dummy_ppm_sender.py) — test sender
- [agents.md](agents.md) — state and checkpoint record
- [setup.md](setup.md) — detailed deployment guide
