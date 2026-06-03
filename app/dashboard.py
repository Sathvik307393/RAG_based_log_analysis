import os
import sys
import json
import time

# AutoHub SRE Control Center & Dashboard (Pushed to main for verification)

# Ensure root directory is in Python path for importing modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(override=True)  # Load environment variables from .env and override cached process envs
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
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT") or os.getenv("AZURE_SEARCH_SERVICE_ENDPOINT")

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


# Inject Font Awesome via a proper link tag (CSS @import is stripped by Streamlit)
st.markdown("""
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css" crossorigin="anonymous" />
""", unsafe_allow_html=True)

# Custom styles for premium dark-themed operational UI
st.markdown("""
<style>
    /* Premium dark-themed operational UI backdrop */
    .stApp {
        background: radial-gradient(circle at 50% 50%, #0e1322, #040712);
        color: #f1f5f9;
        font-family: 'Outfit', 'Inter', system-ui, sans-serif;
    }
    .reportview-container .main .block-container {
        padding-top: 2rem;
    }
    h1 {
        font-family: 'Outfit', 'Barlow Condensed', sans-serif;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #ffffff;
        border-bottom: 2px solid #ff5722;
        padding-bottom: 0.75rem;
        margin-bottom: 1.75rem;
        text-shadow: 0 0 15px rgba(255, 87, 34, 0.2);
    }
    
    /* Sleek Custom Tabs Styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: rgba(15, 23, 42, 0.35);
        padding: 6px 10px;
        border-radius: 12px;
        border: 1px solid rgba(255, 255, 255, 0.05);
        margin-bottom: 1.5rem;
    }
    .stTabs [data-baseweb="tab"] {
        height: 40px;
        white-space: pre-wrap;
        background-color: transparent;
        border-radius: 8px;
        color: #94a3b8;
        font-weight: 600;
        font-size: 0.85rem;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        padding: 0 16px;
        border: none !important;
    }
    .stTabs [data-baseweb="tab"]:hover {
        background-color: rgba(255, 255, 255, 0.03);
        color: #ffffff;
    }
    .stTabs [aria-selected="true"] {
        background-color: rgba(99, 102, 241, 0.15) !important;
        color: #818cf8 !important;
        border: 1px solid rgba(99, 102, 241, 0.3) !important;
        font-weight: 700;
    }
    
    /* custom warning/success alerts */
    .custom-alert {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        background: rgba(15, 23, 42, 0.4);
        backdrop-filter: blur(8px);
        border-radius: 8px;
        padding: 0.75rem 1rem;
        margin-bottom: 1.25rem;
        font-size: 0.85rem;
    }
    .custom-alert.success {
        border-left: 4px solid #10b981;
        border: 1px solid rgba(16, 185, 129, 0.15);
        background: rgba(16, 185, 129, 0.05);
        color: #cbd5e1;
    }
    .custom-alert.warning {
        border-left: 4px solid #fbbf24;
        border: 1px solid rgba(251, 191, 36, 0.15);
        background: rgba(251, 191, 36, 0.05);
        color: #cbd5e1;
    }
    .alert-icon {
        font-size: 1.1rem;
    }
    .alert-content code {
        background: rgba(255, 255, 255, 0.08);
        padding: 2px 6px;
        border-radius: 4px;
        font-family: monospace;
    }

    /* Metric Cards */
    .metric-card {
        background: rgba(15, 23, 42, 0.5);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 12px;
        padding: 1.25rem;
        text-align: center;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
    }
    .metric-card:hover {
        transform: translateY(-3px);
        border-color: rgba(255, 255, 255, 0.12);
        box-shadow: 0 8px 25px rgba(0, 0, 0, 0.25);
    }
    .metric-card.blue {
        border-top: 3px solid #3b82f6;
        box-shadow: 0 4px 20px rgba(59, 130, 246, 0.03);
    }
    .metric-card.blue:hover {
        border-color: rgba(59, 130, 246, 0.3);
    }
    .metric-card.green {
        border-top: 3px solid #10b981;
        box-shadow: 0 4px 20px rgba(16, 185, 129, 0.03);
    }
    .metric-card.green:hover {
        border-color: rgba(16, 185, 129, 0.3);
    }
    .metric-card.orange {
        border-top: 3px solid #ff5722;
        box-shadow: 0 4px 20px rgba(255, 87, 34, 0.03);
    }
    .metric-card.orange:hover {
        border-color: rgba(255, 87, 34, 0.3);
    }
    .metric-card.amber {
        border-top: 3px solid #fbbf24;
        box-shadow: 0 4px 20px rgba(251, 191, 36, 0.03);
    }
    .metric-card.amber:hover {
        border-color: rgba(251, 191, 36, 0.3);
    }
    .stat-label {
        font-size: 0.72rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.1em;
        color: #94a3b8;
        margin-bottom: 0.5rem;
    }
    .stat-val {
        font-size: 1.6rem;
        font-weight: 800;
        font-family: 'Outfit', sans-serif;
        color: #ffffff;
        text-shadow: 0 0 10px rgba(255, 255, 255, 0.05);
    }
    .log-container {
        font-family: 'Fira Code', 'Courier New', Courier, monospace;
        background-color: #03050a;
        color: #10b981;
        border-radius: 12px;
        padding: 1.25rem;
        height: 280px;
        overflow-y: auto;
        border: 1px solid rgba(16, 185, 129, 0.15);
        box-shadow: inset 0 2px 10px rgba(0,0,0,0.5);
        margin-bottom: 1.5rem;
    }
    .log-line {
        margin-bottom: 0.4rem;
        font-size: 0.8rem;
        line-height: 1.4;
        border-bottom: 1px solid rgba(255,255,255,0.02);
        padding-bottom: 0.2rem;
    }
    @keyframes pulse {
        0% { transform: scale(0.92); box-shadow: 0 0 0 0 rgba(251, 191, 36, 0.7); }
        70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(251, 191, 36, 0); }
        100% { transform: scale(0.92); box-shadow: 0 0 0 0 rgba(251, 191, 36, 0); }
    }
    
    /* Kubernetes Status Badges */
    .k8s-badge {
        font-family: 'Outfit', sans-serif;
        font-size: 0.68rem;
        font-weight: 700;
        text-transform: uppercase;
        padding: 3px 8px;
        border-radius: 6px;
        display: inline-block;
        letter-spacing: 0.03em;
        text-align: center;
        min-width: 75px;
    }
    .k8s-badge.running {
        background-color: rgba(16, 185, 129, 0.12);
        color: #10b981;
        border: 1px solid rgba(16, 185, 129, 0.2);
    }
    .k8s-badge.degraded {
        background-color: rgba(251, 191, 36, 0.12);
        color: #fbbf24;
        border: 1px solid rgba(251, 191, 36, 0.2);
        animation: pulse 1.8s infinite;
    }
    .k8s-badge.failed, .k8s-badge.error, .k8s-badge.crashloopbackoff {
        background-color: rgba(239, 68, 68, 0.12);
        color: #ef4444;
        border: 1px solid rgba(239, 68, 68, 0.2);
    }

    /* Terminal window styles */
    .terminal-window {
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 10px 30px rgba(0,0,0,0.4);
        margin-bottom: 1.5rem;
    }
    .terminal-header {
        background-color: #1e293b;
        padding: 0.6rem 1rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        border-bottom: 1px solid rgba(255,255,255,0.05);
    }
    .terminal-buttons {
        display: flex;
        gap: 6px;
    }
    .t-btn {
        width: 10px;
        height: 10px;
        border-radius: 50%;
        display: inline-block;
    }
    .t-btn.red { background-color: #ef4444; }
    .t-btn.yellow { background-color: #fbbf24; }
    .t-btn.green { background-color: #10b981; }
    .terminal-title {
        color: #94a3b8;
        font-size: 0.72rem;
        font-family: 'Fira Code', monospace;
    }
    .terminal-status {
        background-color: rgba(16, 185, 129, 0.12);
        color: #10b981;
        font-size: 0.65rem;
        font-weight: 700;
        padding: 2px 6px;
        border-radius: 4px;
        letter-spacing: 0.05em;
        border: 1px solid rgba(16, 185, 129, 0.15);
    }
    .terminal-body {
        font-family: 'Fira Code', 'Courier New', Courier, monospace;
        background-color: #05070f;
        color: #cbd5e1;
        padding: 1.25rem;
        overflow-y: auto;
        font-size: 0.76rem;
        line-height: 1.5;
        border-top: 1px solid rgba(0, 0, 0, 0.2);
    }
    .terminal-body::-webkit-scrollbar {
        width: 6px;
        height: 6px;
    }
    .terminal-body::-webkit-scrollbar-track {
        background: rgba(0, 0, 0, 0.2);
    }
    .terminal-body::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.12);
        border-radius: 3px;
    }

    .dot-green {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background-color: #10b981;
        box-shadow: 0 0 8px #10b981;
    }
    .dot-red {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background-color: #ef4444;
        box-shadow: 0 0 8px #ef4444;
    }
    .dot-yellow {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background-color: #fbbf24;
        animation: pulse 1.8s infinite;
    }
    .dot-grey {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background-color: #64748b;
    }
    .pipeline-container {
        background: rgba(15, 23, 42, 0.5);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 12px;
        padding: 1.5rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
    }
    .pipeline-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        padding-bottom: 0.75rem;
        margin-bottom: 1.25rem;
    }
    .pipeline-title {
        font-family: 'Outfit', sans-serif;
        font-weight: 700;
        text-transform: uppercase;
        color: #ff5722;
        margin: 0;
        font-size: 1.1rem;
        letter-spacing: 0.05em;
    }
    .pipeline-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
        gap: 0.75rem;
    }
    .pipeline-job-card {
        background: rgba(2, 6, 23, 0.45);
        border: 1px solid rgba(255,255,255,0.05);
        border-radius: 8px;
        padding: 0.75rem;
        text-align: left;
        transition: all 0.2s ease;
    }
    .pipeline-job-card:hover {
        background: rgba(2, 6, 23, 0.7);
        border-color: rgba(99, 102, 241, 0.3);
    }
    .pipeline-job-name {
        font-size: 0.8rem;
        font-weight: 600;
        margin-bottom: 0.35rem;
        color: #e2e8f0;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .pipeline-job-status {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 0.75rem;
        color: #94a3b8;
    }
    .k8s-pod-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
        gap: 1.25rem;
        margin-bottom: 1.5rem;
    }
    .k8s-pod-card {
        background: rgba(15, 23, 42, 0.55);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 1.25rem;
        position: relative;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
    }
    .k8s-pod-card:hover {
        transform: translateY(-4px);
        border-color: rgba(255, 87, 34, 0.35);
        box-shadow: 0 10px 30px rgba(255, 87, 34, 0.12);
    }
    .k8s-pod-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 0.75rem;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        padding-bottom: 0.5rem;
    }
    .k8s-pod-name {
        font-weight: 700;
        font-size: 0.85rem;
        color: #ffffff;
        text-overflow: ellipsis;
        overflow: hidden;
        white-space: nowrap;
        max-width: 170px;
    }
    .k8s-pod-detail {
        font-size: 0.75rem;
        color: #94a3b8;
        margin-bottom: 0.4rem;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }
    .k8s-pod-metric {
        font-weight: 600;
        color: #e2e8f0;
    }
    .k8s-log-terminal {
        font-family: 'Fira Code', 'Courier New', Courier, monospace;
        background-color: #03050a;
        color: #10b981;
        border-radius: 10px;
        padding: 1rem;
        height: 260px;
        overflow-y: auto;
        border: 1px solid rgba(16, 185, 129, 0.15);
        font-size: 0.75rem;
        line-height: 1.4;
        box-shadow: inset 0 2px 10px rgba(0,0,0,0.5);
    }
    
    /* Professional Chatbot UI Styles */
    .chat-welcome-container {
        text-align: center;
        padding: 2.5rem 1rem;
        background: rgba(15, 23, 42, 0.4);
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 16px;
        margin-bottom: 2rem;
        backdrop-filter: blur(10px);
    }
    .chat-welcome-title {
        font-family: 'Outfit', sans-serif;
        font-weight: 800;
        font-size: 2.2rem;
        margin-bottom: 0.5rem;
        background: linear-gradient(135deg, #ffffff, #94a3b8);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .chat-welcome-subtitle {
        color: #94a3b8;
        font-size: 0.95rem;
        margin-bottom: 2rem;
    }
    .chat-suggestion-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 1rem;
        margin-top: 1rem;
    }
    .chat-suggestion-card {
        background: rgba(2, 6, 23, 0.4);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 12px;
        padding: 1.25rem;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        text-align: left;
    }
    .chat-suggestion-card:hover {
        transform: translateY(-2px);
        border-color: rgba(99, 102, 241, 0.4);
        background: rgba(99, 102, 241, 0.05);
        box-shadow: 0 4px 20px rgba(99, 102, 241, 0.15);
    }
    .chat-suggestion-title {
        font-family: 'Outfit', sans-serif;
        font-size: 0.95rem;
        font-weight: 700;
        color: #ffffff;
        margin-bottom: 0.5rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .chat-suggestion-desc {
        font-size: 0.8rem;
        color: #94a3b8;
        line-height: 1.4;
    }
    .chat-bubble-user {
        background: linear-gradient(135deg, rgba(99, 102, 241, 0.18), rgba(99, 102, 241, 0.05));
        border: 1px solid rgba(99, 102, 241, 0.25);
        border-left: 4px solid #6366f1;
        border-radius: 4px 16px 16px 16px;
        padding: 1.25rem;
        margin-bottom: 1.25rem;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
    }
    .chat-bubble-assistant {
        background: linear-gradient(135deg, rgba(30, 41, 59, 0.45), rgba(30, 41, 59, 0.2));
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-left: 4px solid #10b981;
        border-radius: 4px 16px 16px 16px;
        padding: 1.25rem;
        margin-bottom: 1.25rem;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
    }
    .chat-role-header {
        font-family: 'Outfit', sans-serif;
        font-size: 0.78rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 0.5rem;
        display: flex;
        align-items: center;
        gap: 0.4rem;
    }
    .chat-citation-header {
        margin-top: 1rem;
        border-top: 1px solid rgba(255,255,255,0.08);
        padding-top: 0.75rem;
    }
    .chat-citation-title {
        font-size: 0.75rem;
        font-weight: 700;
        color: #94a3b8;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        display: flex;
        align-items: center;
        gap: 0.35rem;
        margin-bottom: 0.5rem;
    }
    .chat-citation-console {
        background-color: #03050a;
        border: 1px solid rgba(255, 255, 255, 0.05);
        border-radius: 8px;
        padding: 0.75rem 1rem;
        max-height: 200px;
        overflow-y: auto;
        box-shadow: inset 0 2px 8px rgba(0,0,0,0.5);
    }
    .chat-citation-row {
        font-family: 'Fira Code', monospace;
        font-size: 0.75rem;
        border-bottom: 1px solid rgba(255,255,255,0.02);
        padding: 6px 0;
        line-height: 1.4;
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        align-items: center;
    }
    .chat-citation-row:last-child {
        border-bottom: none;
    }
    .chat-citation-time {
        color: #64748b;
    }
    .chat-citation-level {
        font-weight: bold;
        padding: 1px 5px;
        border-radius: 4px;
        font-size: 0.7rem;
    }
    .chat-citation-service {
        color: #ff5722;
        font-weight: 600;
    }
    .chat-citation-msg {
        color: #e2e8f0;
        flex-grow: 1;
    }
    
    /* Compact Pod Directory Table Styles */
    .pod-table-container {
        background: rgba(15, 23, 42, 0.5);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 1.25rem;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
        margin-bottom: 1.5rem;
        height: 380px;
        overflow-y: auto;
    }
    .pod-table-container::-webkit-scrollbar {
        width: 6px;
        height: 6px;
    }
    .pod-table-container::-webkit-scrollbar-track {
        background: rgba(255, 255, 255, 0.02);
        border-radius: 4px;
    }
    .pod-table-container::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.15);
        border-radius: 4px;
    }
    .pod-table-container::-webkit-scrollbar-thumb:hover {
        background: rgba(255, 255, 255, 0.3);
    }
    .pod-table {
        width: 100%;
        border-collapse: collapse;
        text-align: left;
        font-size: 0.8rem;
    }
    .pod-table th {
        font-family: 'Outfit', sans-serif;
        font-weight: 700;
        text-transform: uppercase;
        color: #94a3b8;
        letter-spacing: 0.08em;
        border-bottom: 2px solid rgba(255, 255, 255, 0.08);
        padding: 0.75rem 0.5rem;
        font-size: 0.7rem;
    }
    .pod-table td {
        padding: 0.8rem 0.5rem;
        border-bottom: 1px solid rgba(255, 255, 255, 0.04);
        vertical-align: middle;
        color: #e2e8f0;
    }
    .pod-table tr {
        transition: background-color 0.2s ease;
    }
    .pod-table tr:hover {
        background: rgba(255, 255, 255, 0.03);
    }
    .pod-table tr:last-child td {
        border-bottom: none;
    }
    .pod-table-name {
        font-family: 'Fira Code', monospace;
        font-weight: 600;
        color: #ffffff;
        max-width: 180px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .pod-table-badge {
        font-weight: 600;
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 0.7rem;
    }
    .progress-bar-container {
        background-color: rgba(255, 255, 255, 0.08);
        border-radius: 4px;
        height: 6px;
        width: 80px;
        overflow: hidden;
        display: inline-block;
        vertical-align: middle;
        margin-right: 0.5rem;
    }
    .progress-bar-fill {
        height: 100%;
        border-radius: 4px;
    }

    /* Main Tab Headers Icon Injection */
    div.stTabs:first-of-type > div[data-baseweb="tab-list"] button[data-baseweb="tab"]:nth-child(1)::before {
        content: "\f080"; /* fa-chart-column */
        font-family: "Font Awesome 6 Free";
        font-weight: 900;
        margin-right: 8px;
    }
    div.stTabs:first-of-type > div[data-baseweb="tab-list"] button[data-baseweb="tab"]:nth-child(2)::before {
        content: "\f3b6"; /* fa-kubernetes */
        font-family: "Font Awesome 6 Brands";
        font-weight: 400;
        margin-right: 8px;
    }
    div.stTabs:first-of-type > div[data-baseweb="tab-list"] button[data-baseweb="tab"]:nth-child(3)::before {
        content: "\f0eb"; /* fa-lightbulb */
        font-family: "Font Awesome 6 Free";
        font-weight: 900;
        margin-right: 8px;
    }
    div.stTabs:first-of-type > div[data-baseweb="tab-list"] button[data-baseweb="tab"]:nth-child(4)::before {
        content: "\f075"; /* fa-comments */
        font-family: "Font Awesome 6 Free";
        font-weight: 900;
        margin-right: 8px;
    }

    /* Nested Tab Headers Icon Injection */
    div[data-testid="stTab"] div.stTabs > div[data-baseweb="tab-list"] button[data-baseweb="tab"]:nth-child(1)::before {
        content: "\f0ad"; /* fa-wrench */
        font-family: "Font Awesome 6 Free";
        font-weight: 900;
        margin-right: 8px;
    }
    div[data-testid="stTab"] div.stTabs > div[data-baseweb="tab-list"] button[data-baseweb="tab"]:nth-child(2)::before {
        content: "\f135"; /* fa-rocket */
        font-family: "Font Awesome 6 Free";
        font-weight: 900;
        margin-right: 8px;
    }
    div[data-testid="stTab"] div.stTabs > div[data-baseweb="tab-list"] button[data-baseweb="tab"]:nth-child(3)::before {
        content: "\f201"; /* fa-chart-line */
        font-family: "Font Awesome 6 Free";
        font-weight: 900;
        margin-right: 8px;
    }

    /* Proactive Alerts Expanders with Left Borders and Alert Icons */
    .incident-container-critical .stExpander {
        border-left: 4px solid #ef4444 !important;
        background-color: rgba(239, 68, 68, 0.02) !important;
    }
    .incident-container-critical .stExpander details summary::before {
        content: "\f071"; /* fa-triangle-exclamation */
        font-family: "Font Awesome 6 Free";
        font-weight: 900;
        color: #ef4444;
        margin-right: 8px;
    }
    .incident-container-warning .stExpander {
        border-left: 4px solid #fbbf24 !important;
        background-color: rgba(251, 191, 36, 0.02) !important;
    }
    .incident-container-warning .stExpander details summary::before {
        content: "\f071"; /* fa-triangle-exclamation */
        font-family: "Font Awesome 6 Free";
        font-weight: 900;
        color: #fbbf24;
        margin-right: 8px;
    }
    .cited-logs-container .stExpander details summary::before {
        content: "\f1c0"; /* fa-database */
        font-family: "Font Awesome 6 Free";
        font-weight: 900;
        color: #94a3b8;
        margin-right: 8px;
    }

    /* Suggestion Expander Icons */
    .suggestion-expander details summary::before {
        font-family: "Font Awesome 6 Free";
        font-weight: 900;
        margin-right: 10px;
        color: #ff5722;
        font-size: 1rem;
        display: inline-block;
        vertical-align: middle;
    }
    .suggestion-expander.lint-icon details summary::before { content: "\f121"; color: #38bdf8; }
    .suggestion-expander.test-icon details summary::before { content: "\f492"; color: #a855f7; }
    .suggestion-expander.sast-icon details summary::before { content: "\f3ed"; color: #ef4444; }
    .suggestion-expander.quality-icon details summary::before { content: "\f559"; color: #eab308; }
    .suggestion-expander.yaml-icon details summary::before { content: "\f1c9"; color: #34d399; }
    .suggestion-expander.docker-icon details summary::before { content: "\f395"; font-family: "Font Awesome 6 Brands"; font-weight: 400; color: #0db7ed; }
    .suggestion-expander.network-icon details summary::before { content: "\f6ff"; color: #6366f1; }
    .suggestion-expander.db-icon details summary::before { content: "\f1c0"; color: #f97316; }
    .suggestion-expander.timeout-icon details summary::before { content: "\f017"; color: #fbbf24; }
    .suggestion-expander.oom-icon details summary::before { content: "\f2db"; color: #ec4899; }
    .suggestion-expander.probe-icon details summary::before { content: "\f21e"; color: #10b981; }
    .suggestion-expander.brute-icon details summary::before { content: "\f505"; color: #ef4444; }
</style>
""", unsafe_allow_html=True)

def clean_html(html_str):
    return "\n".join([line.strip() for line in html_str.split("\n")])

def colorize_logs(log_text):
    import html
    lines = log_text.split("\n")
    colored_lines = []
    for line in lines:
        if not line.strip():
            continue
        escaped_line = html.escape(line)
        if "ERROR" in line or "java.sql" in line or "Exception" in line or "Failed" in line or "Actuator Health Check Failed" in line:
            escaped_line = f'<span style="color:#ef4444; font-weight:500;">{escaped_line}</span>'
        elif "WARN" in line or "Liveness probe failed" in line or "Readiness probe failed" in line:
            escaped_line = f'<span style="color:#fbbf24; font-weight:500;">{escaped_line}</span>'
        elif "INFO" in line:
            escaped_line = f'<span style="color:#34d399;">{escaped_line}</span>'
        else:
            escaped_line = f'<span style="color:#94a3b8;">{escaped_line}</span>'
        colored_lines.append(escaped_line)
    return "<br>".join(colored_lines)



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
#  Kubernetes Pod and Suggestion Helpers
# ─────────────────────────────────────────────

def get_real_k8s_pods():
    import subprocess
    import json
    import re
    from datetime import datetime
    try:
        # First, query kubectl top pod to get real metrics
        metrics_dict = {}
        try:
            metrics_res = subprocess.run(
                ["kubectl", "top", "pod", "-n", "log-analysis", "--no-headers"],
                capture_output=True,
                text=True,
                check=False
            )
            if metrics_res.returncode == 0:
                for line in metrics_res.stdout.strip().split("\n"):
                    parts = line.split()
                    if len(parts) >= 3:
                        pod_name = parts[0]
                        cpu_str = parts[1]  # e.g., "5m"
                        mem_str = parts[2]  # e.g., "48Mi"
                        
                        # Parse CPU (millicores -> percentage assuming 1 core = 1000m)
                        cpu_val = 0.0
                        if cpu_str.endswith("m"):
                            try:
                                cpu_val = round(float(cpu_str[:-1]) / 10.0, 1)
                            except:
                                pass
                        
                        # Parse Memory (Mi/Ki/Gi -> MB)
                        mem_val = 0.0
                        if mem_str.endswith("Mi"):
                            try: mem_val = float(mem_str[:-2])
                            except: pass
                        elif mem_str.endswith("Gi"):
                            try: mem_val = float(mem_str[:-2]) * 1024
                            except: pass
                        elif mem_str.endswith("Ki"):
                            try: mem_val = float(mem_str[:-2]) / 1024
                            except: pass
                            
                        metrics_dict[pod_name] = {"cpu": cpu_val, "mem": mem_val}
        except Exception:
            pass

        res = subprocess.run(
            ["kubectl", "get", "pods", "-n", "log-analysis", "-o", "json"],
            capture_output=True,
            text=True,
            check=True
        )
        data = json.loads(res.stdout)
        items = data.get("items", [])
        if not items:
            return None
        
        pods = []
        for item in items:
            metadata = item.get("metadata", {})
            name = metadata.get("name")
            labels = metadata.get("labels", {})
            service = labels.get("app", name.split("-")[0] if "-" in name else name)
            
            status_obj = item.get("status", {})
            phase = status_obj.get("phase", "Unknown")
            status = phase
            message = "Healthy and running"
            
            container_statuses = status_obj.get("containerStatuses", [])
            ready_count = 0
            restart_count = 0
            if container_statuses:
                c_status = container_statuses[0]
                restart_count = c_status.get("restartCount", 0)
                state = c_status.get("state", {})
                if "waiting" in state:
                    status = state["waiting"].get("reason", "Waiting")
                    message = state["waiting"].get("message", "Container is waiting")
                elif "terminated" in state:
                    status = state["terminated"].get("reason", "Terminated")
                    message = state["terminated"].get("message", "Container terminated")
                
                for cs in container_statuses:
                    if cs.get("ready"):
                        ready_count += 1
            
            total_containers = len(item.get("spec", {}).get("containers", []))
            probe = f"{ready_count}/{total_containers}"
            
            if metadata.get("deletionTimestamp"):
                status = "Terminating"
                message = "Pod is terminating"
                
            ip = status_obj.get("podIP", "N/A")
            node = status_obj.get("nodeName", "N/A")
            
            creation_str = metadata.get("creationTimestamp")
            age = "N/A"
            if creation_str:
                try:
                    creation_time = datetime.strptime(creation_str, "%Y-%m-%dT%H:%M:%SZ")
                    diff = datetime.utcnow() - creation_time
                    if diff.days > 0:
                        age = f"{diff.days}d"
                    elif diff.seconds >= 3600:
                        age = f"{diff.seconds // 3600}h"
                    elif diff.seconds >= 60:
                        age = f"{diff.seconds // 60}m"
                    else:
                        age = f"{diff.seconds}s"
                except:
                    pass
            
            max_mem = 256
            containers = item.get("spec", {}).get("containers", [])
            if containers:
                resources = containers[0].get("resources", {})
                limits = resources.get("limits", {})
                mem_limit = limits.get("memory", "256Mi")
                match_mem = re.search(r"(\d+)", mem_limit)
                if match_mem:
                    max_mem = int(match_mem.group(1))
                    if "Gi" in mem_limit:
                        max_mem = max_mem * 1024
            
            cpu = 0.0
            mem = 0.0
            health = "Healthy"
            if status == "Running":
                if name in metrics_dict:
                    cpu = metrics_dict[name]["cpu"]
                    mem = metrics_dict[name]["mem"]
                else:
                    import random
                    cpu = round(random.uniform(2.0, 15.0), 1)
                    mem = round(max_mem * random.uniform(0.3, 0.6), 1)
            else:
                health = "Unhealthy" if status in ["ImagePullBackOff", "Error", "CrashLoopBackOff"] else "Degraded"
                
            pods.append({
                "name": name,
                "service": service,
                "status": status,
                "probe": probe,
                "restarts": restart_count,
                "ip": ip,
                "node": node,
                "cpu": cpu,
                "mem": mem,
                "max_mem": max_mem,
                "age": age,
                "health": health,
                "message": message
            })
        return pods
    except Exception:
        return None

def get_real_pod_logs(pod_name):
    import subprocess
    try:
        res = subprocess.run(
            ["kubectl", "logs", pod_name, "-n", "log-analysis", "--tail=100"],
            capture_output=True,
            text=True,
            check=True
        )
        return res.stdout
    except Exception as e:
        return f"Could not retrieve logs for pod '{pod_name}'. Error: {str(e)}"

def get_mock_k8s_pods(anomaly_type):
    import random
    t_now = time.time()
    
    pods = [
        {"name": "gateway-7b89d4fb98-abcde", "service": "gateway", "status": "Running", "probe": "2/2", "restarts": 0, "ip": "10.244.0.15", "node": "aks-agentpool-1-vmss000000", "cpu": 8.0, "mem": 48.0, "max_mem": 128},
        {"name": "auth-service-589fc8c457-b248a", "service": "auth-service", "status": "Running", "probe": "1/1", "restarts": 0, "ip": "10.244.1.22", "node": "aks-agentpool-1-vmss000001", "cpu": 6.0, "mem": 92.0, "max_mem": 256},
        {"name": "auth-service-589fc8c457-c819b", "service": "auth-service", "status": "Running", "probe": "1/1", "restarts": 0, "ip": "10.244.1.23", "node": "aks-agentpool-1-vmss000001", "cpu": 5.5, "mem": 94.0, "max_mem": 256},
        {"name": "inventory-service-678fd9c8d5-w892v", "service": "inventory-service", "status": "Running", "probe": "1/1", "restarts": 0, "ip": "10.244.2.8", "node": "aks-agentpool-2-vmss000000", "cpu": 12.0, "mem": 110.0, "max_mem": 256},
        {"name": "inventory-service-678fd9c8d5-z412a", "service": "inventory-service", "status": "Running", "probe": "1/1", "restarts": 0, "ip": "10.244.2.9", "node": "aks-agentpool-2-vmss000000", "cpu": 11.5, "mem": 105.0, "max_mem": 256},
        {"name": "valuation-service-84cf959b8c-n624b", "service": "valuation-service", "status": "Running", "probe": "1/1", "restarts": 0, "ip": "10.244.2.14", "node": "aks-agentpool-2-vmss000000", "cpu": 4.5, "mem": 75.0, "max_mem": 128},
        {"name": "fuel-service-789bc4f5c-v124a", "service": "fuel-service", "status": "Running", "probe": "1/1", "restarts": 0, "ip": "10.244.1.50", "node": "aks-agentpool-1-vmss000001", "cpu": 3.0, "mem": 68.0, "max_mem": 128},
        {"name": "insurance-service-58cf74b5c-k782v", "service": "insurance-service", "status": "Running", "probe": "1/1", "restarts": 0, "ip": "10.244.0.35", "node": "aks-agentpool-1-vmss000000", "cpu": 5.0, "mem": 80.0, "max_mem": 128},
        {"name": "maintenance-service-9c4cf82d-m912b", "service": "maintenance-service", "status": "Running", "probe": "1/1", "restarts": 0, "ip": "10.244.2.62", "node": "aks-agentpool-2-vmss000000", "cpu": 4.0, "mem": 72.0, "max_mem": 128},
        {"name": "metrics-service-7bc4d5f8c-l456e", "service": "metrics-service", "status": "Running", "probe": "1/1", "restarts": 0, "ip": "10.244.0.98", "node": "aks-agentpool-1-vmss000000", "cpu": 8.0, "mem": 130.0, "max_mem": 256},
        {"name": "mongodb-589dfc74bc-x718a", "service": "mongodb", "status": "Running", "probe": "1/1", "restarts": 0, "ip": "10.244.2.5", "node": "aks-agentpool-2-vmss000000", "cpu": 18.0, "mem": 450.0, "max_mem": 1024}
    ]
    
    for p in pods:
        seed_val = hash(p["name"]) + int(t_now / 5)
        random.seed(seed_val)
        p["cpu"] = round(p["cpu"] + random.uniform(-1.0, 1.0), 1)
        p["mem"] = round(p["mem"] + random.uniform(-2.0, 2.0), 1)
        p["health"] = "Healthy"
        p["message"] = "Probes passing successfully"
        p["age"] = "2d 5h"
        
    if anomaly_type == "db_locked":
        for p in pods:
            if p["service"] == "inventory-service":
                p["status"] = "Failed"
                p["health"] = "Unhealthy"
                p["cpu"] = round(92.0 + random.uniform(-2.0, 2.0), 1)
                p["mem"] = round(252.0 + random.uniform(-2.0, 3.0), 1)
                p["restarts"] = 4
                p["probe"] = "0/1"
                p["message"] = "Liveness probe failed: HTTP GET /health returned status 503"
                p["age"] = "14m"
            elif p["service"] == "gateway":
                p["cpu"] = round(34.0 + random.uniform(-4.0, 4.0), 1)
                p["health"] = "Degraded"
                p["message"] = "Downstream service inventory-service returning 503"
                
    elif anomaly_type == "timeout":
        for p in pods:
            if p["service"] == "valuation-service":
                p["status"] = "Running"
                p["health"] = "Degraded"
                p["cpu"] = round(3.0 + random.uniform(-0.5, 0.5), 1)
                p["mem"] = round(125.0 + random.uniform(-1.0, 2.0), 1)
                p["restarts"] = 2
                p["probe"] = "0/1"
                p["message"] = "Readiness probe failed: HTTP GET /health returned status 502"
                p["age"] = "1h"
                
    elif anomaly_type == "brute_force":
        for p in pods:
            if p["service"] == "auth-service":
                p["cpu"] = round(94.5 + random.uniform(-1.5, 1.5), 1)
                p["mem"] = round(195.0 + random.uniform(-5.0, 5.0), 1)
                p["health"] = "Degraded"
                p["message"] = "High auth load (brute-force hashing active)"
                
    return pods

def get_mock_pod_logs(pod_name, anomaly_type):
    if "inventory-service" in pod_name and anomaly_type == "db_locked":
        return """[2026-05-29 15:48:12] INFO  [main] Starting InventoryServiceApplication on inventory-service-678fd9c8d5-w892v
[2026-05-29 15:48:15] INFO  [main] Tomcat initialized with port(s): 8000 (http)
[2026-05-29 15:48:18] INFO  [main] Connecting to MongoDB on mongodb:27017... Connected!
[2026-05-29 15:48:18] INFO  [main] Connecting to relational fleet database pool (HikariCP)...
[2026-05-29 15:48:20] INFO  [main] HikariPool-1 - Starting...
[2026-05-29 15:48:21] INFO  [main] HikariPool-1 - Start completed.
[2026-05-29 15:48:22] INFO  [main] Active connections: 5, Idle: 45, Max: 50
[2026-05-29 15:50:04] WARN  [pool-1-thread-4] HikariPool-1 - Connection pool acquisition latency high: 4500ms
[2026-05-29 15:50:10] ERROR [pool-1-thread-8] DB Query failed: Database Connection Timeout on pool. Connection count exceeded maximum limit of 50 connections.
[2026-05-29 15:50:10] ERROR [pool-1-thread-8] java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 5000ms.
    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:218)
    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:162)
    at com.zaxxer.hikari.HikariDataSource.getConnection(HikariDataSource.java:128)
    at autohub.inventory.repository.CarRepository.fetchAvailable(CarRepository.java:84)
[2026-05-29 15:50:15] ERROR [http-nio-8000-exec-3] org.springframework.web.util.NestedServletException: Request processing failed; nested exception is java.sql.SQLTransientConnectionException
[2026-05-29 15:50:20] WARN  [liveness-probe] Liveness probe failed. Connection timeout when checking database status.
[2026-05-29 15:50:25] ERROR [liveness-probe] Actuator Health Check Failed - Out of Database Connections (Pool exhausted)"""
    
    elif "valuation-service" in pod_name and anomaly_type == "timeout":
        return """[2026-05-29 15:45:00] INFO  [main] Starting ValuationService on valuation-service-84cf959b8c-n624b
[2026-05-29 15:45:02] INFO  [main] Loaded active depreciation formulas. Target base currency: INR (₹)
[2026-05-29 15:51:10] INFO  [http-nio-8002-exec-1] GET /api/valuation/c1 -> fetching inventory details from http://inventory-service:8000/api/inventory/c1
[2026-05-29 15:51:12] ERROR [http-nio-8002-exec-1] Valuation failed: inventory-service connection exception: HTTPConnectionPool(host='inventory-service', port=8000): Read timed out. (read timeout=2.0)
[2026-05-29 15:51:12] ERROR [http-nio-8002-exec-1] com.netflix.hystrix.exception.HystrixRuntimeException: ValuationService#getInventory(String) timed-out and no fallback available.
[2026-05-29 15:51:15] WARN  [readiness-probe] Readiness probe failed: downstream HTTP client timeout on dependent endpoints.
[2026-05-29 15:51:20] WARN  [readiness-probe] Readiness check failed. HTTP GET /health returned 502 Bad Gateway"""
        
    elif "auth-service" in pod_name and anomaly_type == "brute_force":
        return """[2026-05-29 15:51:00] INFO  [main] Authentication Engine Ready. Algorithm: BCrypt (Work Factor: 12)
[2026-05-29 15:51:02] INFO  [exec-1] POST /api/auth/login -> 401 Unauthorized (user: admin, IP: 198.51.100.42)
[2026-05-29 15:51:05] INFO  [exec-2] POST /api/auth/login -> 401 Unauthorized (user: admin, IP: 198.51.100.42)
[2026-05-29 15:51:08] INFO  [exec-3] POST /api/auth/login -> 401 Unauthorized (user: admin, IP: 198.51.100.42)
[2026-05-29 15:51:11] INFO  [exec-4] POST /api/auth/login -> 401 Unauthorized (user: admin, IP: 198.51.100.42)
[2026-05-29 15:51:14] INFO  [exec-5] POST /api/auth/login -> 401 Unauthorized (user: admin, IP: 198.51.100.42)
[2026-05-29 15:51:15] ERROR [security-monitor] SECURITY ALERT: Multiple failed login attempts (5+) detected on user 'admin' within 60 seconds. Triggering operational throttle.
[2026-05-29 15:51:16] WARN  [auth-throttle] Rate limiting activated on user 'admin' and IP 198.51.100.42 for 15 minutes.
[2026-05-29 15:51:20] INFO  [exec-6] POST /api/auth/login -> 429 Too Many Requests (user: admin, IP: 198.51.100.42)"""
        
    else:
        return f"""[2026-05-29 15:30:00] INFO  [main] Starting container logs for {pod_name}
[2026-05-29 15:30:04] INFO  [main] Service configuration loaded successfully.
[2026-05-29 15:30:06] INFO  [main] Running healthcheck server on port 8000.
[2026-05-29 15:30:10] INFO  [scheduler] Synchronized policies and rules cache.
[2026-05-29 15:35:00] INFO  [http-exec-1] GET /health -> 200 OK (Probes passing)
[2026-05-29 15:40:00] INFO  [http-exec-2] GET /health -> 200 OK (Probes passing)
[2026-05-29 15:45:00] INFO  [http-exec-3] GET /health -> 200 OK (Probes passing)
[2026-05-29 15:50:00] INFO  [http-exec-4] GET /health -> 200 OK (Probes passing)
[2026-05-29 15:51:45] INFO  [http-exec-5] GET /health -> 200 OK (Probes passing)"""

def classify_devops_error(error_log):
    log_lower = error_log.lower()
    
    if any(kw in log_lower for kw in ["flake8", "lint", "syntaxerror", "pep 8", "pep8", "undefined name"]):
        return {
            "stage": "Development Stage",
            "type": "Code Linting & Syntax Violation (flake8)",
            "cause": "The Python codebase contains styling inconsistencies, syntax issues, or reference to undefined variables, violating flake8 lint gates in the CI/CD pipeline.",
            "remedy": """**Action Plan**:
1. Open the referenced file and navigate to the line indicated in the linter warning.
2. Ensure all imported modules are used, all variables are declared before use, and formatting adheres to PEP 8.
3. Run `flake8` locally on your workspace before committing:
   ```bash
   pip install flake8
   flake8 app/
   ```"""
        }
    elif any(kw in log_lower for kw in ["pytest", "assertionerror", "test failed", "failed test"]):
        return {
            "stage": "Development Stage",
            "type": "Unit Test Assertion Failure (pytest)",
            "cause": "One or more unit tests in the test suite failed. This occurs when local code changes return outputs that differ from the expected mock assertions.",
            "remedy": """**Action Plan**:
1. Read the pytest stdout logs carefully. Identify the failing assertion (e.g., `assert 200 == 503`).
2. Verify if the failure is due to a bug in the code, or if the mock/test fixtures need updating to reflect new business logic.
3. Run unit tests locally:
   ```bash
   pytest tests/
   ```"""
        }
    elif any(kw in log_lower for kw in ["snyk", "vulnerability", "cve-", "requirements.txt", "pyjwt"]):
        return {
            "stage": "Development Stage",
            "type": "Security Scanner Gate Failure (Snyk SAST)",
            "cause": "The SAST scanning stage found known CVE vulnerabilities in third-party library versions listed in requirements.txt (e.g., outdated PyJWT or Request packages).",
            "remedy": """**Action Plan**:
1. Check the Snyk scan report for the vulnerable package name and its severity.
2. Upgrade the dependency in `requirements.txt` to the recommended secure version.
3. Example fix in [requirements.txt](file:///c:/Users/ASUS/OneDrive/Desktop/Log-Analysis/requirements.txt):
   ```diff
   - pyjwt>=2.8.0
   + pyjwt>=2.9.0
   ```"""
        }
    elif any(kw in log_lower for kw in ["sonar", "sonarqube", "cognitive complexity", "code smell"]):
        return {
            "stage": "Development Stage",
            "type": "Quality Gate Failure (SonarQube)",
            "cause": "SonarQube analysis detected critical code smells, security hotspots, or high cognitive complexity, blocking the CI quality gate.",
            "remedy": """**Action Plan**:
1. Log in to the SonarQube portal to view the specific issue.
2. Refactor complex nested blocks or split large monolithic functions into clean, single-responsibility functions.
3. Ensure configuration in `sonar-project.properties` matches your repo details and is reachable."""
        }
    elif any(kw in log_lower for kw in ["kubeconform", "kubeval", "apiversion", "yaml schema", "invalid deployment", "indentation"]):
        return {
            "stage": "Deployment Stage",
            "type": "Kubernetes Manifest Schema Validation Failure",
            "cause": "The Kubernetes YAML linter (Kubeconform) detected syntax errors (indentation) or references to deprecated/invalid API schemas (e.g., `apps/v1beta1` instead of `apps/v1`).",
            "remedy": """**Action Plan**:
1. Inspect the failing manifest (e.g. `gateway-deployment.yaml`). Ensure indentation is exactly 2 or 4 spaces consistently.
2. Verify that `apiVersion` values match the target Kubernetes cluster's version specs (e.g., use `apps/v1` for Deployments and DaemonSets).
3. Validate manifests locally using kubeconform:
   ```bash
   kubeconform k8s/
   ```"""
        }
    elif any(kw in log_lower for kw in ["docker build", "trivy", "dockerfile", "base image", "image scan"]):
        return {
            "stage": "Deployment Stage",
            "type": "Container Build or Trivy Image Vulnerability",
            "cause": "Docker build commands failed, or the Trivy scanner detected critical/high CVEs inside the built image's OS package layer.",
            "remedy": """**Action Plan**:
1. Inspect the Dockerfile. If the build failed, verify that commands (e.g., `pip install`) do not trigger permission errors, and ensure the base image is available in the registry.
2. If Trivy failed, update the Dockerfile base image to a minimal/hardened image (e.g., `-slim` or alpine) and run update commands:
   ```dockerfile
   FROM python:3.11-slim
   RUN apt-get update && apt-get upgrade -y
   ```"""
        }
    elif any(kw in log_lower for kw in ["networkpolicy", "istio", "service mesh", "sidecar", "connection refused", "ingress"]):
        return {
            "stage": "Deployment Stage",
            "type": "Service Mesh / Network Policy Block",
            "cause": "The pod connection was blocked by a Kubernetes NetworkPolicy or Istio security boundary (AuthorizationPolicy/PeerAuthentication).",
            "remedy": """**Action Plan**:
1. Check the applied network policies in [network-policies.yaml](file:///c:/Users/ASUS/OneDrive/Desktop/Log-Analysis/k8s/network-policies.yaml).
2. Ensure the pods are labeled correctly (e.g. `app: inventory-service`) so that policies match the selectors.
3. Review Istio virtual services and destination rules to ensure mTLS isn't blocking communications without Envoy proxies sidecar injection."""
        }
    elif any(kw in log_lower for kw in ["oomkilled", "exit code 137", "oom", "out of memory"]):
        return {
            "stage": "Post-Deployment (Runtime)",
            "type": "Pod Out-Of-Memory (OOMKilled - Exit Code 137)",
            "cause": "The container process exceeded the maximum memory limit defined in the Kubernetes deployment specifications (`resources.limits.memory`), causing the OS kernel to terminate it.",
            "remedy": """**Action Plan**:
1. Increase the memory limit in the service deployment yaml file (e.g. from `128Mi` to `256Mi`).
2. Investigate the application code for memory leaks (e.g., open files not closed, global caches growing infinitely).
3. Example change in [gateway-deployment.yaml](file:///c:/Users/ASUS/OneDrive/Desktop/Log-Analysis/k8s/gateway-deployment.yaml):
   ```yaml
   resources:
     limits:
       memory: "256Mi"
     requests:
       memory: "128Mi"
   ```"""
        }
    elif any(kw in log_lower for kw in ["hikaripool", "connection timeout", "database connection pool", "max_connections"]):
        return {
            "stage": "Post-Deployment (Runtime)",
            "type": "Database Connection Pool Exhaustion (503 Service Unavailable)",
            "cause": "The application exhausted its database connection pool under load. No connections were available for new incoming requests, leading to HTTP 503 errors and liveness probe failures.",
            "remedy": """**Action Plan**:
1. Increase the maximum pool size in the database connection settings (e.g., `spring.datasource.hikari.maximum-pool-size=100`).
2. Verify that all database transactions release connections back to the pool promptly (e.g., utilizing try-with-resources or finally blocks).
3. Restart the affected pod to instantly release locked database sessions:
   ```bash
   kubectl rollout restart deployment/inventory-service -n log-analysis
   ```"""
        }
    elif any(kw in log_lower for kw in ["read timed out", "gateway timeout", "502 bad gateway", "504 gateway timeout", "readtimeout"]):
        return {
            "stage": "Post-Deployment (Runtime)",
            "type": "Downstream HTTP Read Timeout (502 / 504 Gateway)",
            "cause": "An upstream service (e.g., Gateway or Valuation service) timed out while waiting for a response from a slower downstream dependency.",
            "remedy": """**Action Plan**:
1. Check the response times and queries on the downstream service (e.g. inventory-service). Optimize any slow DB queries.
2. Implement circuit breakers (e.g. Hystrix / Resilience4j) to return stale/cached default data immediately if the dependency times out.
3. Increase the read timeout threshold (e.g., from `2.0s` to `5.0s`) in client configurations if the latency is expected."""
        }
    elif any(kw in log_lower for kw in ["liveness probe failed", "readiness probe failed", "probe failed", "actuator health"]):
        return {
            "stage": "Post-Deployment (Runtime)",
            "type": "Liveness / Readiness Probe Failure",
            "cause": "The container became unresponsive or unhealthy, causing Kubernetes probes to fail. This leads to the pod being removed from load balancing (readiness failure) or restarted (liveness failure).",
            "remedy": """**Action Plan**:
1. Examine if the `/health` or `/actuator/health` endpoint is checking external systems (database, external API) synchronously and blocking.
2. Separate the system liveness check (does the server run?) from external readiness check (is the database reachable?).
3. Adjust `initialDelaySeconds` and `timeoutSeconds` in the deployment spec to prevent premature failure checks during startup."""
        }
    elif any(kw in log_lower for kw in ["brute-force", "security alert", "failed login", "login attempts"]):
        return {
            "stage": "Post-Deployment (Runtime)",
            "type": "Brute-Force Authentication Attempt Detected",
            "cause": "Security alarms triggered due to rapid consecutive authentication failures, alerting the SRE team of potential malicious intrusion.",
            "remedy": """**Action Plan**:
1. Implement client IP rate limiting (e.g., using Redis Token Bucket or Nginx WAF rules).
2. Lock offending accounts for 15 minutes after 5 consecutive failures.
3. Track and ban abusive origin IP addresses in Azure Front Door / Cloudflare."""
        }
    else:
        return {
            "stage": "Unclassified SRE Log",
            "type": "General DevOps / Operational Issue",
            "cause": "The pasted error trace does not match any known presets. It represents a general runtime exception or compiler error.",
            "remedy": """**Action Plan**:
1. Read the stack trace to locate the origin file name and line number.
2. Check for missing configuration environment variables in the `.env` settings or config maps.
3. If you have Azure connected, submit this query to the **SRE AI Assistant** in the chat tab to do vector log search on past similar tickets!"""
        }

# ─────────────────────────────────────────────
#  GitHub Actions Status Checker & Mock Fallbacks
# ─────────────────────────────────────────────

def get_git_repo_info():
    try:
        git_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".git", "config")
        if os.path.exists(git_config_path):
            with open(git_config_path, "r") as f:
                content = f.read()
            import re
            match = re.search(r"url\s*=\s*(?:git@github\.com:|https://github\.com/)([^/\s]+)/([^/.\s]+)", content)
            if match:
                owner = match.group(1)
                repo = match.group(2)
                if repo.endswith(".git"):
                    repo = repo[:-4]
                return f"{owner}/{repo}"
    except Exception:
        pass
    return "Sathvik307393/RAG_based_log_analysis"

def fetch_github_workflow_status_from_cli():
    try:
        import subprocess
        res = subprocess.run(["gh", "run", "list", "--limit", "1", "--json", "databaseId,number,name,status,conclusion,event,headBranch,headSha,triggeringActor,createdAt,updatedAt,title"], capture_output=True, text=True, check=True)
        runs = json.loads(res.stdout)
        if not runs:
            return None
        latest = runs[0]
        run_id = latest["databaseId"]
        
        res_jobs = subprocess.run(["gh", "run", "view", str(run_id), "--json", "jobs"], capture_output=True, text=True, check=True)
        jobs_data = json.loads(res_jobs.stdout)
        
        jobs = []
        for j in jobs_data.get("jobs", []):
            steps = []
            for s in j.get("steps", []):
                steps.append({
                    "name": s.get("name"),
                    "status": s.get("status"),
                    "conclusion": s.get("conclusion")
                })
            
            jobs.append({
                "id": j.get("id"),
                "name": j.get("name"),
                "status": j.get("status"),
                "conclusion": j.get("conclusion"),
                "started_at": j.get("startedAt"),
                "completed_at": j.get("completedAt"),
                "html_url": f"https://github.com/{get_git_repo_info()}/actions/runs/{run_id}/job/{j.get('id')}",
                "steps": steps
            })
            
        return {
            "source": "cli",
            "repo": get_git_repo_info(),
            "run_id": run_id,
            "run_number": latest.get("number"),
            "name": latest.get("name"),
            "status": latest.get("status").lower() if latest.get("status") else None,
            "conclusion": latest.get("conclusion").lower() if latest.get("conclusion") else None,
            "html_url": f"https://github.com/{get_git_repo_info()}/actions/runs/{run_id}",
            "event": latest.get("event"),
            "head_branch": latest.get("headBranch"),
            "head_commit_message": latest.get("title", "No message"),
            "head_sha": latest.get("headSha"),
            "actor": latest.get("triggeringActor", {}).get("login", "unknown"),
            "created_at": latest.get("createdAt"),
            "updated_at": latest.get("updatedAt"),
            "jobs": jobs
        }
    except Exception:
        return None

def generate_mock_workflow_status(anomaly_type):
    repo_fullname = get_git_repo_info()
    ts = datetime.utcnow()
    
    status = "completed"
    conclusion = "success"
    
    jobs_info = [
        {"name": "Lint & Unit Testing", "status": "completed", "conclusion": "success", "delay": 20},
        {"name": "Snyk Dependency & SAST Scan", "status": "completed", "conclusion": "success", "delay": 40},
        {"name": "SonarQube Analysis", "status": "completed", "conclusion": "success", "delay": 45},
        {"name": "Container Build & Trivy Vulnerability Scan", "status": "completed", "conclusion": "success", "delay": 60},
        {"name": "Kubernetes Deploy Dry Run", "status": "completed", "conclusion": "success", "delay": 20},
        {"name": "Dynamic Application Security Scan (DAST)", "status": "completed", "conclusion": "success", "delay": 90},
    ]
    
    if anomaly_type == "db_locked":
        conclusion = "failure"
        jobs_info[3]["conclusion"] = "failure"
        jobs_info[4]["status"] = "queued"
        jobs_info[4]["conclusion"] = None
        jobs_info[5]["status"] = "queued"
        jobs_info[5]["conclusion"] = None
        jobs_info.append({"name": "Send Email on Failure", "status": "completed", "conclusion": "success", "delay": 15})
        
    elif anomaly_type == "timeout":
        conclusion = "failure"
        jobs_info[5]["conclusion"] = "failure"
        jobs_info.append({"name": "Send Email on Failure", "status": "completed", "conclusion": "success", "delay": 15})
        
    elif anomaly_type == "brute_force":
        status = "in_progress"
        conclusion = None
        jobs_info[1]["status"] = "in_progress"
        jobs_info[1]["conclusion"] = None
        for i in range(2, 6):
            jobs_info[i]["status"] = "queued"
            jobs_info[i]["conclusion"] = None
            
    jobs = []
    for idx, j in enumerate(jobs_info):
        started_at = (ts - timedelta(seconds=180 - j["delay"])).isoformat() + "Z"
        completed_at = (ts - timedelta(seconds=180 - j["delay"] - 15)).isoformat() + "Z" if j["status"] == "completed" else None
        
        steps = []
        if j["status"] == "completed":
            steps = [
                {"name": "Checkout Code", "status": "completed", "conclusion": "success"},
                {"name": "Run main script", "status": "completed", "conclusion": j["conclusion"]}
            ]
        elif j["status"] == "in_progress":
            steps = [
                {"name": "Checkout Code", "status": "completed", "conclusion": "success"},
                {"name": "Run main script", "status": "in_progress", "conclusion": None}
            ]
            
        jobs.append({
            "id": 1000 + idx,
            "name": j["name"],
            "status": j["status"],
            "conclusion": j["conclusion"],
            "started_at": started_at,
            "completed_at": completed_at,
            "html_url": f"https://github.com/{repo_fullname}/actions/runs/12345/job/{1000+idx}",
            "steps": steps
        })
        
    return {
        "source": "simulated",
        "repo": repo_fullname,
        "run_id": 12345,
        "run_number": 15,
        "name": "DevSecOps CI/CD Pipeline",
        "status": status,
        "conclusion": conclusion,
        "html_url": f"https://github.com/{repo_fullname}/actions/runs/12345",
        "event": "push",
        "head_branch": "main",
        "head_commit_message": f"Simulated commit for {anomaly_type} event",
        "head_sha": "d3adb33fd3adb33fd3adb33f",
        "actor": "sathvik307393",
        "created_at": (ts - timedelta(minutes=3)).isoformat() + "Z",
        "updated_at": ts.isoformat() + "Z",
        "jobs": jobs
    }

@st.cache_data(ttl=2)
def fetch_github_workflow_status(anomaly_type="healthy"):
    repo_fullname = get_git_repo_info()
    github_token = os.getenv("GITHUB_TOKEN") or st.session_state.get("github_token", "")
    
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    
    runs_url = f"https://api.github.com/repos/{repo_fullname}/actions/runs"
    
    try:
        r = requests.get(runs_url, headers=headers, timeout=2)
        if r.status_code == 200:
            data = r.json()
            runs = data.get("workflow_runs", [])
            if not runs:
                return generate_mock_workflow_status(anomaly_type)
            
            latest_run = runs[0]
            run_id = latest_run["id"]
            
            jobs_url = f"https://api.github.com/repos/{repo_fullname}/actions/runs/{run_id}/jobs"
            jr = requests.get(jobs_url, headers=headers, timeout=2)
            jobs_data = {}
            if jr.status_code == 200:
                jobs_data = jr.json()
            else:
                return generate_mock_workflow_status(anomaly_type)
            
            jobs = []
            for j in jobs_data.get("jobs", []):
                jobs.append({
                    "id": j.get("id"),
                    "name": j.get("name"),
                    "status": j.get("status"),
                    "conclusion": j.get("conclusion"),
                    "started_at": j.get("started_at"),
                    "completed_at": j.get("completed_at"),
                    "html_url": j.get("html_url"),
                    "steps": [{"name": s.get("name"), "status": s.get("status"), "conclusion": s.get("conclusion")} for s in j.get("steps", [])]
                })
                
            return {
                "source": "api",
                "repo": repo_fullname,
                "run_id": run_id,
                "run_number": latest_run.get("run_number"),
                "name": latest_run.get("name"),
                "status": latest_run.get("status"),
                "conclusion": latest_run.get("conclusion"),
                "html_url": latest_run.get("html_url"),
                "event": latest_run.get("event"),
                "head_branch": latest_run.get("head_branch"),
                "head_commit_message": latest_run.get("head_commit", {}).get("message", "No message"),
                "head_sha": latest_run.get("head_sha"),
                "actor": latest_run.get("triggering_actor", {}).get("login", latest_run.get("actor", {}).get("login", "unknown")),
                "created_at": latest_run.get("created_at"),
                "updated_at": latest_run.get("updated_at"),
                "jobs": jobs
            }
        else:
            cli_res = fetch_github_workflow_status_from_cli()
            if cli_res:
                return cli_res
            return generate_mock_workflow_status(anomaly_type)
    except Exception:
        cli_res = fetch_github_workflow_status_from_cli()
        if cli_res:
            return cli_res
        return generate_mock_workflow_status(anomaly_type)

def get_job_diagnostic(job_name, steps):
    failed_step = ""
    for step in steps:
        if step.get("conclusion") == "failure":
            failed_step = step.get("name", "Unknown Step")
            break
            
    source = "GitHub Actions Workflow Engine"
    remedy = "Check pipeline definition or commit logs."
    
    name_lower = job_name.lower()
    step_lower = failed_step.lower()
    
    if "lint" in name_lower or "lint" in step_lower:
        source = "CI Lint Gate (flake8)"
        remedy = "Run `flake8` locally, fix styling and syntax issues before committing."
    elif "test" in name_lower or "test" in step_lower:
        source = "Unit Tests runner (pytest)"
        remedy = "Verify failures in pytest outputs and fix broken assertions."
    elif "security" in name_lower or "snyk" in name_lower or "snyk" in step_lower:
        source = "Snyk SAST Security Gate"
        remedy = "Upgrade vulnerable dependencies in `requirements.txt` to recommended secure versions."
    elif "build" in name_lower or "docker" in name_lower or "docker" in step_lower:
        source = "Docker Build / Trivy Scanner"
        remedy = "Ensure Dockerfile builds without errors and base images are secure (e.g. use alpine or -slim)."
    elif "kubeconform" in name_lower or "manifest" in name_lower or "yaml" in step_lower:
        source = "Kubeconform Manifest Validator"
        remedy = "Fix yaml indentation or schema versions in the `k8s/` manifests."
    elif "sonar" in name_lower or "quality" in name_lower:
        source = "SonarQube Quality Gate"
        remedy = "Verify cognitive complexity or code smells in SonarQube portal."
        
    return {
        "failed_step": failed_step,
        "source": source,
        "remedy": remedy
    }

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
st.sidebar.markdown("<div style='text-align:center;'><h2 style='border-left:none; padding-left:0; font-size:1.8rem; color:#ff5722;'><i class='fa-solid fa-shield-halved'></i> AutoHub SRE</h2></div>", unsafe_allow_html=True)

# Azure Configurations
st.sidebar.markdown("### <i class='fa-brands fa-microsoft' style='color:#00a4ef;'></i> Azure RAG Status", unsafe_allow_html=True)
gateway_url = st.sidebar.text_input("API Gateway URL", "http://localhost:5000")
github_token_input = st.sidebar.text_input("GitHub Token (Optional)", value=os.getenv("GITHUB_TOKEN", ""), type="password", help="Enter a GitHub PAT to avoid API rate limiting")
if github_token_input:
    st.session_state["github_token"] = github_token_input

azure_configured = False
if RAG_AVAILABLE and AZURE_OPENAI_API_KEY and AZURE_SEARCH_ENDPOINT:
    azure_configured = True
    st.sidebar.success("Azure Search & OpenAI Connected!")
else:
    st.sidebar.warning("Running in LOCAL MOCK Mode (No Azure keys)")

st.sidebar.markdown("---")
st.sidebar.markdown("### <i class='fa-solid fa-arrows-rotate' style='color:#10b981;'></i> Auto-Refresh", unsafe_allow_html=True)
auto_refresh = st.sidebar.checkbox("Enable Auto-Refresh", value=True, help="Automatically refresh data in real-time")
refresh_interval = st.sidebar.slider("Refresh Interval (s)", min_value=2, max_value=30, value=5, help="Select polling interval")

st.sidebar.markdown("---")
st.sidebar.markdown("### <i class='fa-solid fa-gears' style='color:#fbbf24;'></i> Simulator Settings", unsafe_allow_html=True)
mock_batch_interval = st.sidebar.slider(
    "Warning Batch Interval (s)",
    min_value=5,
    max_value=60,
    value=15,
    help="Interval for batching warning events in local simulator"
)
MOCK_BATCH_INTERVAL = mock_batch_interval

st.sidebar.markdown("---")
st.sidebar.markdown("### <i class='fa-solid fa-triangle-exclamation' style='color:#ef4444;'></i> Inject Anomalies", unsafe_allow_html=True)
st.sidebar.info("Simulate typical microservice breakdowns and test the RAG SRE responder.")

active_anomaly = st.sidebar.radio(
    "Select Anomaly Scenario",
    ["🟢 Normal / Healthy Operations", "🔴 Database Connection Timeout (Inventory Service)", "🟡 Valuation Gateway Timeout (Network Failure)", "🔒 Brute-Force Login Security Alert"]
)

# Trigger anomaly HTTP injection
anomaly_mapping = {
    "🟢 Normal / Healthy Operations": "healthy",
    "🔴 Database Connection Timeout (Inventory Service)": "db_locked",
    "🟡 Valuation Gateway Timeout (Network Failure)": "timeout",
    "🔒 Brute-Force Login Security Alert": "brute_force"
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
st.markdown("<h1><i class='fa-solid fa-shield-halved' style='color:#ff5722; margin-right:12px;'></i> AI-Powered DevOps SRE Console</h1>", unsafe_allow_html=True)
# Setup the 4 main tabs
tab_console, tab_k8s, tab_suggestions, tab_chat = st.tabs([
    "Operational Console", 
    "Kubernetes Cluster Watch", 
    "DevOps Suggestion Hub", 
    "AI SRE Chat Assistant"
])

@st.fragment(run_every=refresh_interval if auto_refresh else None)
def show_console_tab():
    st.markdown("A real-time operational RAG system connected to Azure Log Analytics, Event Hubs, and Azure AI Search.")
    
    # Stats Rows (Mocked based on Active Anomaly to look premium)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown('<div class="metric-card"><div class="stat-label">Active Microservices</div><div class="stat-val" style="color:#ffb300;">9 Services</div></div>', unsafe_allow_html=True)
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

    # ─────────────────────────────────────────────
    #  CI/CD Pipeline Monitor Widget
    # ─────────────────────────────────────────────
    st.markdown("### <i class='fa-solid fa-arrows-spin' style='color:#6366f1; margin-right:8px;'></i> CI/CD DevSecOps Pipeline Monitor", unsafe_allow_html=True)
    
    with st.spinner("Fetching latest pipeline status from GitHub..."):
        current_anomaly_state = anomaly_mapping[active_anomaly]
        pipeline_state = fetch_github_workflow_status(current_anomaly_state)
    
    if "error" in pipeline_state:
        st.warning(f"Could not load live pipeline status: {pipeline_state['error']}")
    else:
        conclusion = pipeline_state.get("conclusion")
        status = pipeline_state.get("status")
        
        if conclusion == "success":
            status_bg = "rgba(16, 185, 129, 0.2)"
            status_fg = "#10b981"
            status_label = "SUCCESS"
        elif conclusion == "failure":
            status_bg = "rgba(239, 68, 68, 0.2)"
            status_fg = "#ef4444"
            status_label = "FAILED"
        elif status == "in_progress":
            status_bg = "rgba(255, 179, 0, 0.2)"
            status_fg = "#ffb300"
            status_label = "IN PROGRESS"
        else:
            status_bg = "rgba(100, 116, 139, 0.2)"
            status_fg = "#64748b"
            status_label = "QUEUED / UNKNOWN"
            
        meta_cols = st.columns([3, 2, 3, 2])
        with meta_cols[0]:
            st.markdown(f"**Run:** [#{pipeline_state['run_number']} - {pipeline_state['name']}]({pipeline_state['html_url']})")
        with meta_cols[1]:
            st.markdown(f"**Branch:** `{pipeline_state['head_branch']}`")
        with meta_cols[2]:
            st.markdown(f"**Trigger:** `{pipeline_state['event']}` by @{pipeline_state['actor']}")
        with meta_cols[3]:
            if st.button("↻ Refresh Pipeline", key="refresh_pipeline_btn"):
                st.toast("Refreshing pipeline status...")
                st.rerun()
                
        st.markdown(f"**Latest Commit:** *\"{pipeline_state['head_commit_message']}\"* (`{pipeline_state['head_sha'][:7] if pipeline_state['head_sha'] else ''}`)")
        
        job_html = ""
        for job in pipeline_state.get("jobs", []):
            job_conclusion = job.get("conclusion")
            job_status = job.get("status")
            
            if job_conclusion == "success":
                dot_class = "dot-green"
                status_text = "Success"
                border_color = "rgba(16, 185, 129, 0.3)"
            elif job_conclusion == "failure":
                dot_class = "dot-red"
                status_text = "Failed"
                border_color = "rgba(239, 68, 68, 0.4)"
            elif job_status == "in_progress":
                dot_class = "dot-yellow"
                status_text = "In Progress"
                border_color = "rgba(255, 179, 0, 0.4)"
            else:
                dot_class = "dot-grey"
                status_text = "Queued"
                border_color = "rgba(100, 116, 139, 0.2)"
                
            job_card = f"""<div class="pipeline-job-card" style="border: 1px solid {border_color};">
<div class="pipeline-job-name">{job['name']}</div>
<div class="pipeline-job-status">
<span class="{dot_class}"></span>
<span>{status_text}</span>
</div>
</div>"""
            job_html += job_card
            
        diagnostic_html = ""
        if conclusion == "failure":
            failed_jobs_details = []
            for job in pipeline_state.get("jobs", []):
                if job.get("conclusion") == "failure":
                    diag = get_job_diagnostic(job["name"], job.get("steps", []))
                    step_info = f" (Failed Step: <code>{diag['failed_step']}</code>)" if diag['failed_step'] else ""
                    failed_jobs_details.append(f"""<div style="border-bottom: 1px solid rgba(255,255,255,0.08); padding-bottom: 8px; margin-bottom: 8px;">
<p style="margin: 0 0 4px 0;"><strong>Failed Step:</strong> <a href="{job['html_url']}" target="_blank" style="color: #ef4444; font-weight: bold; text-decoration: none;">{job['name']}{step_info} ↗</a></p>
<p style="margin: 0 0 4px 0; color: #94a3b8; font-size: 0.8rem;"><strong>Source:</strong> {diag['source']}</p>
<p style="margin: 0 0 0 0; color: #f1f5f9; font-size: 0.85rem;"><strong>Remedy:</strong> {diag['remedy']}</p>
</div>""")
            
            failed_list_html = "".join(failed_jobs_details)
            diagnostic_html = f"""<div style="background: rgba(239, 68, 68, 0.05); border: 1px solid rgba(239, 68, 68, 0.2); border-radius: 10px; padding: 1.25rem; height: 100%;">
<h4 style="margin: 0 0 10px 0; color: #ef4444; font-size: 1rem; text-transform: uppercase; font-family: 'Barlow Condensed', sans-serif;"><i class='fa-solid fa-circle-xmark'></i> Pipeline Outage Diagnostics</h4>
<div style="max-height: 250px; overflow-y: auto;">
{failed_list_html}
</div>
</div>"""
        elif status == "in_progress":
            diagnostic_html = f"""<div style="background: rgba(255, 179, 0, 0.05); border: 1px solid rgba(255, 179, 0, 0.2); border-radius: 10px; padding: 1.25rem; height: 100%;">
<h4 style="margin: 0 0 10px 0; color: #ffb300; font-size: 1rem; text-transform: uppercase; font-family: 'Barlow Condensed', sans-serif;"><i class='fa-solid fa-arrows-spin fa-spin'></i> Active Build running</h4>
<p style="font-size: 0.85rem; color: #94a3b8; margin: 0;">GitHub Actions is actively compiling code and executing security gates. Use the Refresh button above to poll live status.</p>
</div>"""
        else:
            diagnostic_html = f"""<div style="background: rgba(16, 185, 129, 0.05); border: 1px solid rgba(16, 185, 129, 0.2); border-radius: 10px; padding: 1.25rem; height: 100%;">
<h4 style="margin: 0 0 10px 0; color: #10b981; font-size: 1rem; text-transform: uppercase; font-family: 'Barlow Condensed', sans-serif;"><i class='fa-solid fa-circle-check'></i> Pipeline Healthy</h4>
<p style="font-size: 0.85rem; color: #94a3b8; margin: 0;">All DevSecOps verification checks and Kubeconform linter tests have passed successfully. System integrity is verified.</p>
</div>"""
    
        col_left, col_right = st.columns([6, 4])
        
        with col_left:
            st.markdown(f"""<div class="pipeline-container" style="border-top: 3px solid {status_fg}; height: 100%;">
<div class="pipeline-header">
<span class="pipeline-title">Workflow Execution Jobs</span>
<span style="background: {status_bg}; color: {status_fg}; padding: 3px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: bold;">{status_label}</span>
</div>
<div class="pipeline-grid">
{job_html}
</div>
</div>""", unsafe_allow_html=True)
    
        with col_right:
            st.markdown(diagnostic_html, unsafe_allow_html=True)

    # ─────────────────────────────────────────────
    #  Proactive Incident Alerts
    # ─────────────────────────────────────────────
    st.markdown("### <i class='fa-solid fa-bell-exclamation' style='color:#ef4444; margin-right:8px;'></i> Proactive AIOps Incident Alerts", unsafe_allow_html=True)
    
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
    
    # ─────────────────────────────────────────────
    #  Ingested Live Log Stream
    # ─────────────────────────────────────────────
    st.markdown("### <i class='fa-solid fa-list-ul' style='color:#10b981; margin-right:8px;'></i> Ingested Live Log Stream", unsafe_allow_html=True)
    all_logs = load_local_logs()
    log_html = ""
    if all_logs:
        for log in reversed(all_logs[-20:]):
            color = "#10b981"
            if log["level"] == "ERROR":
                color = "#ef4444"
            elif log["level"] == "WARNING":
                color = "#ffb300"
                
            log_line = f"<div class='log-line'>[{log['timestamp']}] <span style='color:{color}; font-weight:bold;'>{log['level']}</span> - {log['service']} - {log['message']}</div>"
            log_html += log_line
    else:
        log_html = "<div class='log-line' style='color:#94a3b8;'>No telemetry events captured. Use the sidebar to trigger log generation.</div>"
    
    st.markdown(f'<div class="log-container">{log_html}</div>', unsafe_allow_html=True)

with tab_console:
    show_console_tab()


@st.fragment(run_every=refresh_interval if auto_refresh else None)
def show_k8s_tab():
    st.markdown("### <i class='fa-brands fa-kubernetes' style='color:#326ce5; margin-right:8px;'></i> Kubernetes Pod Cluster Watch", unsafe_allow_html=True)
    st.markdown("Monitor real-time pod replicas status, network configurations, and resource consumptions across the fleet.")
    
    real_pods = get_real_k8s_pods()
    is_real_cluster = real_pods is not None
    
    if is_real_cluster:
        pods_list = real_pods
        st.markdown(clean_html("""
        <div class="custom-alert success">
            <span class="alert-icon"><i class="fa-solid fa-bolt-lightning"></i></span>
            <div class="alert-content">
                <strong>AKS Live Link Active:</strong> Showing real-time pod telemetry in namespace <code>log-analysis</code>.
            </div>
        </div>
        """), unsafe_allow_html=True)
    else:
        pods_list = get_mock_k8s_pods(anomaly_mapping[active_anomaly])
        st.markdown(clean_html("""
        <div class="custom-alert warning">
            <span class="alert-icon"><i class="fa-solid fa-triangle-exclamation"></i></span>
            <div class="alert-content">
                <strong>Simulated Telemetry Mode:</strong> Could not connect to live AKS cluster. Displaying high-fidelity local pod mocks.
            </div>
        </div>
        """), unsafe_allow_html=True)
    
    # Calculate global metrics
    total_pods = len(pods_list)
    running_pods = sum(1 for p in pods_list if p["status"] == "Running" and p["health"] == "Healthy")
    degraded_pods = sum(1 for p in pods_list if p["health"] == "Degraded")
    failed_pods = sum(1 for p in pods_list if p["status"] == "Failed" or p["health"] == "Unhealthy" or p["status"] in ["ImagePullBackOff", "Error", "CrashLoopBackOff"])
    
    avg_cpu = sum(p["cpu"] for p in pods_list) / total_pods
    total_mem = sum(p["mem"] for p in pods_list)
    max_mem_limit = sum(p["max_mem"] for p in pods_list)
    
    # Render Pod KPIs
    k8s_col1, k8s_col2, k8s_col3, k8s_col4 = st.columns(4)
    with k8s_col1:
        st.markdown(f'<div class="metric-card blue"><div class="stat-label"><i class="fa-solid fa-cubes"></i> Total Replicas</div><div class="stat-val">{total_pods} <span style="font-size:0.9rem; font-weight:normal; color:#94a3b8;">Pods</span></div></div>', unsafe_allow_html=True)
    with k8s_col2:
        status_class = "green" if failed_pods == 0 and degraded_pods == 0 else ("amber" if failed_pods == 0 else "orange")
        st.markdown(f'<div class="metric-card {status_class}"><div class="stat-label"><i class="fa-solid fa-heart-pulse"></i> Pod Health States</div><div class="stat-val">{running_pods} <span style="font-size:0.8rem; font-weight:normal; color:#10b981;">OK</span> / {degraded_pods} <span style="font-size:0.8rem; font-weight:normal; color:#fbbf24;">WRN</span> / {failed_pods} <span style="font-size:0.8rem; font-weight:normal; color:#ef4444;">ERR</span></div></div>', unsafe_allow_html=True)
    with k8s_col3:
        st.markdown(f'<div class="metric-card orange"><div class="stat-label">Mean Pod CPU Util</div><div class="stat-val">{avg_cpu:.1f}%</div></div>', unsafe_allow_html=True)
    with k8s_col4:
        st.markdown(f'<div class="metric-card blue"><div class="stat-label">Aggregated Memory</div><div class="stat-val">{int(total_mem)}<span style="font-size:0.9rem; font-weight:normal; color:#94a3b8;">MB</span> <span style="font-size:0.8rem; font-weight:normal; color:#64748b;">/ {max_mem_limit}MB</span></div></div>', unsafe_allow_html=True)
        
    # Memory and CPU Utilization Comparison Charts
    with st.expander("📊 Cluster-Wide Resource Utilization Overview", expanded=False):
        chart_col1, chart_col2 = st.columns(2)
        
        # Prepare DataFrame
        df_pods = pd.DataFrame([
            {
                "Pod Name": p["name"],
                "Service": p["service"],
                "CPU Utilization (%)": p["cpu"],
                "Memory Usage (MB)": p["mem"],
                "Memory Limit (MB)": p["max_mem"],
                "Memory Utilization (%)": round((p["mem"] / p["max_mem"]) * 100, 1) if p["max_mem"] > 0 else 0
            }
            for p in pods_list
        ])
        
        with chart_col1:
            # Use Service name (shorter) to avoid x-axis overlap
            fig_mem_bar = px.bar(
                df_pods,
                x="Service",
                y="Memory Utilization (%)",
                color="Memory Utilization (%)",
                color_continuous_scale="Viridis",
                title="Pod Memory Utilization (%) by Service",
                template="plotly_dark",
                hover_data={"Pod Name": True, "Memory Usage (MB)": True, "Memory Limit (MB)": True}
            )
            fig_mem_bar.update_layout(
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10, r=10, t=45, b=120),
                height=380,
                xaxis=dict(
                    tickangle=-40,
                    tickfont=dict(size=11),
                    title=""
                ),
                yaxis=dict(title="Memory Util (%)"),
                coloraxis_colorbar=dict(thickness=12, len=0.7)
            )
            st.plotly_chart(fig_mem_bar, use_container_width=True)
            
        with chart_col2:
            fig_cpu_bar = px.bar(
                df_pods,
                x="Service",
                y="CPU Utilization (%)",
                color="CPU Utilization (%)",
                color_continuous_scale="Oranges",
                title="Pod CPU Utilization (%) by Service",
                template="plotly_dark",
                hover_data={"Pod Name": True, "CPU Utilization (%)": True}
            )
            fig_cpu_bar.update_layout(
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10, r=10, t=45, b=120),
                height=380,
                xaxis=dict(
                    tickangle=-40,
                    tickfont=dict(size=11),
                    title=""
                ),
                yaxis=dict(title="CPU Util (%)"),
                coloraxis_colorbar=dict(thickness=12, len=0.7)
            )
            st.plotly_chart(fig_cpu_bar, use_container_width=True)

    st.markdown("---")
    
    # 🎯 Pod Selection at top level
    selected_pod = st.selectbox("🎯 Select Pod to Inspect & Analyze Telemetry", [p["name"] for p in pods_list], key="k8s_pod_select")
    selected_pod_obj = next(p for p in pods_list if p["name"] == selected_pod)
    
    # Generate HTML grid for Directory Table
    pod_rows_html = ""
    for p in pods_list:
        mem_pct = (p["mem"] / p["max_mem"]) * 100 if p["max_mem"] > 0 else 0
        mem_color = "#10b981" if mem_pct < 60 else ("#ffb300" if mem_pct < 85 else "#ef4444")
        cpu_color = "#10b981" if p["cpu"] < 60 else ("#ffb300" if p["cpu"] < 85 else "#ef4444")
        
        status_badge_class = "running"
        if p["status"] != "Running":
            status_badge_class = p["status"].lower()
        elif p["health"] == "Degraded":
            status_badge_class = "degraded"
        elif p["health"] == "Unhealthy":
            status_badge_class = "failed"
            
        status_label = p["status"]
        if p["health"] == "Degraded" and p["status"] == "Running":
            status_label = "Degraded"
            
        status_html = f'<span class="k8s-badge {status_badge_class}">{status_label}</span>'
        
        # Highlight selected pod row with subtle background
        row_style = "background: rgba(99, 102, 241, 0.08); border-left: 3px solid #6366f1;" if p["name"] == selected_pod else ""
        
        row = f"""<tr style="{row_style}">
        <td style="text-align: center; padding: 0.6rem 0.5rem;">{status_html}</td>
        <td class="pod-table-name" style="font-family: 'Fira Code', monospace; font-size: 0.78rem; font-weight: 600;" title="{p['name']}">{p['name']}</td>
        <td style="text-align: center;"><span class="pod-table-badge" style="background: rgba(255,255,255,0.06); color:#cbd5e1; font-weight:700; font-family: 'Fira Code', monospace;">{p['restarts']}</span></td>
        <td><span style="color:{cpu_color}; font-weight:700; font-family: 'Fira Code', monospace;">{p['cpu']}%</span></td>
        <td>
            <div class="progress-bar-container" style="background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.03); border-radius: 4px; height: 8px;">
                <div class="progress-bar-fill" style="background-color:{mem_color}; width:{min(100.0, mem_pct)}%; height: 100%;"></div>
            </div>
            <span style="color:{mem_color}; font-weight:700; font-size:0.75rem; font-family: 'Fira Code', monospace;">{int(p['mem'])}MB</span>
        </td>
        </tr>"""
        pod_rows_html += row

    pod_table_html = f"""<div class="pod-table-container">
    <table class="pod-table">
    <thead>
    <tr>
        <th style="text-align: center; width: 85px;">State</th>
        <th>Pod Name</th>
        <th style="text-align: center; width: 70px;">Restarts</th>
        <th style="width: 75px;">CPU</th>
        <th>Memory</th>
    </tr>
    </thead>
    <tbody>
    {pod_rows_html}
    </tbody>
    </table>
    </div>"""

    # Split panel columns
    k8s_col_left, k8s_col_right = st.columns([5, 5])
    
    with k8s_col_left:
        st.markdown("#### <i class='fa-solid fa-table-list' style='color:#3b82f6; margin-right:8px;'></i> Pod Status Directory", unsafe_allow_html=True)
        st.markdown(clean_html(pod_table_html), unsafe_allow_html=True)
        
        # Resource Utilization History
        st.markdown("#### <i class='fa-solid fa-chart-line' style='color:#ff5722; margin-right:8px;'></i> Resource Utilization History", unsafe_allow_html=True)
        base_cpu = selected_pod_obj["cpu"]
        base_mem = selected_pod_obj["mem"]
        
        import random
        random.seed(hash(selected_pod))
        cpu_history = [max(0.1, min(100.0, base_cpu + random.uniform(-4, 4))) for _ in range(14)] + [base_cpu]
        mem_history = [max(10.0, min(float(selected_pod_obj["max_mem"]), base_mem + random.uniform(-8, 8))) for _ in range(14)] + [base_mem]
        history_times = [(datetime.now() - timedelta(minutes=i)).strftime("%H:%M") for i in reversed(range(15))]
        
        df_history = pd.DataFrame({
            "Time": history_times * 2,
            "Metric Value": cpu_history + mem_history,
            "Resource Type": ["CPU Utilization (%)"] * 15 + ["Memory Usage (MB)"] * 15
        })
        
        fig = px.line(
            df_history, 
            x="Time", 
            y="Metric Value", 
            color="Resource Type",
            title=f"Telemetry History: {selected_pod}",
            template="plotly_dark",
            color_discrete_map={"CPU Utilization (%)": "#ff5722", "Memory Usage (MB)": "#3b82f6"}
        )
        fig.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            xaxis_gridcolor="rgba(255,255,255,0.04)",
            yaxis_gridcolor="rgba(255,255,255,0.04)",
            margin=dict(l=20, r=20, t=40, b=20),
            height=255,
            hovermode="x unified"
        )
        fig.update_traces(line=dict(width=3.5, shape="spline"))
        fig.update_layout(
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            )
        )
        st.plotly_chart(fig, use_container_width=True)
        
    with k8s_col_right:
        st.markdown("#### <i class='fa-solid fa-magnifying-glass' style='color:#6366f1; margin-right:8px;'></i> Selected Pod Inspector", unsafe_allow_html=True)
        
        meta_status_color = "#10b981"
        meta_status_pulse = ""
        if selected_pod_obj["health"] == "Degraded":
            meta_status_color = "#fbbf24"
            meta_status_pulse = "animation: pulse 1.8s infinite;"
        elif selected_pod_obj["health"] == "Unhealthy" or selected_pod_obj["status"] in ["Failed", "Error", "CrashLoopBackOff"]:
            meta_status_color = "#ef4444"
            
        metadata_html = f"""
        <div style="background: rgba(15, 23, 42, 0.4); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 1.25rem; margin-bottom: 1.25rem; box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.85rem; border-bottom: 1px solid rgba(255, 255, 255, 0.06); padding-bottom: 0.5rem;">
                <div style="font-size: 0.75rem; font-weight: 800; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.08em; display: flex; align-items: center; gap: 0.4rem;">
                    <span><i class="fa-solid fa-circle-nodes"></i></span> Pod Health Metadata
                </div>
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background-color: {meta_status_color}; box-shadow: 0 0 8px {meta_status_color}; {meta_status_pulse}"></span>
                    <span style="font-size: 0.75rem; font-weight: 700; color: {meta_status_color}; text-transform: uppercase; letter-spacing: 0.05em;">{selected_pod_obj['health']}</span>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.75rem;">
                <div style="background: rgba(2, 6, 23, 0.35); padding: 0.6rem; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.04); text-align: center;">
                    <div style="font-size: 0.62rem; color: #64748b; text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-bottom: 0.2rem;">Status</div>
                    <div style="font-size: 0.85rem; font-weight: 800; color: #ffffff; font-family: monospace;">{selected_pod_obj['status']}</div>
                </div>
                <div style="background: rgba(2, 6, 23, 0.35); padding: 0.6rem; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.04); text-align: center;">
                    <div style="font-size: 0.62rem; color: #64748b; text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-bottom: 0.2rem;">Age</div>
                    <div style="font-size: 0.85rem; font-weight: 800; color: #ffffff; font-family: monospace;">{selected_pod_obj['age']}</div>
                </div>
                <div style="background: rgba(2, 6, 23, 0.35); padding: 0.6rem; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.04); text-align: center;">
                    <div style="font-size: 0.62rem; color: #64748b; text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-bottom: 0.2rem;">Host Node</div>
                    <div style="font-size: 0.85rem; font-weight: 800; color: #e2e8f0; font-family: monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="{selected_pod_obj['node']}">{selected_pod_obj['node'].split('-')[-1]}</div>
                </div>
            </div>
            <div style="font-size: 0.72rem; color: #cbd5e1; margin-top: 0.75rem; border-top: 1px solid rgba(255,255,255,0.06); padding-top: 0.6rem; display: flex; align-items: flex-start; gap: 0.5rem; line-height: 1.4;">
                <span style="color:#64748b; font-weight: 700; text-transform: uppercase; font-size: 0.65rem;">Message:</span>
                <span style="font-family: monospace; color:#94a3b8;">{selected_pod_obj['message']}</span>
            </div>
        </div>
        """
        st.markdown(clean_html(metadata_html), unsafe_allow_html=True)
        
        # stdout Logs Terminal
        st.markdown("#### <i class='fa-solid fa-terminal' style='color:#10b981; margin-right:8px;'></i> Container stdout Log Viewer", unsafe_allow_html=True)
        if is_real_cluster:
            pod_logs = get_real_pod_logs(selected_pod)
        else:
            pod_logs = get_mock_pod_logs(selected_pod, anomaly_mapping[active_anomaly])
            
        colored_pod_logs = colorize_logs(pod_logs)
        
        terminal_html = f"""
        <div class="terminal-window">
            <div class="terminal-header">
                <div class="terminal-buttons">
                    <span class="t-btn red"></span>
                    <span class="t-btn yellow"></span>
                    <span class="t-btn green"></span>
                </div>
                <span class="terminal-title">bash &mdash; stdout &mdash; {selected_pod}</span>
                <span class="terminal-status">LIVE STREAM</span>
            </div>
            <div class="terminal-body" style="height: 180px;">
                {colored_pod_logs}
            </div>
        </div>
        """
        st.markdown(clean_html(terminal_html), unsafe_allow_html=True)
        
        # Telemetry Data Table inside expander
        st.markdown("<div style='margin-top: 1rem;'></div>", unsafe_allow_html=True)
        with st.expander("📊 View Resource Telemetry History Logs Table", expanded=False):
            df_tabular = pd.DataFrame({
                "Timestamp (Relative)": history_times,
                "CPU Utilization": [f"{v:.1f}%" for v in cpu_history],
                "Memory Usage": [f"{int(v)} MB / {selected_pod_obj['max_mem']} MB" for v in mem_history]
            })
            st.dataframe(df_tabular.iloc[::-1], use_container_width=True, hide_index=True)

with tab_k8s:
    show_k8s_tab()


with tab_suggestions:
    st.markdown("### <i class='fa-solid fa-lightbulb' style='color:#fbbf24; margin-right:8px;'></i> Multi-Stage DevOps Suggestion Hub", unsafe_allow_html=True)
    st.markdown("Diagnose problems and view pre-mapped resolutions for typical errors encountered across the software lifecycle.")
    
    stage_tab1, stage_tab2, stage_tab3 = st.tabs(["🛠️ Development Stage", "🚀 Deployment Stage", "📈 Post-Deployment (Runtime)"])
    
    with stage_tab1:
        st.markdown("#### Development Stage Errors & Remedies")
        st.info("These errors are typically caught in IDEs or during CI linting/testing jobs prior to containerization.")
        with st.expander("📝 Lint Violation (flake8 style checks)"):
            st.markdown(classify_devops_error("flake8 undefined name")["remedy"])
        with st.expander("🧪 Unit Test Failure (pytest execution)"):
            st.markdown(classify_devops_error("pytest AssertionError")["remedy"])
        with st.expander("🛡️ SAST Security Alert (Snyk dependency vulnerabilities)"):
            st.markdown(classify_devops_error("Snyk vulnerability requirements.txt")["remedy"])
        with st.expander("💎 Quality Gate Warning (SonarQube analysis)"):
            st.markdown(classify_devops_error("SonarQube cognitive complexity")["remedy"])
            
    with stage_tab2:
        st.markdown("#### Deployment Stage Errors & Remedies")
        st.info("These errors block container assembly, manifest parsing, or helm charts dry-runs prior to production delivery.")
        with st.expander("📄 Kubernetes YAML Schema Mismatch (Kubeconform validation)"):
            st.markdown(classify_devops_error("Kubeconform invalid deployment apiVersion")["remedy"])
        with st.expander("🐳 Docker Assembly & Trivy CVE Alert"):
            st.markdown(classify_devops_error("Trivy Docker build vulnerability")["remedy"])
        with st.expander("🕸️ NetworkPolicy or Ingress Communication Refused"):
            st.markdown(classify_devops_error("NetworkPolicy connection refused Istio")["remedy"])
            
    with stage_tab3:
        st.markdown("#### Post-Deployment / Runtime Errors & Remedies")
        st.info("These anomalies arise inside the running cluster during live operations and affect service SLA/SLOs.")
        with st.expander("🗄️ Database Connection Pool Exhaustion (HTTP 503 Outages)"):
            st.markdown(classify_devops_error("Hikaripool connection timeout")["remedy"])
        with st.expander("🌐 Downstream Service Network Timeout (HTTP 502 / 504)"):
            st.markdown(classify_devops_error("read timed out 502 gateway")["remedy"])
        with st.expander("❌ Container Memory Limit Exceeded (OOMKilled - Exit Code 137)"):
            st.markdown(classify_devops_error("OOMKilled exit code 137")["remedy"])
        with st.expander("🩺 Liveness / Readiness Probes Repeatedly Failing"):
            st.markdown(classify_devops_error("liveness probe failed Actuator")["remedy"])
        with st.expander("🔒 Bruteforce Login Attempts Security Alert"):
            st.markdown(classify_devops_error("brute-force login attempts auth-service")["remedy"])
            
    st.markdown("---")
    st.markdown("### <i class='fa-solid fa-brain' style='color:#a855f7; margin-right:8px;'></i> AI DevOps Proactive Advisor", unsafe_allow_html=True)
    st.markdown("Paste any terminal dump, exception stack, or pipeline trace below to extract a direct classification, cause explanation, and repair code.")
    
    user_error_input = st.text_area("Paste error log dump here...", height=120, key="advisor_log_input")
    if st.button("Run Diagnostic Scan", key="advisor_run_btn"):
        if user_error_input.strip():
            with st.spinner("Decoding log signature..."):
                time.sleep(0.8)
                diag_res = classify_devops_error(user_error_input)
                
                st.markdown(f"""
<div style="background: rgba(255, 87, 34, 0.05); border: 1px solid rgba(255, 87, 34, 0.2); border-radius: 8px; padding: 1.25rem; margin-top: 1rem;">
<h4 style="color:#ff5722; margin:0 0 8px 0; font-family:'Barlow Condensed', sans-serif; text-transform:uppercase; font-size:1.1rem; letter-spacing:0.05em;">🔍 AI Diagnostic Report</h4>
<p style="margin: 0 0 6px 0; font-size:0.9rem;"><strong>Lifecycle Phase:</strong> {diag_res['stage']}</p>
<p style="margin: 0 0 6px 0; font-size:0.9rem;"><strong>Error Signature:</strong> {diag_res['type']}</p>
<p style="margin: 0 0 12px 0; color:#94a3b8; font-size:0.9rem;"><strong>Root Cause Analysis:</strong> {diag_res['cause']}</p>
<hr style="border:0; border-top:1px solid rgba(255,255,255,0.08); margin: 12px 0;">
<div style="margin-top:8px; font-size:0.9rem;">
{diag_res['remedy']}
</div>
</div>
""", unsafe_allow_html=True)
        else:
            st.warning("Please copy-paste an error string first.")


with tab_chat:
    # 1. Chat Header and Status Badge
    header_col1, header_col2 = st.columns([7, 3])
    with header_col1:
        st.markdown("<h3 style='margin-bottom:0.25rem; border:none; text-shadow:none; text-transform:none; padding-bottom:0;'><i class='fa-solid fa-comments' style='color:#10b981; margin-right:8px;'></i> AI SRE Incident Assistant</h3>", unsafe_allow_html=True)
        if azure_configured:
            st.markdown('<span style="background-color: rgba(16, 185, 129, 0.12); color: #10b981; padding: 4px 12px; border-radius: 20px; font-size: 0.78rem; font-weight: bold; border: 1px solid rgba(16, 185, 129, 0.25); display: inline-block; margin-bottom:1rem;">● RAG Connected (Azure OpenAI)</span>', unsafe_allow_html=True)
        else:
            st.markdown('<span style="background-color: rgba(251, 191, 36, 0.12); color: #fbbf24; padding: 4px 12px; border-radius: 20px; font-size: 0.78rem; font-weight: bold; border: 1px solid rgba(251, 191, 36, 0.25); display: inline-block; margin-bottom:1rem;">● Fallback Mode (Local DevOps Rulebase)</span>', unsafe_allow_html=True)
    with header_col2:
        st.markdown("<div style='text-align: right; padding-top: 15px;'>", unsafe_allow_html=True)
        if st.button("🗑️ Clear Chat History", key="clear_chat_history_btn", use_container_width=True):
            st.session_state.messages = []
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # 2. Render messages history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.markdown(f"""
                <div class="chat-bubble-user">
                    <div class="chat-role-header" style="color: #6366f1;"><i class="fa-solid fa-user-gear"></i> SRE Operator</div>
                    <div style="color: #e2e8f0; font-size: 0.92rem;">{message['content']}</div>
                </div>
                """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                <div class="chat-bubble-assistant">
                    <div class="chat-role-header" style="color: #10b981;"><i class="fa-solid fa-robot"></i> AI SRE Assistant</div>
                    <div style="color: #e2e8f0; font-size: 0.92rem; line-height: 1.6;">
                """, unsafe_allow_html=True)
                st.markdown(message["content"])
                
                if "citations" in message and message["citations"]:
                    cit_rows = ""
                    for cit in message["citations"]:
                        level = cit.get("level", "INFO")
                        lvl_color = "#10b981"
                        if level in ["ERROR", "CRITICAL"]:
                            lvl_color = "#ef4444"
                        elif level == "WARNING":
                            lvl_color = "#fbbf24"
                            
                        cit_rows += f"""
                        <div class="chat-citation-row">
                            <span class="chat-citation-time">[{cit.get('timestamp')}]</span>
                            <span class="chat-citation-level" style="background:{lvl_color}18; color:{lvl_color}; border:1px solid {lvl_color}30;">{level}</span>
                            <span class="chat-citation-service">{cit.get('service')}</span>:
                            <span class="chat-citation-msg">{cit.get('message')}</span>
                        </div>
                        """
                    st.markdown(f"""
                    <div class="chat-citation-header">
                        <div class="chat-citation-title"><i class="fa-solid fa-database"></i> Cited Source Telemetry</div>
                        <div class="chat-citation-console">
                            {cit_rows}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                st.markdown("</div></div>", unsafe_allow_html=True)

    # 3. Render Empty state landing page if there are no messages
    quick_query = None
    if not st.session_state.messages:
        st.markdown(f"""
        <div class="chat-welcome-container">
            <div style="font-size: 3rem; color: #6366f1; margin-bottom: 1rem;"><i class="fa-solid fa-user-astronaut"></i></div>
            <div class="chat-welcome-title">AI SRE Incident Co-Pilot</div>
            <div class="chat-welcome-subtitle">Ask the operational RAG about active alerts, deployment failures, or cluster status.</div>
            <div style="font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.1em; color: #64748b; font-weight: 700; margin-bottom: 1rem;">Suggested Queries</div>
        </div>
        """, unsafe_allow_html=True)
        
        card_cols = st.columns(3)
        suggestions = [
            {
                "title": "<i class='fa-solid fa-triangle-exclamation' style='color:#ef4444;'></i> Outage Diagnostic",
                "text": "Why is /api/inventory returning 503 errors?",
                "desc": "Analyze connection pool exhaustion and database timeouts in inventory-service.",
                "action": "Diagnose Outage ↗",
                "key": "card_q1"
            },
            {
                "title": "<i class='fa-solid fa-gauge-high' style='color:#fbbf24;'></i> Performance Audit",
                "text": "Explain what caused the valuation timeout.",
                "desc": "Investigate HTTP connection read timeouts and downstream dependency delays.",
                "action": "Analyze Latency ↗",
                "key": "card_q2"
            },
            {
                "title": "<i class='fa-solid fa-shield-lock' style='color:#3b82f6;'></i> Security Investigation",
                "text": "Show all security warnings or failed logins.",
                "desc": "Audit brute-force authentication attempts and admin account lock throttles.",
                "action": "Audit Security ↗",
                "key": "card_q3"
            }
        ]
        
        for idx, sug in enumerate(suggestions):
            with card_cols[idx]:
                st.markdown(f"""
                <div class="chat-suggestion-card">
                    <div class="chat-suggestion-title">{sug['title']}</div>
                    <div class="chat-suggestion-desc" style="margin-bottom: 1rem; height: 50px; overflow: hidden;">{sug['desc']}</div>
                </div>
                """, unsafe_allow_html=True)
                if st.button(sug["action"], key=sug["key"], use_container_width=True):
                    quick_query = sug["text"]

    # 4. Handle input
    user_input = st.chat_input("Query incident logs...")
    if user_input or quick_query:
        query_to_run = user_input if user_input else quick_query
        
        st.session_state.messages.append({"role": "user", "content": query_to_run})
        
        with st.chat_message("user"):
            st.markdown(f"""
            <div class="chat-bubble-user">
                <div class="chat-role-header" style="color: #6366f1;"><i class="fa-solid fa-user-gear"></i> SRE Operator</div>
                <div style="color: #e2e8f0; font-size: 0.92rem;">{query_to_run}</div>
            </div>
            """, unsafe_allow_html=True)
            
        with st.chat_message("assistant"):
            st.markdown(f"""
            <div class="chat-bubble-assistant">
                <div class="chat-role-header" style="color: #10b981;"><i class="fa-solid fa-robot"></i> AI SRE Assistant</div>
                <div style="color: #e2e8f0; font-size: 0.92rem; line-height: 1.6;">
            """, unsafe_allow_html=True)
            
            with st.spinner("Analyzing operational telemetry indices..."):
                answer = ""
                citations = []
                
                is_pipeline_query = any(kw in query_to_run.lower() for kw in ["pipeline", "job", "workflow", "ci/cd", "ci-cd", "github action", "actions run", "current build", "build status", "task", "run status", "what is being performed", "execution jobs", "current run"])
                is_k8s_query = any(kw in query_to_run.lower() for kw in ["kubernetes", "k8s", "cluster", "pod", "deployment", "node", "namespace"])
                
                if is_pipeline_query:
                    current_anomaly_state = anomaly_mapping[active_anomaly]
                    pipeline_state = fetch_github_workflow_status(current_anomaly_state)
                    
                    if "error" in pipeline_state:
                        answer = f"""### 🔄 GitHub Actions Pipeline Status
                        
⚠️ **Could not fetch live status:** {pipeline_state['error']}
                        
Please verify your GitHub credentials or connectivity."""
                        citations = []
                    else:
                        status = pipeline_state.get("status", "unknown")
                        conclusion = pipeline_state.get("conclusion")
                        run_number = pipeline_state.get("run_number")
                        repo_name = pipeline_state.get("repo")
                        html_url = pipeline_state.get("html_url")
                        branch = pipeline_state.get("head_branch")
                        commit_msg = pipeline_state.get("head_commit_message")
                        actor = pipeline_state.get("actor")
                        source = pipeline_state.get("source")
                        
                        status_emoji = "🟢 Success" if conclusion == "success" else ("🔴 Failed" if conclusion == "failure" else "🟡 In Progress" if status == "in_progress" else "⚪ Queued/Unknown")
                        
                        jobs_status_list = []
                        for job in pipeline_state.get("jobs", []):
                            job_conclusion = job.get("conclusion")
                            job_status = job.get("status")
                            job_emoji = "🟢" if job_conclusion == "success" else ("🔴" if job_conclusion == "failure" else "🟡" if job_status == "in_progress" else "⚪")
                            
                            job_state = "Completed successfully" if job_conclusion == "success" else ("Failed" if job_conclusion == "failure" else "In Progress" if job_status == "in_progress" else "Queued/Skipped")
                            jobs_status_list.append(f"- {job_emoji} **{job['name']}**: {job_state}")
                            
                        jobs_summary = "\n".join(jobs_status_list)
                        
                        answer = f"""### 🔄 GitHub Actions Pipeline Status (Source: {source.upper()})
                        
The latest workflow run **#{run_number}** for repository **[{repo_name}](https://github.com/{repo_name})** on branch `{branch}` is currently **{status_emoji}**.
                        
#### 📋 Run Details
- **Workflow Name**: {pipeline_state.get('name', 'DevSecOps CI/CD Pipeline')}
- **Trigger Event**: `{pipeline_state.get('event')}` by **@{actor}**
- **Latest Commit**: `{commit_msg}` (`{pipeline_state.get('head_sha')[:7] if pipeline_state.get('head_sha') else ''}`)
- **Workflow Link**: [View on GitHub Actions]({html_url})
                        
#### 🛠️ Job Execution Breakdown
{jobs_summary}"""
                        if conclusion == "failure":
                            failed_jobs = [j['name'] for j in pipeline_state.get('jobs', []) if j.get('conclusion') == 'failure']
                            answer += f"\n\n#### 🚨 SRE Outage Correlation\n"
                            answer += f"The pipeline failed during the **{', '.join(failed_jobs)}** stage. "
                            
                            recent_incidents = load_local_incidents()
                            if recent_incidents:
                                latest_inc = recent_incidents[-1]
                                answer += f"This failure correlates with the active incident: **{latest_inc['service']} ({latest_inc['severity']})** - *\"{latest_inc['message']}\"*.\n\n"
                                answer += f"**Recommended Action:**\n{latest_inc['answer']}"
                            else:
                                answer += "Check the live logs panel for recent exception traces. Verify configs in sonar-project.properties or pull secrets."
                                
                        citations = []
                        for job in pipeline_state.get("jobs", []):
                            if job.get("conclusion") == "failure" or job.get("status") == "in_progress":
                                citations.append({
                                    "timestamp": datetime.utcnow().isoformat() + "Z",
                                    "service": "GitHub Actions",
                                    "level": "ERROR" if job.get("conclusion") == "failure" else "WARNING",
                                    "message": f"Job '{job['name']}' status is '{job['conclusion'] or job['status']}' on branch {branch}"
                                })
                                
                elif is_k8s_query:
                    real_pods = get_real_k8s_pods()
                    if real_pods:
                        pod_rows = []
                        for p in real_pods:
                            status_emoji = "🟢" if p["status"] == "Running" else ("🔴" if p["status"] in ["ImagePullBackOff", "Error", "CrashLoopBackOff"] else "🟡")
                            pod_rows.append(f"| {p['name']} | {status_emoji} `{p['status']}` | `{p['probe']}` | {p['restarts']} | `{p['ip']}` | `{p['node']}` | {p['age']} |")
                        
                        pods_summary = "\n".join(pod_rows)
                        answer = f"""### ☸️ Live AKS Kubernetes Cluster Status
                        
Here is the current real-time status of the pods running in the `log-analysis` namespace on your AKS cluster:

| Pod Name | Status | Probes | Restarts | Pod IP | Node | Age |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
{pods_summary}

#### 📋 Cluster Summary
- **Namespace**: `log-analysis`
- **Active Node**: `aks-loganalysis-17870339-vmss000000`
- **Telemetry Health**: Telemetry is flowing successfully from all active microservice endpoints.
"""
                    else:
                        answer = """### ☸️ Kubernetes Cluster Status (Local Simulator Mode)
                        
⚠️ **Could not connect to live AKS Cluster.** Showing simulated pod status:

| Pod Name | Status | Probes | Restarts | Node | Age |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `gateway-7b89d4fb98-abcde` | 🟢 `Running` | `2/2` | 0 | `aks-agentpool-1` | `2d 5h` |
| `auth-service-589fc8c457-b248a` | 🟢 `Running` | `1/1` | 0 | `aks-agentpool-1` | `2d 5h` |
| `inventory-service-678fd9c8d5-w892v` | 🟢 `Running` | `1/1` | 0 | `aks-agentpool-2` | `2d 5h` |
| `valuation-service-84cf959b8c-n624b` | 🟢 `Running` | `1/1` | 0 | `aks-agentpool-2` | `2d 5h` |
| `mongodb-589dfc74bc-x718a` | 🟢 `Running` | `1/1` | 0 | `aks-agentpool-2` | `2d 5h` |

Please verify that:
1. You have logged into Azure CLI using `az login`.
2. You have retrieved AKS credentials using:
   `az aks get-credentials --resource-group log_analysis-rg --name log-analysis-cluster`
3. Your local `kubectl` is configured and active.
"""
                    citations = []
                    
                elif azure_configured:
                    try:
                        engine = LogRageEngine()
                        result = engine.run_query(query_to_run)
                        answer = result.get("answer")
                        citations = result.get("citations", [])
                    except Exception as rag_err:
                        err_msg = f"⚠️ **Azure RAG Execution failed**: {str(rag_err)}"
                        diag_res = classify_devops_error(query_to_run)
                        if diag_res["type"] != "General DevOps / Operational Issue":
                            answer = f"""{err_msg}

### 🔍 Local DevOps Diagnostics (Fallback)

We analyzed the signature of your query and matched it to a **{diag_res['type']}** during the **{diag_res['stage']}**.

#### 📁 Root Cause Analysis
* **Identified Cause**: {diag_res['cause']}

#### 🛠️ Recommended Action Plan
{diag_res['remedy']}
"""
                        else:
                            answer = f"""{err_msg}

### AutoHub Operations Summary (Fallback)

All systems are reporting healthy. If you are currently troubleshooting an incident, please select an anomaly scenario in the left sidebar to generate error traces.
"""
                        citations = []
                        azure_configured = False
                        
                if not is_pipeline_query and not is_k8s_query and not azure_configured and not answer:
                    time.sleep(1.2)
                    relevant_logs = load_local_logs()
                    local_incidents = load_local_incidents()
                    
                    # Identify the currently active anomaly state
                    current_anomaly = anomaly_mapping[active_anomaly]  # 'healthy', 'db_locked', 'timeout', 'brute_force'
                    
                    # Check if the user is asking a general question about status/outage/errors
                    is_general_query = any(kw in query_to_run.lower() for kw in [
                        "what is", "why is", "outage", "error", "fail", "issue", "problem", "wrong", 
                        "happen", "status", "health", "analyze", "diagnose", "incident", "out", "down", 
                        "broken", "crash", "alert"
                    ])
                    
                    # 1. If an active anomaly is simulated and the query matches the service, or is general
                    if current_anomaly == "db_locked" and (is_general_query or any(kw in query_to_run.lower() for kw in ["db", "database", "503", "inventory", "hikaripool", "connection", "lock"])):
                        citations = [l for l in relevant_logs if l["service"] in ["inventory-service", "gateway"]]
                        db_incident = next((inc for inc in local_incidents if inc["service"] == "inventory-service" or "inventory" in inc["message"].lower()), None)
                        if db_incident:
                            answer = db_incident["answer"]
                        else:
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
                    elif current_anomaly == "timeout" and (is_general_query or any(kw in query_to_run.lower() for kw in ["timeout", "502", "504", "valuation", "gateway", "read timed out", "delay", "slow"])):
                        citations = [l for l in relevant_logs if l["service"] in ["valuation-service", "gateway"]]
                        val_incident = next((inc for inc in local_incidents if inc["service"] == "valuation-service" or "valuation" in inc["message"].lower()), None)
                        if val_incident:
                            answer = val_incident["answer"]
                        else:
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
                    elif current_anomaly == "brute_force" and (is_general_query or any(kw in query_to_run.lower() for kw in ["security", "login", "auth", "brute", "failed", "attempt", "unauthorized", "admin", "401", "429"])):
                        citations = [l for l in relevant_logs if l["service"] in ["auth-service", "gateway"]]
                        auth_incident = next((inc for inc in local_incidents if inc["service"] == "auth-service" or "auth" in inc["message"].lower()), None)
                        if auth_incident:
                            answer = auth_incident["answer"]
                        else:
                            answer = """
### Security Investigation: Brute-Force Authentication Attempt Detected

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
                        diag_res = classify_devops_error(query_to_run)
                        if diag_res["type"] != "General DevOps / Operational Issue":
                            citations = []
                            answer = f"""### 🔍 DevOps Diagnostic Advisor (Local Fallback)

We analyzed the signature of your query and matched it to a **{diag_res['type']}** during the **{diag_res['stage']}**.

#### 📁 Root Cause Analysis
* **Identified Cause**: {diag_res['cause']}

#### 🛠️ Recommended Action Plan
{diag_res['remedy']}
"""
                        else:
                            if current_anomaly != "healthy":
                                citations = relevant_logs[-5:] if relevant_logs else []
                                if current_anomaly == "db_locked":
                                    answer = f"""### 🛡️ Active Outage Incident Detected

There is an active simulated outage: **Database Connection Timeout on inventory-service**.
Your query *"{query_to_run}"* does not specifically target this issue, but here is the quick diagnostic summary:

- **Affected Service**: `inventory-service` / `gateway`
- **Current Status**: Failing health checks with 503 errors.
- **Root Cause**: Relational database connection pool exhaustion (max limit of 50 connections exceeded).

**Ask me: "Why is /api/inventory returning 503 errors?"** or check the **Operational Console** tab for complete details.
"""
                                elif current_anomaly == "timeout":
                                    answer = f"""### 🛡️ Active Outage Incident Detected

There is an active simulated outage: **Valuation Gateway Timeout (Network Failure)**.
Your query *"{query_to_run}"* does not specifically target this issue, but here is the quick diagnostic summary:

- **Affected Service**: `valuation-service` / `gateway`
- **Current Status**: Returning 502/504 errors on readiness probe failure.
- **Root Cause**: Downstream network read timeout (2.0s exceeded) calling `inventory-service`.

**Ask me: "Explain what caused the valuation timeout."** or check the **DevOps Suggestion Hub** tab for complete details.
"""
                                else: # brute_force
                                    answer = f"""### 🛡️ Active Security Incident Detected

There is an active simulated anomaly: **Brute-Force Login Security Alert**.
Your query *"{query_to_run}"* does not specifically target this issue, but here is the quick diagnostic summary:

- **Affected Service**: `auth-service` / `gateway`
- **Current Status**: Throwing security alerts and activating auth throttles.
- **Root Cause**: Multiple failed login attempts (5+) detected on user `admin` within 60 seconds.

**Ask me: "Show all security warnings or failed logins."** or check the **Operational Console** tab for complete details.
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
                    cit_rows = ""
                    for cit in citations:
                        level = cit.get("level", "INFO")
                        lvl_color = "#10b981"
                        if level in ["ERROR", "CRITICAL"]:
                            lvl_color = "#ef4444"
                        elif level == "WARNING":
                            lvl_color = "#fbbf24"
                            
                        cit_rows += f"""
                        <div class="chat-citation-row">
                            <span class="chat-citation-time">[{cit.get('timestamp')}]</span>
                            <span class="chat-citation-level" style="background:{lvl_color}18; color:{lvl_color}; border:1px solid {lvl_color}30;">{level}</span>
                            <span class="chat-citation-service">{cit.get('service')}</span>:
                            <span class="chat-citation-msg">{cit.get('message')}</span>
                        </div>
                        """
                    
                    st.markdown(f"""
                    <div class="chat-citation-header">
                        <div class="chat-citation-title"><i class="fa-solid fa-database"></i> Cited Source Telemetry</div>
                        <div class="chat-citation-console">
                            {cit_rows}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                
                st.session_state.messages.append({"role": "assistant", "content": answer, "citations": citations})
                st.markdown("</div></div>", unsafe_allow_html=True)


# Start the background simulator thread after all functions and variables are loaded
start_simulation_thread()
