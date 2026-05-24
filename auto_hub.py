#!/usr/bin/env python3
"""
AutoHub - Automobile Management Platform
Single-file Python app with path-based routing, server monitoring metrics,
car images pulled from Unsplash, and 6 microservices:
  /api/inventory   - Car Inventory Service
  /api/service     - Service & Maintenance Service
  /api/valuation   - Car Valuation Service
  /api/fuel        - Fuel Tracker Service
  /api/insurance   - Insurance Manager Service
  /api/metrics     - System Performance & Monitoring Service
  /                - Serves the frontend UI

Run: python auto_hub.py
Visit: http://localhost:5000
"""

import json
import uuid	
import math
import time
import threading
import platform
import subprocess
from datetime import datetime, date
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Try to use ThreadingHTTPServer to avoid single-thread blocking (Python 3.7+)
try:
    from http.server import ThreadingHTTPServer
except ImportError:
    from socketserver import ThreadingMixIn
    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

# ─────────────────────────────────────────────
#  Metrics tracking datastore & lock
# ─────────────────────────────────────────────
METRICS = {
    "uptime_start": time.time(),
    "requests_total": 0,
    "requests_by_status": {},
    "requests_by_service": {},
    "latency_by_service": {},
    "system_cpu": 0.0,
    "system_memory": 0.0,
}
METRICS_LOCK = threading.Lock()

def record_request(service, status_code, elapsed_ms):
    with METRICS_LOCK:
        METRICS["requests_total"] += 1
        METRICS["requests_by_status"][status_code] = METRICS["requests_by_status"].get(status_code, 0) + 1
        
        if service not in METRICS["requests_by_service"]:
            METRICS["requests_by_service"][service] = 0
            METRICS["latency_by_service"][service] = []
        
        METRICS["requests_by_service"][service] += 1
        METRICS["latency_by_service"][service].append(elapsed_ms)
        # Keep only the last 100 values to avoid infinite memory growth
        if len(METRICS["latency_by_service"][service]) > 100:
            METRICS["latency_by_service"][service].pop(0)

def get_windows_cpu_percent():
    try:
        p = subprocess.Popen(["typeperf", "\\Processor(_Total)\\% Processor Time", "-sc", "1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, _ = p.communicate(timeout=1.5)
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        if len(lines) > 2:
            val_str = lines[2].split(",")[-1].replace('"', '')
            return round(float(val_str), 1)
    except:
        pass
    
    try:
        p = subprocess.Popen(["wmic", "cpu", "get", "loadpercentage"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, _ = p.communicate(timeout=1.5)
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        if len(lines) > 1:
            return float(lines[1])
    except:
        pass
        
    try:
        p = subprocess.Popen(["powershell", "-Command", "(Get-CimInstance Win32_Processor).LoadPercentage"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, _ = p.communicate(timeout=2.0)
        if stdout.strip():
            return float(stdout.strip())
    except:
        pass
        
    return 0.0

def get_windows_memory_percent():
    try:
        p = subprocess.Popen(["typeperf", "\\Memory\\% Committed Bytes In Use", "-sc", "1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, _ = p.communicate(timeout=1.5)
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        if len(lines) > 2:
            val_str = lines[2].split(",")[-1].replace('"', '')
            return round(float(val_str), 1)
    except:
        pass
        
    try:
        p = subprocess.Popen(["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, _ = p.communicate(timeout=1.5)
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        if len(lines) > 1:
            parts = lines[1].split()
            if len(parts) == 2:
                free = float(parts[0])
                total = float(parts[1])
                return round((total - free) / total * 100, 1)
    except:
        pass
        
    try:
        p = subprocess.Popen(["powershell", "-Command", "$os = Get-CimInstance Win32_OperatingSystem; [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize * 100, 1)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, _ = p.communicate(timeout=2.0)
        if stdout.strip():
            return float(stdout.strip())
    except:
        pass
        
    return 0.0

def get_linux_cpu_percent():
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        parts = line.split()
        if len(parts) >= 5:
            vals = [float(x) for x in parts[1:5]]
            total = sum(vals)
            idle = vals[3]
            return total, idle
    except:
        pass
    return 0.0, 0.0

def get_linux_memory_percent():
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
        mem_total = 0
        mem_free = 0
        mem_cached = 0
        mem_buffers = 0
        for line in lines:
            if 'MemTotal:' in line:
                mem_total = int(line.split()[1])
            elif 'MemFree:' in line:
                mem_free = int(line.split()[1])
            elif 'Cached:' in line:
                mem_cached = int(line.split()[1])
            elif 'Buffers:' in line:
                mem_buffers = int(line.split()[1])
        if mem_total > 0:
            used = mem_total - (mem_free + mem_cached + mem_buffers)
            return round((used / mem_total) * 100, 1)
    except:
        pass
    return 0.0

def get_cgroup_memory_percent():
    try:
        # Try cgroups v2
        with open('/sys/fs/cgroup/memory.current', 'r') as f:
            usage = int(f.read().strip())
        with open('/sys/fs/cgroup/memory.max', 'r') as f:
            limit_str = f.read().strip()
            if limit_str != "max":
                limit = int(limit_str)
                if limit > 0:
                    return round((usage / limit) * 100, 1)
    except:
        pass
        
    try:
        # Try cgroups v1
        with open('/sys/fs/cgroup/memory/memory.usage_in_bytes', 'r') as f:
            usage = int(f.read().strip())
        with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'r') as f:
            limit = int(f.read().strip())
        if limit > 0:
            return round((usage / limit) * 100, 1)
    except:
        pass
    return None

def update_system_load():
    """Background thread to poll Windows or Linux load metrics dynamically"""
    # Try importing psutil inside the thread
    has_psutil = False
    try:
        import psutil
        has_psutil = True
    except ImportError:
        pass
        
    while True:
        cpu = 0.0
        mem = 0.0
        
        if has_psutil:
            try:
                cpu = psutil.cpu_percent(interval=None)
                mem = psutil.virtual_memory().percent
            except:
                pass
        else:
            if platform.system() == "Windows":
                cpu = get_windows_cpu_percent()
                mem = get_windows_memory_percent()
            else:
                # Linux CPU calculation with 0.5s interval
                try:
                    total1, idle1 = get_linux_cpu_percent()
                    time.sleep(0.5)
                    total2, idle2 = get_linux_cpu_percent()
                    diff_total = total2 - total1
                    diff_idle = idle2 - idle1
                    if diff_total > 0:
                        cpu = round(100.0 * (1.0 - diff_idle / diff_total), 1)
                except:
                    cpu = 0.0
                
                # Linux Memory
                cgroup_mem = get_cgroup_memory_percent()
                if cgroup_mem is not None:
                    mem = cgroup_mem
                else:
                    mem = get_linux_memory_percent()
                    
        # Log to server console if they are both 0 (meaning queries failed)
        if cpu == 0.0 and mem == 0.0:
            print("  [Warning] System metrics could not be resolved from OS commands.")
        
        with METRICS_LOCK:
            METRICS["system_cpu"] = cpu
            METRICS["system_memory"] = mem
            
        time.sleep(3)


# ─────────────────────────────────────────────
#  In-memory data stores
# ─────────────────────────────────────────────
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

SERVICE_RECORDS = {
    "s1": {"id": "s1", "car_id": "c1", "type": "Oil Change",      "date": "2024-03-15", "cost": 6500,   "mileage": 15000, "notes": "Synthetic 5W-30",          "next_due_miles": 20000},
    "s2": {"id": "s2", "car_id": "c3", "type": "Tyre Rotation",   "date": "2024-02-10", "cost": 800,    "mileage": 25000,  "notes": "All 4 tyres rotated",      "next_due_miles": 30000},
    "s3": {"id": "s3", "car_id": "c5", "type": "Brake Inspection", "date": "2024-04-01","cost": 1500,   "mileage": 18000,  "notes": "Front pads 70% remaining", "next_due_miles": 28000},
}

FUEL_LOGS = {
    "f1": {"id": "f1", "car_id": "c1", "date": "2024-04-20", "litres": 45.2, "cost_per_litre": 102.50, "odometer": 14800, "full_tank": True},
    "f2": {"id": "f2", "car_id": "c3", "date": "2024-04-18", "litres": 38.0, "cost_per_litre": 104.20, "odometer": 24900,  "full_tank": True},
    "f3": {"id": "f3", "car_id": "c1", "date": "2024-03-30", "litres": 42.0, "cost_per_litre": 101.80, "odometer": 14200, "full_tank": True},
}

INSURANCE_POLICIES = {
    "i1": {"id": "i1", "car_id": "c1", "provider": "HDFC ERGO General Insurance", "policy_no": "HE-2024-441",  "type": "Comprehensive", "premium": 45000, "start": "2024-01-01", "end": "2025-01-01", "status": "active"},
    "i2": {"id": "i2", "car_id": "c3", "provider": "ICICI Lombard Auto Co.",       "policy_no": "IL-2024-882",  "type": "Third Party",   "premium": 8500,  "start": "2024-02-15", "end": "2025-02-15", "status": "active"},
    "i3": {"id": "i3", "car_id": "c5", "provider": "Tata AIG Insurance Ltd.",      "policy_no": "TA-2024-003",  "type": "Comprehensive", "premium": 32000, "start": "2024-03-01", "end": "2025-03-01", "status": "active"},
}

# ─────────────────────────────────────────────
#  Microservice handlers
# ─────────────────────────────────────────────

def service_inventory(method, path_parts, body, query):
    """Car Inventory Service"""
    car_id = path_parts[3] if len(path_parts) > 3 else None

    if method == "GET":
        if car_id:
            car = INVENTORY.get(car_id)
            return (200, car) if car else (404, {"error": "Car not found"})
        status_filter = query.get("status", [None])[0]
        cars = list(INVENTORY.values())
        if status_filter:
            cars = [c for c in cars if c["status"] == status_filter]
        return 200, {"cars": cars, "total": len(cars)}

    if method == "POST":
        cid = "c" + str(len(INVENTORY) + 1)
        while cid in INVENTORY:
            cid = "c" + str(int(cid[1:]) + 1)
            
        img_url = body.get("image_url") or "https://images.unsplash.com/photo-1533473359331-0135ef1b58bf?auto=format&fit=crop&w=600&q=80"
        car = {
            "id": cid,
            "make": body.get("make", ""),
            "model": body.get("model", ""),
            "year": body.get("year", 2024),
            "color": body.get("color", "Unknown"),
            "mileage": body.get("mileage", 0),
            "price": body.get("price", 0),
            "status": body.get("status", "available"),
            "image_url": img_url
        }
        INVENTORY[cid] = car
        return 201, car

    if method == "PUT" and car_id:
        if car_id not in INVENTORY:
            return 404, {"error": "Car not found"}
        INVENTORY[car_id].update(body)
        return 200, INVENTORY[car_id]

    if method == "DELETE" and car_id:
        if car_id not in INVENTORY:
            return 404, {"error": "Car not found"}
        deleted = INVENTORY.pop(car_id)
        return 200, {"deleted": deleted}

    return 405, {"error": "Method not allowed"}


def service_maintenance(method, path_parts, body, query):
    """Service & Maintenance Service"""
    record_id = path_parts[3] if len(path_parts) > 3 else None

    if method == "GET":
        if record_id:
            rec = SERVICE_RECORDS.get(record_id)
            return (200, rec) if rec else (404, {"error": "Record not found"})
        car_id_filter = query.get("car_id", [None])[0]
        records = []
        for r_id, r in SERVICE_RECORDS.items():
            r_copy = dict(r)
            if not car_id_filter or r_copy["car_id"] == car_id_filter:
                r_copy["car"] = INVENTORY.get(r_copy["car_id"], {})
                records.append(r_copy)
        total_cost = sum(r["cost"] for r in records)
        return 200, {"records": records, "total": len(records), "total_cost": total_cost}

    if method == "POST":
        sid = "s" + str(len(SERVICE_RECORDS) + 1)
        while sid in SERVICE_RECORDS:
            sid = "s" + str(int(sid[1:]) + 1)
        record = {**body, "id": sid, "date": body.get("date", str(date.today()))}
        SERVICE_RECORDS[sid] = record
        return 201, record

    if method == "DELETE" and record_id:
        if record_id not in SERVICE_RECORDS:
            return 404, {"error": "Record not found"}
        deleted = SERVICE_RECORDS.pop(record_id)
        return 200, {"deleted": deleted}

    return 405, {"error": "Method not allowed"}


def service_valuation(method, path_parts, body, query):
    """Car Valuation Service — depreciation-based pricing"""
    if method != "GET":
        return 405, {"error": "Method not allowed"}

    car_id = path_parts[3] if len(path_parts) > 3 else None
    if car_id:
        car = INVENTORY.get(car_id)
        if not car:
            return 404, {"error": "Car not found"}
        cars_to_value = [car]
    else:
        cars_to_value = list(INVENTORY.values())

    results = []
    current_year = datetime.now().year
    for car in cars_to_value:
        age = current_year - car["year"]
        base = car["price"]
        dep_rate = 0.15 + (0.12 * max(0, age - 1))
        dep_rate = min(dep_rate, 0.75)
        mileage_penalty = (car["mileage"] / 100000) * 0.05 * base
        market_value = round(base * (1 - dep_rate) - mileage_penalty, 2)
        market_value = max(market_value, base * 0.10)
        results.append({
            "car_id": car["id"],
            "make": car["make"],
            "model": car["model"],
            "year": car["year"],
            "original_price": base,
            "market_value": market_value,
            "depreciation_pct": round(dep_rate * 100, 1),
            "age_years": age,
            "mileage": car["mileage"],
            "image_url": car.get("image_url", "")
        })

    if car_id:
        return 200, results[0]
    return 200, {"valuations": results, "total": len(results)}


def service_fuel(method, path_parts, body, query):
    """Fuel Tracker Service"""
    log_id = path_parts[3] if len(path_parts) > 3 else None

    if method == "GET":
        if log_id:
            log = FUEL_LOGS.get(log_id)
            return (200, log) if log else (404, {"error": "Log not found"})

        car_id_filter = query.get("car_id", [None])[0]
        logs = []
        for l_id, l in FUEL_LOGS.items():
            l_copy = dict(l)
            if not car_id_filter or l_copy["car_id"] == car_id_filter:
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
            s["avg_cost_per_litre"] = round(s["total_cost"] / s["total_litres"], 3) if s["total_litres"] else 0
            s["car"] = INVENTORY.get(cid, {})

        return 200, {"logs": logs, "stats_by_car": stats, "total_entries": len(logs)}

    if method == "POST":
        fid = "f" + str(len(FUEL_LOGS) + 1)
        while fid in FUEL_LOGS:
            fid = "f" + str(int(fid[1:]) + 1)
        log = {**body, "id": fid, "date": body.get("date", str(date.today()))}
        FUEL_LOGS[fid] = log
        return 201, log

    if method == "DELETE" and log_id:
        if log_id not in FUEL_LOGS:
            return 404, {"error": "Log not found"}
        FUEL_LOGS.pop(log_id)
        return 200, {"message": "Deleted"}

    return 405, {"error": "Method not allowed"}


def service_insurance(method, path_parts, body, query):
    """Insurance Manager Service"""
    policy_id = path_parts[3] if len(path_parts) > 3 else None

    if method == "GET":
        if policy_id:
            pol = INSURANCE_POLICIES.get(policy_id)
            if not pol:
                return 404, {"error": "Policy not found"}
            pol_copy = dict(pol)
            pol_copy["car"] = INVENTORY.get(pol_copy["car_id"], {})
            return 200, pol_copy

        policies = []
        for p_id, p in INSURANCE_POLICIES.items():
            p_copy = dict(p)
            p_copy["car"] = INVENTORY.get(p_copy["car_id"], {})
            days_left = (datetime.strptime(p_copy["end"], "%Y-%m-%d") - datetime.now()).days
            p_copy["days_until_expiry"] = days_left
            p_copy["expiry_alert"] = days_left <= 30
            policies.append(p_copy)

        total_premium = sum(p["premium"] for p in policies)
        expiring_soon = [p for p in policies if p.get("expiry_alert")]
        return 200, {
            "policies": policies,
            "total": len(policies),
            "total_annual_premium": total_premium,
            "expiring_soon": len(expiring_soon),
        }

    if method == "POST":
        pid = "i" + str(len(INSURANCE_POLICIES) + 1)
        while pid in INSURANCE_POLICIES:
            pid = "i" + str(int(pid[1:]) + 1)
        policy = {**body, "id": pid, "status": "active"}
        INSURANCE_POLICIES[pid] = policy
        return 201, policy

    if method == "PUT" and policy_id:
        if policy_id not in INSURANCE_POLICIES:
            return 404, {"error": "Policy not found"}
        INSURANCE_POLICIES[policy_id].update(body)
        return 200, INSURANCE_POLICIES[policy_id]

    if method == "DELETE" and policy_id:
        if policy_id not in INSURANCE_POLICIES:
            return 404, {"error": "Policy not found"}
        INSURANCE_POLICIES.pop(policy_id)
        return 200, {"message": "Policy deleted"}

    return 405, {"error": "Method not allowed"}


def service_metrics(method, path_parts, body, query):
    """System Performance & Metrics Monitoring Service"""
    if method != "GET":
        return 405, {"error": "Method not allowed"}
    
    uptime = round(time.time() - METRICS["uptime_start"], 1)
    
    by_svc = {}
    with METRICS_LOCK:
        for svc in METRICS["requests_by_service"]:
            count = METRICS["requests_by_service"][svc]
            latencies = METRICS["latency_by_service"][svc]
            avg_lat = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
            by_svc[svc] = {
                "count": count,
                "avg_latency_ms": avg_lat
            }
        
        status_counts = dict(METRICS["requests_by_status"])
        cpu = METRICS["system_cpu"]
        mem = METRICS["system_memory"]

    db_counts = {
        "inventory": len(INVENTORY),
        "service": len(SERVICE_RECORDS),
        "fuel": len(FUEL_LOGS),
        "insurance": len(INSURANCE_POLICIES)
    }

    return 200, {
        "uptime_seconds": uptime,
        "system": {
            "cpu_usage": cpu,
            "memory_usage": mem,
            "platform": platform.system()
        },
        "db": db_counts,
        "requests": {
            "total": METRICS["requests_total"],
            "by_status": status_counts,
            "by_service": by_svc
        }
    }


# ─────────────────────────────────────────────
#  Router
# ─────────────────────────────────────────────
ROUTES = {
    "inventory":  service_inventory,
    "service":    service_maintenance,
    "valuation":  service_valuation,
    "fuel":       service_fuel,
    "insurance":  service_insurance,
    "metrics":    service_metrics,
}

# ─────────────────────────────────────────────
#  Frontend HTML
# ─────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AutoHub — Automobile Platform</title>
<!-- Modern typography: Outfit for regular content, Barlow Condensed for accents/headers -->
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=Barlow+Condensed:wght@500;600;700;900&display=swap" rel="stylesheet">
<!-- Chart.js for beautiful real-time performance graphics -->
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>
  :root {
    --bg: #070a13; 
    --surface: rgba(18, 22, 35, 0.75); 
    --surface-solid: #111522;
    --surface2: rgba(30, 39, 61, 0.7); 
    --border: rgba(255, 255, 255, 0.08);
    --border-hover: rgba(255, 87, 34, 0.4);
    --accent: #ff5722; 
    --accent-glow: rgba(255, 87, 34, 0.35);
    --accent2: #ffb300; 
    --text: #f1f5f9; 
    --muted: #94a3b8;
    --green: #10b981; 
    --red: #ef4444; 
    --blue: #3b82f6;
    --card-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.4);
  }
  
  * { box-sizing: border-box; margin: 0; padding: 0; }
  
  body { 
    background: radial-gradient(circle at 50% 50%, #0d1527 0%, #04070f 100%); 
    color: var(--text); 
    font-family: 'Outfit', sans-serif; 
    min-height: 100vh;
    overflow-x: hidden;
  }
  
  header { 
    background: rgba(11, 15, 26, 0.85); 
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border); 
    padding: 0 2.5rem; 
    display: flex; 
    align-items: center; 
    justify-content: space-between;
    height: 75px; 
    position: sticky; 
    top: 0; 
    z-index: 100; 
    box-shadow: 0 4px 30px rgba(0,0,0,0.5);
  }
  
  .logo { 
    font-family: 'Barlow Condensed', sans-serif; 
    font-size: 2.1rem; 
    font-weight: 900; 
    letter-spacing: .08em; 
    text-transform: uppercase;
    background: linear-gradient(135deg, #fff 40%, var(--accent) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  .logo span { color: var(--accent); }
  
  nav { display: flex; gap: .5rem; }
  
  nav button { 
    background: none; 
    border: none; 
    color: var(--muted); 
    font-family: 'Outfit', sans-serif; 
    font-size: 0.95rem; 
    font-weight: 600; 
    padding: .6rem 1.2rem; 
    cursor: pointer; 
    border-radius: 8px;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  }
  nav button:hover { 
    color: var(--text); 
    background: rgba(255, 255, 255, 0.04);
  }
  nav button.active { 
    color: #fff; 
    background: linear-gradient(135deg, var(--accent) 0%, #e64a19 100%); 
    box-shadow: 0 4px 12px var(--accent-glow);
  }
  
  .status-bar { 
    background: linear-gradient(90deg, #05070c, var(--surface-solid)); 
    border-bottom: 1px solid var(--border); 
    padding: .5rem 2.5rem; 
    font-size: .8rem; 
    color: var(--muted); 
    letter-spacing: .05em; 
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .status-bar span { color: var(--accent2); font-weight: 600; }
  
  main { max-width: 1300px; margin: 0 auto; padding: 2.5rem 2rem; }
  
  .page { display: none; opacity: 0; transition: opacity 0.3s ease-in-out; } 
  .page.active { display: block; opacity: 1; }
  
  h2 { 
    font-family: 'Barlow Condensed', sans-serif; 
    font-size: 2.4rem; 
    font-weight: 900; 
    letter-spacing: .04em; 
    margin-bottom: 2rem; 
    text-transform: uppercase; 
    border-left: 4px solid var(--accent);
    padding-left: 0.75rem;
  }
  h2 span { color: var(--accent); }
  
  /* Cards */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1.75rem; margin-bottom: 2rem; }
  .card { 
    background: var(--surface); 
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid var(--border); 
    border-radius: 12px; 
    overflow: hidden;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: var(--card-shadow);
    display: flex;
    flex-direction: column;
  }
  .card:hover { 
    border-color: var(--border-hover); 
    transform: translateY(-5px); 
    box-shadow: 0 12px 30px rgba(0,0,0,0.6), 0 0 15px rgba(255, 87, 34, 0.12);
  }
  
  .card-img-container {
    position: relative;
    width: 100%;
    height: 190px;
    overflow: hidden;
    background: #090e18;
  }
  .card-img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    transition: transform 0.5s ease;
  }
  .card:hover .card-img {
    transform: scale(1.06);
  }
  .card-img-overlay {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 50%;
    background: linear-gradient(to top, rgba(17, 21, 34, 1) 0%, rgba(17, 21, 34, 0) 100%);
  }
  
  .card-content {
    padding: 1.5rem;
    display: flex;
    flex-direction: column;
    flex: 1;
  }
  
  .card-title { 
    font-family: 'Barlow Condensed', sans-serif; 
    font-size: 1.35rem; 
    font-weight: 800; 
    text-transform: uppercase; 
    letter-spacing: .04em; 
    margin-bottom: .85rem; 
    color: #fff;
  }
  
  .card-meta { 
    font-size: .88rem; 
    color: var(--muted); 
    margin-bottom: .4rem; 
    display: flex;
    justify-content: space-between;
  }
  .card-meta span { color: var(--text); font-weight: 500; }
  
  .card-value { 
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 1.8rem; 
    font-weight: 800; 
    color: var(--accent2); 
    text-shadow: 0 0 10px rgba(255, 179, 0, 0.25);
  }
  
  /* Badges */
  .badge { 
    display: inline-block; 
    padding: .3rem .75rem; 
    border-radius: 6px; 
    font-size: .74rem; 
    font-weight: 700; 
    text-transform: uppercase; 
    letter-spacing: .08em; 
    backdrop-filter: blur(4px);
    -webkit-backdrop-filter: blur(4px);
  }
  .badge-green { background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.4); color: var(--green); text-shadow: 0 0 4px rgba(16,185,129,0.3); }
  .badge-red { background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.4); color: var(--red); text-shadow: 0 0 4px rgba(239,68,68,0.3); }
  .badge-yellow { background: rgba(255, 179, 0, 0.15); border: 1px solid rgba(255, 179, 0, 0.4); color: var(--accent2); text-shadow: 0 0 4px rgba(255,179,0,0.3); }
  .badge-blue { background: rgba(59, 130, 246, 0.15); border: 1px solid rgba(59, 130, 246, 0.4); color: var(--blue); text-shadow: 0 0 4px rgba(59,130,246,0.3); }
  
  /* Stats Cards */
  .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1.5rem; margin-bottom: 2.5rem; }
  .stat { 
    background: var(--surface); 
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    border: 1px solid var(--border); 
    border-radius: 12px; 
    padding: 1.5rem 1.25rem; 
    text-align: center; 
    box-shadow: var(--card-shadow);
    transition: all 0.2s ease;
  }
  .stat:hover {
    border-color: rgba(255, 179, 0, 0.35);
    transform: translateY(-2px);
  }
  .stat-label { font-size: .78rem; text-transform: uppercase; letter-spacing: .12em; color: var(--muted); margin-bottom: .6rem; font-weight: 600; }
  .stat-val { font-family: 'Barlow Condensed', sans-serif; font-size: 2.4rem; font-weight: 900; color: var(--accent2); text-shadow: 0 0 10px rgba(255, 179, 0, 0.2); }
  
  /* Forms */
  .form-section { 
    background: var(--surface); 
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    border: 1px solid var(--border); 
    border-radius: 12px; 
    padding: 1.75rem; 
    margin-top: 2rem; 
    box-shadow: var(--card-shadow);
  }
  .form-section h3 { 
    font-family: 'Barlow Condensed', sans-serif; 
    font-size: 1.35rem; 
    font-weight: 700; 
    text-transform: uppercase; 
    letter-spacing: .06em; 
    margin-bottom: 1.5rem; 
    color: var(--accent2); 
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.5rem;
  }
  
  .form-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1.25rem; margin-bottom: 1.25rem; }
  .form-group { display: flex; flex-direction: column; }
  
  input, select { 
    background: rgba(10, 14, 24, 0.85); 
    border: 1px solid var(--border); 
    color: var(--text); 
    padding: .75rem 1rem; 
    border-radius: 8px; 
    font-size: .92rem; 
    font-family: 'Outfit', sans-serif; 
    width: 100%; 
    transition: all .25s ease; 
  }
  input:focus, select:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 10px var(--accent-glow); background: rgba(16, 21, 35, 0.95); }
  input::placeholder { color: #475569; }
  label { font-size: .76rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin-bottom: .45rem; display: block; font-weight: 600; }
  
  /* Buttons */
  .btn { 
    background: linear-gradient(135deg, var(--accent) 0%, #d84315 100%); 
    color: #fff; 
    border: none; 
    padding: .75rem 2rem; 
    border-radius: 8px; 
    font-family: 'Outfit', sans-serif; 
    font-size: 0.95rem; 
    font-weight: 700; 
    letter-spacing: .05em; 
    text-transform: uppercase; 
    cursor: pointer; 
    transition: all 0.25s ease; 
    box-shadow: 0 4px 14px var(--accent-glow);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
  }
  .btn:hover { 
    background: linear-gradient(135deg, #ff6e40 0%, #e64a19 100%); 
    transform: translateY(-2px); 
    box-shadow: 0 6px 18px rgba(255, 87, 34, 0.5); 
  }
  .btn:active { transform: translateY(0); }
  .btn-sm { padding: .45rem 1rem; font-size: .8rem; border-radius: 6px; }
  .btn-outline { background: none; color: var(--accent); border: 1px solid var(--accent); box-shadow: none; }
  .btn-outline:hover { background: var(--accent); color: #fff; box-shadow: 0 4px 12px var(--accent-glow); }
  
  /* Tables */
  .table-wrap { 
    background: var(--surface); 
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    border: 1px solid var(--border); 
    border-radius: 12px; 
    padding: 1.75rem; 
    box-shadow: var(--card-shadow);
    margin-bottom: 2rem; 
    overflow-x: auto; 
  }
  
  .table-wrap h3 {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 1.4rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .06em;
    margin-bottom: 1.25rem;
    color: var(--accent2);
  }
  
  table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .9rem; }
  th { 
    background: rgba(30, 39, 61, 0.6); 
    padding: 1rem 1.25rem; 
    text-align: left; 
    font-family: 'Barlow Condensed', sans-serif; 
    font-size: .9rem; 
    font-weight: 700; 
    letter-spacing: .1em; 
    text-transform: uppercase; 
    color: var(--muted); 
    border-bottom: 1px solid var(--border); 
  }
  th:first-child { border-top-left-radius: 8px; }
  th:last-child { border-top-right-radius: 8px; }
  
  td { padding: 1.1rem 1.25rem; border-bottom: 1px solid var(--border); vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255,255,255,.03); }
  
  .loading { color: var(--muted); font-style: italic; padding: 2.5rem; text-align: center; font-size: 1rem; }
  .err { color: var(--red); padding: .75rem; background: rgba(239,68,68,.12); border-radius: 6px; margin-top: .75rem; font-size: .88rem; border: 1px solid rgba(239,68,68,0.25); }
</style>
</head>
<body>

<header>
  <div class="logo">Auto<span>Hub</span></div>
  <nav>
    <button class="active" onclick="navigate('inventory')">Inventory</button>
    <button onclick="navigate('service')">Service</button>
    <button onclick="navigate('valuation')">Valuation</button>
    <button onclick="navigate('fuel')">Fuel</button>
    <button onclick="navigate('insurance')">Insurance</button>
    <button onclick="navigate('metrics')">Metrics</button>
  </nav>
</header>

<div class="status-bar">
  <div>AutoHub Dashboard &nbsp;|&nbsp; <span>6 Active Microservices</span> &nbsp;|&nbsp; Path Routing</div>
  <div id="quick-perf-indicator">Server Health: <span style="color:var(--green)">Online</span></div>
</div>

<main>
  <!-- Inventory Page -->
  <div class="page active" id="page-inventory">
    <h2>Car <span>Inventory</span></h2>
    <div class="stats-row" id="inv-stats"></div>
    <div class="grid" id="inv-cards"><div class="loading">Loading inventory…</div></div>
    
    <div class="form-section">
      <h3>+ Add New Fleet Vehicle</h3>
      <div class="form-row">
        <div><label>Make</label><input id="i-make" placeholder="e.g. Tata"></div>
        <div><label>Model</label><input id="i-model" placeholder="e.g. Nexon EV"></div>
        <div><label>Year</label><input id="i-year" type="number" placeholder="e.g. 2023"></div>
      </div>
      <div class="form-row">
        <div><label>Color</label><input id="i-color" placeholder="e.g. Empowered Oxide"></div>
        <div><label>Mileage (km)</label><input id="i-mileage" type="number" placeholder="e.g. 15000"></div>
        <div><label>Original Price (₹)</label><input id="i-price" type="number" placeholder="e.g. 1750000"></div>
      </div>
      <div class="form-row">
        <div style="grid-column: span 3;"><label>Image URL (Optional)</label><input id="i-image_url" placeholder="https://images.unsplash.com/photo-..."></div>
      </div>
      <button class="btn" onclick="addCar()">Add to Fleet</button>
      <div id="inv-err" class="err" style="display:none"></div>
    </div>
  </div>

  <!-- Service Page -->
  <div class="page" id="page-service">
    <h2>Service &amp; <span>Maintenance Logs</span></h2>
    <div class="stats-row" id="svc-stats"></div>
    <div class="table-wrap">
      <h3>Logged Operations</h3>
      <table id="svc-table"><tr><td class="loading">Loading records…</td></tr></table>
    </div>
    
    <div class="form-section">
      <h3>+ Log Maintenance Operation</h3>
      <div class="form-row">
        <div><label>Car ID</label><input id="s-car_id" placeholder="e.g. c1"></div>
        <div><label>Service Type</label><input id="s-type" placeholder="e.g. Oil Change"></div>
        <div><label>Operation Cost (₹)</label><input id="s-cost" type="number" placeholder="e.g. 6500"></div>
      </div>
      <div class="form-row">
        <div><label>Odometer Mileage (km)</label><input id="s-mileage" type="number" placeholder="e.g. 32000"></div>
        <div><label>Next Due Odometer (km)</label><input id="s-next_due_miles" type="number" placeholder="e.g. 37000"></div>
        <div><label>Service Notes</label><input id="s-notes" placeholder="Synthetic 5W-30, Filter replaced..."></div>
      </div>
      <button class="btn" onclick="addService()">Log Service Record</button>
    </div>
  </div>

  <!-- Valuation Page -->
  <div class="page" id="page-valuation">
    <h2>Fleet <span>Valuations</span></h2>
    <div class="stats-row" id="val-stats"></div>
    <div class="grid" id="val-cards"><div class="loading">Computing valuations…</div></div>
  </div>

  <!-- Fuel Page -->
  <div class="page" id="page-fuel">
    <h2>Fuel <span>Tracker &amp; Analytics</span></h2>
    <div class="stats-row" id="fuel-stats"></div>
    <div class="table-wrap">
      <h3>Fill-up Logs</h3>
      <table id="fuel-table"><tr><td class="loading">Loading fuel logs…</td></tr></table>
    </div>
    
    <div class="form-section">
      <h3>+ Log Fuel Fill-up</h3>
      <div class="form-row">
        <div><label>Car ID</label><input id="f-car_id" placeholder="e.g. c1"></div>
        <div><label>Litres Pumped</label><input id="f-litres" type="number" step="0.1" placeholder="e.g. 45.0"></div>
        <div><label>Cost/Litre (₹)</label><input id="f-cost_per_litre" type="number" step="0.01" placeholder="e.g. 102.50"></div>
        <div><label>Current Odometer (km)</label><input id="f-odometer" type="number" placeholder="e.g. 32100"></div>
      </div>
      <button class="btn" onclick="addFuel()">Log Fuel Entry</button>
    </div>
  </div>

  <!-- Insurance Page -->
  <div class="page" id="page-insurance">
    <h2>Insurance <span>Manager</span></h2>
    <div class="stats-row" id="ins-stats"></div>
    <div class="grid" id="ins-cards"><div class="loading">Loading policies…</div></div>
    
    <div class="form-section">
      <h3>+ Attach New Policy</h3>
      <div class="form-row">
        <div><label>Car ID</label><input id="p-car_id" placeholder="e.g. c1"></div>
        <div><label>Provider</label><input id="p-provider" placeholder="e.g. SafeDrive Co."></div>
        <div><label>Policy Number</label><input id="p-policy_no" placeholder="e.g. SD-2024-001"></div>
      </div>
      <div class="form-row">
        <div><label>Coverage Type</label>
          <select id="p-type"><option>Comprehensive</option><option>Third Party</option><option>Third Party Fire &amp; Theft</option></select>
        </div>
        <div><label>Annual Premium (₹)</label><input id="p-premium" type="number" placeholder="e.g. 32000"></div>
        <div><label>Start Date</label><input id="p-start" type="date"></div>
        <div><label>End Date</label><input id="p-end" type="date"></div>
      </div>
      <button class="btn" onclick="addPolicy()">Attach Policy</button>
    </div>
  </div>

  <!-- Metrics / Performance Monitoring Page -->
  <div class="page" id="page-metrics">
    <h2>Server <span>Performance Metrics</span></h2>
    <div class="stats-row">
      <div class="stat"><div class="stat-label">Server Uptime</div><div class="stat-val" id="met-uptime">0s</div></div>
      <div class="stat"><div class="stat-label">Total API Requests</div><div class="stat-val" id="met-requests">0</div></div>
      <div class="stat"><div class="stat-label">Active CPU Load</div><div class="stat-val" style="color:var(--accent)" id="met-cpu">0.0%</div></div>
      <div class="stat"><div class="stat-label">RAM Load</div><div class="stat-val" style="color:var(--blue)" id="met-mem">0.0%</div></div>
    </div>
    
    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(450px, 1fr)); gap: 2rem; margin-bottom: 2.5rem;">
      <div class="form-section" style="margin-top:0;">
        <h3>System Load Trends (Real-time)</h3>
        <div style="position: relative; height: 320px; width: 100%;">
          <canvas id="chart-system"></canvas>
        </div>
      </div>
      <div class="form-section" style="margin-top:0;">
        <h3>API Service Volume &amp; Response Latency</h3>
        <div style="position: relative; height: 320px; width: 100%;">
          <canvas id="chart-endpoints"></canvas>
        </div>
      </div>
    </div>
    
    <div class="table-wrap">
      <h3>Active System Resources &amp; Response Profiles</h3>
      <table id="met-table">
        <thead>
          <tr>
            <th>Service / Endpoint</th>
            <th>Type</th>
            <th>Total Hits</th>
            <th>Average Response Latency</th>
            <th>Service Status</th>
          </tr>
        </thead>
        <tbody id="met-table-body">
          <tr><td colspan="5" class="loading">Loading performance metrics…</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</main>

<script>
const API = '/api';
const statusBadge = s => { 
  const map = {available:'badge-green', sold:'badge-red', reserved:'badge-yellow'}; 
  return `<span class="badge ${map[s]||'badge-blue'}">${s}</span>`; 
};
const $ = id => document.getElementById(id);
const val = id => $(id)?.value || '';

// System historical metrics polling
let currentTab = 'inventory';
let systemCpuHistory = [];
let systemMemHistory = [];
let timeLabels = [];
const MAX_HISTORY = 12;

let systemChart = null;
let endpointChart = null;

async function api(path, method='GET', body=null) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(API + path, opts);
  return r.json();
}

function navigate(page) {
  currentTab = page;
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  $(`page-${page}`).classList.add('active');
  
  const pages = ['inventory','service','valuation','fuel','insurance','metrics'];
  const idx = pages.indexOf(page);
  if (idx !== -1) {
    document.querySelectorAll('nav button')[idx].classList.add('active');
  }
  
  if (loaders[page]) {
    loaders[page]();
  }
}

// ─────────────────────────────────────────────
//  Inventory Service Handling
// ─────────────────────────────────────────────
async function loadInventory() {
  const data = await api('/inventory');
  const cars = data.cars || [];
  const avail = cars.filter(c=>c.status==='available').length;
  $('inv-stats').innerHTML = `
    <div class="stat"><div class="stat-label">Total Fleet</div><div class="stat-val">${cars.length}</div></div>
    <div class="stat"><div class="stat-label">Available</div><div class="stat-val" style="color:var(--green)">${avail}</div></div>
    <div class="stat"><div class="stat-label">Sold</div><div class="stat-val" style="color:var(--red)">${cars.filter(c=>c.status==='sold').length}</div></div>
    <div class="stat"><div class="stat-label">Reserved</div><div class="stat-val" style="color:var(--accent2)">${cars.filter(c=>c.status==='reserved').length}</div></div>
    <div class="stat"><div class="stat-label">Portfolio Value</div><div class="stat-val">₹${(cars.reduce((a,c)=>a+c.price,0)/100000).toFixed(1)} Lakhs</div></div>`;
    
  $('inv-cards').innerHTML = cars.map(c => `
    <div class="card">
      <div class="card-img-container">
        <img class="card-img" src="${c.image_url || 'https://images.unsplash.com/photo-1533473359331-0135ef1b58bf?auto=format&fit=crop&w=600&q=80'}" alt="${c.make} ${c.model}" onerror="this.src='https://images.unsplash.com/photo-1533473359331-0135ef1b58bf?auto=format&fit=crop&w=600&q=80'">
        <div class="card-img-overlay"></div>
        <div style="position: absolute; top: 15px; right: 15px; z-index: 2;">
          ${statusBadge(c.status)}
        </div>
      </div>
      <div class="card-content">
        <div class="card-title">${c.year} ${c.make} ${c.model}</div>
        <div class="card-meta">Color: <span>${c.color}</span></div>
        <div class="card-meta">Odometer: <span>${c.mileage.toLocaleString()} km</span></div>
        <div class="card-meta">Asset ID: <code style="color:var(--accent)">${c.id}</code></div>
        <div style="margin-top: auto; padding-top: 1.25rem; display: flex; justify-content: space-between; align-items: center;">
          <div class="card-value">₹${c.price.toLocaleString('en-IN')}</div>
          <button class="btn btn-sm btn-outline" onclick="deleteCar('${c.id}')">Remove</button>
        </div>
      </div>
    </div>`).join('');
}

async function addCar() {
  const body = {
    make: val('i-make'), 
    model: val('i-model'), 
    year: +val('i-year'), 
    color: val('i-color'), 
    mileage: +val('i-mileage'), 
    price: +val('i-price'),
    image_url: val('i-image_url')
  };
  if (!body.make || !body.model) {
    $('inv-err').textContent = 'Make and Model required.'; 
    $('inv-err').style.display = 'block'; 
    return; 
  }
  $('inv-err').style.display = 'none';
  await api('/inventory', 'POST', body);
  
  // Clear inputs
  $('i-make').value = '';
  $('i-model').value = '';
  $('i-year').value = '';
  $('i-color').value = '';
  $('i-mileage').value = '';
  $('i-price').value = '';
  $('i-image_url').value = '';
  loadInventory();
}

async function deleteCar(id) { 
  await api('/inventory/'+id, 'DELETE'); 
  loadInventory(); 
}

// ─────────────────────────────────────────────
//  Service Records Handling
// ─────────────────────────────────────────────
async function loadService() {
  const data = await api('/service');
  const recs = data.records || [];
  $('svc-stats').innerHTML = `
    <div class="stat"><div class="stat-label">Total Tasks</div><div class="stat-val">${recs.length}</div></div>
    <div class="stat"><div class="stat-label">Fleet Spend</div><div class="stat-val">₹${data.total_cost.toLocaleString('en-IN')}</div></div>
    <div class="stat"><div class="stat-label">Avg Repair cost</div><div class="stat-val">₹${recs.length ? Math.round(data.total_cost/recs.length).toLocaleString('en-IN') : 0}</div></div>`;
  
  $('svc-table').innerHTML = `<thead><tr><th>ID</th><th>Car Model</th><th>Maintenance Type</th><th>Operation Date</th><th>Cost</th><th>Odometer</th><th>Next Due</th><th>Notes</th><th></th></tr></thead><tbody>`
    + (recs.length ? recs.map(r=>`<tr>
      <td><code style="color:var(--muted)">${r.id}</code></td>
      <td><strong>${r.car?.make||''} ${r.car?.model||''}</strong> <small style="color:var(--muted)">(${r.car_id})</small></td>
      <td><span class="badge badge-blue">${r.type}</span></td>
      <td>${r.date}</td>
      <td style="color:var(--green)">₹${r.cost.toLocaleString('en-IN')}</td>
      <td>${r.mileage ? r.mileage.toLocaleString() + ' km' : '-'}</td>
      <td>${r.next_due_miles ? r.next_due_miles.toLocaleString() + ' km' : '-'}</td>
      <td style="color:var(--muted); font-size:.85rem; max-width: 200px; text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${r.notes||''}</td>
      <td><button class="btn btn-sm btn-outline" onclick="deleteService('${r.id}')">Del</button></td>
    </tr>`).join('') : `<tr><td colspan="9" style="text-align:center; color:var(--muted); padding:2rem;">No service operations logged yet.</td></tr>`) + '</tbody>';
}

async function addService() {
  const body = {
    car_id: val('s-car_id'), 
    type: val('s-type'), 
    cost: +val('s-cost'), 
    mileage: +val('s-mileage'), 
    next_due_miles: +val('s-next_due_miles'), 
    notes: val('s-notes')
  };
  await api('/service', 'POST', body); 
  
  $('s-car_id').value = '';
  $('s-type').value = '';
  $('s-cost').value = '';
  $('s-mileage').value = '';
  $('s-next_due_miles').value = '';
  $('s-notes').value = '';
  loadService();
}

async function deleteService(id) { 
  await api('/service/'+id, 'DELETE'); 
  loadService(); 
}

// ─────────────────────────────────────────────
//  Car Valuations Handling
// ─────────────────────────────────────────────
async function loadValuation() {
  const data = await api('/valuation');
  const vals = data.valuations || [];
  const total = vals.reduce((a,v)=>a+v.market_value, 0);
  $('val-stats').innerHTML = `
    <div class="stat"><div class="stat-label">Active Appraisals</div><div class="stat-val">${vals.length}</div></div>
    <div class="stat"><div class="stat-label">Market Evaluation</div><div class="stat-val">₹${(total/100000).toFixed(1)} Lakhs</div></div>
    <div class="stat"><div class="stat-label">Avg Depreciation</div><div class="stat-val">${vals.length ? Math.round(vals.reduce((a,v)=>a+v.depreciation_pct, 0)/vals.length) : 0}%</div></div>`;
    
  $('val-cards').innerHTML = vals.map(v=>`
    <div class="card">
      <div class="card-img-container">
        <img class="card-img" src="${v.image_url || 'https://images.unsplash.com/photo-1533473359331-0135ef1b58bf?auto=format&fit=crop&w=600&q=80'}" alt="${v.make} ${v.model}" onerror="this.src='https://images.unsplash.com/photo-1533473359331-0135ef1b58bf?auto=format&fit=crop&w=600&q=80'">
        <div class="card-img-overlay"></div>
      </div>
      <div class="card-content">
        <div class="card-title">${v.year} ${v.make} ${v.model}</div>
        <div class="card-meta">Original Portfolio Price: <span>₹${v.original_price.toLocaleString('en-IN')}</span></div>
        <div class="card-meta">Depreciation Run: <span>${v.age_years} yr</span> · Odometer: <span>${v.mileage.toLocaleString()} km</span></div>
        
        <div style="margin: 1.25rem 0 .4rem; font-size:.78rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted)">Estimated Market Value</div>
        <div class="card-value" style="font-size:1.9rem; margin-bottom:.5rem;">₹${v.market_value.toLocaleString('en-IN')}</div>
        <div style="margin-top: auto;"><span class="badge badge-red">-${v.depreciation_pct}% deprecation rate</span></div>
      </div>
    </div>`).join('');
}

// ─────────────────────────────────────────────
//  Fuel Tracker Handling
// ─────────────────────────────────────────────
async function loadFuel() {
  const data = await api('/fuel');
  const logs = data.logs || [];
  const totalL = logs.reduce((a,l)=>a+l.litres, 0);
  const totalCost = logs.reduce((a,l)=>a+l.total_cost, 0);
  $('fuel-stats').innerHTML = `
    <div class="stat"><div class="stat-label">Logged Refuels</div><div class="stat-val">${logs.length}</div></div>
    <div class="stat"><div class="stat-label">Total Fuel</div><div class="stat-val">${totalL.toFixed(1)} L</div></div>
    <div class="stat"><div class="stat-label">Total Outlay</div><div class="stat-val">₹${totalCost.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div></div>
    <div class="stat"><div class="stat-label">Active Cars</div><div class="stat-val">${Object.keys(data.stats_by_car||{}).length}</div></div>`;
  
  $('fuel-table').innerHTML = `<thead><tr><th>ID</th><th>Car ID</th><th>Refuel Date</th><th>Volume</th><th>Rate per Litre</th><th>Transaction Total</th><th>Odometer</th><th></th></tr></thead><tbody>`
    + (logs.length ? logs.map(l=>`<tr>
      <td><code style="color:var(--muted)">${l.id}</code></td>
      <td><code style="color:var(--accent)">${l.car_id}</code></td>
      <td>${l.date}</td>
      <td><strong>${l.litres.toFixed(1)}</strong> L</td>
      <td>₹${l.cost_per_litre.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</td>
      <td style="color:var(--accent2); font-weight: 700;">₹${l.total_cost.toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2})}</td>
      <td>${l.odometer ? l.odometer.toLocaleString() + ' km' : '-'}</td>
      <td><button class="btn btn-sm btn-outline" onclick="deleteFuel('${l.id}')">Del</button></td>
    </tr>`).join('') : `<tr><td colspan="8" style="text-align:center; color:var(--muted); padding:2rem;">No fuel logs found.</td></tr>`) + '</tbody>';
}

async function addFuel() {
  const body = {
    car_id: val('f-car_id'), 
    litres: +val('f-litres'), 
    cost_per_litre: +val('f-cost_per_litre'), 
    odometer: +val('f-odometer'), 
    full_tank: true
  };
  await api('/fuel', 'POST', body); 
  
  $('f-car_id').value = '';
  $('f-litres').value = '';
  $('f-cost_per_litre').value = '';
  $('f-odometer').value = '';
  loadFuel();
}

async function deleteFuel(id) { 
  await api('/fuel/'+id, 'DELETE'); 
  loadFuel(); 
}

// ─────────────────────────────────────────────
//  Insurance Manager Handling
// ─────────────────────────────────────────────
async function loadInsurance() {
  const data = await api('/insurance');
  const pols = data.policies || [];
  $('ins-stats').innerHTML = `
    <div class="stat"><div class="stat-label">Active Policies</div><div class="stat-val">${data.total}</div></div>
    <div class="stat"><div class="stat-label">Premium outlay</div><div class="stat-val">₹${data.total_annual_premium?.toLocaleString('en-IN')}</div></div>
    <div class="stat"><div class="stat-label">Expiring &lt; 30 Days</div><div class="stat-val" style="color:var(--red)">${data.expiring_soon}</div></div>`;
    
  $('ins-cards').innerHTML = pols.map(p=>`
    <div class="card" style="${p.expiry_alert ? 'border-color: var(--red); box-shadow: 0 8px 32px rgba(239, 68, 68, 0.15)' : ''}">
      <div class="card-content" style="padding-top: 1.5rem;">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom: 0.5rem;">
          <div class="card-title" style="margin-bottom:0;">${p.provider}</div>
          ${p.expiry_alert ? '<span class="badge badge-red">⚠ Expiring Soon</span>' : '<span class="badge badge-green">Active</span>'}
        </div>
        <div class="card-meta" style="margin-bottom: 0.75rem;">Vehicle: <strong>${p.car?.make||''} ${p.car?.model||''} (${p.car_id})</strong></div>
        <div class="card-meta">Policy No: <code>${p.policy_no}</code></div>
        <div class="card-meta">Start Date: <span>${p.start}</span></div>
        <div class="card-meta">End Date: <span>${p.end}</span></div>
        
        <div style="margin-top: 1.25rem; padding-top: 1.25rem; border-top: 1px solid var(--border); display:flex; justify-content:space-between; align-items:center;">
          <div>
            <div style="font-size:0.72rem; text-transform:uppercase; color:var(--muted)">Annual Cost</div>
            <div class="card-value">₹${p.premium.toLocaleString('en-IN')}/yr</div>
          </div>
          <div style="display:flex; gap:0.5rem; align-items:center;">
            <span class="badge badge-blue">${p.type}</span>
            <button class="btn btn-sm btn-outline" onclick="deletePolicy('${p.id}')">Del</button>
          </div>
        </div>
      </div>
    </div>`).join('');
}

async function addPolicy() {
  const body = {
    car_id: val('p-car_id'), 
    provider: val('p-provider'), 
    policy_no: val('p-policy_no'), 
    type: val('p-type'), 
    premium: +val('p-premium'), 
    start: val('p-start'), 
    end: val('p-end')
  };
  await api('/insurance', 'POST', body); 
  
  $('p-car_id').value = '';
  $('p-provider').value = '';
  $('p-policy_no').value = '';
  $('p-premium').value = '';
  $('p-start').value = '';
  $('p-end').value = '';
  loadInsurance();
}

async function deletePolicy(id) { 
  await api('/insurance/'+id, 'DELETE'); 
  loadInsurance(); 
}


// ─────────────────────────────────────────────
//  Metrics Dashboard Handling
// ─────────────────────────────────────────────
function formatUptime(sec) {
  if (sec < 60) return sec.toFixed(0) + 's';
  const min = Math.floor(sec / 60);
  const remainingSec = Math.floor(sec % 60);
  if (min < 60) return min + 'm ' + remainingSec + 's';
  const hrs = Math.floor(min / 60);
  const remainingMin = min % 60;
  return hrs + 'h ' + remainingMin + 'm';
}

function updateMetricsUI(data) {
  $('met-uptime').textContent = formatUptime(data.uptime_seconds);
  $('met-requests').textContent = data.requests.total;
  $('met-cpu').textContent = data.system.cpu_usage.toFixed(1) + '%';
  $('met-mem').textContent = data.system.memory_usage.toFixed(1) + '%';
  
  // Render Realtime System Load Line Chart
  const ctxSys = $('chart-system').getContext('2d');
  if (!systemChart) {
    systemChart = new Chart(ctxSys, {
      type: 'line',
      data: {
        labels: timeLabels,
        datasets: [
          {
            label: 'CPU Utilization (%)',
            data: systemCpuHistory,
            borderColor: '#ff5722',
            backgroundColor: 'rgba(255, 87, 34, 0.08)',
            borderWidth: 2.5,
            pointRadius: 2,
            tension: 0.3,
            fill: true
          },
          {
            label: 'RAM Load (%)',
            data: systemMemHistory,
            borderColor: '#3b82f6',
            backgroundColor: 'rgba(59, 130, 246, 0.08)',
            borderWidth: 2.5,
            pointRadius: 2,
            tension: 0.3,
            fill: true
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { min: 0, max: 100, grid: { color: 'rgba(255, 255, 255, 0.05)' }, ticks: { color: '#94a3b8', font: { family: 'Outfit' } } },
          x: { grid: { display: false }, ticks: { color: '#94a3b8', font: { family: 'Outfit' } } }
        },
        plugins: {
          legend: { labels: { color: '#f1f5f9', font: { family: 'Outfit', size: 12 } } }
        }
      }
    });
  } else {
    systemChart.data.labels = timeLabels;
    systemChart.data.datasets[0].data = systemCpuHistory;
    systemChart.data.datasets[1].data = systemMemHistory;
    systemChart.update('none'); // silent update
  }
  
  // Render Endpoint Usage Bar Chart
  const services = Object.keys(data.requests.by_service);
  const volumes = [];
  const latencies = [];
  
  services.forEach(svc => {
    volumes.push(data.requests.by_service[svc].count);
    latencies.push(data.requests.by_service[svc].avg_latency_ms);
  });
  
  const ctxEnd = $('chart-endpoints').getContext('2d');
  if (!endpointChart) {
    endpointChart = new Chart(ctxEnd, {
      type: 'bar',
      data: {
        labels: services,
        datasets: [
          {
            label: 'Total Requests',
            data: volumes,
            backgroundColor: 'rgba(255, 179, 0, 0.55)',
            borderColor: '#ffb300',
            borderWidth: 1.5,
            yAxisID: 'y'
          },
          {
            label: 'Latency (ms)',
            data: latencies,
            backgroundColor: 'rgba(16, 185, 129, 0.55)',
            borderColor: '#10b981',
            borderWidth: 1.5,
            yAxisID: 'y1'
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: {
            type: 'linear',
            position: 'left',
            grid: { color: 'rgba(255, 255, 255, 0.05)' },
            ticks: { color: '#94a3b8', font: { family: 'Outfit' } },
            title: { display: true, text: 'Requests', color: '#ffb300', font: { family: 'Outfit', weight: 'bold' } }
          },
          y1: {
            type: 'linear',
            position: 'right',
            grid: { drawOnChartArea: false },
            ticks: { color: '#94a3b8', font: { family: 'Outfit' } },
            title: { display: true, text: 'Latency (ms)', color: '#10b981', font: { family: 'Outfit', weight: 'bold' } }
          },
          x: { ticks: { color: '#94a3b8', font: { family: 'Outfit' } } }
        },
        plugins: {
          legend: { labels: { color: '#f1f5f9', font: { family: 'Outfit', size: 12 } } }
        }
      }
    });
  } else {
    endpointChart.data.labels = services;
    endpointChart.data.datasets[0].data = volumes;
    endpointChart.data.datasets[1].data = latencies;
    endpointChart.update('none');
  }
  
  // Render Metrics Table Detail Rows
  let html = '';
  // Database collection entries counts
  Object.keys(data.db).forEach(col => {
    html += `<tr>
      <td><code>/db/${col}</code></td>
      <td><span class="badge badge-blue">Datastore Collection</span></td>
      <td>-</td>
      <td><strong>${data.db[col]}</strong> records</td>
      <td><span class="badge badge-green">Healthy</span></td>
    </tr>`;
  });
  
  // Requests per service details
  services.forEach(svc => {
    const req = data.requests.by_service[svc];
    let latColor = 'var(--green)';
    let statusClass = 'badge-green';
    let statusTxt = 'Optimal';
    
    if (req.avg_latency_ms > 100.0) {
      latColor = 'var(--red)';
      statusClass = 'badge-red';
      statusTxt = 'Slow Response';
    } else if (req.avg_latency_ms > 20.0) {
      latColor = 'var(--accent2)';
      statusClass = 'badge-yellow';
      statusTxt = 'Acceptable';
    }
    
    html += `<tr>
      <td><code>/api/${svc}</code></td>
      <td><span class="badge badge-blue">API Microservice</span></td>
      <td><strong>${req.count}</strong> calls</td>
      <td style="color:${latColor}; font-weight:700;">${req.avg_latency_ms.toFixed(2)} ms</td>
      <td><span class="badge ${statusClass}">${statusTxt}</span></td>
    </tr>`;
  });
  
  $('met-table-body').innerHTML = html || '<tr><td colspan="5" class="loading">No request performance logged yet.</td></tr>';
}

async function loadMetrics() {
  // UI draws immediately on routing if metrics available
  try {
    const data = await api('/metrics');
    updateMetricsUI(data);
  } catch (err) {
    console.error("Immediate metrics update error", err);
  }
}

// ─────────────────────────────────────────────
//  Metrics Background Polling Loop
// ─────────────────────────────────────────────
async function pollMetrics() {
  try {
    const data = await api('/metrics');
    if (!data) return;
    
    // Add real-time timestamp labels and system stats to line charts
    const now = new Date().toLocaleTimeString();
    timeLabels.push(now);
    systemCpuHistory.push(data.system.cpu_usage);
    systemMemHistory.push(data.system.memory_usage);
    
    if (timeLabels.length > MAX_HISTORY) {
      timeLabels.shift();
      systemCpuHistory.shift();
      systemMemHistory.shift();
    }
    
    // Update the quick status indicator
    let loadColor = 'var(--green)';
    let loadTxt = 'Optimal';
    if (data.system.cpu_usage > 85.0 || data.system.memory_usage > 90.0) {
      loadColor = 'var(--red)';
      loadTxt = 'Heavy Load';
    } else if (data.system.cpu_usage > 50.0) {
      loadColor = 'var(--accent2)';
      loadTxt = 'Moderate Load';
    }
    $('quick-perf-indicator').innerHTML = `Server Health: <span style="color:${loadColor}">${loadTxt}</span> (CPU: ${data.system.cpu_usage.toFixed(0)}%)`;
    
    if (currentTab === 'metrics') {
      updateMetricsUI(data);
    }
  } catch (err) {
    console.error("Background metrics polling failed", err);
    $('quick-perf-indicator').innerHTML = `Server Health: <span style="color:var(--red)">Offline</span>`;
  }
}

// Loaders mapping
const loaders = {
  inventory: loadInventory,
  service: loadService,
  valuation: loadValuation,
  fuel: loadFuel,
  insurance: loadInsurance,
  metrics: loadMetrics
};

// Start metrics tracking polling loop
pollMetrics();
setInterval(pollMetrics, 3000);

// Initialize application state
loadInventory();
</script>

</body>
</html>
"""

# ─────────────────────────────────────────────
#  HTTP Handler & Request Tracker
# ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Clean custom request log output
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"  [{ts}] {self.command} {self.path}  →  {args[1] if len(args)>1 else ''}")

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            try: return json.loads(self.rfile.read(length))
            except: return {}
        return {}

    def _respond(self, status, data):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        start_time = time.time()
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)
        parts = [p for p in path.split("/") if p]

        # Route 1: Serves the Frontend UI
        if path == "/" or path == "":
            html = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            
            elapsed = (time.time() - start_time) * 1000.0
            record_request("frontend", 200, elapsed)
            return

        # Route 2: Path-based API routing
        if len(parts) >= 2 and parts[0] == "api":
            svc_name = parts[1]
            handler_fn = ROUTES.get(svc_name)
            if handler_fn:
                body = self._read_body()
                try:
                    status, result = handler_fn(self.command, parts, body, query)
                except Exception as e:
                    status, result = 500, {"error": f"Internal Server Error: {str(e)}"}
                
                self._respond(status, result)
                elapsed = (time.time() - start_time) * 1000.0
                record_request(svc_name, status, elapsed)
            else:
                self._respond(404, {"error": f"Unknown microservice: /api/{svc_name}"})
                elapsed = (time.time() - start_time) * 1000.0
                record_request("unknown", 404, elapsed)
            return

        # Route 3: Not Found catch-all
        self._respond(404, {"error": "Not found"})
        elapsed = (time.time() - start_time) * 1000.0
        record_request("unknown", 404, elapsed)

    def do_GET(self):    self._handle()
    def do_POST(self):   self._handle()
    def do_PUT(self):    self._handle()
    def do_DELETE(self): self._handle()
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


if __name__ == "__main__":
    PORT = 5000
    
    # Launch system load monitor in a daemon thread
    monitor_thread = threading.Thread(target=update_system_load, daemon=True)
    monitor_thread.start()
    
    print(f"""
  ╔══════════════════════════════════════════╗
  ║    AutoHub — Multithreaded Web Server    ║
  ╠══════════════════════════════════════════╣
  ║  UI  →  http://localhost:{PORT}            ║
  ╠══════════════════════════════════════════╣
  ║  /api/inventory   Car Inventory          ║
  ║  /api/service     Maintenance Records    ║
  ║  /api/valuation   Market Valuation       ║
  ║  /api/fuel        Fuel Tracker & Spend   ║
  ║  /api/insurance   Insurance Manager      ║
  ║  /api/metrics     Performance Dashboard  ║
  ╚══════════════════════════════════════════╝
    """)
    
    server = ThreadingHTTPServer(("", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Shutting down server.")
