import os
import sys
import json
import time

# Ensure root directory is in Python path for importing modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env
from datetime import datetime, timedelta
import requests
import streamlit as st
import pandas as pd
import plotly.express as px
import threading
import uuid

# Try to import Azure Table client
try:
    from azure.data.tables import TableClient
    TABLES_AVAILABLE = True
except Exception:
    TABLES_AVAILABLE = False

# Try to import RAG Engine
try:
    from rag.rag_engine import LogRageEngine
    RAG_AVAILABLE = True
except Exception as e:
    RAG_AVAILABLE = False
    RAG_ERROR_DETAILS = str(e)

AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT")

# Streamlit App Styling
st.set_page_config(
    page_title="AutoHub SRE RAG Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# File path to store local logs & incidents for Mock Mode
LOCAL_LOGS_FILE = "local_logs.json"
LOCAL_INCIDENTS_FILE = "local_incidents.json"
LOCAL_WARNING_QUEUE_FILE = "local_warning_queue.json"

# Shared global variables for the background simulator thread
MOCK_BATCH_INTERVAL = 15 # default 15s for quick feedback
last_processed_count = 0
last_batch_time = time.time()

def load_local_incidents():
    if os.path.exists(LOCAL_INCIDENTS_FILE):
        try:
            with open(LOCAL_INCIDENTS_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_local_incidents(incidents):
    with open(LOCAL_INCIDENTS_FILE, 'w') as f:
        json.dump(incidents, f, indent=2)

def load_local_warnings_queue():
    if os.path.exists(LOCAL_WARNING_QUEUE_FILE):
        try:
            with open(LOCAL_WARNING_QUEUE_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_local_warnings_queue(warnings):
    with open(LOCAL_WARNING_QUEUE_FILE, 'w') as f:
        json.dump(warnings, f, indent=2)

def generate_mock_incident_report(log, severity, batch_count=1):
    service = log.get("service", "")
    message = log.get("message", "")
    timestamp = log.get("timestamp", "")
    req_id = log.get("request_id", "")
    status_code = log.get("status_code", 0)
    latency_ms = log.get("latency_ms", 0.0)
    
    citation = [{
        "timestamp": timestamp,
        "service": service,
        "level": log.get("level", "WARNING"),
        "message": message,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "request_id": req_id
    }]
    
    if severity == "CRITICAL":
        if "inventory" in service.lower() or "db query" in message.lower() or "timeout" in message.lower():
            answer = f"""### Root Cause Analysis: `/api/inventory` returning 503 Service Unavailable

A review of recent logs reveals that the **inventory-service** is failing health checks and returning HTTP 503 errors.

#### Timeline & Flow
1. **Trace Correlation**: Downstream gateway requests mapped to `ReqID: {req_id}` are throwing `503 Service Unavailable` with a high latency of ~5000ms.
2. **Root Cause**: The **inventory-service** logs show a critical database timeout:
   ```text
   DB Query failed: Database Connection Timeout on pool. Connection count exceeded maximum limit of 50 connections.
   ```
3. **Trigger**: This occurred following a simulated high-load event which exhausted the active database connection pool.

#### Recommendation
- **Immediate Action**: Restart the `inventory-service` container/pod to release locked database connections.
- **Permanent Fix**: Adjust the database connection pool configuration in the service environment variables (e.g. set `MAX_CONNECTIONS=100`) and implement connection pooling cleanups.
"""
        elif "auth" in service.lower() or "security" in message.lower() or "failed login" in message.lower():
            answer = f"""### Security Investigation: Brute-Force Authentication Attempt Detected

Operational logs contain multiple authentication alerts on `/api/auth/login`.

#### Event Timeline
- Over a span of 60 seconds, 5 consecutive failed login attempts were recorded for user `admin` resulting in `401 Unauthorized` responses.
- The **auth-service** triggered a security alert log:
  ```text
  SECURITY ALERT: Multiple failed login attempts (5+) detected on user 'admin' within 60 seconds. Triggering operational throttle.
  ```

#### Recommended Resolution
- Lock the account of user `admin` for 15 minutes.
- Check the source IP address in the Log Analytics gateway logs to verify if this is a DDoS or credential stuffing attack.
- Enable Multi-Factor Authentication (MFA) for administrative fleet logins.
"""
        else:
            answer = f"""### Critical Incident Detected: {service}

We detected a critical incident on service `{service}`.

#### Incident Details
- **Timestamp**: `{timestamp}`
- **Triggering Log**: `{message}`
- **Status Code**: `{status_code}`

#### Recommendation
- Check logs for container `{service}`.
- Verify down-stream database connections and service memory usage.
"""
    else:
        # WARNING
        if "valuation" in service.lower() or "connection exception" in message.lower() or "read timed out" in message.lower():
            answer = f"""### Root Cause Analysis: `/api/valuation` Gateway Timeout (502/504)

We detected a latency spike in `/api/valuation` leading to Gateway Timeout errors.

#### Analysis
- The **valuation-service** threw a connection exception:
  ```text
  Valuation failed: inventory-service connection exception: HTTPConnectionPool(host='inventory-service', port=8000): Read timed out.
  ```
- **Correlation**: The valuation service relies on fetching active car records from the `inventory-service`. Since `inventory-service` was running slowly or failing, `valuation-service` exceeded its HTTP timeout of 2.0 seconds.

#### Recommendation
- Check the health of downstream service `inventory-service`.
- Increase the HTTP connection timeout threshold or implement a circuit breaker (e.g. returning cached valuation pricing when the inventory service is unreachable).
"""
        else:
            answer = f"""### Warning Incident [Batch of {batch_count}]: {service}

We detected a recurring warning pattern ({batch_count} events) on service `{service}`.

#### Warning Details
- **Service**: `{service}`
- **Message**: `{message}`
- **Latency**: `{latency_ms}ms`

#### Recommendation
- Monitor `{service}` for error rate spikes.
- Check if resources need to scale or if request parameters are malformed.
"""
            
    return {
        "PartitionKey": "incidents",
        "RowKey": str(uuid.uuid4()),
        "timestamp": timestamp,
        "service": service,
        "severity": severity,
        "message": f"[Batch of {batch_count}] {message}" if batch_count > 1 else message,
        "answer": answer,
        "citations": json.dumps(citation)
    }

def run_local_simulation_loop():
    global last_processed_count, last_batch_time
    while True:
        try:
            time.sleep(1)
            logs = load_local_logs()
            
            # Reset logs trigger
            if len(logs) < last_processed_count:
                last_processed_count = 0
                if os.path.exists(LOCAL_INCIDENTS_FILE):
                    try: os.remove(LOCAL_INCIDENTS_FILE)
                    except: pass
                if os.path.exists(LOCAL_WARNING_QUEUE_FILE):
                    try: os.remove(LOCAL_WARNING_QUEUE_FILE)
                    except: pass
                continue

            if len(logs) > last_processed_count:
                new_logs = logs[last_processed_count:]
                last_processed_count = len(logs)
                
                for log in new_logs:
                    level = log.get("level", "INFO")
                    status_code = log.get("status_code", 0)
                    latency_ms = log.get("latency_ms", 0.0)
                    service = log.get("service", "")
                    message = log.get("message", "")
                    
                    if level in ["ERROR", "CRITICAL"] or status_code >= 500:
                        incident = generate_mock_incident_report(log, "CRITICAL")
                        incidents = load_local_incidents()
                        incidents.append(incident)
                        save_local_incidents(incidents)
                    elif level == "WARNING" or (400 <= status_code < 500) or latency_ms > 1000:
                        warn_queue = load_local_warnings_queue()
                        warn_queue.append(log)
                        save_local_warnings_queue(warn_queue)
            
            # Run warnings batch processing
            if time.time() - last_batch_time >= MOCK_BATCH_INTERVAL:
                last_batch_time = time.time()
                warn_queue = load_local_warnings_queue()
                if warn_queue:
                    deduped = {}
                    for warn in warn_queue:
                        key = (warn["service"], warn["message"])
                        if key not in deduped:
                            deduped[key] = []
                        deduped[key].append(warn)
                    
                    incidents = load_local_incidents()
                    for (service, message), group in deduped.items():
                        group.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                        latest_warn = group[0]
                        
                        incident = generate_mock_incident_report(latest_warn, "WARNING", batch_count=len(group))
                        incidents.append(incident)
                    
                    save_local_incidents(incidents)
                    save_local_warnings_queue([])
        except Exception:
            time.sleep(2)

def start_simulation_thread():
    thread_name = "mock_detector_thread"
    for thread in threading.enumerate():
        if thread.name == thread_name:
            return
    t = threading.Thread(target=run_local_simulation_loop, name=thread_name, daemon=True)
    t.start()


# Custom styles for premium dark-themed operational UI
st.markdown("""
<style>
    .stApp {
        background-color: #0b0f19;
        color: #f1f5f9;
    }
    .reportview-container .main .block-container {
        padding-top: 2rem;
    }
    h1 {
        font-family: 'Barlow Condensed', sans-serif;
        font-weight: 900;
        text-transform: uppercase;
        color: #f1f5f9;
        border-bottom: 2px solid #ff5722;
        padding-bottom: 0.5rem;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background: rgba(18, 22, 35, 0.6);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 10px;
        padding: 1.5rem;
        text-align: center;
    }
    .log-container {
        font-family: 'Courier New', Courier, monospace;
        background-color: #04070f;
        color: #39ff14;
        border-radius: 8px;
        padding: 1rem;
        height: 250px;
        overflow-y: scroll;
        border: 1px solid rgba(57, 255, 20, 0.2);
        margin-bottom: 1.5rem;
    }
    .log-line {
        margin-bottom: 0.25rem;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# Local logs storage helpers

def load_local_logs():
    if os.path.exists(LOCAL_LOGS_FILE):
        try:
            with open(LOCAL_LOGS_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_local_logs(logs):
    with open(LOCAL_LOGS_FILE, 'w') as f:
        json.dump(logs, f, indent=2)

def append_local_logs(new_logs):
    logs = load_local_logs()
    logs.extend(new_logs)
    # Keep only the last 200 logs to avoid disk bloat
    if len(logs) > 200:
        logs = logs[-200:]
    save_local_logs(logs)

# ─────────────────────────────────────────────
#  Local Mock Log Generator for Anomalies
# ─────────────────────────────────────────────
def generate_mock_logs(anomaly_type):
    ts = datetime.utcnow()
    logs = []
    req_id = str(uuid_uuid4()[:8])
    
    if anomaly_type == "db_locked":
        # Auth Service database connection error
        req_id_1 = str(uuid_uuid4()[:8])
        logs.append({
            "id": str(uuid_uuid4()),
            "timestamp": (ts - timedelta(seconds=15)).isoformat() + "Z",
            "service": "gateway",
            "level": "INFO",
            "message": f"GET /api/inventory -> PROXY TO inventory-service | ReqID: {req_id_1}",
            "latency_ms": 5005.2,
            "status_code": 503,
            "request_id": req_id_1
        })
        logs.append({
            "id": str(uuid_uuid4()),
            "timestamp": (ts - timedelta(seconds=14)).isoformat() + "Z",
            "service": "inventory-service",
            "level": "ERROR",
            "message": "DB Query failed: Database Connection Timeout on pool. Connection count exceeded maximum limit of 50 connections.",
            "latency_ms": 5000.0,
            "status_code": 503,
            "request_id": req_id_1
        })
        logs.append({
            "id": str(uuid_uuid4()),
            "timestamp": (ts - timedelta(seconds=5)).isoformat() + "Z",
            "service": "metrics-service",
            "level": "WARNING",
            "message": f"Health check probe failed for inventory-service on http://inventory-service:8000/health: Status 503 Service Unavailable",
            "latency_ms": 12.1,
            "status_code": 503,
            "request_id": ""
        })
        
    elif anomaly_type == "timeout":
        # Valuation Service network timeout calling inventory
        req_id_2 = str(uuid_uuid4()[:8])
        logs.append({
            "id": str(uuid_uuid4()),
            "timestamp": (ts - timedelta(seconds=20)).isoformat() + "Z",
            "service": "gateway",
            "level": "INFO",
            "message": f"GET /api/valuation -> PROXY TO valuation-service | ReqID: {req_id_2}",
            "latency_ms": 2012.4,
            "status_code": 502,
            "request_id": req_id_2
        })
        logs.append({
            "id": str(uuid_uuid4()),
            "timestamp": (ts - timedelta(seconds=19)).isoformat() + "Z",
            "service": "valuation-service",
            "level": "ERROR",
            "message": f"Valuation failed for all fleet: inventory-service connection exception: HTTPConnectionPool(host='inventory-service', port=8000): Read timed out. (read timeout=2.0)",
            "latency_ms": 2004.1,
            "status_code": 502,
            "request_id": req_id_2
        })
        
    elif anomaly_type == "brute_force":
        # Auth Service login failures
        for i in range(5):
            req_id_bf = str(uuid_uuid4()[:8])
            logs.append({
                "id": str(uuid_uuid4()),
                "timestamp": (ts - timedelta(seconds=i * 10)).isoformat() + "Z",
                "service": "auth-service",
                "level": "WARNING",
                "message": f"Failed login attempt for user: admin | ReqID: {req_id_bf}",
                "latency_ms": 1002.5,
                "status_code": 401,
                "request_id": req_id_bf
            })
            logs.append({
                "id": str(uuid_uuid4()),
                "timestamp": (ts - timedelta(seconds=i * 10 - 1)).isoformat() + "Z",
                "service": "gateway",
                "level": "INFO",
                "message": f"POST /api/auth/login -> 401 Unauthorized | ReqID: {req_id_bf}",
                "latency_ms": 1005.1,
                "status_code": 401,
                "request_id": req_id_bf
            })
        logs.append({
            "id": str(uuid_uuid4()),
            "timestamp": ts.isoformat() + "Z",
            "service": "auth-service",
            "level": "ERROR",
            "message": "SECURITY ALERT: Multiple failed login attempts (5+) detected on user 'admin' within 60 seconds. Triggering operational throttle.",
            "latency_ms": 0.0,
            "status_code": 429,
            "request_id": ""
        })
        
    else:
        # Normal Logs
        logs.append({
            "id": str(uuid_uuid4()),
            "timestamp": ts.isoformat() + "Z",
            "service": "gateway",
            "level": "INFO",
            "message": f"GET /api/inventory -> 200 OK | ReqID: {req_id}",
            "latency_ms": 14.2,
            "status_code": 200,
            "request_id": req_id
        })
        logs.append({
            "id": str(uuid_uuid4()),
            "timestamp": ts.isoformat() + "Z",
            "service": "inventory-service",
            "level": "INFO",
            "message": f"Car c1 fetched successfully | ReqID: {req_id}",
            "latency_ms": 5.4,
            "status_code": 200,
            "request_id": req_id
        })
        
    return logs

def uuid_uuid4():
    import uuid
    return str(uuid.uuid4())

# ─────────────────────────────────────────────
#  Sidebar Controls
# ─────────────────────────────────────────────
st.sidebar.markdown("<div style='text-align:center;'><h2 style='border-left:none; padding-left:0; font-size:1.8rem; color:#ff5722;'>AutoHub SRE Console</h2></div>", unsafe_allow_html=True)

# Azure Configurations
st.sidebar.subheader("🔌 Azure RAG Status")
gateway_url = st.sidebar.text_input("API Gateway URL", "http://localhost:5000")

azure_configured = False
if RAG_AVAILABLE and AZURE_OPENAI_API_KEY and AZURE_SEARCH_ENDPOINT:
    azure_configured = True
    st.sidebar.success("Azure Search & OpenAI Connected!")
else:
    st.sidebar.warning("Running in LOCAL MOCK Mode (No Azure keys)")
    st.sidebar.markdown("---")
    st.sidebar.subheader("⚙️ Hybrid Simulator Settings")
    mock_batch_interval = st.sidebar.slider(
        "Warning Batch Interval (s)",
        min_value=5,
        max_value=60,
        value=15,
        help="Interval for batching warning events in local simulator"
    )
    MOCK_BATCH_INTERVAL = mock_batch_interval

st.sidebar.markdown("---")
st.sidebar.subheader("🚨 Inject Failure Anomalies")
st.sidebar.info("Simulate typical microservice breakdowns and test the RAG SRE responder.")

active_anomaly = st.sidebar.radio(
    "Select Anomaly Scenario",
    ["Normal / Healthy Operations", "Database Connection Timeout (Inventory Service)", "Valuation Gateway Timeout (Network Failure)", "Brute-Force Login Security Alert"]
)

# Trigger anomaly HTTP injection
anomaly_mapping = {
    "Normal / Healthy Operations": "healthy",
    "Database Connection Timeout (Inventory Service)": "db_locked",
    "Valuation Gateway Timeout (Network Failure)": "timeout",
    "Brute-Force Login Security Alert": "brute_force"
}

def inject_anomaly_into_services(scenario):
    endpoint_suffix = {
        "db_locked": "/api/inventory/simulate-anomaly",
        "timeout": "/api/valuation/simulate-anomaly",
        "brute_force": "/api/auth/simulate-anomaly"
    }
    
    # Reset all services first
    for svc_name in ["inventory", "valuation", "auth"]:
        try:
            requests.post(f"{gateway_url}/api/{svc_name}/simulate-anomaly", json={"db_error": False, "db_locked": False, "network_error": False, "latency_ms": 0}, timeout=1.0)
        except: pass
        
    # Inject current anomaly
    if scenario != "healthy":
        svc_target = "inventory" if scenario == "db_locked" else ("valuation" if scenario == "timeout" else "auth")
        payload = {}
        if scenario == "db_locked":
            payload = {"db_error": True, "latency_ms": 5000}
        elif scenario == "timeout":
            payload = {"network_error": True, "latency_ms": 2000}
        elif scenario == "brute_force":
            payload = {"db_locked": True} # Locks database to fail logins
            
        try:
            r = requests.post(f"{gateway_url}/api/{svc_target}/simulate-anomaly", json=payload, timeout=2.0)
            if r.status_code == 200:
                st.sidebar.success(f"Anomaly '{scenario}' successfully injected into {svc_target}-service!")
        except Exception as ex:
            st.sidebar.error(f"Failed to reach microservice. Make sure docker-compose stack is running. Details: {str(ex)}")
            
    # Also generate logs for local logs buffer (useful for both Local Mock and live view)
    new_logs = generate_mock_logs(scenario)
    append_local_logs(new_logs)

if st.sidebar.button("Execute Ingestion / Anomaly Alert"):
    inject_anomaly_into_services(anomaly_mapping[active_anomaly])
    st.toast(f"Injected anomaly state: {active_anomaly}")

st.sidebar.markdown("---")
if st.sidebar.button("Reset In-Memory Databases & Logs"):
    if os.path.exists(LOCAL_LOGS_FILE):
        try: os.remove(LOCAL_LOGS_FILE)
        except: pass
    if os.path.exists(LOCAL_INCIDENTS_FILE):
        try: os.remove(LOCAL_INCIDENTS_FILE)
        except: pass
    if os.path.exists(LOCAL_WARNING_QUEUE_FILE):
        try: os.remove(LOCAL_WARNING_QUEUE_FILE)
        except: pass
    # Clear services states
    for svc_name in ["inventory", "valuation", "auth"]:
        try:
            requests.post(f"{gateway_url}/api/{svc_name}/simulate-anomaly", json={"db_error": False, "db_locked": False, "network_error": False, "latency_ms": 0}, timeout=1.0)
        except: pass
    st.sidebar.success("Telemetry logs database cleared!")

# ─────────────────────────────────────────────
#  Main Board Layout
# ─────────────────────────────────────────────
st.title("🛡️ AI-Powered DevOps Log Analyzer & Incident Responder")
st.markdown("A real-time operational RAG system connected to Azure Log Analytics, Event Hubs, and Azure AI Search.")

# Stats Rows (Mocked based on Active Anomaly to look premium)
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown('<div class="metric-card"><div class="stat-label">Active Microservices</div><div class="stat-val" style="color:#ffb300;">8 Services</div></div>', unsafe_allow_html=True)
with col2:
    if "Normal" in active_anomaly:
        st.markdown('<div class="metric-card"><div class="stat-label">Incident Alerts</div><div class="stat-val" style="color:#10b981;">0 Warnings</div></div>', unsafe_allow_html=True)
    elif "Security" in active_anomaly:
        st.markdown('<div class="metric-card"><div class="stat-label">Incident Alerts</div><div class="stat-val" style="color:#ef4444;">1 Security Alert</div></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="metric-card"><div class="stat-label">Incident Alerts</div><div class="stat-val" style="color:#ef4444;">1 Outage warning</div></div>', unsafe_allow_html=True)
with col3:
    if "Normal" in active_anomaly:
        st.markdown('<div class="metric-card"><div class="stat-label">System MTTR</div><div class="stat-val" style="color:#10b981;">Optimal</div></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="metric-card"><div class="stat-label">System MTTR</div><div class="stat-val" style="color:#ff5722;">9.4 Mins (Est)</div></div>', unsafe_allow_html=True)
with col4:
    if "Normal" in active_anomaly:
        st.markdown('<div class="metric-card"><div class="stat-label">Average API Latency</div><div class="stat-val" style="color:#10b981;">8.4 ms</div></div>', unsafe_allow_html=True)
    elif "Database" in active_anomaly:
        st.markdown('<div class="metric-card"><div class="stat-label">Average API Latency</div><div class="stat-val" style="color:#ef4444;">1240.2 ms</div></div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="metric-card"><div class="stat-label">Average API Latency</div><div class="stat-val" style="color:#ffb300;">85.1 ms</div></div>', unsafe_allow_html=True)

st.markdown("### 🚨 Proactive AIOps Incident Alerts")

incidents = []
if azure_configured and TABLES_AVAILABLE:
    try:
        incidents_table = TableClient.from_connection_string(
            conn_str=os.getenv("AZURE_STORAGE_CONNECTION_STRING"),
            table_name="incidents"
        )
        entities = list(incidents_table.list_entities())
        for e in entities:
            incidents.append({
                "timestamp": e.get("timestamp"),
                "service": e.get("service"),
                "severity": e.get("severity"),
                "message": e.get("message"),
                "answer": e.get("answer"),
                "citations": json.loads(e.get("citations", "[]"))
            })
    except Exception as e_table:
        st.error(f"Failed to fetch proactive incidents from Azure Table: {str(e_table)}")
else:
    local_inc = load_local_incidents()
    for e in local_inc:
        incidents.append({
            "timestamp": e.get("timestamp"),
            "service": e.get("service"),
            "severity": e.get("severity"),
            "message": e.get("message"),
            "answer": e.get("answer"),
            "citations": json.loads(e.get("citations")) if isinstance(e.get("citations"), str) else e.get("citations", [])
        })

# Sort: CRITICAL first, then newest timestamp
def sort_key(inc):
    sev_val = 0 if inc["severity"] == "CRITICAL" else 1
    return (sev_val, inc["timestamp"])

incidents.sort(key=sort_key)

if incidents:
    for idx, inc in enumerate(reversed(incidents)):
        severity = inc["severity"]
        symbol = "🔴" if severity == "CRITICAL" else "🟡"
        
        with st.expander(f"{symbol} [{inc['timestamp']}] {severity}: {inc['service']} — {inc['message'][:80]}"):
            st.markdown(inc["answer"])
            citations = inc.get("citations", [])
            if citations:
                with st.expander("📚 Cited Source Logs"):
                    for cit in citations:
                        cit_color = "red" if cit.get("level") == "ERROR" else ("orange" if cit.get("level") == "WARNING" else "green")
                        st.markdown(f"**[{cit.get('timestamp')}]** `:{cit_color}[{cit.get('level')}]` **{cit.get('service')}**: {cit.get('message')}")
else:
    st.info("No proactive incidents detected. Operational state is clean and healthy.")

st.markdown("### 📊 Ingested Live Log Stream")
# Read recent logs
all_logs = load_local_logs()
log_html = ""
if all_logs:
    # Reverse to show newest on top
    for log in reversed(all_logs[-20:]):
        color = "#10b981" # green
        if log["level"] == "ERROR":
            color = "#ef4444" # red
        elif log["level"] == "WARNING":
            color = "#ffb300" # yellow
            
        log_line = f"<div class='log-line'>[{log['timestamp']}] <span style='color:{color}; font-weight:bold;'>{log['level']}</span> - {log['service']} - {log['message']}</div>"
        log_html += log_line
else:
    log_html = "<div class='log-line' style='color:#94a3b8;'>No telemetry events captured. Use the sidebar to trigger log generation.</div>"

st.markdown(f'<div class="log-container">{log_html}</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────
#  AI SRE Chat Assistant
# ─────────────────────────────────────────────
st.markdown("### 💬 SRE Incident AI Scent-Responder")
st.markdown("Ask the operational RAG about errors, system health, and outages.")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if "citations" in message and message["citations"]:
            with st.expander("📚 Cited Source Logs"):
                for cit in message["citations"]:
                    color = "red" if cit["level"] == "ERROR" else ("orange" if cit["level"] == "WARNING" else "green")
                    st.markdown(f"**[{cit['timestamp']}]** `:{color}[{cit['level']}]` **{cit['service']}**: {cit['message']}")

# Quick suggestion queries
st.markdown("**Suggested operational queries:**")
q_col1, q_col2, q_col3 = st.columns(3)
quick_query = None
with q_col1:
    if st.button("Why is /api/inventory returning 503 errors?"):
        quick_query = "Why is /api/inventory returning 503 errors?"
with q_col2:
    if st.button("Explain what caused the valuation timeout."):
        quick_query = "Explain what caused the valuation timeout."
with q_col3:
    if st.button("Show all security warnings or failed logins."):
        quick_query = "Show all security warnings or failed logins."

# Chat input
if user_input := st.chat_input("Query incident logs...") or quick_query:
    query_to_run = user_input or quick_query
    
    # Add user message to history
    st.session_state.messages.append({"role": "user", "content": query_to_run})
    
    with st.chat_message("user"):
        st.markdown(query_to_run)
        
    with st.chat_message("assistant"):
        with st.spinner("Analyzing operational telemetry indices..."):
            # Initialize response variables
            answer = ""
            citations = []
            
            if azure_configured:
                # Run actual Azure RAG Engine
                try:
                    engine = LogRageEngine()
                    result = engine.run_query(query_to_run)
                    answer = result.get("answer")
                    citations = result.get("citations", [])
                except Exception as rag_err:
                    answer = f"Azure RAG Execution failed: {str(rag_err)}. Falling back to local responder."
                    azure_configured = False
            
            if not azure_configured:
                # Local Mock Mode Anomaly responses
                # We matches query words with the current active anomaly state
                time.sleep(1.5) # Simulate thinking latency
                
                # Retrieve active logs corresponding to user query
                relevant_logs = load_local_logs()
                
                if "503" in query_to_run.lower() or "inventory" in query_to_run.lower():
                    # Filter for inventory logs
                    citations = [l for l in relevant_logs if l["service"] in ["inventory-service", "gateway"]]
                    answer = """
### Root Cause Analysis: `/api/inventory` returning 503 Service Unavailable

A review of recent logs reveals that the **inventory-service** is failing health checks and returning HTTP 503 errors.

#### Timeline & Flow
1. **Trace Correlation**: Downstream gateway requests mapped to `ReqID: [various]` are throwing `503 Service Unavailable` with a high latency of ~5000ms.
2. **Root Cause**: The **inventory-service** logs show a critical database timeout:
   ```text
   DB Query failed: Database Connection Timeout on pool. Connection count exceeded maximum limit of 50 connections.
   ```
3. **Trigger**: This occurred following a simulated high-load event which exhausted the active database connection pool.

#### Recommendation
- **Immediate Action**: Restart the `inventory-service` container/pod to release locked database connections.
- **Permanent Fix**: Adjust the database connection pool configuration in the service environment variables (e.g. set `MAX_CONNECTIONS=100`) and implement connection pooling cleanups.
"""
                elif "valuation" in query_to_run.lower() or "timeout" in query_to_run.lower():
                    citations = [l for l in relevant_logs if l["service"] in ["valuation-service", "gateway"]]
                    answer = """
### Root Cause Analysis: `/api/valuation` Gateway Timeout (502/504)

We detected a latency spike in `/api/valuation` leading to Gateway Timeout errors.

#### Analysis
- The **valuation-service** threw a connection exception:
  ```text
  Valuation failed: inventory-service connection exception: HTTPConnectionPool(host='inventory-service', port=8000): Read timed out.
  ```
- **Correlation**: The valuation service relies on fetching active car records from the `inventory-service`. Since `inventory-service` was running slowly or failing, `valuation-service` exceeded its HTTP timeout of 2.0 seconds.

#### Recommendation
- Check the health of downstream service `inventory-service`.
- Increase the HTTP connection timeout threshold or implement a circuit breaker (e.g. returning cached valuation pricing when the inventory service is unreachable).
"""
                elif "security" in query_to_run.lower() or "login" in query_to_run.lower() or "auth" in query_to_run.lower():
                    citations = [l for l in relevant_logs if l["service"] in ["auth-service", "gateway"]]
                    answer = """
### Security Investigation: Brute-Force Authentication Attempt Detected

Operational logs contain multiple authentication alerts on `/api/auth/login`.

#### Event Timeline
- Over a span of 60 seconds, 5 consecutive failed login attempts were recorded for user `admin` resulting in `401 Unauthorized` responses.
- At `2026-05-24T11:00:00Z`, the **auth-service** triggered a security alert log:
  ```text
  SECURITY ALERT: Multiple failed login attempts (5+) detected on user 'admin' within 60 seconds. Triggering operational throttle.
  ```

#### Recommended Resolution
- Lock the account of user `admin` for 15 minutes.
- Check the source IP address in the Log Analytics gateway logs to verify if this is a DDoS or credential stuffing attack.
- Enable Multi-Factor Authentication (MFA) for administrative fleet logins.
"""
                else:
                    citations = relevant_logs[-5:] if relevant_logs else []
                    answer = """
### AutoHub Operations Summary

All systems are reporting healthy. 
- Log analytics telemetry is active.
- Uptime metrics verified.
- Fleet pricing, services, and policies are fully synced in Indian Rupees (₹).

If you are currently troubleshooting an incident, please select an anomaly scenario in the left sidebar to generate error traces.
"""
            
            st.markdown(answer)
            if citations:
                with st.expander("📚 Cited Source Logs"):
                    for cit in citations:
                        color = "red" if cit["level"] == "ERROR" else ("orange" if cit["level"] == "WARNING" else "green")
                        st.markdown(f"**[{cit['timestamp']}]** `:{color}[{cit['level']}]` **{cit['service']}**: {cit['message']}")
            
            # Save assistant message to history
            st.session_state.messages.append({"role": "assistant", "content": answer, "citations": citations})

# Start the background simulator thread after all functions and variables are loaded
start_simulation_thread()
