# Enterprise Agentic RAG - Knowledge BaaS

An Enterprise-grade **Knowledge Backend-as-a-Service (Knowledge BaaS)** architecture exposing internal knowledge base capabilities via the **Model Context Protocol (MCP)** for both internal LangGraph agents and external clients (Claude Desktop, MS Teams, Custom GPTs).

---

## 1. Architecture Overview (4 Isolated Zones)

```
                       [ External Clients ]
              (Claude Desktop, MS Teams, Custom GPTs)
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │   ZONE 3: MCP SERVER  │
                     │    (Knowledge BaaS)   │
                     └───────────┬───────────┘
                                 │
       ┌─────────────────────────┴────────────────────────┐
       │ (JSON Tools)                                     │ (JSON Tools)
       ▼                                                  ▼
┌─────────────────────────┐                      ┌──────────────────┐
│   ZONE 2: AGENTIC CORE  │                      │  HYBRID STORAGE  │
│       (LangGraph)       │                      ├──────────────────┤
│  • Query Formulator     │                      │ • Vector DB &    │
│  • Parallel Retriever ──┼──────────────────────┤   BM25 Index     │
│  • Synthesizer          │                      │ • Neo4j Graph DB │
│  • Critic (Reflection)  │                      │ • Semantic Cache │
└───────────┬─────────────┘                      └────────┬─────────┘
            │                                             │
            │ (Hallucination/Errors)                      │ (Embeddings/Relations)
            ▼                                             │
┌─────────────────────────┐                      ┌────────┴─────────┐
│   ZONE 4: EVALUATION    │                      │ ZONE 1: INGESTION│
│ • RAGAs Metrics         │                      │ • Parser & DDU   │
│ • Backlog / Telemetry   │                      │ • VLM Captioning │
└─────────────────────────┘                      │ • Structure Chunks
                                                 └──────────────────┘
```

*   **Zone 1: Ingestion Pipeline (Offline):** Multi-modal document parsing. AI Vision & Layout Analysis separates text, tables, and images. Extracted images -> VLM for technical captioning -> merged into Structure-aware chunks -> Vector & Graph DB.
*   **Zone 2: Agentic Core (Real-time):** LangGraph StateGraph orchestrator with shared `AgentState`. Sub-agents: Query Formulator -> Parallel Retriever -> Synthesizer -> Critic (Self-reflection loop max_retries=3).
*   **Zone 3: MCP Gateway & Data Infrastructure:** Exposes standard JSON-RPC MCP Tools for hybrid retrieval (Dense Vector + BM25) and Neo4j Cypher knowledge graph exploration.
*   **Zone 4: Evaluation:** RAGAs metric validation (Faithfulness, Relevance) with automated error logging into an evaluation backlog.

---

## 2. Directory Structure

```
agentic_rag_project/
├── src/
│   ├── api/                    # HTTP Gateway (FastAPI)
│   │   ├── routes/             # API Endpoints (Upload, Chat, Admin)
│   │   └── dependencies.py     # Auth, session, rate-limiting
│   │
│   ├── mcp_server/             # MCP Server (Knowledge BaaS output)
│   │   ├── server.py           # MCP Endpoint Initialization
│   │   └── tools/              # JSON-RPC Tools for Vector & Graph DB
│   │
│   ├── agents/                 # ZONE 2: LangGraph Agentic Core
│   │   ├── orchestrator/       
│   │   │   ├── graph.py        # StateGraph wiring & conditional reflection edges
│   │   │   └── state.py        # TypedDict AgentState & Pydantic models
│   │   ├── nodes/              # Independent Node implementations
│   │   │   ├── query_formulator.py 
│   │   │   ├── parallel_retriever.py 
│   │   │   ├── synthesizer.py  
│   │   │   └── critic_reflection.py 
│   │   └── tools/              # Internal LangGraph tools (calls MCP client)
│   │
│   ├── ingestion/              # ZONE 1: Knowledge Ingestion Pipeline
│   │   ├── tasks.py            # Celery/Redis background worker
│   │   ├── parser/             # Layout Analysis, Document Parser, Metadata
│   │   ├── chunking/           # Structure-aware chunker
│   │   └── embedding/          # Embedding generation & metadata tagging
│   │
│   ├── retrieval/              # ZONE 3 (Internal): Storage Infrastructure
│   │   ├── vector_db.py        # Qdrant client connection
│   │   ├── graph_db.py         # Neo4j Cypher queries
│   │   ├── hybrid_search.py    # BM25 + Vector Fusion
│   │   └── reranker.py         # Cross-Encoder Reranking
│   │
│   ├── cache/                  # Caching Layer
│   │   └── semantic_cache.py   # Redis Semantic Cache
│   │
│   ├── evaluation/             # ZONE 4: Evaluation & Telemetry
│   │   ├── ragas_metrics.py    # Faithfulness & Relevance metrics
│   │   └── backlog.py          # Hallucination & reflection error backlog
│   │
│   └── core/                   # Cross-Cutting Infrastructure
│       ├── config.py           # Pydantic Settings & JSON Loguru logger
│       ├── prompts.py          # Centralized System Prompts repository
│       ├── exceptions.py       # Hierarchical system exceptions
│       └── llm_client.py       # LLM provider factory (Chat, VLM, Embeddings)
│
├── tests/                      # Pytest suite
├── docker-compose.yml          # FastAPI, Redis, Neo4j, Qdrant
├── requirements.txt            # Dependency manifest
├── .env.example                # Environment configuration template
└── main.py                     # Application entry point
```

---

## 3. Dedicated Environment Setup (`.venv`)

To prevent dependency collisions with other projects on your machine, always use the dedicated virtual environment:

### Windows (PowerShell):
```powershell
# 1. Navigate to project root
cd d:\Antigravity\A-RAG

# 2. Create virtual environment (if not already created)
python -m venv .venv

# 3. Activate virtual environment
.\.venv\Scripts\Activate.ps1

# 4. Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### Linux / macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Environment Configuration:
```powershell
# Copy example configuration to active .env
Copy-Item .env.example .env
```

---

## 4. Step-by-Step Test Execution Guide

We adopt a strict, test-driven validation approach. Do not proceed to downstream stages without passing each test milestone.

### Milestone 1: Base Config, Pydantic Models & State
Validates system configuration, Loguru JSON logging, prompt repository, and `AgentState` schema.
```powershell
pytest tests/test_step1_config_and_state.py -v
```

### Milestone 2: LangGraph Agentic Core Workflow
Validates the LangGraph multi-agent loop: Query Formulation -> Parallel Retrieval -> Synthesis -> Critic Self-Reflection (with up to 3 retries on quality failure).
```powershell
pytest tests/test_langgraph_workflow.py -v
```

### Milestone 3: Ingestion Parser, Layout Analysis & Metadata
Validates that documents (PDF, Markdown, Docs, Text) correctly separate into Text, Tables, and Images with strict metadata preservation (source, page, element index, confidence).
```powershell
pytest tests/test_parser_metadata.py -v
```

### Run All Tests:
```powershell
pytest tests/ -v
```

---

## 5. Architectural Invariants & Rules

1.  **Shared State ONLY:** Sub-agents must NEVER pass data via function parameters. All state transfer occurs through `AgentState`.
2.  **Zero Hardcoded Prompts:** Task nodes and ingestion scripts must import all prompt templates from `src/core/prompts.py`.
3.  **MCP Isolation:** `parallel_retriever.py` never imports DB drivers directly. It retrieves knowledge exclusively via MCP tool interfaces in `src/mcp_server/tools/`.
4.  **JSON Structured Logging:** All logging uses `loguru` configured with JSON serialization for MCP tool compatibility.
5.  **Strict Typing:** PEP 484 type hints and Pydantic v2 validation are required across all models.
