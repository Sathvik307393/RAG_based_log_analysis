import time
import uuid
import os
import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from logger import get_json_logger

app = FastAPI(title="AutoHub Fuel Tracker Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = get_json_logger("fuel_service", "fuel-service")

INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://inventory-service:8000")

# Localized Indian fuel rates (INR per litre) and odometer logs
FUEL_LOGS = {
    "f1": {"id": "f1", "car_id": "c1", "date": "2024-04-20", "litres": 45.2, "cost_per_litre": 101.50, "odometer": 14800, "full_tank": True},
    "f2": {"id": "f2", "car_id": "c3", "date": "2024-04-18", "litres": 38.0, "cost_per_litre": 96.70, "odometer": 24200, "full_tank": True},
    "f3": {"id": "f3", "car_id": "c1", "date": "2024-03-30", "litres": 42.0, "cost_per_litre": 99.80, "odometer": 13900, "full_tank": True},
}

ANOMALY_STATE = {
    "db_error": False,
    "latency_ms": 0
}

class FuelCreate(BaseModel):
    car_id: str
    litres: float
    cost_per_litre: float
    odometer: int
    full_tank: bool = True
    date: str = None

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
        raise HTTPException(status_code=503, detail="Database Connection Failure")
    return {"status": "healthy", "service": "fuel-service"}

@app.get("/api/fuel")
def get_fuel_logs(car_id: str = None):
    if ANOMALY_STATE["db_error"]:
        logger.error("DB Query failed in get_fuel_logs: connection error")
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    logs = []
    for l_id, l in FUEL_LOGS.items():
        l_copy = dict(l)
        if not car_id or l_copy["car_id"] == car_id:
            l_copy["total_cost"] = round(l_copy["litres"] * l_copy["cost_per_litre"], 2)
            logs.append(l_copy)

    stats = {}
    for l in logs:
        cid = l["car_id"]
        if cid not in stats:
            stats[cid] = {"total_litres": 0, "total_cost": 0, "fill_count": 0}
        stats[cid]["total_litres"] += l["litres"]
        stats[cid]["total_cost"] += round(l["litres"] * l["cost_per_litre"], 2)
        stats[cid]["fill_count"] += 1

    for cid, s in stats.items():
        s["avg_cost_per_litre"] = round(s["total_cost"] / s["total_litres"], 2) if s["total_litres"] else 0
        try:
            resp = requests.get(f"{INVENTORY_SERVICE_URL}/api/inventory/{cid}", timeout=2.0)
            if resp.status_code == 200:
                s["car"] = resp.json()
            else:
                s["car"] = {}
        except Exception as e:
            logger.error(f"Error fetching car info from inventory service: {str(e)}")
            s["car"] = {}

    return {"logs": logs, "stats_by_car": stats, "total_entries": len(logs)}

@app.post("/api/fuel")
def create_fuel_log(log: FuelCreate):
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    fid = "f" + str(len(FUEL_LOGS) + 1)
    while fid in FUEL_LOGS:
        fid = "f" + str(int(fid[1:]) + 1)
        
    import datetime
    log_date = log.date or str(datetime.date.today())
    new_log = {
        "id": fid,
        "car_id": log.car_id,
        "date": log_date,
        "litres": log.litres,
        "cost_per_litre": log.cost_per_litre,
        "odometer": log.odometer,
        "full_tank": log.full_tank
    }
    
    # Check if car exists
    try:
        resp = requests.get(f"{INVENTORY_SERVICE_URL}/api/inventory/{log.car_id}", timeout=2.0)
        if resp.status_code == 404:
            logger.warning(f"Fuel log rejected: car {log.car_id} not found in fleet")
            raise HTTPException(status_code=400, detail="Invalid Car ID")
    except requests.RequestException:
        pass
        
    FUEL_LOGS[fid] = new_log
    logger.info(f"New fuel fill-up logged: {fid} - {log.litres}L for car {log.car_id}")
    return new_log

@app.delete("/api/fuel/{log_id}")
def delete_fuel_log(log_id: str):
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    if log_id not in FUEL_LOGS:
        raise HTTPException(status_code=404, detail="Log not found")
        
    deleted = FUEL_LOGS.pop(log_id)
    logger.info(f"Fuel log deleted: {log_id}")
    return {"message": "Deleted", "deleted": deleted}

@app.post("/api/fuel/simulate-anomaly")
def simulate_anomaly(anomaly: dict):
    ANOMALY_STATE["db_error"] = anomaly.get("db_error", False)
    ANOMALY_STATE["latency_ms"] = anomaly.get("latency_ms", 0)
    logger.warning(f"Anomaly injected in fuel-service: {ANOMALY_STATE}")
    return {"message": "Anomaly updated", "current_state": ANOMALY_STATE}
