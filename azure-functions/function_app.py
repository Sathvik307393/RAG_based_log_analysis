import logging
import json
import os
import uuid
from datetime import datetime, timedelta
import azure.functions as func
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.data.tables import TableClient
from openai import AzureOpenAI

# Initialize Azure Function App v2 programming model
app = func.FunctionApp()

# Retrieve Azure environment variables
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview")
CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o")
EMBEDDING_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")

AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_SERVICE_ENDPOINT")
AZURE_SEARCH_ADMIN_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY")
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "devops-logs-index")

AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")

# Initialize clients
try:
    openai_client = AzureOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
        azure_endpoint=AZURE_OPENAI_ENDPOINT
    )
except Exception as e:
    logging.error(f"Failed to initialize Azure OpenAI Client: {str(e)}")
    openai_client = None

try:
    search_client = SearchClient(
        endpoint=AZURE_SEARCH_ENDPOINT,
        index_name=AZURE_SEARCH_INDEX_NAME,
        credential=AzureKeyCredential(AZURE_SEARCH_ADMIN_KEY)
    )
except Exception as e:
    logging.error(f"Failed to initialize Azure AI Search Client: {str(e)}")
    search_client = None

# Initialize Table Storage clients for Incident Store and Warning Queue
try:
    if AZURE_STORAGE_CONNECTION_STRING:
        incidents_table_client = TableClient.from_connection_string(
            conn_str=AZURE_STORAGE_CONNECTION_STRING,
            table_name="incidents"
        )
        try:
            incidents_table_client.create_table()
        except Exception:
            pass

        warnings_table_client = TableClient.from_connection_string(
            conn_str=AZURE_STORAGE_CONNECTION_STRING,
            table_name="warningqueue"
        )
        try:
            warnings_table_client.create_table()
        except Exception:
            pass
    else:
        logging.warning("AZURE_STORAGE_CONNECTION_STRING not set. Table storage features will be bypassed.")
        incidents_table_client = None
        warnings_table_client = None
except Exception as e:
    logging.error(f"Failed to initialize Azure Table Storage clients: {str(e)}")
    incidents_table_client = None
    warnings_table_client = None

def get_embedding(text: str) -> list:
    """Generate vector embedding for a text chunk using Azure OpenAI"""
    if not openai_client:
        raise ValueError("OpenAI client not initialized")
    
    response = openai_client.embeddings.create(
        model=EMBEDDING_DEPLOYMENT,
        input=text
    )
    return response.data[0].embedding

def analyze_incident(query: str, retrieved_logs: list) -> str:
    """Uses GPT-4o to analyze incident logs and return operational recommendations"""
    if not openai_client:
        return "Synthesis Error: Azure OpenAI Client is not initialized."

    # Build context string
    context_str = "\n".join([
        f"- [{log['timestamp']}] Service: {log['service']} | Level: {log['level']} | Message: {log['message']} "
        f"| Status: {log.get('status_code', 0)} | Latency: {log.get('latency_ms', 0.0)}ms | ReqID: {log.get('request_id', '')}"
        for log in retrieved_logs
    ])

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

    try:
        response = openai_client.chat.completions.create(
            model=CHAT_DEPLOYMENT,
            messages=[
                {"role": "system", "content": system_prompt.format(context=context_str, query=query)},
                {"role": "user", "content": query}
            ],
            temperature=0.1
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Synthesis Error: Could not generate response via Azure OpenAI. Details: {str(e)}"

def run_proactive_rag(service_name: str, message: str, severity: str) -> dict:
    """Executes vector search and synthesis for proactive incidents"""
    query = f"Analyze this {severity.lower()} error: {service_name} - {message}"
    
    try:
        query_vector = get_embedding(query)
    except Exception as e:
        logging.error(f"Failed to generate embedding for query: {str(e)}")
        return {"answer": f"Embedding failure: {str(e)}", "citations": []}

    retrieved_logs = []
    if search_client:
        try:
            # Look back 30 minutes
            cutoff_time = (datetime.utcnow() - timedelta(minutes=30)).isoformat() + "Z"
            filter_expr = f"timestamp ge {cutoff_time}"

            results = search_client.search(
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
            
            for r in results:
                retrieved_logs.append({
                    "timestamp": r.get("timestamp"),
                    "service": r.get("service"),
                    "level": r.get("level"),
                    "message": r.get("message"),
                    "latency_ms": r.get("latency_ms"),
                    "status_code": r.get("status_code"),
                    "request_id": r.get("request_id")
                })
        except Exception as se:
            logging.error(f"Vector search failed during proactive RAG: {str(se)}")
            
    if not retrieved_logs:
        return {"answer": f"Proactive analysis for {service_name} triggered. No relevant historical logs found in search index.", "citations": []}

    analysis_text = analyze_incident(query, retrieved_logs)
    return {
        "answer": analysis_text,
        "citations": retrieved_logs
    }

@app.event_hub_trigger(
    arg_name="events", 
    event_hub_name="devops-logs-eh",
    connection="EventHubConnectionString"
)
def log_ingest_event_hub(events: list[func.EventHubEvent]):
    """Event Hub Trigger to ingest logs, run critical proactive RAG, and queue warnings"""
    logging.info(f"Processing Event Hub batch containing {len(events)} events.")
    
    if not search_client:
        logging.error("AI Search Client not available. Ingestion aborted.")
        return

    documents_to_upload = []
    critical_logs_to_process = []
    warning_logs_to_queue = []
    
    for event in events:
        try:
            body_str = event.get_body().decode('utf-8')
            payload = json.loads(body_str)
            records = payload.get("records", [payload])
            
            for record in records:
                properties = record.get("properties", {})
                raw_log = properties.get("Log", record.get("message", ""))
                
                service_name = properties.get("ContainerName", record.get("service", "unknown-service"))
                log_level = "INFO"
                timestamp_str = record.get("time", datetime.utcnow().isoformat() + "Z")
                message = raw_log
                latency_ms = 0.0
                status_code = 0
                request_id = ""
                
                try:
                    structured_log = json.loads(raw_log)
                    service_name = structured_log.get("service", service_name)
                    log_level = structured_log.get("level", log_level)
                    message = structured_log.get("message", message)
                    timestamp_str = structured_log.get("timestamp", timestamp_str)
                    latency_ms = float(structured_log.get("latency_ms", 0.0))
                    status_code = int(structured_log.get("status_code", 0))
                    request_id = structured_log.get("request_id", "")
                except (json.JSONDecodeError, TypeError, ValueError):
                    if "error" in raw_log.lower() or "fail" in raw_log.lower() or "exception" in raw_log.lower():
                        log_level = "ERROR"
                    elif "warn" in raw_log.lower():
                        log_level = "WARNING"
                
                formatted_timestamp = timestamp_str.replace("T", " ").replace("Z", "")
                embed_text = f"[{formatted_timestamp}] Service: {service_name} | Level: {log_level} | Message: {message}"
                
                if status_code > 0:
                    embed_text += f" | Status: {status_code} | Latency: {latency_ms}ms"
                if request_id:
                    embed_text += f" | RequestId: {request_id}"
                    
                try:
                    vector = get_embedding(embed_text)
                except Exception as ex:
                    logging.error(f"Failed to generate embedding: {str(ex)}")
                    continue
                
                doc = {
                    "id": str(uuid.uuid4()),
                    "timestamp": timestamp_str,
                    "service": service_name,
                    "level": log_level,
                    "message": message,
                    "latency_ms": latency_ms,
                    "status_code": status_code,
                    "request_id": request_id,
                    "formatted_log": embed_text,
                    "vector": vector
                }
                documents_to_upload.append(doc)
                
                # Classify logs for Proactive AIOps Pipeline
                if log_level in ["ERROR", "CRITICAL"] or status_code >= 500:
                    critical_logs_to_process.append(doc)
                elif log_level == "WARNING" or (400 <= status_code < 500) or latency_ms > 1000:
                    warning_logs_to_queue.append(doc)
                
        except Exception as e:
            logging.error(f"Error parsing Event Hub event: {str(e)}")
            
    # Ingest logs into vector search
    if documents_to_upload:
        try:
            results = search_client.upload_documents(documents=documents_to_upload)
            failures = sum(1 for r in results if not r.succeeded)
            logging.info(f"Uploaded {len(documents_to_upload)} documents to Search index. Successes: {len(documents_to_upload) - failures}, Failures: {failures}")
        except Exception as search_ex:
            logging.error(f"Failed to upload batch to Azure AI Search: {str(search_ex)}")

    # 🔴 Process Critical Logs Immediately
    if critical_logs_to_process and incidents_table_client:
        for log in critical_logs_to_process:
            try:
                logging.info(f"Proactive critical error detected in {log['service']}. Analyzing immediately...")
                result = run_proactive_rag(log["service"], log["message"], "CRITICAL")
                
                incident_entity = {
                    "PartitionKey": "incidents",
                    "RowKey": str(uuid.uuid4()),
                    "timestamp": log["timestamp"],
                    "service": log["service"],
                    "severity": "CRITICAL",
                    "message": log["message"],
                    "answer": result["answer"],
                    "citations": json.dumps(result["citations"])
                }
                incidents_table_client.create_entity(entity=incident_entity)
                logging.info(f"Immediate Critical Incident logged successfully in Table Storage.")
            except Exception as crit_err:
                logging.error(f"Failed to process critical proactive incident: {str(crit_err)}")

    # 🟡 Queue Warning Logs in warningqueue Table for Batch Processing
    if warning_logs_to_queue and warnings_table_client:
        for log in warning_logs_to_queue:
            try:
                warn_entity = {
                    "PartitionKey": "warnings",
                    "RowKey": str(uuid.uuid4()),
                    "timestamp": log["timestamp"],
                    "service": log["service"],
                    "message": log["message"],
                    "level": log["level"],
                    "status_code": log["status_code"],
                    "latency_ms": log["latency_ms"],
                    "request_id": log["request_id"]
                }
                warnings_table_client.create_entity(entity=warn_entity)
            except Exception as warn_err:
                logging.error(f"Failed to queue warning log: {str(warn_err)}")


@app.timer_trigger(schedule="0 */5 * * * *", arg_name="myTimer", run_on_startup=False)
def batch_process_warnings_trigger(myTimer: func.TimerRequest):
    """Timer trigger executing every 5 minutes to deduplicate and process queued warning logs"""
    logging.info("Timer trigger batch warnings processor started.")
    
    if not warnings_table_client or not incidents_table_client:
        logging.warning("Table storage clients not available. Batch warning processing skipped.")
        return
        
    try:
        # Retrieve all queued warning logs
        warning_entities = list(warnings_table_client.list_entities())
        if not warning_entities:
            logging.info("No warnings queued in Table Storage warningqueue.")
            return

        logging.info(f"Found {len(warning_entities)} warning logs in queue. Running deduplication...")

        # Deduplicate warnings by (service, message)
        deduped = {}
        for entity in warning_entities:
            key = (entity["service"], entity["message"])
            if key not in deduped:
                deduped[key] = []
            deduped[key].append(entity)

        # Process each unique warning pattern
        for (service, message), group in deduped.items():
            try:
                logging.info(f"Processing batch warning: Service: {service} | Count: {len(group)}")
                # Sort group to find the latest log timestamp
                group.sort(key=lambda x: x["timestamp"], reverse=True)
                latest_warn = group[0]

                # Run RAG analysis
                result = run_proactive_rag(service, message, "WARNING")

                incident_entity = {
                    "PartitionKey": "incidents",
                    "RowKey": str(uuid.uuid4()),
                    "timestamp": latest_warn["timestamp"],
                    "service": service,
                    "severity": "WARNING",
                    "message": f"[Batch of {len(group)}] {message}",
                    "answer": result["answer"],
                    "citations": json.dumps(result["citations"])
                }
                incidents_table_client.create_entity(entity=incident_entity)
            except Exception as p_err:
                logging.error(f"Error processing warning group {service} - {message}: {str(p_err)}")

        # Purge all processed warnings from queue
        for entity in warning_entities:
            try:
                warnings_table_client.delete_entity(partition_key=entity["PartitionKey"], row_key=entity["RowKey"])
            except Exception as del_err:
                logging.error(f"Failed to delete processed warning entity from table queue: {str(del_err)}")
                
        logging.info(f"Successfully processed and cleared {len(warning_entities)} warning logs.")

    except Exception as e:
        logging.error(f"Error during batch warnings execution: {str(e)}")
