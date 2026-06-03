import os
from datetime import datetime, timedelta
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from openai import AzureOpenAI
from langchain_openai import AzureChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# Configuration is retrieved dynamically during instantiation to avoid cached imports
class LogRageEngine:
    def __init__(self):
        # Retrieve Azure OpenAI Credentials
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
        chat_deployment = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-5.3-chat")
        self.embedding_deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")

        # Retrieve Azure AI Search Credentials
        search_endpoint = os.getenv("AZURE_SEARCH_SERVICE_ENDPOINT")
        search_admin_key = os.getenv("AZURE_SEARCH_ADMIN_KEY")
        search_index_name = os.getenv("AZURE_SEARCH_INDEX_NAME", "devops-logs-index")

        # Initialize Azure OpenAI Client for Embeddings
        self.openai_client = AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=endpoint
        )
        
        # Initialize LangChain Azure OpenAI Client for Chat Synthesis
        self.chat_model = AzureChatOpenAI(
            azure_deployment=chat_deployment,
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version,
            temperature=0.1
        )
        
        # Initialize Search Client
        self.search_client = SearchClient(
            endpoint=search_endpoint,
            index_name=search_index_name,
            credential=AzureKeyCredential(search_admin_key)
        )
        
    def _get_embedding(self, text: str) -> list:
        response = self.openai_client.embeddings.create(
            model=self.embedding_deployment,
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
            # Fallback to loading local logs so GPT-4o has actual telemetry context
            import json
            # Find project root directory containing local_logs.json
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            local_logs_path = os.path.join(current_dir, "local_logs.json")
            if not os.path.exists(local_logs_path):
                local_logs_path = "local_logs.json"
                
            if os.path.exists(local_logs_path):
                try:
                    with open(local_logs_path, "r") as f:
                        local_logs = json.load(f)
                    if local_logs:
                        # Try to find logs matching query keywords
                        keywords = [w.lower() for w in query.split() if len(w) > 3]
                        matched = []
                        if keywords:
                            for log in local_logs:
                                msg = log.get("message", "").lower()
                                svc = log.get("service", "").lower()
                                if any(kw in msg or kw in svc for kw in keywords):
                                    matched.append(log)
                        # Fallback to latest 30 logs if no keyword match
                        retrieved_logs = matched[-30:] if matched else local_logs[-30:]
                except Exception:
                    pass

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
