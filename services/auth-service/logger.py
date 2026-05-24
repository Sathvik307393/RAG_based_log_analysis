import logging
import json
import sys
from datetime import datetime

class JsonFormatter(logging.Formatter):
    def __init__(self, service_name):
        super().__init__()
        self.service_name = service_name

    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "service": self.service_name,
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        # Add HTTP specific fields if passed via extra
        if hasattr(record, "http_method"):
            log_data["http_method"] = record.http_method
        if hasattr(record, "http_path"):
            log_data["http_path"] = record.http_path
        if hasattr(record, "status_code"):
            log_data["status_code"] = record.status_code
        if hasattr(record, "latency_ms"):
            log_data["latency_ms"] = record.latency_ms
        if hasattr(record, "request_id"):
            log_data["request_id"] = record.request_id
            
        return json.dumps(log_data)

def get_json_logger(name, service_name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = JsonFormatter(service_name)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger
