output "env_template" {
  description = "A formatted block of environment variables to copy directly into your local .env file."
  value = <<EOT
# Azure OpenAI Credentials
AZURE_OPENAI_API_KEY="${try(azurerm_cognitive_account.openai.primary_access_key != null ? azurerm_cognitive_account.openai.primary_access_key : "", "")}"
AZURE_OPENAI_ENDPOINT="${try(azurerm_cognitive_account.openai.endpoint != null ? azurerm_cognitive_account.openai.endpoint : "", "")}"
AZURE_OPENAI_API_VERSION="2024-02-15-preview"
AZURE_OPENAI_CHAT_DEPLOYMENT="${try(azurerm_cognitive_deployment.chat.name != null ? azurerm_cognitive_deployment.chat.name : "", "")}"
AZURE_OPENAI_EMBEDDING_DEPLOYMENT="${try(azurerm_cognitive_deployment.embedding.name != null ? azurerm_cognitive_deployment.embedding.name : "", "")}"

# Azure AI Search Credentials
AZURE_SEARCH_SERVICE_ENDPOINT="${try(azurerm_search_service.search.name != null ? "https://${azurerm_search_service.search.name}.search.windows.net" : "", "")}"
AZURE_SEARCH_ADMIN_KEY="${try(azurerm_search_service.search.primary_key != null ? azurerm_search_service.search.primary_key : "", "")}"
AZURE_SEARCH_INDEX_NAME="devops-logs-index"

# Azure Storage Table Credentials
AZURE_STORAGE_CONNECTION_STRING="${try(azurerm_storage_account.storage.primary_connection_string != null ? azurerm_storage_account.storage.primary_connection_string : "", "")}"

# Azure Event Hubs Stream
EVENT_HUB_CONNECTION_STRING="${try(azurerm_eventhub_namespace.eh_ns.default_primary_connection_string != null ? azurerm_eventhub_namespace.eh_ns.default_primary_connection_string : "", "")}"
EVENT_HUB_NAME="${try(azurerm_eventhub.eh.name != null ? azurerm_eventhub.eh.name : "", "")}"
EOT
  sensitive = true
}
