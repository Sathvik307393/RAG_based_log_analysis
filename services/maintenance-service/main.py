import time
import uuid
import os
import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from logger import get_json_logger

app = FastAPI(title="AutoHub Service & Maintenance Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = get_json_logger("maintenance_service", "maintenance-service")

INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://inventory-service:8000")

# Prepopulated maintenance records (Costs in INR, mileage in km)
SERVICE_RECORDS = {
    "s1": {"id": "s1", "car_id": "c1", "type": "Engine Oil Service", "date": "2024-03-15", "cost": 6500, "mileage": 14000, "notes": "Shell Helix Ultra 5W-40 Synthetic Oil replaced", "next_due_miles": 24000},
    "s2": {"id": "s2", "car_id": "c3", "type": "Wheel Alignment & Balancing", "date": "2024-02-10", "cost": 1500, "mileage": 24500, "notes": "All 4 wheels aligned, nitrogen filled", "next_due_miles": 29500},
    "s3": {"id": "s3", "car_id": "c5", "type": "Brake Pad Replacement", "date": "2024-04-01", "cost": 4200, "mileage": 17500, "notes": "Front brake pads replaced. Rotors polished.", "next_due_miles": 37500},
}

ANOMALY_STATE = {
    "db_error": False,
    "latency_ms": 0
}

class RecordCreate(BaseModel):
    car_id: str
    type: str
    cost: float
    mileage: int
    next_due_miles: int
    notes: str
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
        raise HTTPException(status_code=503, detail="Database Connection Error")
    return {"status": "healthy", "service": "maintenance-service"}

@app.get("/api/service")
def get_records(car_id: str = None):
    if ANOMALY_STATE["db_error"]:
        logger.error("DB Query failed in get_records: connection error")
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    records = []
    for r_id, r in SERVICE_RECORDS.items():
        r_copy = dict(r)
        if not car_id or r_copy["car_id"] == car_id:
            # Call inventory-service to enrich record
            try:
                resp = requests.get(f"{INVENTORY_SERVICE_URL}/api/inventory/{r_copy['car_id']}", timeout=2.0)
                if resp.status_code == 200:
                    r_copy["car"] = resp.json()
                else:
                    logger.warning(f"Failed to fetch car info from {INVENTORY_SERVICE_URL}: Status {resp.status_code}")
                    r_copy["car"] = {}
            except Exception as e:
                logger.error(f"Error calling inventory-service: {str(e)}")
                r_copy["car"] = {}
                
            records.append(r_copy)
            
    total_cost = sum(r["cost"] for r in records)
    return {"records": records, "total": len(records), "total_cost": total_cost}

@app.get("/api/service/{record_id}")
def get_record(record_id: str):
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    rec = SERVICE_RECORDS.get(record_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")
        
    rec_copy = dict(rec)
    try:
        resp = requests.get(f"{INVENTORY_SERVICE_URL}/api/inventory/{rec['car_id']}", timeout=2.0)
        if resp.status_code == 200:
            rec_copy["car"] = resp.json()
        else:
            rec_copy["car"] = {}
    except Exception as e:
        logger.error(f"Error calling inventory-service: {str(e)}")
        rec_copy["car"] = {}
        
    return rec_copy

@app.post("/api/service")
def create_record(record: RecordCreate):
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    sid = "s" + str(len(SERVICE_RECORDS) + 1)
    while sid in SERVICE_RECORDS:
        sid = "s" + str(int(sid[1:]) + 1)
        
    import datetime
    rec_date = record.date or str(datetime.date.today())
    new_record = {
        "id": sid,
        "car_id": record.car_id,
        "type": record.type,
        "date": rec_date,
        "cost": record.cost,
        "mileage": record.mileage,
        "next_due_miles": record.next_due_miles,
        "notes": record.notes
    }
    
    # Check if car exists in inventory before logging
    try:
        resp = requests.get(f"{INVENTORY_SERVICE_URL}/api/inventory/{record.car_id}", timeout=2.0)
        if resp.status_code == 404:
            logger.warning(f"Service log rejected: car {record.car_id} not found in fleet")
            raise HTTPException(status_code=400, detail="Invalid Car ID: vehicle does not exist")
    except requests.RequestException as e:
        logger.error(f"Inventory validation failed due to network: {str(e)}")
        
    SERVICE_RECORDS[sid] = new_record
    logger.info(f"New service task logged: {sid} - {record.type} for car {record.car_id}")
    return new_record

@app.delete("/api/service/{record_id}")
def delete_record(record_id: str):
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    if record_id not in SERVICE_RECORDS:
        raise HTTPException(status_code=404, detail="Record not found")
        
    deleted = SERVICE_RECORDS.pop(record_id)
    logger.info(f"Service task deleted: {record_id}")
    return {"deleted": deleted}

@app.post("/api/service/simulate-anomaly")
def simulate_anomaly(anomaly: dict):
    ANOMALY_STATE["db_error"] = anomaly.get("db_error", False)
    ANOMALY_STATE["latency_ms"] = anomaly.get("latency_ms", 0)
    logger.warning(f"Anomaly injected in maintenance-service: {ANOMALY_STATE}")
    return {"message": "Anomaly updated", "current_state": ANOMALY_STATE}
