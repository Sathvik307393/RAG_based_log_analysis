import os
from datetime import datetime, timedelta
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from openai import AzureOpenAI
from langchain_openai import AzureChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# Retrieve configuration
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o")
EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")

AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_SERVICE_ENDPOINT")
AZURE_SEARCH_ADMIN_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY")
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "devops-logs-index")

class LogRageEngine:
    def __init__(self):
        # Initialize Azure OpenAI Client for Embeddings
        self.openai_client = AzureOpenAI(
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
            azure_endpoint=AZURE_OPENAI_ENDPOINT
        )
        
        # Initialize LangChain Azure OpenAI Client for Chat Synthesis
        self.chat_model = AzureChatOpenAI(
            azure_deployment=CHAT_DEPLOYMENT,
            api_key=AZURE_OPENAI_API_KEY,
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_version=AZURE_OPENAI_API_VERSION,
            temperature=0.1
        )
        
        # Initialize Search Client
        self.search_client = SearchClient(
            endpoint=AZURE_SEARCH_ENDPOINT,
            index_name=AZURE_SEARCH_INDEX_NAME,
            credential=AzureKeyCredential(AZURE_SEARCH_ADMIN_KEY)
        )
        
    def _get_embedding(self, text: str) -> list:
        response = self.openai_client.embeddings.create(
            model=EMBEDDING_DEPLOYMENT,
            input=text
        )
        return response.data[0].embedding

    def run_query(self, query: str, time_window_mins: int = 30) -> dict:
        """Runs vector search on log indexes and performs root cause analysis with GPT-4o"""
        # Generate query embedding
        query_vector = self._get_embedding(query)
        
        # Build filter for time window (if specified)
        filter_expr = None
        if time_window_mins:
            cutoff_time = (datetime.utcnow() - timedelta(minutes=time_window_mins)).isoformat() + "Z"
            filter_expr = f"timestamp ge {cutoff_time}"

        # Search Azure AI Search index
        # We do a Vector Search (using VectorizableTextQuery or direct vectors)
        try:
            results = self.search_client.search(
                search_text=query,
                vector_queries=[{
                    "vector": query_vector,
                    "fields": "vector",
                    "k": 15,
                    "kind": "vector"
                }],
                filter=filter_expr,
                top=15
            )
            
            retrieved_logs = []
            for r in results:
                retrieved_logs.append({
                    "id": r.get("id"),
                    "timestamp": r.get("timestamp"),
                    "service": r.get("service"),
                    "level": r.get("level"),
                    "message": r.get("message"),
                    "latency_ms": r.get("latency_ms"),
                    "status_code": r.get("status_code"),
                    "request_id": r.get("request_id"),
                    "formatted_log": r.get("formatted_log")
                })
        except Exception as e:
            # Fallback for local simulation mode or if connection fails
            return {
                "answer": f"Retrieval Error: Could not connect to Azure AI Search. Details: {str(e)}",
                "citations": []
            }

        if not retrieved_logs:
            return {
                "answer": "No relevant logs found in the specified time window. The system appears stable, or no telemetry is flowing.",
                "citations": []
            }

        # Build context from logs
        context_str = "\n".join([
            f"- [{log['timestamp']}] Service: {log['service']} | Level: {log['level']} | Message: {log['message']} "
            f"| Status: {log['status_code']} | Latency: {log['latency_ms']}ms | ReqID: {log['request_id']}"
            for log in retrieved_logs
        ])

        # Define LangChain System Prompt
        system_prompt = (
            "You are an expert DevSecOps AI SRE Assistant. Your job is to analyze operational logs, traces, "
            "and system metrics to identify incident root causes.\n\n"
            "Format the response using professional markdown with headers, bullet points, and code blocks. "
            "Do NOT use simple placeholders. Localize all money mentions in Indian Rupees (₹).\n\n"
            "Here is the context representing the retrieved logs from Azure AI Search:\n"
            "---CONTEXT START---\n"
            "{context}\n"
            "---CONTEXT END---\n\n"
            "Analyze these logs to answer the user query: \"{query}\"\n\n"
            "Follow these strict troubleshooting guidelines:\n"
            "1. **Trace Correlation**: Look for matching `request_id` across different microservices. "
            "Correlate failures in one service (e.g. gateway 503 or valuation timeout) to errors or database locks in downstream services (e.g. auth-service db_locked, or inventory-service latency).\n"
            "2. **Outage Timeline**: Summarize the sequence of events leading up to the issue.\n"
            "3. **Identified Cause**: State clearly which microservice is the root cause, what error occurred, and why.\n"
            "4. **Recommendations**: Provide concrete operational steps to resolve the issue (e.g., restarting a pod, scaling out, fixing database connection pool, checking token expiration).\n"
            "5. **Citations**: Mention the timestamps, log levels, and services involved in your analysis."
        )

        prompt_template = ChatPromptTemplate.from_messages([
            ("system", system_prompt)
        ])

        chain = prompt_template | self.chat_model | StrOutputParser()
        
        try:
            answer = chain.invoke({
                "context": context_str,
                "query": query
            })
        except Exception as chat_ex:
            answer = f"Synthesis Error: Could not generate response via Azure OpenAI. Details: {str(chat_ex)}"
            
        return {
            "answer": answer,
            "citations": retrieved_logs
        }
