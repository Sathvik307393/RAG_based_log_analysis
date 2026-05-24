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
    @keyframes pulse {
        0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(255, 179, 0, 0.7); }
        70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(255, 179, 0, 0); }
        100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(255, 179, 0, 0); }
    }
    .dot-green {
        display: inline-block;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background-color: #10b981;
    }
    .dot-red {
        display: inline-block;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background-color: #ef4444;
    }
    .dot-yellow {
        display: inline-block;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background-color: #ffb300;
        animation: pulse 1.5s infinite;
    }
    .dot-grey {
        display: inline-block;
        width: 12px;
        height: 12px;
        border-radius: 50%;
        background-color: #64748b;
    }
    .pipeline-container {
        background: rgba(18, 22, 35, 0.6);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 10px;
        padding: 1.5rem;
        margin-bottom: 1.5rem;
    }
    .pipeline-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px solid rgba(255,255,255,0.08);
        padding-bottom: 0.75rem;
        margin-bottom: 1rem;
    }
    .pipeline-title {
        font-family: 'Barlow Condensed', sans-serif;
        font-weight: 700;
        text-transform: uppercase;
        color: #ff5722;
        margin: 0;
        font-size: 1.25rem;
    }
    .pipeline-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 1rem;
    }
    .pipeline-job-card {
        background: rgba(4, 7, 15, 0.5);
        border: 1px solid rgba(255,255,255,0.05);
        border-radius: 6px;
        padding: 0.75rem;
        text-align: left;
    }
    .pipeline-job-name {
        font-size: 0.85rem;
        font-weight: 600;
        margin-bottom: 0.25rem;
        color: #f1f5f9;
    }
    .pipeline-job-status {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        font-size: 0.75rem;
        color: #94a3b8;
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
        r = requests.get(runs_url, headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            runs = data.get("workflow_runs", [])
            if not runs:
                return generate_mock_workflow_status(anomaly_type)
            
            latest_run = runs[0]
            run_id = latest_run["id"]
            
            jobs_url = f"https://api.github.com/repos/{repo_fullname}/actions/runs/{run_id}/jobs"
            jr = requests.get(jobs_url, headers=headers, timeout=5)
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

# ─────────────────────────────────────────────
#  CI/CD Pipeline Monitor Widget
# ─────────────────────────────────────────────
st.markdown("### 🔄 CI/CD DevSecOps Pipeline Monitor")

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
        
    def get_job_diagnostic(name, steps=None):
        name_lower = name.lower()
        
        # Check step details first to find the exact failed step
        failed_step = None
        if steps:
            for s in steps:
                if s.get("conclusion") == "failure":
                    failed_step = s.get("name")
                    break
        
        # Determine source and remedy based on failed step or job name
        check_str = ((failed_step.lower() + " ") if failed_step else "") + name_lower
        
        if "lint" in check_str or "flake8" in check_str:
            source = "Python code style check (flake8)"
            remedy = "Verify that Python syntax is correct and formatting matches PEP 8 guidelines. Run flake8 locally to identify and fix style violations."
        elif "unit test" in check_str or "pytest" in check_str or "testing" in check_str:
            source = "Unit Testing Framework (pytest)"
            remedy = "Check the unit test logs. Fix failing assertions, mock dependencies correctly, or check for unhandled exceptions in the test files."
        elif "snyk" in check_str or "dependency" in check_str:
            source = "Snyk Dependency & SAST Scanner"
            remedy = "Check the dependency vulnerability log. Upgrade vulnerable third-party library versions listed in requirements.txt to clean security gates."
        elif "sonar" in check_str:
            source = "SonarQube Quality Gate & Analysis"
            remedy = "Check that the SonarQube scanner is correctly configured in sonar-project.properties and that the target SonarQube instance is reachable over the private network."
        elif "docker" in check_str or "build" in check_str or "image" in check_str or "trivy" in check_str:
            source = "Docker Build / Trivy Vulnerability Scan"
            remedy = "Inspect the Dockerfile for build command failures, ensure base images are accessible, and check Trivy logs for high/critical security issues."
        elif "deploy" in check_str or "kube" in check_str or "k8s" in check_str or "manifest" in check_str:
            source = "Kubernetes Deployment Dry Run (Kubeconform)"
            remedy = "Validate your k8s/ manifests. Ensure proper YAML syntax (indentation) and correct resource schemas (e.g. apps/v1 for Deployments). (Note: Deprecated kubeval has been replaced by kubeconform to resolve schema certificate issues)."
        elif "dast" in check_str or "zap" in check_str or "baseline" in check_str:
            source = "Dynamic Application Security Testing (OWASP ZAP)"
            remedy = "Verify the target application endpoint is running and responding. Adjust DAST rules in .zap/rules.tsv if there are false positives."
        elif "mail" in check_str or "email" in check_str or "notify" in check_str:
            source = "Notification Workflow (action-send-mail)"
            remedy = "Check SMTP server settings, confirm credentials (MAIL_USERNAME/MAIL_PASSWORD) are set in secrets, and check recipient mailboxes."
        else:
            source = "General Actions Workflow Step"
            remedy = f"Inspect the job step logs directly in the GitHub Actions Console. Check the build logs for '{failed_step or name}'."

        return {
            "source": source,
            "failed_step": failed_step,
            "remedy": remedy
        }

    # Build the diagnostic panel content
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
<h4 style="margin: 0 0 10px 0; color: #ef4444; font-size: 1rem; text-transform: uppercase; font-family: 'Barlow Condensed', sans-serif;">🚨 Pipeline Outage Diagnostics</h4>
<div style="max-height: 250px; overflow-y: auto;">
{failed_list_html}
</div>
</div>"""
    elif status == "in_progress":
        diagnostic_html = f"""<div style="background: rgba(255, 179, 0, 0.05); border: 1px solid rgba(255, 179, 0, 0.2); border-radius: 10px; padding: 1.25rem; height: 100%;">
<h4 style="margin: 0 0 10px 0; color: #ffb300; font-size: 1rem; text-transform: uppercase; font-family: 'Barlow Condensed', sans-serif;">🟡 Active Build running</h4>
<p style="font-size: 0.85rem; color: #94a3b8; margin: 0;">GitHub Actions is actively compiling code and executing security gates. Use the Refresh button above to poll live status.</p>
</div>"""
    else:
        diagnostic_html = f"""<div style="background: rgba(16, 185, 129, 0.05); border: 1px solid rgba(16, 185, 129, 0.2); border-radius: 10px; padding: 1.25rem; height: 100%;">
<h4 style="margin: 0 0 10px 0; color: #10b981; font-size: 1rem; text-transform: uppercase; font-family: 'Barlow Condensed', sans-serif;">🟢 Pipeline Healthy</h4>
<p style="font-size: 0.85rem; color: #94a3b8; margin: 0;">All DevSecOps verification checks and Kubeconform linter tests have passed successfully. System integrity is verified.</p>
</div>"""

    # We use Streamlit columns to separate jobs grid (left) and diagnostic details (right)
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
            
            is_pipeline_query = any(kw in query_to_run.lower() for kw in ["pipeline", "job", "workflow", "ci/cd", "ci-cd", "github action", "actions run", "current build", "build status"])
            
            if is_pipeline_query:
                # Fetch pipeline status
                current_anomaly_state = anomaly_mapping[active_anomaly]
                pipeline_state = fetch_github_workflow_status(current_anomaly_state)
                
                if "error" in pipeline_state:
                    answer = f"""### 🔄 GitHub Actions Pipeline Status
                    
⚠️ **Could not fetch live status:** {pipeline_state['error']}
                    
Please verify your GitHub credentials or connectivity. Under healthy simulated operations, the local mock pipeline reports success."""
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
                            answer += "Check the live logs panel for recent exception traces. If SonarQube fails, ensure that the sonar-project.properties is correctly configured and the AWS SonarQube server is reachable over the VPN. If Trivy or Deploy fails, verify the docker file path and registry pull secrets."
                    
                    citations = []
                    for job in pipeline_state.get("jobs", []):
                        if job.get("conclusion") == "failure" or job.get("status") == "in_progress":
                            citations.append({
                                "timestamp": datetime.utcnow().isoformat() + "Z",
                                "service": "GitHub Actions",
                                "level": "ERROR" if job.get("conclusion") == "failure" else "WARNING",
                                "message": f"Job '{job['name']}' status is '{job['conclusion'] or job['status']}' on branch {branch}"
                            })
                            
            elif azure_configured:
                # Run actual Azure RAG Engine
                try:
                    engine = LogRageEngine()
                    result = engine.run_query(query_to_run)
                    answer = result.get("answer")
                    citations = result.get("citations", [])
                except Exception as rag_err:
                    answer = f"Azure RAG Execution failed: {str(rag_err)}. Falling back to local responder."
                    azure_configured = False
            
            if not is_pipeline_query and not azure_configured:
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
