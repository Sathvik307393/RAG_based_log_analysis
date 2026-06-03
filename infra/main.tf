resource "azurerm_resource_group" "rg" {
  name     = var.resource_group_name
  location = var.location
}

# 1. Azure Container Registry (ACR)
resource "azurerm_container_registry" "acr" {
  name                = var.acr_name
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Basic"
  admin_enabled       = true
}

# 2. Azure Kubernetes Service (AKS)
resource "azurerm_kubernetes_cluster" "aks" {
  name                = var.aks_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  dns_prefix          = var.aks_name

  default_node_pool {
    name       = "default"
    node_count = 1
    vm_size    = var.aks_vm_size
  }

  identity {
    type = "SystemAssigned"
  }
}

# 3. Connect AKS to ACR (AcrPull Role Assignment)
resource "azurerm_role_assignment" "aks_acr_pull" {
  principal_id                     = azurerm_kubernetes_cluster.aks.kubelet_identity[0].object_id
  role_definition_name             = "AcrPull"
  scope                            = azurerm_container_registry.acr.id
  skip_service_principal_aad_check = true
}

# 4. Azure OpenAI Service & Model Deployments
resource "azurerm_cognitive_account" "openai" {
  name                = var.openai_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  kind                = "OpenAI"
  sku_name            = "S0"
  custom_subdomain_name = var.openai_name
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.openai_chat_model_deployment
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = var.openai_chat_model_name
    version = "2024-11-20"
  }
  sku {
    name = "GlobalStandard"
  }
}

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = var.openai_embedding_model_deployment
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = var.openai_embedding_model_name
    version = "1"
  }
  sku {
    name = "Standard"
  }
}

# 5. Azure AI Search
resource "azurerm_search_service" "search" {
  name                = var.search_name
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "basic"
}

# 6. Azure Storage Account & Tables
resource "azurerm_storage_account" "storage" {
  name                     = var.storage_account_name
  resource_group_name      = azurerm_resource_group.rg.name
  location                 = azurerm_resource_group.rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_storage_table" "incidents" {
  name                 = "incidents"
  storage_account_name = azurerm_storage_account.storage.name
}

resource "azurerm_storage_table" "warningqueue" {
  name                 = "warningqueue"
  storage_account_name = azurerm_storage_account.storage.name
}

# 7. Azure Event Hubs Namespace, Hub, and Access Policy
resource "azurerm_eventhub_namespace" "eh_ns" {
  name                = var.eventhub_namespace_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "Standard"
  capacity            = 1
}

resource "azurerm_eventhub" "eh" {
  name                = var.eventhub_name
  namespace_name      = azurerm_eventhub_namespace.eh_ns.name
  resource_group_name = azurerm_resource_group.rg.name
  partition_count     = 2
  message_retention   = 1
}




# 8. App Service Plan for Function App (Linux Consumption)
resource "azurerm_service_plan" "asp" {
  name                = "${var.function_app_name}-plan"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  os_type             = "Linux"
  sku_name            = "Y1"
}

# 9. Linux Function App with Environment variables configured
resource "azurerm_linux_function_app" "fa" {
  name                = var.function_app_name
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location

  storage_account_name       = azurerm_storage_account.storage.name
  storage_account_access_key = azurerm_storage_account.storage.primary_access_key
  service_plan_id            = azurerm_service_plan.asp.id

  site_config {
    application_stack {
      python_version = "3.10"
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME"          = "python"
    "AZURE_OPENAI_API_KEY"              = azurerm_cognitive_account.openai.primary_access_key
    "AZURE_OPENAI_ENDPOINT"             = azurerm_cognitive_account.openai.endpoint
    "AZURE_OPENAI_API_VERSION"          = "2024-02-15-preview"
    "AZURE_OPENAI_CHAT_DEPLOYMENT"      = azurerm_cognitive_deployment.chat.name
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT" = azurerm_cognitive_deployment.embedding.name
    "AZURE_SEARCH_SERVICE_ENDPOINT"     = "https://${azurerm_search_service.search.name}.search.windows.net"
    "AZURE_SEARCH_ADMIN_KEY"            = azurerm_search_service.search.primary_key
    "AZURE_SEARCH_INDEX_NAME"           = "devops-logs-index"
    "AZURE_STORAGE_CONNECTION_STRING"   = azurerm_storage_account.storage.primary_connection_string
    "EventHubConnectionString"          = azurerm_eventhub_namespace.eh_ns.default_primary_connection_string
  }
}
