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
│ • RAGAs Metrics         │                      │ • Parser & MinerU│
│ • Backlog / Telemetry   │                      │ • VLM Captioning │
└─────────────────────────┘                      │ • Structure Chunks
                                                 └──────────────────┘
```

*   **Zone 1: Ingestion Pipeline:** A LangGraph orchestrator loads the runtime `.vibeflow` skill into a `SystemMessage`, profiles the source through a tool, lets the LLM select one format-specific parser tool, and loops through `ToolNode` for execution/review. Every supported path ends in a validated `ParsedDocument` and JSON output. A second deterministic StateGraph converts that document into lineage-safe `ContentChunk` objects before embedding/indexing.
*   **Zone 2: Agentic Core (Real-time):** LangGraph StateGraph orchestrator with shared `AgentState`. Sub-agents: Query Formulator -> Parallel Retriever -> Synthesizer -> Critic (Self-reflection loop max_retries=3).
*   **Zone 3: MCP Gateway & Data Infrastructure:** Exposes standard JSON-RPC MCP Tools for hybrid retrieval (Dense Vector + BM25) and Neo4j Cypher knowledge graph exploration.
*   **Zone 4: Evaluation:** RAGAs metric validation (Faithfulness, Relevance) with automated error logging into an evaluation backlog.

---

## 2. Directory Structure

```
A-RAG/
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
│   ├── ingestion/              # ZONE 1: active ingestion implementation
│   │   ├── parser/
│   │   │   ├── models.py       # Pydantic parser and chunk contracts
│   │   │   ├── profiler.py     # File classification and strategy selection
│   │   │   ├── layout_parser.py # Native Markdown, text, CSV/TSV, code parsing
│   │   │   ├── mineru_adapter.py # MinerU API client + ParsedDocument normalizer
│   │   │   ├── skills.py       # Runtime skills and LangChain @tool wrappers
│   │   │   ├── state.py        # Ingestion graph AgentState contract
│   │   │   └── orchestrator.py # LLM → ToolNode → LLM ingestion loop
│   │   ├── chunking/            # ParsedDocument -> ContentChunk StateGraph
│   │   │   ├── state.py         # ChunkingState contract and controls
│   │   │   ├── strategies.py    # Format-aware deterministic splitters
│   │   │   └── orchestrator.py  # Plan -> build -> validate graph
│   │   └── embedding/           # Downstream embedding package scaffold
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
├── .vibeflow/skills/           # Agent procedures in SKILL.md format
├── docker-compose.yml          # FastAPI, Redis, Neo4j, Qdrant
├── requirements.txt            # Dependency manifest
├── .env.example                # Environment configuration template
└── main.py                     # Application entry point
```

The active parser source of truth is the flat `src/ingestion/parser/` package:
`models.py`, `profiler.py`, `layout_parser.py`, `skills.py`, `state.py`, and
`orchestrator.py`.
Runtime parsing code stays there; agent-facing procedures stay under
`.vibeflow/skills/` as `SKILL.md` files.

### Zone 1 parser flow

The ingestion orchestrator loads
`.vibeflow/skills/ingestion-orchestrator/SKILL.md` at runtime, injects it into
the LLM `SystemMessage`, and lets the model select exactly one parser tool
after profiling:

```text
file_path/raw Markdown
        ↓
LLM orchestrator + runtime SKILL.md
        ↓
profile_*_tool (fingerprint + category + strategy)
        ↓
LLM selects one MinerU/native parser tool
        ↓
ToolNode executes parser → LLM reviews result
        ↓
ParsedDocument (Pydantic)
        ↓
output_json / optional UTF-8 .json file
```

Examples:

```python
from langchain_openai import ChatOpenAI
from src.ingestion.parser.orchestrator import run_ingestion

result = run_ingestion(
    file_path="docs/architecture.md",
    output_path="out/architecture.json",
    llm=ChatOpenAI(model="gpt-4o", temperature=0),
)
assert result["stage"] == "json"
```

Raw Markdown can be converted directly:

```python
result = run_ingestion(
    raw_content="# Title\n\nContent.",
    llm=ChatOpenAI(model="gpt-4o", temperature=0),
)
json_document = result["output_json"]
```

MinerU API handles PDF, DOCX, PPTX, XLSX, and supported images with the
`pipeline` backend by default, requesting Markdown plus `content_list.json`.
PDF requests fall back to native `pypdf` only when MinerU is unavailable;
Office and image requests fail fast with structured errors. Markdown, text,
CSV/TSV, and source code remain on the native deterministic parsers. `pypdf`
is not OCR, so the PDF fallback does not claim scanned-document coverage.

### Run MinerU as an API service

Start MinerU separately from this application (use another port if the
project's FastAPI service already owns port 8000):

```powershell
# Install in the MinerU service environment (official all-in-one package)
python -m pip install --upgrade "mineru[all]"

# Start the API service
mineru-api --host 0.0.0.0 --port 8000

# Health check
curl.exe http://localhost:8000/health

# Optional direct API smoke test (replace the source path with a real PDF)
curl.exe -X POST http://localhost:8000/file_parse `
  -F "files=@path/to/your/sample.pdf" `
  -F "backend=pipeline" `
  -F "parse_method=auto" `
  -F "return_md=true" `
  -F "return_content_list=true" `
  -F "response_format_zip=false"
```

The adapter calls `POST /file_parse` synchronously with multipart `files`,
`backend=pipeline` (or the explicit `vlm` override), `parse_method=auto`,
`return_md=true`, `return_content_list=true`, and the remaining intermediate
outputs disabled. The response is normalized to the project's
`ParsedDocument`; when MinerU returns an archive, its Markdown and
`content_list.json` are stored under `MINERU_ARTIFACT_DIR` before mapping.

The API boundary is configured through `MINERU_BASE_URL`, `MINERU_API_KEY`,
`MINERU_BACKEND`, `MINERU_TIMEOUT_SECONDS`, `MINERU_MAX_RETRIES`,
`MINERU_RETRY_BACKOFF_SECONDS`, and `MINERU_ARTIFACT_DIR`. The integration
fixtures in `tests/fixtures/mineru/` contain a small representative
`content_list.json` + Markdown pair; adapter tests use temporary source files
so large binary samples are not committed to the repository.

The adapter also accepts the complete MinerU Web/CLI JSON shape
`pdf_info[] -> preproc_blocks[] -> lines[] -> spans[]`. It preserves each raw
block under element metadata while projecting text, title, table HTML, image
path, page, score, and bounding box into the typed `ParsedDocument` contract.
For a saved Web export, call `MinerUAdapter.parse_json_result(...)` with the
original filename (and, when available, the original PDF bytes) before running
the chunking graph.

### Chunking boundary: ParsedDocument → Knowledge Base

Chunking is a separate Zone 1 boundary. The query-time Zone 2 graph does not
re-split retrieved text. It receives indexed `ContentChunk` objects with
stable IDs and explicit lineage.

```text
ParsedDocument
    ↓
load chunking-orchestrator/SKILL.md
    ↓
plan_strategy
    ↓
build_chunks (deterministic, no LLM rewrite)
    ↓
validate_chunks
    ├── ContentChunk[]
    ├── element_chunk_map: source element → chunk IDs
    └── section_chunk_map: heading breadcrumb → chunk IDs
    ↓
embedding / Vector DB + Knowledge Graph
```

The planner chooses one strategy from the parsed file shape. Markdown and
heading-aware text use `hierarchical_semantic`; heading-free text uses
`token_window`; PDF/Office/layout elements use `page_element`; CSV/TSV uses
`row_window`; source code uses `code_boundary`. A graph is not used as a raw
text splitter: `section_path`, `parent_header`, `parent_chunk_id`, and the
reverse indexes project the document hierarchy after deterministic boundaries
are created. This prevents unrelated sections from being mixed and allows
retrieval to reconstruct the parent context without depending on vector-store
insertion order.

An optional LLM planner can propose only a `ChunkPlan` containing a strategy and
ordered `element_ids`; it receives metadata inventory, never source text. The
executor resolves those IDs against the original `ParsedDocument`. Extra fields
such as `content`, unknown IDs, duplicate IDs, reordered IDs, or cross-section
groups are rejected by the Pydantic/provenance gate. Invalid planner output
falls back to the deterministic plan, so production can run without an LLM.

```python
from src.ingestion.chunking import run_chunking

state = run_chunking(parsed_document, max_tokens=400, overlap_tokens=40)
assert state["stage"] == "validated"
chunks = state["chunks"]
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
Validates that supported Zone 1 sources (PDF, Markdown, text, CSV/TSV, Office, images, code) become typed elements with strict metadata preservation (source, page, element index, confidence).
```powershell
pytest tests/test_parser_metadata.py -v
```

### Milestone 4: Ingestion Profiler
Validates file classification, magic-byte MIME detection, extraction strategy selection, feature detection, and page estimation.
```powershell
pytest tests/test_profiler.py -v
```

### Milestone 5: LLM-driven Ingestion Orchestrator
Validates runtime skill injection, LLM tool binding, the `ToolNode` review loop, format-specific parser tools, and JSON artifact output.
```powershell
pytest tests/test_ingestion_orchestrator.py -v
```

### Milestone 6: Provenance-safe Chunking Orchestrator
Validates format-aware strategy selection, token limits, table/header handling,
hierarchical parent links, deterministic IDs, and complete element coverage.
```powershell
pytest tests/test_chunking_orchestrator.py -v
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
6.  **Chunk Lineage:** Every indexed chunk must retain `document_id`, source `element_ids`, page/section metadata, a stable `chunk_index`, and a reconstructable parent relationship.
