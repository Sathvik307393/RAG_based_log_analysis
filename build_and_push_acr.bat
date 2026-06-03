@echo off
if "%~1"=="" (
    echo Usage: build_and_push_acr.bat loganalysisregistrysrev2
    exit /b 1
)
set ACR_NAME=%~1

echo Building and Pushing images to %ACR_NAME% via Azure Container Registry Tasks (no local Docker required) ...

rem 1. Frontend
echo Building frontend...
call az acr build --registry %ACR_NAME% --image autohub-frontend:latest -f services/frontend/Dockerfile services/frontend/

rem 2. Gateway
echo Building gateway...
call az acr build --registry %ACR_NAME% --image autohub-gateway:latest -f services/gateway/Dockerfile services/gateway/

rem 3. Auth Service
echo Building auth-service...
call az acr build --registry %ACR_NAME% --image autohub-auth-service:latest -f services/auth-service/Dockerfile services/auth-service/

rem 4. Inventory Service
echo Building inventory-service...
call az acr build --registry %ACR_NAME% --image autohub-inventory-service:latest -f services/inventory-service/Dockerfile services/inventory-service/

rem 5. Maintenance Service
echo Building maintenance-service...
call az acr build --registry %ACR_NAME% --image autohub-maintenance-service:latest -f services/maintenance-service/Dockerfile services/maintenance-service/

rem 6. Valuation Service
echo Building valuation-service...
call az acr build --registry %ACR_NAME% --image autohub-valuation-service:latest -f services/valuation-service/Dockerfile services/valuation-service/

rem 7. Fuel Service
echo Building fuel-service...
call az acr build --registry %ACR_NAME% --image autohub-fuel-service:latest -f services/fuel-service/Dockerfile services/fuel-service/

rem 8. Insurance Service
echo Building insurance-service...
call az acr build --registry %ACR_NAME% --image autohub-insurance-service:latest -f services/insurance-service/Dockerfile services/insurance-service/

rem 9. Metrics Service
echo Building metrics-service...
call az acr build --registry %ACR_NAME% --image autohub-metrics-service:latest -f services/metrics-service/Dockerfile services/metrics-service/

echo Done! All images built and pushed via ACR.
