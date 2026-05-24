import time
import uuid
import os
from datetime import datetime
import requests
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from logger import get_json_logger

app = FastAPI(title="AutoHub Car Valuation Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = get_json_logger("valuation_service", "valuation-service")

INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://inventory-service:8000")

ANOMALY_STATE = {
    "network_error": False,
    "latency_ms": 0
}

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
    if ANOMALY_STATE["network_error"]:
        raise HTTPException(status_code=503, detail="Service Unavailable - Backend timeout")
    return {"status": "healthy", "service": "valuation-service"}

def compute_valuation(car: dict) -> dict:
    current_year = datetime.now().year
    age = current_year - car["year"]
    base = car["price"]
    dep_rate = 0.15 + (0.12 * max(0, age - 1))
    dep_rate = min(dep_rate, 0.75)
    mileage_penalty = (car["mileage"] / 100000) * 0.05 * base
    market_value = round(base * (1 - dep_rate) - mileage_penalty, 2)
    market_value = max(market_value, base * 0.10)
    return {
        "car_id": car["id"],
        "make": car["make"],
        "model": car["model"],
        "year": car["year"],
        "original_price": base,
        "market_value": round(market_value),
        "depreciation_pct": round(dep_rate * 100, 1),
        "age_years": age,
        "mileage": car["mileage"],
        "image_url": car.get("image_url", "")
    }

@app.get("/api/valuation")
def get_valuations():
    if ANOMALY_STATE["network_error"]:
        logger.error("Valuation calculation failed: inventory-service did not respond within timeout")
        raise HTTPException(status_code=504, detail="Gateway Timeout - Inventory service unreachable")
        
    try:
        resp = requests.get(f"{INVENTORY_SERVICE_URL}/api/inventory", timeout=2.0)
        if resp.status_code != 200:
            logger.error(f"Failed to fetch inventory for valuation: Status {resp.status_code}")
            raise HTTPException(status_code=502, detail="Failed to fetch data from Inventory service")
            
        inventory_data = resp.json()
        cars = inventory_data.get("cars", [])
        
        valuations = [compute_valuation(car) for car in cars]
        logger.info(f"Valuation batch computed for {len(valuations)} vehicles")
        return {"valuations": valuations, "total": len(valuations)}
        
    except requests.RequestException as e:
        logger.error(f"Valuation calculation failed: inventory-service connection exception: {str(e)}")
        raise HTTPException(status_code=502, detail="Inventory service communication failure")

@app.get("/api/valuation/{car_id}")
def get_car_valuation(car_id: str):
    if ANOMALY_STATE["network_error"]:
        logger.error(f"Valuation failed for {car_id}: inventory-service did not respond within timeout")
        raise HTTPException(status_code=504, detail="Gateway Timeout - Inventory service unreachable")
        
    try:
        resp = requests.get(f"{INVENTORY_SERVICE_URL}/api/inventory/{car_id}", timeout=2.0)
        if resp.status_code == 404:
            raise HTTPException(status_code=404, detail="Car not found")
        elif resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch car data")
            
        car = resp.json()
        valuation = compute_valuation(car)
        logger.info(f"Single valuation computed for car: {car_id}")
        return valuation
        
    except requests.RequestException as e:
        logger.error(f"Valuation failed for {car_id}: inventory-service connection exception: {str(e)}")
        raise HTTPException(status_code=502, detail="Inventory service communication failure")

@app.post("/api/valuation/simulate-anomaly")
def simulate_anomaly(anomaly: dict):
    ANOMALY_STATE["network_error"] = anomaly.get("network_error", False)
    ANOMALY_STATE["latency_ms"] = anomaly.get("latency_ms", 0)
    logger.warning(f"Anomaly injected in valuation-service: {ANOMALY_STATE}")
    return {"message": "Anomaly updated", "current_state": ANOMALY_STATE}
