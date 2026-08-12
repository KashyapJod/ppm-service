// Workstation Application State
const state = {
    patients: {},          // patient_id -> patient details
    vitalsHistory: {},     // patient_id -> history array
    activeAlarms: [],      // list of active unacknowledged alarms
    selectedPatientId: null,
    ws: null,
    audioContext: null,
    alarmSoundInterval: null,
    
    // Toolbar configuration states
    audioMuted: false,
    audioPausedUntil: 0,   // timestamp
    ecgGain: 1.0,
    ecgSpeed: 25.0,
    gridVisible: true,
    
    // ECG Queues for live data
    ecgQueues: {}          // patient_id -> array of raw points
};

// Global focused ECG sweep renderer state
let focusedEcgRenderer = {
    canvas: null,
    ctx: null,
    points: [],
    writeIndex: 0
};

// Play audio alarms using Web Audio API
function playAlarmSound() {
    if (state.activeAlarms.length === 0 || state.audioMuted || Date.now() < state.audioPausedUntil) {
        return;
    }
    
    if (!state.audioContext) {
        state.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    
    if (state.audioContext.state === 'suspended') {
        state.audioContext.resume();
    }
    
    const critical = state.activeAlarms.some(a => a.severity === 'CRITICAL');
    const freq1 = critical ? 880 : 540;
    const freq2 = critical ? 780 : 440;
    
    const osc = state.audioContext.createOscillator();
    const gain = state.audioContext.createGain();
    
    osc.connect(gain);
    gain.connect(state.audioContext.destination);
    
    osc.type = 'sine';
    
    // Play alternating beep tones for critical state
    const t = state.audioContext.currentTime;
    osc.frequency.setValueAtTime(freq1, t);
    if (critical) {
        osc.frequency.setValueAtTime(freq2, t + 0.1);
    }
    
    gain.gain.setValueAtTime(0.08, t);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + (critical ? 0.25 : 0.35));
    
    osc.start();
    osc.stop(t + (critical ? 0.25 : 0.35));
}

// Clock Header
function startClock() {
    const clockEl = document.getElementById("clock");
    setInterval(() => {
        const now = new Date();
        clockEl.textContent = now.toLocaleTimeString();
    }, 1000);
}

// Cloud & Local Host Routing Configurations
const isLocal = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";

// Extract backend mapping via query parameters (e.g. ?backend=ppm-backend.onrender.com)
const urlParams = new URLSearchParams(window.location.search);
const paramBackend = urlParams.get("backend");
if (paramBackend) {
    localStorage.setItem("ppm_backend_host", paramBackend.replace(/^https?:\/\//, ""));
}

const cloudBackendHost = localStorage.getItem("ppm_backend_host") || "ppm-backend.onrender.com";

const API_BASE = isLocal 
    ? "" 
    : `https://${cloudBackendHost}`;

const WS_BASE = isLocal 
    ? `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}` 
    : `wss://${cloudBackendHost}`;

// REST Helpers
async function apiGet(endpoint) {
    try {
        const url = endpoint.startsWith("http") ? endpoint : `${API_BASE}${endpoint}`;
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        return await response.json();
    } catch (e) {
        console.error(`API Error reading from ${endpoint}:`, e);
        return null;
    }
}

async function apiPost(endpoint, body = {}) {
    try {
        const url = endpoint.startsWith("http") ? endpoint : `${API_BASE}${endpoint}`;
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        return await response.json();
    } catch (e) {
        console.error(`API Error posting to ${endpoint}:`, e);
        return null;
    }
}

// Render patient bed cards list in sidebar
function renderBedList(patientsData) {
    const container = document.getElementById("bed-list-container");
    if (!patientsData || patientsData.length === 0) {
        container.innerHTML = `<div class="loading-state">No beds active.</div>`;
        return;
    }
    
    let html = "";
    patientsData.forEach(p => {
        state.patients[p.patient_id] = p;
        if (!state.ecgQueues[p.patient_id]) {
            state.ecgQueues[p.patient_id] = [];
        }
        
        const activeClass = state.selectedPatientId === p.patient_id ? "active" : "";
        const vital = p.vitals || {};
        
        html += `
        <div class="bed-item ${activeClass}" id="bed-item-${p.patient_id}" onclick="selectPatient('${p.patient_id}')">
            <div class="bed-item-header">
                <span class="bed-item-num">${p.bed_number}</span>
                <span id="bed-status-dot-${p.patient_id}" class="status-dot" style="background-color: ${p.device?.status === 'ONLINE' ? 'var(--color-normal)' : 'var(--color-offline)'};"></span>
            </div>
            <div class="bed-item-name">${p.name}</div>
            <div class="bed-item-vitals">
                HR: <span class="text-cyan" id="bed-hr-${p.patient_id}">${vital.heart_rate || '--'}</span> |
                SpO2: <span class="text-orange" id="bed-spo2-${p.patient_id}">${vital.spo2 ? Math.round(vital.spo2) : '--'}%</span>
            </div>
        </div>
        `;
    });
    
    container.innerHTML = html;
}

// Swaps the main panel focus to the selected patient
async function selectPatient(patientId) {
    state.selectedPatientId = patientId;
    
    // Toggle active list items CSS class
    document.querySelectorAll(".bed-item").forEach(el => el.classList.remove("active"));
    const selectedEl = document.getElementById(`bed-item-${patientId}`);
    if (selectedEl) selectedEl.classList.add("active");
    
    const p = state.patients[patientId];
    if (!p) return;
    
    // Update headers and badges
    document.getElementById("focus-bed-label").textContent = p.bed_number;
    document.getElementById("focus-patient-name").textContent = p.name;
    document.getElementById("focus-patient-details").textContent = `Patient ID: ${p.patient_id} | Gender: ${p.gender} | Age: ${p.age} yrs`;
    
    // Clear old ECG drawing sweep line
    if (focusedEcgRenderer.points) {
        focusedEcgRenderer.points.fill(null);
        focusedEcgRenderer.writeIndex = 0;
    }
    
    // Render latest vitals
    const currentVitals = p.vitals || {};
    updateFocusedVitalsDisplay(currentVitals);
    
    // Set device online/offline badge
    updateFocusedDeviceBadge(p.device?.status || 'OFFLINE');
    
    // Reset ECG Lead selector to Lead II default
    const leadSelect = document.getElementById("ecg-lead");
    if (leadSelect) leadSelect.value = "II";
    
    // Fetch and render historical trends
    const history = await apiGet(`/api/history/${patientId}`);
    if (history) {
        state.vitalsHistory[patientId] = history;
        renderHistoricalCharts(history);
    }
    
    // Load alarm configurations, care notes, and alarm history
    loadPatientThresholds(patientId);
    loadPatientNotes(patientId);
    loadPatientAlarmsHistory(patientId);
    
    // Re-evaluate alarm border highlighting on main view
    refreshAlarmsUI();
}

function updateFocusedVitalsDisplay(vitals) {
    const hrVal = vitals.hr || vitals.heart_rate || '--';
    const spo2Val = vitals.spo2 !== undefined ? (vitals.spo2 % 1 === 0 ? vitals.spo2 : vitals.spo2.toFixed(1)) : '--';
    const sys = vitals.nibp_sys || '--';
    const dia = vitals.nibp_dia || '--';
    const tempVal = vitals.temp !== undefined ? vitals.temp.toFixed(1) : (vitals.temperature ? vitals.temperature.toFixed(1) : '--.-');
    const respVal = vitals.resp || vitals.respiration_rate || '--';
    
    document.getElementById("focus-val-hr").textContent = hrVal;
    document.getElementById("focus-val-spo2").textContent = spo2Val;
    document.getElementById("focus-val-nibp").textContent = `${sys}/${dia}`;
    document.getElementById("focus-val-temp").textContent = tempVal;
    document.getElementById("focus-val-resp").textContent = respVal;
}

function updateFocusedDeviceBadge(status) {
    const badge = document.getElementById("focus-device-badge");
    badge.className = `device-badge ${status.toLowerCase()}`;
    badge.querySelector(".status-text").textContent = status;
}

// Websocket Telemetry Handler
function connectWebSocket() {
    const wsUrl = `${WS_BASE}/ws/live`;
    console.log(`[WebSocket] Connecting to ${wsUrl}...`);
    
    const ws = new WebSocket(wsUrl);
    state.ws = ws;
    
    ws.onopen = () => {
        console.log("[WebSocket] Surveillance link connected.");
        startAlarmAudioLoop();
    };
    
    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        
        if (msg.type === "telemetry") {
            const pid = msg.patient_id;
            const vitals = msg.vitals;
            
            // Push incoming ECG points to client queue buffer
            if (!state.ecgQueues[pid]) {
                state.ecgQueues[pid] = [];
            }
            if (vitals.ecg_points) {
                state.ecgQueues[pid].push(...vitals.ecg_points);
            }
            
            // Keep latest vitals cached
            if (state.patients[pid]) {
                state.patients[pid].vitals = {
                    heart_rate: vitals.hr,
                    spo2: vitals.spo2,
                    nibp_sys: vitals.nibp_sys,
                    nibp_dia: vitals.nibp_dia,
                    temperature: vitals.temp,
                    respiration_rate: vitals.resp
                };
            }
            
            // Update sidebar mini vitals
            const sideHr = document.getElementById(`bed-hr-${pid}`);
            const sideSpo2 = document.getElementById(`bed-spo2-${pid}`);
            if (sideHr) sideHr.textContent = vitals.hr;
            if (sideSpo2) sideSpo2.textContent = Math.round(vitals.spo2) + "%";
            
            // If this is the active patient, update main vitals
            if (state.selectedPatientId === pid) {
                updateFocusedVitalsDisplay(vitals);
            }
        } else if (msg.type === "alarm_acknowledged") {
            const alarmId = msg.alarm_id;
            state.activeAlarms = state.activeAlarms.filter(a => a.alarm_id !== alarmId);
            refreshAlarmsUI();
        }
    };
    
    ws.onclose = () => {
        console.warn("[WebSocket] Connection lost. Reconnecting in 3s...");
        setTimeout(connectWebSocket, 3000);
    };
    
    ws.onerror = (err) => {
        console.error("[WebSocket] Interface error:", err);
    };
}

// Global 100Hz Focused ECG Canvas Sweep Loop
function initFocusedEcgRenderer() {
    const canvas = document.getElementById("focus-ecg-canvas");
    if (!canvas) return;
    
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    
    const width = rect.width;
    const height = rect.height;
    
    focusedEcgRenderer = {
        canvas: canvas,
        ctx: ctx,
        width: width,
        height: height,
        points: new Array(Math.floor(width)).fill(null),
        writeIndex: 0
    };
    
    function renderEcg() {
        if (!state.selectedPatientId) {
            requestAnimationFrame(renderEcg);
            return;
        }
        
        const q = state.ecgQueues[state.selectedPatientId] || [];
        const { ctx, width, height, points } = focusedEcgRenderer;
        
        // Sweep speed logic: translates to how many ECG points we plot per animation frame
        // 25 mm/s (standard) -> 2 points/frame (120 Hz)
        // 12.5 mm/s -> 1 point/frame (60 Hz)
        // 50 mm/s -> 4 points/frame (240 Hz)
        let pointsToDraw = 2;
        if (state.ecgSpeed === 12.5) pointsToDraw = 1;
        else if (state.ecgSpeed === 50.0) pointsToDraw = 4;
        
        // Constrain drawing speed by points in queue to prevent buffer drift
        const actualPointsToDraw = Math.min(q.length, pointsToDraw);
        
        for (let i = 0; i < actualPointsToDraw; i++) {
            const val = q.shift();
            // Scale and map value on Y axis using the toolbar amplitude gain multiplier
            const scaledY = height / 2 - (val * state.ecgGain * (height / 250));
            points[focusedEcgRenderer.writeIndex] = scaledY;
            
            focusedEcgRenderer.writeIndex = (focusedEcgRenderer.writeIndex + 1) % points.length;
        }
        
        // Background clear
        ctx.fillStyle = '#040810';
        ctx.fillRect(0, 0, width, height);
        
        // Draw grid lines if toggle is on (faint clinical red grid)
        if (state.gridVisible) {
            ctx.strokeStyle = 'rgba(255, 42, 95, 0.08)';
            ctx.lineWidth = 0.5;
            
            // Draw fine vertical lines
            for (let x = 0; x < width; x += 15) {
                ctx.beginPath();
                ctx.moveTo(x, 0);
                ctx.lineTo(x, height);
                ctx.stroke();
            }
            // Draw fine horizontal lines
            for (let y = 0; y < height; y += 15) {
                ctx.beginPath();
                ctx.moveTo(0, y);
                ctx.lineTo(width, y);
                ctx.stroke();
            }
        }
        
        // Render sweep line trace
        ctx.strokeStyle = '#00f0ff';
        ctx.lineWidth = 2.0;
        ctx.shadowBlur = 5;
        ctx.shadowColor = '#00f0ff';
        ctx.beginPath();
        
        const gapSize = 20; // width gap ahead of scan sweep dot
        let first = true;
        
        for (let i = 0; i < points.length; i++) {
            const dist = (i - focusedEcgRenderer.writeIndex + points.length) % points.length;
            if (dist < gapSize) {
                continue; // Skip drawing gap zone
            }
            
            const y = points[i];
            if (y === null) continue;
            
            if (first) {
                ctx.moveTo(i, y);
                first = false;
            } else {
                ctx.lineTo(i, y);
            }
        }
        ctx.stroke();
        ctx.shadowBlur = 0; // reset
        
        // Draw trailing sweep dot
        const headIdx = (focusedEcgRenderer.writeIndex - 1 + points.length) % points.length;
        const headY = points[headIdx];
        if (headY !== null) {
            ctx.fillStyle = '#00ff66';
            ctx.beginPath();
            ctx.arc(headIdx, headY, 3.5, 0, 2 * Math.PI);
            ctx.fill();
        }
        
        requestAnimationFrame(renderEcg);
    }
    
    renderEcg();
}

// System Diagnostics and Stats Polling
async function pollDiagnostics() {
    const health = await apiGet("/api/system_health");
    if (!health) return;
    
    document.getElementById("diag-db-vitals").textContent = `${health.total_vital_records} records`;
    document.getElementById("diag-db-packets").textContent = `${health.total_communication_logs} packets`;
    document.getElementById("diag-active-alarms").textContent = `${health.active_unacknowledged_alarms} active`;
    
    if (health.last_captured_packet) {
        const date = new Date(health.last_captured_packet);
        document.getElementById("diag-last-packet").textContent = date.toLocaleTimeString();
    }
    
    // Status listing
    const devList = document.getElementById("device-status-list");
    let html = "";
    health.devices.forEach(d => {
        const cls = d.status === 'ONLINE' ? 'text-green' : 'text-offline';
        const hb = d.last_heartbeat ? new Date(d.last_heartbeat).toLocaleTimeString() : 'Never';
        html += `
        <div class="device-status-item">
            <span class="dev-name">${d.device_id} (${d.ip_address || 'Unset'})</span>
            <span class="dev-status ${cls}">${d.status} <span style="font-size: 0.65rem; color:#8a9fc4;">(${hb})</span></span>
        </div>
        `;
        
        // Update status dot of beds in selector sidebar
        Object.values(state.patients).forEach(p => {
            if (p.device?.device_id === d.device_id) {
                const dot = document.getElementById(`bed-status-dot-${p.patient_id}`);
                if (dot) {
                    dot.style.backgroundColor = d.status === 'ONLINE' ? 'var(--color-normal)' : 'var(--color-offline)';
                }
            }
        });
    });
    devList.innerHTML = html;
}

// Poll hexadecimal packet logs
async function pollCommunicationLogs() {
    const logs = await apiGet("/api/comm_logs");
    const feed = document.getElementById("logs-feed");
    
    if (!logs || logs.length === 0) {
        feed.innerHTML = `<div class="log-placeholder">Listening for incoming hexadecimal packet streams...</div>`;
        return;
    }
    
    let html = "";
    logs.forEach(l => {
        const timeStr = new Date(l.timestamp).toLocaleTimeString();
        html += `
        <div class="log-entry">
            <span class="time">[${timeStr}]</span> 
            <span class="meta">${l.source_ip} -> ${l.dest_ip} (${l.protocol} ${l.packet_size}B) [${l.log_type}]</span>
            <div>HEX: ${l.raw_hex.substring(0, 48)}...</div>
        </div>
        `;
    });
    feed.innerHTML = html;
}

// Active Alarms Surveillance
async function pollAlarms() {
    const alarms = await apiGet("/api/alarms");
    if (alarms) {
        state.activeAlarms = alarms;
        refreshAlarmsUI();
    }
}

function refreshAlarmsUI() {
    const container = document.getElementById("alarm-banner-container");
    const messageEl = document.getElementById("alarm-message");
    const ackBtn = document.getElementById("ack-alarm-btn");
    const mainContentCard = document.getElementById("focused-bed-container");
    
    // 1. Reset all card alarm visual highlights
    document.querySelectorAll(".bed-item").forEach(el => {
        el.classList.remove("alarm-critical-flash", "alarm-warning-flash");
    });
    if (mainContentCard) {
        mainContentCard.classList.remove("alarm-critical-focused", "alarm-warning-focused");
    }
    
    if (state.activeAlarms.length === 0) {
        container.classList.add("hidden");
        return;
    }
    
    // Sort critical severity alarms first
    state.activeAlarms.sort((a, b) => {
        if (a.severity === 'CRITICAL' && b.severity !== 'CRITICAL') return -1;
        if (a.severity !== 'CRITICAL' && b.severity === 'CRITICAL') return 1;
        return 0;
    });
    
    // 2. Display the primary alarm banner at top of workspace
    const primaryAlarm = state.activeAlarms[0];
    messageEl.textContent = `Patient ${primaryAlarm.patient_name} (${primaryAlarm.bed_number}): ${primaryAlarm.message}`;
    ackBtn.onclick = () => acknowledgeAlarm(primaryAlarm.alarm_id);
    container.classList.remove("hidden");
    
    // 3. Highlight alarming beds in sidebar list
    state.activeAlarms.forEach(a => {
        const item = document.getElementById(`bed-item-${a.patient_id}`);
        if (item) {
            const cls = a.severity === 'CRITICAL' ? 'alarm-critical-flash' : 'alarm-warning-flash';
            item.classList.add(cls);
        }
        
        // Highlight active pane if focused patient is alarming
        if (state.selectedPatientId === a.patient_id && mainContentCard) {
            const focusedCls = a.severity === 'CRITICAL' ? 'alarm-critical-focused' : 'alarm-warning-focused';
            mainContentCard.classList.add(focusedCls);
        }
    });
}

async function acknowledgeAlarm(alarmId) {
    const res = await apiPost(`/api/alarms/${alarmId}/acknowledge`);
    if (res && res.status === "success") {
        state.activeAlarms = state.activeAlarms.filter(a => a.alarm_id !== alarmId);
        refreshAlarmsUI();
    }
}

// Load patient alarm thresholds
async function loadPatientThresholds(patientId) {
    const thresh = await apiGet(`/api/patients/${patientId}/thresholds`);
    if (thresh) {
        document.getElementById("limit-hr-low").value = thresh.hr_low;
        document.getElementById("limit-hr-high").value = thresh.hr_high;
        document.getElementById("limit-spo2-low").value = Math.round(thresh.spo2_low);
        document.getElementById("limit-temp-high").value = thresh.temp_high.toFixed(1);
    }
}

// Update patient alarm thresholds
async function savePatientThresholds() {
    if (!state.selectedPatientId) return;
    const hr_low = parseInt(document.getElementById("limit-hr-low").value);
    const hr_high = parseInt(document.getElementById("limit-hr-high").value);
    const spo2_low = parseFloat(document.getElementById("limit-spo2-low").value);
    const temp_high = parseFloat(document.getElementById("limit-temp-high").value);
    
    const btn = document.getElementById("save-limits-btn");
    btn.textContent = "SAVING...";
    
    const res = await apiPost(`/api/patients/${state.selectedPatientId}/thresholds`, {
        hr_low, hr_high, spo2_low, temp_high
    });
    
    if (res && res.status === "success") {
        btn.textContent = "APPLIED";
        btn.style.backgroundColor = "rgba(0, 230, 118, 0.4)";
        setTimeout(() => {
            btn.textContent = "APPLY ALARM LIMITS";
            btn.style.backgroundColor = "";
        }, 1500);
    } else {
        btn.textContent = "FAILED";
        btn.style.backgroundColor = "rgba(255, 42, 95, 0.4)";
        setTimeout(() => {
            btn.textContent = "APPLY ALARM LIMITS";
            btn.style.backgroundColor = "";
        }, 1500);
    }
}

// Load patient care notes
async function loadPatientNotes(patientId) {
    const notes = await apiGet(`/api/patients/${patientId}/notes`);
    const container = document.getElementById("notes-list-container");
    if (!notes || notes.length === 0) {
        container.innerHTML = `<div class="notes-placeholder">No care notes logged.</div>`;
        return;
    }
    
    let html = "";
    notes.forEach(n => {
        const timeStr = new Date(n.timestamp).toLocaleTimeString();
        html += `
        <div class="note-item">
            <div class="note-item-meta">
                <span class="author">${escapeHtml(n.author)}</span>
                <span class="time">${timeStr}</span>
            </div>
            <div class="note-item-text">${escapeHtml(n.note)}</div>
        </div>
        `;
    });
    container.innerHTML = html;
}

// Add a patient care note
async function savePatientNote() {
    if (!state.selectedPatientId) return;
    const authorEl = document.getElementById("note-author");
    const noteEl = document.getElementById("note-text");
    const author = authorEl.value.trim() || "Anonymous Clinician";
    const note = noteEl.value.trim();
    
    if (!note) {
        alert("Please enter note content.");
        return;
    }
    
    const btn = document.getElementById("save-note-btn");
    btn.textContent = "SAVING...";
    
    const res = await apiPost(`/api/patients/${state.selectedPatientId}/notes`, { author, note });
    if (res && res.status === "success") {
        noteEl.value = ""; // clear text
        btn.textContent = "SAVED";
        btn.style.backgroundColor = "rgba(0, 230, 118, 0.4)";
        await loadPatientNotes(state.selectedPatientId);
        setTimeout(() => {
            btn.textContent = "SAVE BEDSIDE NOTE";
            btn.style.backgroundColor = "";
        }, 1500);
    } else {
        btn.textContent = "FAILED";
        btn.style.backgroundColor = "rgba(255, 42, 95, 0.4)";
        setTimeout(() => {
            btn.textContent = "SAVE BEDSIDE NOTE";
            btn.style.backgroundColor = "";
        }, 1500);
    }
}

// Load patient alarms history
async function loadPatientAlarmsHistory(patientId) {
    const alarms = await apiGet(`/api/patients/${patientId}/alarms/history`);
    const tbody = document.getElementById("alarm-audit-tbody");
    if (!alarms || alarms.length === 0) {
        tbody.innerHTML = `
        <tr>
            <td colspan="5" class="table-placeholder">No alarm history records found.</td>
        </tr>
        `;
        return;
    }
    
    let html = "";
    alarms.forEach(a => {
        const timeStr = new Date(a.timestamp).toLocaleString();
        const severityClass = `severity-${a.severity.toLowerCase()}`;
        const statusClass = a.is_acknowledged ? "status-ack" : "status-active";
        const statusText = a.is_acknowledged ? "Acknowledged" : "ACTIVE ALARM";
        
        html += `
        <tr>
            <td>${timeStr}</td>
            <td><strong>${a.alarm_type}</strong></td>
            <td><span class="${severityClass}">${a.severity}</span></td>
            <td>${escapeHtml(a.message)}</td>
            <td><span class="${statusClass}">${statusText}</span></td>
        </tr>
        `;
    });
    tbody.innerHTML = html;
}

// Helper to escape HTML tags to prevent XSS
function escapeHtml(text) {
    if (!text) return "";
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function startAlarmAudioLoop() {
    if (state.alarmSoundInterval) return;
    state.alarmSoundInterval = setInterval(() => {
        if (state.activeAlarms.length > 0) {
            playAlarmSound();
        }
    }, 1200);
}

// Inline Vitals Trends Chart Drawing on Canvas
async function refreshVitalsTrends() {
    if (!state.selectedPatientId) return;
    const history = await apiGet(`/api/history/${state.selectedPatientId}`);
    if (history) {
        state.vitalsHistory[state.selectedPatientId] = history;
        renderHistoricalCharts(history);
    }
}

function renderHistoricalCharts(history) {
    if (!history || history.length === 0) return;
    
    drawSingleChart("focus-chart-hr", history.map(h => h.heart_rate), "BPM", "#00f0ff", 40, 140);
    drawSingleChart("focus-chart-spo2", history.map(h => parseFloat(h.spo2)), "%", "#ff9100", 70, 100);
    drawDoubleChart("focus-chart-nibp", history.map(h => h.nibp_sys), history.map(h => h.nibp_dia), "mmHg", "#ffea00", "#ff5252", 40, 180);
    drawSingleChart("focus-chart-temp", history.map(h => parseFloat(h.temperature)), "°C", "#00e676", 35.0, 41.0);
}

function drawSingleChart(canvasId, data, unit, color, yMin, yMax) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#0c101d";
    ctx.fillRect(0, 0, width, height);
    
    ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
    ctx.lineWidth = 1;
    ctx.font = "10px Outfit";
    ctx.fillStyle = "#8a9fc4";
    
    const numGrids = 3;
    for (let i = 0; i <= numGrids; i++) {
        const y = 15 + (i / numGrids) * (height - 30);
        ctx.beginPath();
        ctx.moveTo(35, y);
        ctx.lineTo(width - 15, y);
        ctx.stroke();
        
        const val = yMax - (i / numGrids) * (yMax - yMin);
        ctx.fillText(val.toFixed(1), 4, y + 4);
    }
    
    if (data.length < 2) return;
    
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    
    const getX = (index) => 40 + (index / (data.length - 1)) * (width - 65);
    const getY = (val) => {
        if (val === null || val === undefined || isNaN(val)) return height - 15;
        const clamped = Math.max(yMin, Math.min(yMax, val));
        return height - 15 - ((clamped - yMin) / (yMax - yMin)) * (height - 30);
    };
    
    for (let i = 0; i < data.length; i++) {
        const x = getX(i);
        const y = getY(data[i]);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    }
    ctx.stroke();
    
    data.forEach((val, i) => {
        if (val === null || val === undefined || isNaN(val)) return;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(getX(i), getY(val), 2.5, 0, 2 * Math.PI);
        ctx.fill();
    });
}

function drawDoubleChart(canvasId, sysData, diaData, unit, sysColor, diaColor, yMin, yMax) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#0c101d";
    ctx.fillRect(0, 0, width, height);
    
    ctx.strokeStyle = "rgba(255, 255, 255, 0.05)";
    ctx.lineWidth = 1;
    ctx.font = "10px Outfit";
    ctx.fillStyle = "#8a9fc4";
    
    const numGrids = 3;
    for (let i = 0; i <= numGrids; i++) {
        const y = 15 + (i / numGrids) * (height - 30);
        ctx.beginPath();
        ctx.moveTo(35, y);
        ctx.lineTo(width - 15, y);
        ctx.stroke();
        
        const val = yMax - (i / numGrids) * (yMax - yMin);
        ctx.fillText(Math.round(val), 4, y + 4);
    }
    
    const getX = (index) => 40 + (index / (sysData.length - 1)) * (width - 65);
    const getY = (val) => {
        if (val === null || val === undefined || isNaN(val)) return height - 15;
        const clamped = Math.max(yMin, Math.min(yMax, val));
        return height - 15 - ((clamped - yMin) / (yMax - yMin)) * (height - 30);
    };
    
    // Sys line
    if (sysData.length >= 2) {
        ctx.strokeStyle = sysColor;
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        for (let i = 0; i < sysData.length; i++) {
            const x = getX(i);
            const y = getY(sysData[i]);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();
        
        sysData.forEach((val, i) => {
            if (val === null || val === undefined || isNaN(val)) return;
            ctx.fillStyle = sysColor;
            ctx.beginPath();
            ctx.arc(getX(i), getY(val), 2.5, 0, 2 * Math.PI);
            ctx.fill();
        });
    }
    
    // Dia line
    if (diaData.length >= 2) {
        ctx.strokeStyle = diaColor;
        ctx.lineWidth = 1.8;
        ctx.beginPath();
        for (let i = 0; i < diaData.length; i++) {
            const x = getX(i);
            const y = getY(diaData[i]);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();
        
        diaData.forEach((val, i) => {
            if (val === null || val === undefined || isNaN(val)) return;
            ctx.fillStyle = diaColor;
            ctx.beginPath();
            ctx.arc(getX(i), getY(val), 2.5, 0, 2 * Math.PI);
            ctx.fill();
        });
    }
}

// Inject Condition via API Call
async function injectCondition() {
    if (!state.selectedPatientId) return;
    const p = state.patients[state.selectedPatientId];
    if (!p || !p.device?.device_id) return;
    
    const condSelect = document.getElementById("condition-select");
    const condition = condSelect.value;
    
    const res = await apiPost("/api/simulate_condition", {
        device_id: p.device.device_id,
        condition: condition
    });
    
    if (res && res.status === "success") {
        console.log(`[Toolbar] Injected condition ${condition} to ${p.device.device_id}`);
        // Visual indicator on Inject button
        const btn = document.getElementById("inject-btn");
        btn.textContent = "INJECTED";
        btn.style.backgroundColor = "rgba(0, 230, 118, 0.4)";
        setTimeout(() => {
            btn.textContent = "INJECT";
            btn.style.backgroundColor = "";
        }, 1500);
    }
}

// Export trends history to CSV
function exportToCSV() {
    if (!state.selectedPatientId) return;
    const history = state.vitalsHistory[state.selectedPatientId] || [];
    if (history.length === 0) {
        alert("No trend records available for this patient yet.");
        return;
    }
    
    const p = state.patients[state.selectedPatientId];
    
    let csvContent = "data:text/csv;charset=utf-8,";
    csvContent += "Timestamp,Heart Rate (BPM),SpO2 (%),Systolic BP (mmHg),Diastolic BP (mmHg),Temp (C),Respiration Rate (/min)\n";
    
    history.forEach(h => {
        const timeStr = new Date(h.timestamp).toISOString();
        csvContent += `${timeStr},${h.heart_rate},${h.spo2},${h.nibp_sys},${h.nibp_dia},${h.temperature},${h.respiration_rate}\n`;
    });
    
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `Vitals_History_${p.bed_number}_${p.name.replace(/\s+/g, '_')}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// Attach control buttons in Toolbar
function initToolbar() {
    const muteBtn = document.getElementById("mute-audio-btn");
    const pauseBtn = document.getElementById("pause-audio-btn");
    const injectBtn = document.getElementById("inject-btn");
    const gainSelect = document.getElementById("ecg-gain");
    const speedSelect = document.getElementById("ecg-speed");
    const gridBtn = document.getElementById("toggle-grid-btn");
    const exportBtn = document.getElementById("export-csv-btn");
    
    // Mute alarm sound toggle
    muteBtn.onclick = () => {
        state.audioMuted = !state.audioMuted;
        muteBtn.textContent = state.audioMuted ? "🔇 AUDIO MUTED" : "🔊 AUDIO ALARM";
        muteBtn.className = state.audioMuted ? "tool-btn muted" : "tool-btn";
    };
    
    // Pause alarm silence for 60s
    pauseBtn.onclick = () => {
        state.audioPausedUntil = Date.now() + 60000;
        pauseBtn.textContent = "⏸️ SILENCED";
        pauseBtn.className = "tool-btn active";
        
        setTimeout(() => {
            pauseBtn.textContent = "⏸️ SILENCE (60s)";
            pauseBtn.className = "tool-btn";
        }, 60000);
    };
    
    // Inject selected condition to simulator
    injectBtn.onclick = injectCondition;
    
    // ECG custom drawing settings
    gainSelect.onchange = function() {
        state.ecgGain = parseFloat(this.value);
    };
    
    speedSelect.onchange = function() {
        state.ecgSpeed = parseFloat(this.value);
    };
    
    // Toggle grid lines visibility
    gridBtn.onclick = () => {
        state.gridVisible = !state.gridVisible;
        gridBtn.textContent = state.gridVisible ? "GRID ON" : "GRID OFF";
        if (state.gridVisible) {
            gridBtn.classList.add("active");
        } else {
            gridBtn.classList.remove("active");
        }
    };
    
    // Export CSV
    exportBtn.onclick = exportToCSV;
    
    // ECG Lead selector change event
    const leadSelect = document.getElementById("ecg-lead");
    leadSelect.onchange = async function() {
        if (!state.selectedPatientId) return;
        const p = state.patients[state.selectedPatientId];
        if (!p || !p.device?.device_id) return;
        
        const cmd = "LEAD_" + this.value;
        const res = await apiPost("/api/simulate_condition", {
            device_id: p.device.device_id,
            condition: cmd
        });
        if (res && res.status === "success") {
            console.log(`[Toolbar] ECG Lead command '${cmd}' sent.`);
        }
    };
    
    // Threshold config and Bedside Notes save click events
    document.getElementById("save-limits-btn").onclick = savePatientThresholds;
    document.getElementById("save-note-btn").onclick = savePatientNote;
}

// App Entry Point
async function initApp() {
    startClock();
    
    // Fetch and draw beds list
    const patientsData = await apiGet("/api/patients");
    if (patientsData && patientsData.length > 0) {
        state.selectedPatientId = patientsData[0].patient_id;
        renderBedList(patientsData);
        await selectPatient(state.selectedPatientId);
    }
    
    // Start diagnostics, logs and alarms loops
    pollDiagnostics();
    pollAlarms();
    pollCommunicationLogs();
    
    setInterval(pollDiagnostics, 3000);
    setInterval(pollAlarms, 2000);
    setInterval(pollCommunicationLogs, 2000);
    setInterval(refreshVitalsTrends, 5000); // refresh trend chart data
    
    // Periodically refresh note listings and alarm history for the active patient
    setInterval(() => {
        if (state.selectedPatientId) {
            loadPatientNotes(state.selectedPatientId);
            loadPatientAlarmsHistory(state.selectedPatientId);
        }
    }, 4000);
    
    // Setup toolbar control bindings
    initToolbar();
    
    // Start WebSockets Live Stream
    connectWebSocket();
    
    // Start ECG sweeper drawing loop
    initFocusedEcgRenderer();
    
    // Audio activation trigger
    document.body.addEventListener('click', () => {
        if (state.audioContext && state.audioContext.state === 'suspended') {
            state.audioContext.resume();
        }
    }, { once: true });
}

window.onload = initApp;
