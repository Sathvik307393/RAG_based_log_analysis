# Azure Portal Manual Setup Guide

This guide provides step-by-step, click-by-click instructions to provision the required cloud infrastructure for the AutoHub DevOps Log Analysis & RAG system directly through the [Azure Portal](https://portal.azure.com).

---

## Step 1: Create an Azure Resource Group
All your resources must reside in a single logical container for easy management and billing.

1.  Log in to the [Azure Portal](https://portal.azure.com).
2.  In the search bar at the top, search for **Resource groups** and click on it.
3.  Click the **+ Create** button.
4.  Configure the following settings:
    *   **Subscription**: Select your active Azure subscription.
    *   **Resource group**: Enter `autohub-rg`.
    *   **Region**: Select **East US** (recommended, as all required AI models are available here).
5.  Click **Review + create**, and then click **Create** once validation passes.

---

## Step 2: Create Azure OpenAI Service & Deploy Models
This service handles log embeddings generation and root-cause analysis synthesis.

1.  In the top search bar, search for **Azure OpenAI** and click on it.
2.  Click **+ Create**.
3.  In the **Basics** tab, configure:
    *   **Resource Group**: Select `autohub-rg`.
    *   **Region**: Select **East US** (or the region chosen in Step 1).
    *   **Name**: Enter `log-analysis-openai-sre` (must be unique).
    *   **Pricing tier**: Select **Standard S0**.
4.  Click **Next** through the options, then click **Review + create** -> **Create**.
5.  Once deployment completes, click **Go to resource**.
6.  Under the **Overview** tab, click **Model deployments** or the **Go to Azure AI Studio** button.
7.  In the **Azure AI Studio** portal:
    *   Navigate to **Deployments** on the left menu.
    *   Click **+ Create new deployment**.
    *   Select **gpt-4o** from the model list, name the deployment `gpt-4o`, set model version to **Default**, and click **Create**.
    *   Click **+ Create new deployment** again.
    *   Select **text-embedding-3-large** from the model list, name the deployment `text-embedding-3-large`, set model version to **Default**, and click **Create**.

---

## Step 3: Create Azure AI Search Service & Index
This service stores log vectors and serves as the database for the RAG search.

1.  In the top search bar, search for **AI Search** and click on it.
2.  Click **+ Create**.
3.  Configure the following settings:
    *   **Resource Group**: Select `autohub-rg`.
    *   **Service name**: Enter `autohub-search-sre` (must be unique).
    *   **Location**: Select **East US** (same as your Resource Group).
    *   **Pricing tier**: Select **Basic** or **Standard** (avoid *Free* as it lacks vector features and limits storage).
4.  Click **Review + create** -> **Create**.
5.  Once created, navigate to the search resource in the portal.
6.  Configure the Log Index:
    *   Click the **Indexes** tab on the left menu.
    *   Click **Add Index** -> select **Add Index (JSON)**.
    *   Open the [schema.json](file:///c:/Users/ASUS/OneDrive/Desktop/Log-Analysis/rag/schema.json) file in your codebase, copy its entire JSON content, and paste it into the portal editor window.
    *   Click **Save** at the bottom.

---

## Step 4: Create Azure Storage Account & Tables
Used as the persistent SRE log queue and incident registry.

1.  Search for **Storage accounts** in the top search bar and click on it.
2.  Click **+ Create**.
3.  Configure the basics:
    *   **Resource Group**: Select `autohub-rg`.
    *   **Storage account name**: Enter `autohubstoragesre` (lowercase, numbers only, globally unique).
    *   **Region**: Select **East US**.
    *   **Performance**: Select **Standard**.
    *   **Redundancy**: Select **Locally-redundant storage (LRS)** (cost-efficient).
4.  Click **Review + create** -> **Create**.
5.  Once deployed, navigate to the storage account.
6.  Create the database tables:
    *   Scroll down the left menu to the **Data storage** section and click on **Tables**.
    *   Click **+ Table** at the top.
    *   Enter the table name: `incidents` and click **OK**.
    *   Click **+ Table** again.
    *   Enter the table name: `warningqueue` and click **OK**.

---

## Step 5: Create Azure Event Hubs
Event Hubs ingest log streams from backend microservices.

1.  Search for **Event Hubs** in the top search bar and click on it.
2.  Click **+ Create**.
3.  Configure settings:
    *   **Resource Group**: Select `autohub-rg`.
    *   **Namespace name**: Enter `autohub-eh-namespace` (globally unique).
    *   **Location**: Select **East US**.
    *   **Pricing tier**: Select **Standard** (required for capturing stream properties).
4.  Click **Review + create** -> **Create**.
5.  Navigate to the Event Hubs Namespace resource.
6.  Under **Entities** in the left menu, click **Event Hubs**.
7.  Click **+ Event Hub** at the top:
    *   **Name**: Enter `devops-logs-eh`.
    *   **Partition Count**: Set to `2`.
    *   **Message Retention**: Set to `1` (days).
8.  Click **Review + create** -> **Create**.

---

## Step 6: Create Azure Kubernetes Service (AKS) Cluster
This cluster hosts the microservice pods (`gateway`, `auth-service`, etc.).

1.  Search for **Kubernetes services** in the top search bar and click on it.
2.  Click **+ Create** -> select **Create a Kubernetes cluster**.
3.  Configure settings:
    *   **Resource Group**: Select `autohub-rg`.
    *   **Cluster preset configuration**: Select **Dev/Test** (optimal for cost).
    *   **Kubernetes cluster name**: Enter `autohub-aks-cluster`.
    *   **Region**: Select **East US**.
    *   **Primary node pool size**: Click *Change size* -> select **Standard_DS2_v2** (2 vCPUs, 7 GB RAM).
    *   **Scale method**: Select **Manual**, and set **Node count** to `1` (sufficient for testing and saves cost).
4.  Click **Review + create** -> **Create** (this take 4-7 minutes).

---

## Step 7: Create Azure Function App
Processes raw logs from the Event Hub and uploads vector records to AI Search.

1.  Search for **Function App** in the top search bar and click on it.
2.  Click **+ Create** -> select **Function App**.
3.  Configure settings:
    *   **Resource Group**: Select `autohub-rg`.
    *   **Function App name**: Enter `autohub-functions-sre` (globally unique).
    *   **Perform deployment**: Select **Code**.
    *   **Runtime stack**: Select **Python**.
    *   **Version**: Select **3.10** or **3.11**.
    *   **Region**: Select **East US**.
    *   **Operating System**: Select **Linux**.
    *   **Plan**: Select **Consumption (Serverless)**.
4.  Click **Review + create** -> **Create**.

---

## Step 8: Retrieve Configuration Keys for your `.env` File
Now collect the keys from the portal and write them to your local [`.env`](file:///c:/Users/ASUS/OneDrive/Desktop/Log-Analysis/.env) file.

### 1. Azure OpenAI Keys
*   Go to **Azure OpenAI** -> select `autohub-openai-sre` -> click **Keys and Endpoint** (under Resource Management).
*   Copy **KEY 1** and paste it into `AZURE_OPENAI_API_KEY`.
*   Copy the **Endpoint** URL and paste it into `AZURE_OPENAI_ENDPOINT`.

### 2. Azure AI Search Keys
*   Go to **AI Search** -> select `autohub-search-sre` -> copy the **Url** from the Overview tab (e.g. `https://autohub-search-sre.search.windows.net`) and paste it into `AZURE_SEARCH_SERVICE_ENDPOINT`.
*   Click **Keys** (under Settings) on the left menu.
*   Copy the **Primary admin key** and paste it into `AZURE_SEARCH_ADMIN_KEY`.

### 3. Azure Storage Keys
*   Go to **Storage accounts** -> select `autohubstoragesre` -> click **Access keys** (under Security + networking).
*   Click **Show keys** -> copy the **Connection string** of Key 1 and paste it into `AZURE_STORAGE_CONNECTION_STRING`.

### 4. Event Hub Connection String
*   Go to **Event Hubs** -> select `autohub-eh-namespace` -> click **Shared access policies** (under Settings).
*   Click **RootManageSharedAccessKey** -> copy **Connection string–primary key** and paste it into `EVENT_HUB_CONNECTION_STRING`.

---

## Populated `.env` Template
Once completed, your [`.env`](file:///c:/Users/ASUS/OneDrive/Desktop/Log-Analysis/.env) file should be structured as follows:

```env
# Azure OpenAI Credentials
AZURE_OPENAI_API_KEY="<copied-key-1>"
AZURE_OPENAI_ENDPOINT="https://autohub-openai-sre.openai.azure.com/"
AZURE_OPENAI_API_VERSION="2024-02-15-preview"
AZURE_OPENAI_CHAT_DEPLOYMENT="gpt-4o"
AZURE_OPENAI_EMBEDDING_DEPLOYMENT="text-embedding-3-large"

# Azure AI Search Credentials
AZURE_SEARCH_SERVICE_ENDPOINT="https://autohub-search-sre.search.windows.net"
AZURE_SEARCH_ADMIN_KEY="<copied-search-admin-key>"
AZURE_SEARCH_INDEX_NAME="devops-logs-index"

# Azure Storage Table Credentials
AZURE_STORAGE_CONNECTION_STRING="<copied-storage-connection-string>"

# Azure Event Hubs Stream
EVENT_HUB_CONNECTION_STRING="<copied-event-hub-connection-string>"
EVENT_HUB_NAME="devops-logs-eh"

# Local Telemetry Settings
GATEWAY_URL="http://localhost:5000"
```
