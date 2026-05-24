import time
import uuid
import jwt
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from passlib.context import CryptContext
from logger import get_json_logger

app = FastAPI(title="AutoHub Authentication Service", version="1.0.0")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = get_json_logger("auth_service", "auth-service")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = "autohub_secret_key_for_jwt_tokens"
ALGORITHM = "HS256"

# In-memory users DB (pre-populated with an admin user)
USERS = {
    "admin": {
        "username": "admin",
        "email": "admin@autohub.in",
        "hashed_password": pwd_context.hash("admin123"),
        "name": "Sathvik Admin"
    }
}

# Operational anomaly state
ANOMALY_STATE = {
    "db_locked": False,
    "latency_ms": 0
}

class UserRegister(BaseModel):
    username: str
    email: str
    password: str
    name: str

class UserLogin(BaseModel):
    username: str
    password: str

class TokenVerification(BaseModel):
    token: str

@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Simulate DB locks or slow responses
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
def health_check():
    if ANOMALY_STATE["db_locked"]:
        raise HTTPException(status_code=503, detail="Database Connection Locked / Connection Timeout")
    return {"status": "healthy", "service": "auth-service"}

@app.post("/api/auth/register")
def register(user: UserRegister):
    if ANOMALY_STATE["db_locked"]:
        logger.error("Database registration failed: DB Connection locked")
        raise HTTPException(status_code=503, detail="Database Connection Locked")
        
    if user.username in USERS:
        logger.warning(f"Registration failed: User {user.username} already exists")
        raise HTTPException(status_code=400, detail="Username already registered")
        
    hashed_pwd = pwd_context.hash(user.password)
    USERS[user.username] = {
        "username": user.username,
        "email": user.email,
        "hashed_password": hashed_pwd,
        "name": user.name
    }
    logger.info(f"User registered successfully: {user.username}")
    return {"message": "User registered successfully", "username": user.username}

@app.post("/api/auth/login")
def login(user: UserLogin):
    if ANOMALY_STATE["db_locked"]:
        logger.error("Database auth failed: DB Connection locked")
        raise HTTPException(status_code=503, detail="Database Connection Locked")

    db_user = USERS.get(user.username)
    if not db_user or not pwd_context.verify(user.password, db_user["hashed_password"]):
        logger.warning(f"Failed login attempt for user: {user.username}")
        # Add artificial delay to prevent brute-force (and generate logs)
        time.sleep(1.0)
        raise HTTPException(status_code=401, detail="Invalid username or password")
        
    # Generate JWT token
    payload = {
        "sub": user.username,
        "name": db_user["name"],
        "email": db_user["email"],
        "exp": time.time() + 3600  # Token expires in 1 hour
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    logger.info(f"User logged in successfully: {user.username}")
    return {"access_token": token, "token_type": "bearer", "name": db_user["name"]}

@app.post("/api/auth/verify")
def verify_token(verification: TokenVerification):
    try:
        payload = jwt.decode(verification.token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username not in USERS:
            raise HTTPException(status_code=401, detail="User not found")
        return {"valid": True, "username": username, "name": payload.get("name")}
    except jwt.ExpiredSignatureError:
        logger.warning("Token verification failed: Token expired")
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        logger.warning("Token verification failed: Invalid token")
        raise HTTPException(status_code=401, detail="Invalid token")

@app.post("/api/auth/simulate-anomaly")
def simulate_anomaly(anomaly: dict):
    """Control endpoint to inject issues for Devops/SRE simulation"""
    ANOMALY_STATE["db_locked"] = anomaly.get("db_locked", False)
    ANOMALY_STATE["latency_ms"] = anomaly.get("latency_ms", 0)
    logger.warning(f"Anomaly injected in auth-service: {ANOMALY_STATE}")
    return {"message": "Anomaly updated", "current_state": ANOMALY_STATE}
