variable "resource_group_name" {
  type        = string
  description = "The name of the resource group in which to create the resources."
  default     = "log_analysis-rg"
}

variable "location" {
  type        = string
  description = "The Azure region where all resources should be created."
  default     = "southindia"
}

variable "acr_name" {
  type        = string
  description = "The name of the Azure Container Registry. Must be globally unique, alphanumeric only."
  default     = "loganalysisregistrysrev2"
}

variable "storage_account_name" {
  type        = string
  description = "The name of the Azure Storage Account. Must be globally unique, lowercase alphanumeric only, between 3 and 24 characters."
  default     = "loganalysisstoragesrev2"
}

variable "eventhub_namespace_name" {
  type        = string
  description = "The name of the Event Hubs Namespace."
  default     = "log-analysis-eh-namespace"
}

variable "eventhub_name" {
  type        = string
  description = "The name of the Event Hub."
  default     = "devops-logs-eh"
}

variable "openai_name" {
  type        = string
  description = "The name of the Azure OpenAI Service."
  default     = "log-analysis-openai-sre-v4"
}

variable "search_name" {
  type        = string
  description = "The name of the Azure AI Search Service."
  default     = "log-analysis-search-sre-v4"
}

variable "function_app_name" {
  type        = string
  description = "The name of the Linux Function App."
  default     = "log-analysis-functions-sre-v4"
}

variable "aks_name" {
  type        = string
  description = "The name of the AKS cluster."
  default     = "log-analysis-aks-cluster"
}

variable "aks_vm_size" {
  type        = string
  description = "The size of the Virtual Machine for the AKS node pool."
  default     = "Standard_D2s_v3"
}

variable "openai_chat_model_deployment" {
  type        = string
  description = "The deployment name for the chat model in Azure OpenAI."
  default     = "gpt-5.3-chat"
}

variable "openai_chat_model_name" {
  type        = string
  description = "The actual model name of the chat model."
  default     = "gpt-4o"
}

variable "openai_embedding_model_deployment" {
  type        = string
  description = "The deployment name for the embedding model in Azure OpenAI."
  default     = "text-embedding-3-large"
}

variable "openai_embedding_model_name" {
  type        = string
  description = "The actual model name of the embedding model."
  default     = "text-embedding-3-large"
}
