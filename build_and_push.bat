@echo off
if "%~1"=="" (
    echo Usage: build_and_push.bat loganalysisregistry
    exit /b 1
)
set ACR_NAME=%~1

echo Building and Pushing images to %ACR_NAME%.azurecr.io ...

rem 1. Frontend
echo Building frontend...
docker build -t %ACR_NAME%.azurecr.io/autohub-frontend:latest -f services/frontend/Dockerfile services/frontend/
docker push %ACR_NAME%.azurecr.io/autohub-frontend:latest

rem 2. Gateway
echo Building gateway...
docker build -t %ACR_NAME%.azurecr.io/autohub-gateway:latest -f services/gateway/Dockerfile services/gateway/
docker push %ACR_NAME%.azurecr.io/autohub-gateway:latest

rem 3. Auth Service
echo Building auth-service...
docker build -t %ACR_NAME%.azurecr.io/autohub-auth-service:latest -f services/auth-service/Dockerfile services/auth-service/
docker push %ACR_NAME%.azurecr.io/autohub-auth-service:latest

rem 4. Inventory Service
echo Building inventory-service...
docker build -t %ACR_NAME%.azurecr.io/autohub-inventory-service:latest -f services/inventory-service/Dockerfile services/inventory-service/
docker push %ACR_NAME%.azurecr.io/autohub-inventory-service:latest

rem 5. Maintenance Service
echo Building maintenance-service...
docker build -t %ACR_NAME%.azurecr.io/autohub-maintenance-service:latest -f services/maintenance-service/Dockerfile services/maintenance-service/
docker push %ACR_NAME%.azurecr.io/autohub-maintenance-service:latest

rem 6. Valuation Service
echo Building valuation-service...
docker build -t %ACR_NAME%.azurecr.io/autohub-valuation-service:latest -f services/valuation-service/Dockerfile services/valuation-service/
docker push %ACR_NAME%.azurecr.io/autohub-valuation-service:latest

rem 7. Fuel Service
echo Building fuel-service...
docker build -t %ACR_NAME%.azurecr.io/autohub-fuel-service:latest -f services/fuel-service/Dockerfile services/fuel-service/
docker push %ACR_NAME%.azurecr.io/autohub-fuel-service:latest

rem 8. Insurance Service
echo Building insurance-service...
docker build -t %ACR_NAME%.azurecr.io/autohub-insurance-service:latest -f services/insurance-service/Dockerfile services/insurance-service/
docker push %ACR_NAME%.azurecr.io/autohub-insurance-service:latest

rem 9. Metrics Service
echo Building metrics-service...
docker build -t %ACR_NAME%.azurecr.io/autohub-metrics-service:latest -f services/metrics-service/Dockerfile services/metrics-service/
docker push %ACR_NAME%.azurecr.io/autohub-metrics-service:latest

echo Done! All images built and pushed.
