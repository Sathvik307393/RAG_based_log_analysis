import time
import uuid
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from logger import get_json_logger

app = FastAPI(title="AutoHub Car Inventory Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = get_json_logger("inventory_service", "inventory-service")

# Indian fleet inventory (prices in INR, mileage in km, high-quality images)
INVENTORY = {
    "c1": {
        "id": "c1", 
        "make": "Mahindra", 
        "model": "XUV700", 
        "year": 2023, 
        "color": "Midnight Black", 
        "mileage": 15000, 
        "status": "available", 
        "price": 2200000,
        "image_url": "https://images.unsplash.com/photo-1533473359331-0135ef1b58bf?auto=format&fit=crop&w=600&q=80"
    },
    "c2": {
        "id": "c2", 
        "make": "Tata", 
        "model": "Nexon EV", 
        "year": 2023, 
        "color": "Empowered Oxide", 
        "mileage": 8000, 
        "status": "sold", 
        "price": 1750000,
        "image_url": "https://images.unsplash.com/photo-1563720223185-11003d516935?auto=format&fit=crop&w=600&q=80"
    },
    "c3": {
        "id": "c3", 
        "make": "Maruti Suzuki", 
        "model": "Swift", 
        "year": 2022, 
        "color": "Solid Fire Red", 
        "mileage": 25000, 
        "status": "available", 
        "price": 750000,
        "image_url": "https://images.unsplash.com/photo-1583121274602-3e2820c69888?auto=format&fit=crop&w=600&q=80"
    },
    "c4": {
        "id": "c4", 
        "make": "Mahindra", 
        "model": "Thar 4x4", 
        "year": 2023, 
        "color": "Rocky Beige", 
        "mileage": 12000, 
        "status": "reserved", 
        "price": 1600000,
        "image_url": "https://images.unsplash.com/photo-1605559424843-9e4c228bf1c2?auto=format&fit=crop&w=600&q=80"
    },
    "c5": {
        "id": "c5", 
        "make": "Hyundai", 
        "model": "Creta", 
        "year": 2023, 
        "color": "Ranger Khaki", 
        "mileage": 18000, 
        "status": "available", 
        "price": 1850000,
        "image_url": "https://images.unsplash.com/photo-1549399542-7e3f8b79c341?auto=format&fit=crop&w=600&q=80"
    },
}

ANOMALY_STATE = {
    "db_error": False,
    "latency_ms": 0
}

class CarCreate(BaseModel):
    make: str
    model: str
    year: int
    color: str
    mileage: int
    price: float
    status: str = "available"
    image_url: str = None

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
        raise HTTPException(status_code=503, detail="Database Connection Timeout / DB Crash")
    return {"status": "healthy", "service": "inventory-service"}

@app.get("/api/inventory")
def get_inventory(status: str = None):
    if ANOMALY_STATE["db_error"]:
        logger.error("DB Query failed in get_inventory: database connection timed out")
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    cars = list(INVENTORY.values())
    if status:
        cars = [c for c in cars if c["status"] == status]
    return {"cars": cars, "total": len(cars)}

@app.get("/api/inventory/{car_id}")
def get_car(car_id: str):
    if ANOMALY_STATE["db_error"]:
        logger.error(f"DB Query failed in get_car for ID {car_id}: database connection timed out")
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    car = INVENTORY.get(car_id)
    if not car:
        logger.warning(f"Car search failed: ID {car_id} not found")
        raise HTTPException(status_code=404, detail="Car not found")
    return car

@app.post("/api/inventory")
def create_car(car: CarCreate):
    if ANOMALY_STATE["db_error"]:
        logger.error("DB Write failed in create_car: database connection timed out")
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    cid = "c" + str(len(INVENTORY) + 1)
    while cid in INVENTORY:
        cid = "c" + str(int(cid[1:]) + 1)
        
    img_url = car.image_url or "https://images.unsplash.com/photo-1533473359331-0135ef1b58bf?auto=format&fit=crop&w=600&q=80"
    new_car = {
        "id": cid,
        "make": car.make,
        "model": car.model,
        "year": car.year,
        "color": car.color,
        "mileage": car.mileage,
        "price": car.price,
        "status": car.status,
        "image_url": img_url
    }
    INVENTORY[cid] = new_car
    logger.info(f"New vehicle added to fleet: {cid} - {car.make} {car.model}")
    return new_car

@app.put("/api/inventory/{car_id}")
def update_car(car_id: str, car_updates: dict):
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    if car_id not in INVENTORY:
        raise HTTPException(status_code=404, detail="Car not found")
        
    INVENTORY[car_id].update(car_updates)
    logger.info(f"Vehicle updated: {car_id}")
    return INVENTORY[car_id]

@app.delete("/api/inventory/{car_id}")
def delete_car(car_id: str):
    if ANOMALY_STATE["db_error"]:
        raise HTTPException(status_code=503, detail="Database Connection Error")
        
    if car_id not in INVENTORY:
        raise HTTPException(status_code=404, detail="Car not found")
        
    deleted = INVENTORY.pop(car_id)
    logger.info(f"Vehicle removed from fleet: {car_id} - {deleted['make']} {deleted['model']}")
    return {"deleted": deleted}

@app.post("/api/inventory/simulate-anomaly")
def simulate_anomaly(anomaly: dict):
    ANOMALY_STATE["db_error"] = anomaly.get("db_error", False)
    ANOMALY_STATE["latency_ms"] = anomaly.get("latency_ms", 0)
    logger.warning(f"Anomaly injected in inventory-service: {ANOMALY_STATE}")
    return {"message": "Anomaly updated", "current_state": ANOMALY_STATE}
