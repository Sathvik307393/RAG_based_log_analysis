# RAG Workflow: How AI Analyzes Logs and Provides Solutions

## Overview

This document explains how the Retrieval-Augmented Generation (RAG) system fetches logs, analyzes them, and provides accurate solutions to errors.

---

## Phase 1: Log Fetching (Retrieval)

### Step 1: Log Generation
Microservices generate structured JSON logs during operation:

```json
{
  "timestamp": "2026-05-24T11:00:00Z",
  "service": "inventory-service",
  "level": "ERROR",
  "message": "DB Query failed: Database Connection Timeout on pool. Connection count exceeded maximum limit of 50 connections.",
  "latency_ms": 5000.0,
  "status_code": 503,
  "request_id": "abc123"
}
```

### Step 2: Log Ingestion
Logs flow through the ingestion pipeline:

```
Microservices → Event Hubs → Azure Functions → Azure AI Search
```

**Azure Function Processing** (`azure-functions/function_app.py`):
- Receives log batches from Event Hub
- Parses structured JSON logs
- Formats logs into semantic text blocks
- Generates vector embeddings using Azure OpenAI
- Uploads indexed documents to Azure AI Search (vector database)

### Step 3: Vector Embedding Generation
Each log is converted to a vector representation:

```python
vector = openai_client.embeddings.create(
    model="text-embedding-3-large",
    input="[2026-05-24 11:00:00] Service: inventory-service | Level: ERROR | Message: DB Query failed..."
)
```

- Text → 3072-dimensional vector
- Similar logs have similar vectors in vector space
- Enables semantic search (not just keyword matching)

### Step 4: Vector Storage
Documents stored in Azure AI Search:

```python
doc = {
    "id": "uuid-123",
    "timestamp": "2026-05-24T11:00:00Z",
    "service": "inventory-service",
    "level": "ERROR",
    "message": "DB Query failed: Database Connection Timeout...",
    "vector": [0.1, -0.2, 0.5, ...],  # 3072 dimensions
    "latency_ms": 5000.0,
    "status_code": 503,
    "request_id": "abc123"
}
```

---

## Phase 2: Error Detection & Query Processing

The system uses a **Hybrid Approach** (Recommended) for optimal performance and cost-efficiency:

### Hybrid Mode: Critical Errors (Immediate) + Warnings (Batch)

**How it works**:
- **Critical Errors** (ERROR/CRITICAL): Immediate analysis and real-time dashboard updates
- **Warning Errors** (WARNING): Batch analysis every 5 minutes for cost optimization
- **User Query**: Optional reactive mode for specific investigations

### Step 5: Automatic Error Detection with Classification
Azure Functions or a monitoring service continuously scans incoming logs:

```python
# In Azure Function or separate monitoring service
def detect_and_classify_errors(logs):
    critical_errors = []
    warning_errors = []
    
    for log in logs:
        if log["level"] in ["ERROR", "CRITICAL"]:
            critical_errors.append(log)
        elif log["level"] == "WARNING":
            warning_errors.append(log)
    
    return critical_errors, warning_errors
```

**Detection Criteria**:
- **Critical**: ERROR, CRITICAL log levels, HTTP 5xx status codes, security events
- **Warning**: WARNING log levels, HTTP 4xx status codes, high latency (>1000ms)
- **Patterns**: "timeout", "failed", "exception", "connection refused"

### Step 6A: Immediate Processing for Critical Errors
Critical errors are analyzed immediately:

```python
for error_log in critical_errors:
    query = f"Analyze this critical error: {error_log['service']} - {error_log['message']}"
    query_vector = openai_client.embeddings.create(
        model="text-embedding-3-large",
        input=query
    )
    
    results = search_client.search(
        search_text=query,
        vector_queries=[{
            "vector": query_vector,
            "fields": "vector",
            "k": 15,
            "kind": "vector"
        }],
        filter="timestamp ge 2026-05-24T10:30:00Z",
        top=15
    )
    
    # Immediate analysis
    analysis = analyze_error(query, results)
    store_and_display_immediately(analysis)
```

### Step 6B: Batch Processing for Warning Errors
Warnings are accumulated and processed in batches:

```python
# Store warnings for batch processing
for warning_log in warning_errors:
    store_for_batch(warning_log)

# Timer-triggered function runs every 5 minutes
def batch_process_warnings():
    accumulated_warnings = get_accumulated_warnings()
    
    # Deduplicate similar warnings
    unique_warnings = deduplicate_warnings(accumulated_warnings)
    
    for warning in unique_warnings:
        query = f"Analyze this warning: {warning['service']} - {warning['message']}"
        # Generate embedding and search (same as critical)
        results = search_and_retrieve(query)
        analysis = analyze_error(query, results)
        store_analysis_result(analysis)
    
    # Update dashboard with all batch results
    update_dashboard_batch()
```

### Step 7: User Query (Optional)
Users can ask specific questions if needed:

```
"Why is /api/inventory returning 503 errors?"
```

The system processes user queries the same way as auto-generated queries.

---

## Phase 3: Log Analysis (Generation)

### Step 8: Context Building
The system builds context for each error (both immediate and batch):

```python
def build_context(retrieved_logs):
    context_str = "\n".join([
        f"- [{log['timestamp']}] Service: {log['service']} | Level: {log['level']} | Message: {log['message']} "
        f"| Status: {log['status_code']} | Latency: {log['latency_ms']}ms | ReqID: {log['request_id']}"
        for log in retrieved_logs
    ])
    return context_str
```

**Example context for one error**:
```
- [2026-05-24T11:00:00Z] Service: inventory-service | Level: ERROR | Message: DB Query failed: Database Connection Timeout on pool. Connection count exceeded maximum limit of 50 connections. | Status: 503 | Latency: 5000ms | ReqID: abc123
- [2026-05-24T11:00:05Z] Service: gateway | Level: INFO | Message: GET /api/inventory -> PROXY TO inventory-service | ReqID: abc123 | Status: 503 | Latency: 5005ms
- [2026-05-24T11:00:10Z] Service: metrics-service | Level: WARNING | Message: Health check probe failed for inventory-service on http://inventory-service:8000/health: Status 503 Service Unavailable
```

### Step 9: Prompt Engineering
System prompt instructs GPT-4o on how to analyze:

```python
system_prompt = """
You are an expert DevSecOps AI SRE Assistant. Analyze operational logs to identify incident root causes.

---CONTEXT START---
{context}
---CONTEXT END---

Analyze these logs to answer: "{query}"

Follow these strict troubleshooting guidelines:
1. Trace Correlation: Look for matching request_id across different microservices. Correlate failures in one service to errors in downstream services.
2. Outage Timeline: Summarize the sequence of events leading up to the issue.
3. Identified Cause: State clearly which microservice is the root cause, what error occurred, and why.
4. Recommendations: Provide concrete operational steps to resolve the issue (e.g., restarting a pod, scaling out, fixing database connection pool).
5. Citations: Mention the timestamps, log levels, and services involved in your analysis.
"""
```

### Step 10: AI Analysis
GPT-4o processes errors (immediately for critical, in parallel for batch):

```python
chain = prompt_template | chat_model | StrOutputParser()

# For critical errors (immediate)
for critical_error in critical_errors:
    context = build_context(critical_error['logs'])
    answer = chain.invoke({
        "context": context,
        "query": critical_error['query']
    })
    critical_error['answer'] = answer
    critical_error['citations'] = critical_error['logs']

# For warning errors (batch)
for warning in unique_warnings:
    context = build_context(warning['logs'])
    answer = chain.invoke({
        "context": context,
        "query": warning['query']
    })
    warning['answer'] = answer
    warning['citations'] = warning['logs']
```

**Analysis Process**:
- Reads all retrieved logs for each error
- Identifies patterns and correlations
- Traces request IDs across services
- Determines root cause
- Formulates specific recommendations

---

## Phase 4: Solution Generation & Display

### Step 11: Structured Response Generation
GPT-4o generates detailed analysis for each detected error:

```markdown
### Root Cause Analysis: `/api/inventory` returning 503 Service Unavailable

#### Timeline & Flow
1. **Trace Correlation**: Gateway requests mapped to `ReqID: abc123` are throwing `503 Service Unavailable` with a high latency of ~5000ms.
2. **Root Cause**: The **inventory-service** logs show a critical database timeout:
   ```
   DB Query failed: Database Connection Timeout on pool. Connection count exceeded maximum limit of 50 connections.
   ```
3. **Trigger**: This occurred following a simulated high-load event which exhausted the active database connection pool.

#### Recommendation
- **Immediate Action**: Restart the `inventory-service` container/pod to release locked database connections.
- **Permanent Fix**: Adjust the database connection pool configuration in the service environment variables (e.g. set `MAX_CONNECTIONS=100`) and implement connection pooling cleanups.
```

### Step 12: Source Citations
Each response includes exact log entries for verification:

```python
error_report = {
    "error_id": "err-123",
    "timestamp": "2026-05-24T11:00:00Z",
    "service": "inventory-service",
    "severity": "ERROR",
    "answer": ai_generated_analysis,
    "citations": retrieved_logs
}
```

### Step 13: Dashboard Display (Automatic Mode)
The Streamlit dashboard automatically displays detected errors in a structured format:

```
┌─────────────────────────────────────────────────────────────┐
│              � DETECTED ERRORS & SOLUTIONS                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  🔴 ERROR #1: Database Connection Timeout                   │
│  Service: inventory-service                                 │
│  Time: 2026-05-24T11:00:00Z                                 │
│  Severity: CRITICAL                                          │
│                                                             │
│  📋 Root Cause:                                             │
│  Database Connection Timeout on pool. Connection count      │
│  exceeded maximum limit of 50 connections.                 │
│                                                             │
│  ✅ Solution Steps:                                          │
│  1. Restart inventory-service container/pod                 │
│  2. Adjust MAX_CONNECTIONS to 100 in environment vars       │
│  3. Implement connection pooling cleanups                    │
│                                                             │
│  📚 View Source Logs [Expand]                                │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  🟡 ERROR #2: Valuation Service Timeout                      │
│  Service: valuation-service                                 │
│  Time: 2026-05-24T11:05:00Z                                 │
│  Severity: WARNING                                          │
│                                                             │
│  📋 Root Cause:                                             │
│  HTTPConnectionPool read timed out calling inventory-service│
│                                                             │
│  ✅ Solution Steps:                                          │
│  1. Check inventory-service health                          │
│  2. Increase HTTP timeout threshold                          │
│  3. Implement circuit breaker pattern                        │
│                                                             │
│  📚 View Source Logs [Expand]                                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Dashboard Features**:
- **Real-time updates**: New errors appear automatically as they're detected
- **Priority sorting**: Critical errors shown first
- **Expandable details**: Click to see full analysis and source logs
- **Action buttons**: Quick access to recommended steps
- **Historical view**: Past errors with resolution status

---

## Accuracy Factors (90%+ Target)

### What Ensures High Accuracy

1. **Grounded Responses**: AI only analyzes actual retrieved logs, no hallucinations
2. **Source Citations**: Every claim can be verified against cited logs
3. **Rich Context**: Complete operational data (timestamps, request IDs, errors)
4. **Trace Correlation**: Request IDs link events across microservices
5. **Semantic Search**: Finds relevant logs even with different wording
6. **GPT-4o**: Most capable model for complex reasoning
7. **Specialized Prompting**: Enforces structured troubleshooting approach

### What Affects Accuracy

- **Quality of Logs**: Structured JSON with detailed error messages
- **Request ID Propagation**: Essential for correlating events
- **Log Retention**: Sufficient history for context
- **Time Window Filtering**: Reduces noise in retrieval

---

## Complete Flow Diagram

### Hybrid Mode (Recommended: Critical Immediate + Warnings Batch)

```
┌─────────────────────────────────────────────────────────────┐
│                    LOG INGESTION                            │
├─────────────────────────────────────────────────────────────┤
│ Microservices → Event Hubs → Azure Functions                │
│                      ↓                                       │
│              Parse & Format Logs                            │
│                      ↓                                       │
│              Generate Embeddings (Azure OpenAI)              │
│                      ↓                                       │
│              Store in Azure AI Search (Vector DB)            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              ERROR CLASSIFICATION                           │
├─────────────────────────────────────────────────────────────┤
│ Classify logs by severity:                                   │
│   - CRITICAL (ERROR/CRITICAL, 5xx, security)                │
│   - WARNING (WARNING, 4xx, high latency)                    │
│                      ↓                                       │
│              Split into two paths                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
        ┌───────────────────────┴───────────────────────┐
        ↓                                               ↓
┌───────────────────────┐                   ┌───────────────────────┐
│  CRITICAL ERRORS      │                   │  WARNING ERRORS       │
│  (Immediate Path)     │                   │  (Batch Path)         │
├───────────────────────┤                   ├───────────────────────┤
│ 1. Generate Query     │                   │ 1. Store in Queue     │
│ 2. Vector Search      │                   │ 2. Accumulate (5 min) │
│ 3. Retrieve Logs      │                   │ 3. Deduplicate        │
│ 4. Build Context      │                   │ 4. Batch Vector Search│
│ 5. GPT-4o Analysis    │                   │ 5. Retrieve Logs      │
│ 6. Store Result       │                   │ 6. Build Context      │
│ 7. Real-time Update   │                   │ 7. GPT-4o Analysis    │
└───────────────────────┘                   │ 8. Store Result       │
        ↓                                   │ 9. Batch Update       │
┌───────────────────────┐                   └───────────────────────┘
│  REAL-TIME DISPLAY    │                            ↓
│  (Critical Errors)    │                   ┌───────────────────────┐
├───────────────────────┤                   │  BATCH DISPLAY        │
│ 🔴 Database Timeout   │                   │  (Warning Errors)     │
│    • Immediate analysis│                   ├───────────────────────┤
│    • Solution steps   │                   │ 🟡 High Latency       │
│    • Source logs      │                   │    • Batch analysis   │
│    • Priority: HIGH   │                   │    • Solution steps   │
└───────────────────────┘                   │    • Source logs      │
        ↓                                   │    • Priority: MED   │
        └───────────────────┬───────────────┴───────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│              UNIFIED DASHBOARD VIEW                         │
├─────────────────────────────────────────────────────────────┤
│ All Errors Displayed:                                        │
│   - Critical errors (real-time, top of list)                │
│   - Warning errors (batch updates every 5 min)              │
│   - Historical view with resolution status                  │
│   - Priority sorting and filtering                           │
└─────────────────────────────────────────────────────────────┘
```

### User Query Mode (Optional)

```
┌─────────────────────────────────────────────────────────────┐
│                    USER QUERY INPUT                         │
├─────────────────────────────────────────────────────────────┤
│ User asks: "Why is /api/inventory returning 503?"          │
│                      ↓                                       │
│              Generate Query Embedding                        │
│                      ↓                                       │
│              Vector Search in Azure AI Search                │
│                      ↓                                       │
│              Retrieve Top 15 Relevant Logs                  │
│                      ↓                                       │
│              GPT-4o Analysis & Display                      │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Components

| Component | Purpose |
|-----------|---------|
| **Azure Event Hubs** | Real-time log streaming |
| **Azure Functions** | Log processing and embedding generation |
| **Azure OpenAI** | Text-to-vector embeddings and GPT-4o reasoning |
| **Azure AI Search** | Vector database for semantic search |
| **LangChain** | RAG orchestration and prompt management |
| **Streamlit Dashboard** | User interface for queries and visualization |

---

## Important Notes

- **Hybrid Approach**: Critical errors processed immediately, warnings processed in batches (recommended for optimal performance and cost)
- **Proactive Monitoring**: System automatically detects and analyzes errors without user intervention
- **No Automatic Fixes**: System provides recommendations, human SRE implements solutions
- **High Accuracy**: Grounded in actual logs with source citations (90%+ target)
- **Real-Time for Critical**: Critical errors appear immediately with solutions
- **Cost Optimization**: Batch processing for warnings reduces API costs and handles error storms
- **Scalable**: Vector search handles millions of log entries efficiently
- **Triple Mode**: Supports hybrid (recommended), automatic (batch only), and user query modes
