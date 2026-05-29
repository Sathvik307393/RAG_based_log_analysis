# Integrating Istio Service Mesh with AutoHub

This document explains how to deploy, configure, and utilize the **Azure-managed Istio Service Mesh** in the AutoHub AKS cluster to enhance security, enrich telemetry logs for our RAG assistant, and run chaos engineering tests.

---

## 1. Enabling Istio on AKS

You can enable the Istio add-on directly on your cluster:

```bash
# 1. Enable the Istio mesh add-on
az aks mesh enable --resource-group log_analysis-rg --name log-analysis-aks-cluster --revision asm-1-20

# 2. Label your namespace to enable automatic sidecar injection
kubectl label namespace log-analysis istio-injection=enabled

# 3. Restart your deployment pods to inject the Envoy proxies
kubectl rollout restart deployment -n log-analysis
```

Once restarted, each pod will run with an additional `istio-proxy` container that intercepts all inbound and outbound network traffic.

---

## 2. Observability: Enriching RAG Telemetry Logs

Standard container standard outputs only show basic application logs. The Envoy sidecar logs **every network event** in a structured, consistent format.

### Example Envoy Log Entry
```json
{
  "start_time": "2026-05-24T11:00:00.123Z",
  "method": "GET",
  "path": "/api/inventory/c1",
  "protocol": "HTTP/1.1",
  "response_code": 503,
  "response_flags": "UH",
  "duration": 5005,
  "upstream_service": "inventory-service.log-analysis.svc.cluster.local",
  "x-request-id": "abc123-trace-id-xyz"
}
```

### How the RAG Assistant Uses This
*   **Trace Propagation**: By sending these network proxy logs to Azure Log Analytics, the RAG engine can map the exact routing path (e.g., `Gateway` -> `valuation-service` -> `inventory-service`).
*   **Log Correlation**: The `x-request-id` header is injected automatically by Envoy at the gateway edge and propagated across internal microservices. The RAG model uses this ID to connect database timeout warnings in `inventory-service` directly to the `502 Bad Gateway` failures shown on the Frontend.

---

## 3. Zero-Trust Security Configuration

With Istio, we can secure container communication without writing security logic in Python.

### Enforcing Strict Mutual TLS (mTLS)
Apply this policy to force all services in the `log-analysis` namespace to communicate using encrypted TLS tunnels:

```yaml
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: default
  namespace: log-analysis
spec:
  mtls:
    mode: STRICT
```

### Restricting Access via Authorization Policies
Ensure that backend microservices (like `auth-service` or `inventory-service`) only accept HTTP requests that originate from your API Gateway:

```yaml
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: allow-only-gateway
  namespace: log-analysis
spec:
  selector:
    matchLabels:
      tier: private
  action: ALLOW
  rules:
  - from:
    - source:
        principals: ["cluster.local/ns/log-analysis/sa/autohub-gateway-service-account"]
```

---

## 4. SRE Chaos Engineering: Fault Injection

Rather than modifying Python scripts to inject anomalies, you can use Istio to simulate failures at the network layer to test the RAG SRE responder.

### Injecting a 5-Second Latency Anomaly
This `VirtualService` policy intercepts traffic going to `inventory-service` and injects a 5-second delay for 100% of requests, simulating a database connection timeout:

```yaml
apiVersion: networking.istio.io/v1alpha3
kind: VirtualService
metadata:
  name: inventory-service-delay
  namespace: log-analysis
spec:
  hosts:
  - inventory-service
  http:
  - fault:
      delay:
        percentage:
          value: 100.0
        fixedDelay: 5s
    route:
    - destination:
        host: inventory-service
```

### Injecting a 503 HTTP Service Unavailable Error
Simulate an immediate backend crash for 50% of incoming authorization login requests:

```yaml
apiVersion: networking.istio.io/v1alpha3
kind: VirtualService
metadata:
  name: auth-service-abort
  namespace: log-analysis
spec:
  hosts:
  - auth-service
  http:
  - fault:
      abort:
        percentage:
          value: 50.0
        httpStatus: 503
    route:
    - destination:
        host: auth-service
```

---

## 5. Resilience: Circuit Breakers

To protect your cluster from cascading failures (like `valuation-service` crashing because it is waiting infinitely for a broken `inventory-service`), apply a circuit breaker policy:

```yaml
apiVersion: networking.istio.io/v1alpha3
kind: DestinationRule
metadata:
  name: inventory-service-circuit-breaker
  namespace: log-analysis
spec:
  host: inventory-service
  trafficPolicy:
    connectionPool:
      tcp:
        maxConnections: 100
      http:
        http1MaxPendingRequests: 10
        maxRequestsPerConnection: 10
    outlierDetection:
      consecutive5xxErrors: 3
      interval: 10s
      baseEjectionTime: 30s
      maxEjectionPercent: 50
```

If the `inventory-service` returns 3 consecutive 5xx errors, Istio will eject it from the load-balancing pool for 30 seconds, keeping the rest of the services functional.
