# DevOps Log Analysis Platform: Deployment & Multi-Cloud Guide

This document provides a step-by-step checklist of Azure resources to provision manually, detailed guidance on network configuration (including VNet peering and subnets), and multi-cloud connection setups for AWS databases.

---

## 1. Azure Resource Provisioning Checklist

You must provision the following resources manually in Azure:

1.  **Azure Resource Group**: Create a Resource Group (e.g. `autohub-rg`) in your preferred region.
2.  **Azure AI Search**:
    *   Provision an Azure AI Search service.
    *   Create an index named `devops-logs-index` with vector search support (dimensions: 3072 to match `text-embedding-3-large`). Use the [schema.json](file:///c:/Users/ASUS/OneDrive/Desktop/Log-Analysis/rag/schema.json) for configuration.
3.  **Azure OpenAI Service**:
    *   Provision Azure OpenAI in a region where the latest models are available.
    *   Deploy **`text-embedding-3-large`** (deployment name: `text-embedding-3-large`).
    *   Deploy **`gpt-4o`** (deployment name: `gpt-4o`).
4.  **Azure Storage Account**:
    *   Create a general-purpose v2 Storage Account.
    *   Create two tables under the Table service:
        *   `incidents` (for storing SRE root cause analyses).
        *   `warningqueue` (for queuing warnings before batch execution).
5.  **Azure Event Hubs**:
    *   Provision an Event Hubs Namespace.
    *   Create an Event Hub named `devops-logs-eh`.
6.  **Azure Functions App**:
    *   Deploy a Python serverless function app (V2 programming model).
    *   Configure environment variables matching the keys from AI Search, OpenAI, Storage Account, and Event Hubs.
7.  **Azure Kubernetes Service (AKS)**:
    *   Provision an AKS cluster to host your containerized microservices.

---

## 2. Network Isolation Architecture (VNet, Subnets & Peering)

To protect backend microservices, configure network traffic isolation inside Azure:

```
                  PUBLIC INTERNET
                         │
                         ▼
        ┌──────────────────────────────────┐
        │        Azure VNet (10.0.0.0/16)  │
        │                                  │
        │  ┌────────────────────────────┐  │
        │  │ Public Subnet (10.0.1.0/24)│  │
        │  │  • Frontend Service (Port 80) │
        │  │  • API Gateway (Port 5000) │  │
        │  └─────────────┬──────────────┘  │
        │                │                 │
        │                ▼ (Ingress Only)  │
        │  ┌────────────────────────────┐  │
        │  │Private Subnet (10.0.2.0/24)│  │
        │  │  • AKS Backend Nodes       │  │
        │  │  • internal ClusterIPs     │  │
        │  └─────────────┬──────────────┘  │
        └────────────────┼─────────────────┘
                         │
                         ▼ (IPSec VPN / Peering Tunnel)
        ┌──────────────────────────────────┐
        │        AWS VPC (172.31.0.0/16)   │
        │                                  │
        │  ┌────────────────────────────┐  │
        │  │ Private Subnet             │  │
        │  │  • AWS DocumentDB / MongoDB│  │
        │  └────────────────────────────┘  │
        └──────────────────────────────────┘
```

### Subnet Isolation in Azure
1.  **Public Subnet**: Holds the load balancers exposing the Gateway and Frontend pods.
2.  **Private Subnet**: Holds the AKS nodepools hosting backend pods (`auth-service`, `inventory-service`, etc.).
3.  **Network Security Groups (NSGs)**:
    *   Attach an NSG to the **Private Subnet** that denies all inbound traffic from the internet (`0.0.0.0/0`) but allows ingress from the **Public Subnet's IP range** (`10.0.1.0/24`) on port 8000.
    *   Use Kubernetes **NetworkPolicies** (provided in `k8s/network-policies.yaml`) to enforce this segmentation at the pod layer.

### VNet Peering
If you choose to run the frontend and backend in separate Azure VNets (e.g. `frontend-vnet` and `backend-vnet`):
1.  Navigate to your virtual network settings in the Azure Portal.
2.  Add a **Peering Link** from `frontend-vnet` to `backend-vnet`.
3.  Add a reciprocal Peering Link from `backend-vnet` to `frontend-vnet`.
4.  Ensure `Allow Virtual Network Access` is checked on both sides to allow internal routing.

---

## 3. AWS Database Connection Setup (Multi-Cloud Networking)

To route connections securely from the private Azure subnets to MongoDB/DocumentDB on AWS VPC:

### Option A: IPSec Site-to-Site VPN Connection (Recommended)
1.  **Azure Side**:
    *   Deploy a **Virtual Network Gateway** (VPN Gateway type) in your Azure VNet.
    *   Deploy a **Local Network Gateway** representing the public IP of your AWS VPN endpoint.
2.  **AWS Side**:
    *   Deploy a **Virtual Private Gateway (VGW)** or **Transit Gateway** and attach it to your AWS VPC.
    *   Create a **Customer Gateway** representing the public IP of your Azure VPN Gateway.
    *   Establish a **Site-to-Site VPN Connection** using the VGW and Customer Gateway.
3.  **Configuration**:
    *   Configure the IPSec tunnel on the Azure Local Network Gateway.
    *   Update AWS and Azure Route Tables: add routes to direct `172.31.0.0/16` traffic (AWS) through the Azure VPN Gateway, and `10.0.0.0/16` traffic (Azure) through the AWS VGW.

### Option B: Firewalled Public Access (Alternative)
If a VPN tunnel is not feasible:
1.  Expose the AWS DocumentDB / MongoDB instance publicly.
2.  Modify the database **Security Group**:
    *   Add an **Inbound Rule** allowing MongoDB (port 27017) access.
    *   Restrict the Source IP to the public outbound NAT IP of your Azure AKS cluster (Azure egress IP).
    *   This denies all unauthorized internet traffic while permitting AKS backend pods to connect safely.

---

## 4. Kubernetes Deployment & Verification

Deploy the application within the Kubernetes cluster:

```bash
# 1. Create the dedicated namespace
kubectl create namespace autohub

# 2. Map AWS MongoDB endpoint in Cluster DNS
kubectl apply -f k8s/database-external-service.yaml

# 3. Apply pod isolation network policies
kubectl apply -f k8s/network-policies.yaml

# 4. Deploy private backend services
kubectl apply -f k8s/backend-deployments.yaml

# 5. Deploy public gateway and UI services
kubectl apply -f k8s/gateway-deployment.yaml
kubectl apply -f k8s/frontend-deployment.yaml

# 6. Verify pods are running
kubectl get pods -n autohub
```

---

## 5. Local Hybrid RAG Verification

Validate the proactive hybrid analysis engine locally without Azure connection:

1.  Run the Streamlit dashboard:
    ```bash
    streamlit run app/dashboard.py
    ```
2.  Open your browser at `http://localhost:8501`.
3.  Inject a **Database Connection Timeout (Inventory Service)** anomaly from the sidebar.
    *   Observe the immediate generation of a **🔴 CRITICAL** incident alert in the proactive alerts pane.
4.  Inject warning events or use the **Valuation Gateway Timeout** anomaly.
    *   Observe warnings being queued.
    *   After the countdown/batch interval completes (e.g. 15 seconds), check if they are consolidated into a single **🟡 WARNING** incident card.
