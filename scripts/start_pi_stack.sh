#!/usr/bin/env bash
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR" || exit 1

export PPM_PROTOCOL="${PPM_PROTOCOL:-udp}"
export PPM_LISTEN_PORT="${PPM_LISTEN_PORT:-5005}"
export CMS_IP="${CMS_IP:-192.168.2.200}"
export FORWARD_TO_CMS="${FORWARD_TO_CMS:-false}"
export DASHBOARD_API_IP="${DASHBOARD_API_IP:-127.0.0.1}"
export DASHBOARD_API_PORT="${DASHBOARD_API_PORT:-3000}"
export DEVICE_ID="${DEVICE_ID:-YK-8000C-001}"
export AP_INTERFACE="${AP_INTERFACE:-br0}"
export PPM_INTERFACE="${PPM_INTERFACE:-eth0}"
export CAPTURE_INTERFACE="${CAPTURE_INTERFACE:-br0}"
export PPM_ADDRESS="${PPM_ADDRESS:-192.168.2.1/24}"
export AP_SSID="${AP_SSID:-PPM-CMS-Network}"
export AP_PASSWORD="${AP_PASSWORD:-ppmtest123}"
export HTTP_HOST="${HTTP_HOST:-0.0.0.0}"
export HTTP_PORT="${HTTP_PORT:-3000}"
export GATEWAY_IP="${GATEWAY_IP:-192.168.2.200}"
export GATEWAY_PROTOCOL="${GATEWAY_PROTOCOL:-udp}"
export GATEWAY_PORT="${GATEWAY_PORT:-5005}"

log() {
  echo "[pi-stack] $*"
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must run with sudo."
    exit 1
  fi
}

start_ap() {
  log "RaspAP already manages the Wi‑Fi AP. No AP helper is required in this project."
  log "Bridge validation is done by tcpdump and network checks on the shared Pi bridge."
}

start_gateway() {
  log "Starting gateway..."
  PPM_PROTOCOL="$PPM_PROTOCOL" \
  PPM_LISTEN_PORT="$PPM_LISTEN_PORT" \
  CMS_IP="$CMS_IP" \
  FORWARD_TO_CMS="$FORWARD_TO_CMS" \
  DASHBOARD_API_IP="$DASHBOARD_API_IP" \
  DASHBOARD_API_PORT="$DASHBOARD_API_PORT" \
  python3 scripts/rpi_gateway.py > /tmp/ppm_gateway.log 2>&1 &
  echo $! > /tmp/ppm_gateway.pid
  log "Gateway PID: $(cat /tmp/ppm_gateway.pid)"
}

start_dashboard() {
  log "Starting dashboard..."
  HTTP_HOST="$HTTP_HOST" \
  HTTP_PORT="$HTTP_PORT" \
  HTTP_HOST="${HTTP_HOST:-0.0.0.0}" HTTP_PORT="${HTTP_PORT:-3000}" python3 scripts/laptop_server.py > /tmp/ppm_dashboard.log 2>&1 &
  echo $! > /tmp/ppm_dashboard.pid
  log "Dashboard PID: $(cat /tmp/ppm_dashboard.pid)"
}

stop_existing() {
  for pidfile in /tmp/ppm_gateway.pid /tmp/ppm_dashboard.pid; do
    if [[ -f "$pidfile" ]]; then
      pid=$(cat "$pidfile" 2>/dev/null || true)
      if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
      fi
    fi
  done
}

usage() {
  cat <<EOF
Usage:
  sudo ./scripts/start_pi_stack.sh [ap|gateway|dashboard|all]

Examples:
  sudo ./scripts/start_pi_stack.sh all
  sudo ./scripts/start_pi_stack.sh ap
  sudo ./scripts/start_pi_stack.sh gateway
EOF
}

case "${1:-all}" in
  ap)
    require_root
    start_ap
    ;;
  gateway)
    start_gateway
    ;;
  dashboard)
    start_dashboard
    ;;
  all)
    require_root
    stop_existing
    start_ap
    start_gateway
    start_dashboard
    log "Pi stack started."
    log "Dashboard: http://127.0.0.1:${HTTP_PORT}"
    log "CMS Wi-Fi target: ${CMS_IP}"
    log "Gateway target: ${PPM_PROTOCOL^^} ${CMS_IP}:${PPM_LISTEN_PORT}"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
 esac
