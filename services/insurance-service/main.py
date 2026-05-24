import time
import uuid
import os
from datetime import datetime
import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from logger import get_json_logger

app = FastAPI(title="AutoHub Insurance Manager Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = get_json_logger("insurance_service", "insurance-service")

INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://inventory-service:8000")

# Localized Indian insurance policies (Premiums in INR)
INSURANCE_POLICIES = {
    "i1": {"id": "i1", "car_id": "c1", "provider": "Tata AIG General Insurance", "policy_no": "TA-2024-5412", "type": "Comprehensive", "premium": 45000, "start": "2024-01-01", "end": "2025-01-01", "status": "active"},
    "i2": {"id": "i2", "car_id": "c3", "provider": "HDFC ERGO General Insurance", "policy_no": "HE-2024-7892", "type": "Third Party", "premium": 18000, "start": "2024-02-15", "end": "2025-02-15", "status": "active"},
    "i3": {"id": "i3", "car_id": "c5", "provider": "ICICI Lombard Insurance", "policy_no": "IL-2024-0034", "type": "Comprehensive", "premium": 40000, "start": "2024-03-01", "end": "2025-03-01", "status": "active"},
}

ANOMALY_STATE = {
    "db_error": False,
    "latency_ms": 0
}

class PolicyCreate(BaseModel):
    car_id: str
    provider: str
    policy_no: str
    type: str
    premium: float
    start: str
    end: str

@app.middleware("http")
async def log_requests(request: Request, call_next):
    if ANOMALY_STATE["latency_ms"] > 0:
        time.sleep(ANOMALY_STATE["latency_ms"] / 1000.0)
        
    start_time = time.time()
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception as e:
        status_code = 500
        logger.error(
            f"Unhandled exception: {str(e)}",
            extra={
                "http_method": request.method,
                "http_path": request.url.path,
                "status_code": status_code,
                "latency_ms": round((time.time() - start_time) * 1000.0, 2),
                "request_id": request_id
            }
        )
        return Response(content=f"Internal Server Error: {str(e)}", status_code=500)
    finally:
        latency = (time.time() - start_time) * 1000.0
        logger.info(
            f"{request.method} {request.url.path} -> {status_code} ({latency:.1f}ms)",
            extra={
                "http_method": request.method,
                "http_path": request.url.path,
                "status_code": status_code,
                "latency_ms": round(latency, 2),
                "request_id": request_id
            }
        )

@app.get("/health")
def health():
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Throttled / Latency")
    return {"status": "healthy", "service": "insurance-service"}

@app.get("/api/insurance")
def get_policies():
    if ANOMALY_STATE["db_error"]:
        logger.error("DB Query failed in get_policies: database timed out")
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    policies = []
    for p_id, p in INSURANCE_POLICIES.items():
        p_copy = dict(p)
        # Fetch car info
        try:
            resp = requests.get(f"{INVENTORY_SERVICE_URL}/api/inventory/{p['car_id']}", timeout=2.0)
            if resp.status_code == 200:
                p_copy["car"] = resp.json()
            else:
                p_copy["car"] = {}
        except Exception as e:
            logger.error(f"Error fetching car from inventory: {str(e)}")
            p_copy["car"] = {}
            
        try:
            end_date = datetime.strptime(p_copy["end"], "%Y-%m-%d")
            days_left = (end_date - datetime.now()).days
            p_copy["days_until_expiry"] = days_left
            p_copy["expiry_alert"] = days_left <= 30
        except Exception:
            p_copy["days_until_expiry"] = 365
            p_copy["expiry_alert"] = False
            
        policies.append(p_copy)
        
    total_premium = sum(p["premium"] for p in policies)
    expiring_soon = len([p for p in policies if p.get("expiry_alert")])
    
    return {
        "policies": policies,
        "total": len(policies),
        "total_annual_premium": total_premium,
        "expiring_soon": expiring_soon
    }

@app.get("/api/insurance/{policy_id}")
def get_policy(policy_id: str):
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    pol = INSURANCE_POLICIES.get(policy_id)
    if not pol:
        raise HTTPException(status_code=404, detail="Policy not found")
        
    pol_copy = dict(pol)
    try:
        resp = requests.get(f"{INVENTORY_SERVICE_URL}/api/inventory/{pol['car_id']}", timeout=2.0)
        if resp.status_code == 200:
            pol_copy["car"] = resp.json()
        else:
            pol_copy["car"] = {}
    except Exception as e:
        logger.error(f"Error calling inventory service: {str(e)}")
        pol_copy["car"] = {}
        
    return pol_copy

@app.post("/api/insurance")
def create_policy(policy: PolicyCreate):
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    pid = "i" + str(len(INSURANCE_POLICIES) + 1)
    while pid in INSURANCE_POLICIES:
        pid = "i" + str(int(pid[1:]) + 1)
        
    # Check if car exists
    try:
        resp = requests.get(f"{INVENTORY_SERVICE_URL}/api/inventory/{policy.car_id}", timeout=2.0)
        if resp.status_code == 404:
            logger.warning(f"Insurance policy rejected: car {policy.car_id} not found in fleet")
            raise HTTPException(status_code=400, detail="Invalid Car ID")
    except requests.RequestException:
        pass
        
    new_policy = {
        "id": pid,
        "car_id": policy.car_id,
        "provider": policy.provider,
        "policy_no": policy.policy_no,
        "type": policy.type,
        "premium": policy.premium,
        "start": policy.start,
        "end": policy.end,
        "status": "active"
    }
    
    INSURANCE_POLICIES[pid] = new_policy
    logger.info(f"New policy attached: {pid} for car {policy.car_id} issued by {policy.provider}")
    return new_policy

@app.put("/api/insurance/{policy_id}")
def update_policy(policy_id: str, updates: dict):
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    if policy_id not in INSURANCE_POLICIES:
        raise HTTPException(status_code=404, detail="Policy not found")
        
    INSURANCE_POLICIES[policy_id].update(updates)
    logger.info(f"Policy updated: {policy_id}")
    return INSURANCE_POLICIES[policy_id]

@app.delete("/api/insurance/{policy_id}")
def delete_policy(policy_id: str):
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    if policy_id not in INSURANCE_POLICIES:
        raise HTTPException(status_code=404, detail="Policy not found")
        
    deleted = INSURANCE_POLICIES.pop(policy_id)
    logger.info(f"Policy deleted: {policy_id}")
    return {"message": "Policy deleted", "deleted": deleted}

@app.post("/api/insurance/simulate-anomaly")
def simulate_anomaly(anomaly: dict):
    ANOMALY_STATE["db_error"] = anomaly.get("db_error", False)
    ANOMALY_STATE["latency_ms"] = anomaly.get("latency_ms", 0)
    logger.warning(f"Anomaly injected in insurance-service: {ANOMALY_STATE}")
    return {"message": "Anomaly updated", "current_state": ANOMALY_STATE}
