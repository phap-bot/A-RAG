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
        │ (JSON Tools)                                     │ (Cypher/indexes)
        ▼                                                  ▼
 ┌─────────────────────────┐                      ┌──────────────────┐
 │   ZONE 2: AGENTIC CORE  │                      │  NEO4J STORAGE   │
 │       (LangGraph)       │                      ├──────────────────┤
 │  • Main supervisor     │                      │ • Vector index   │
 │  • Specialist agents   │                      │ • Full-text/BM25 │
 │  • Shared ToolNode ────┼──────────────────────┤ • Graph lineage  │
 │  • Critic / best answer│                      │ • Document store  │
 └───────────┬─────────────┘                      └────────┬─────────┘
            │                                             │
            │ (Hallucination/Errors)                      │ (Embeddings/Relations)
            ▼                                             │
┌─────────────────────────┐                      ┌────────┴─────────┐
│   ZONE 4: EVALUATION    │                      │ ZONE 1: INGESTION│
│ • LLM-as-judge metrics  │                      │ • Parser & MinerU│
│ • Retrieval traces      │                      │ • VLM Captioning │
└─────────────────────────┘                      │ • Structure Chunks
                                                 └──────────────────┘
```

*   **Zone 1: Ingestion Pipeline:** The public upload path runs an in-process LangGraph pipeline: detect type → format parser tool → quality check → bounded OCR retry → normalize → semantic chunk subgraph → structural entity/relationship extraction → parallel embedding and graph preparation → provenance validation → Neo4j index. The separate parser graph remains available for LLM-driven skill/tool selection, but the web ingestion executor is deterministic and emits real LangGraph task, update and custom events so the UI can monitor every node without allowing an LLM to rewrite source content.
*   **Zone 2: Agentic Core (Real-time):** a Main-agent supervisor coordinates Query Formulator, Retriever, Synthesizer and Critic. Every role receives a scoped LangChain tool catalog and can call tools through the shared LangGraph `ToolNode`; conditional edges return tool results to the calling agent or hand work to a specialist. Critic review and best-candidate selection gate completion. See the [workflow and task checklist](docs/agentic-rag-workflow.md) for node duties, context loops, tool routing, evaluation and storage boundaries.
*   **Zone 3: MCP Gateway & Data Infrastructure:** Exposes standard MCP tools for Neo4j vector/full-text retrieval, evidence lookup and bounded provenance graph expansion. Neo4j stores query-time chunks and relationships when enabled; the local API checkpoint stores workspace metadata and parsed chunks for the single-process development mode.
*   **Zone 4: Evaluation:** CSV/XLSX question sets run through the same workspace query service, save retrieval/provenance traces, and use the configured chat model as an LLM judge for Faithfulness, Response Relevancy, Context Precision and Context Recall. RAGAs itself is not installed or claimed as the scoring engine.

---

## 2. Directory Structure

```
A-RAG/
├── src/
│   ├── api/                    # HTTP Gateway (FastAPI)
│   │   ├── main.py              # Web/UI endpoint contract
│   │   └── service.py           # Ingestion, query and storage boundary
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
│   │   └── tools/              # Reserved for future agent-only tools
│   │
│   ├── ingestion/              # ZONE 1: active ingestion implementation
│   │   ├── parser/
│   │   │   ├── models.py       # Pydantic parser and chunk contracts
│   │   │   ├── profiler.py     # File classification and strategy selection
│   │   │   ├── layout_parser.py # Native Markdown, text, CSV/TSV, code parsing
│   │   │   ├── mineru_adapter.py # MinerU API client + ParsedDocument normalizer
│   │   │   ├── skills.py       # Runtime skills and LangChain @tool wrappers
│   │   │   ├── state.py        # Ingestion graph AgentState contract
│   │   │   └── orchestrator.py # Standalone LLM parser-agent loop (not Web executor)
│   │   ├── chunking/            # ParsedDocument -> ContentChunk StateGraph
│   │   │   ├── state.py         # ChunkingState contract and controls
│   │   │   ├── strategies.py    # Format-aware deterministic splitters
│   │   │   └── orchestrator.py  # Plan -> build -> validate graph
│   │   ├── pipeline/             # End-to-end ingestion runtime StateGraph
│   │   │   └── graph.py          # Detect -> extract -> index graph + stream events
│   │   └── embedding/            # Reserved; active provider is src/retrieval/embeddings.py
│   │
│   ├── retrieval/              # ZONE 3 (Internal): Neo4j storage infrastructure
│   │   ├── neo4j_repository.py # Chunk/vector/full-text/graph repository
│   │   ├── embeddings.py       # Configured embedding provider boundary
│   │   └── __init__.py
│   │
│   ├── cache/                  # Reserved for a later Redis cache zone
│   │   └── __init__.py
│   │
│   ├── evaluation/             # Evaluation primitives and judge support
│   │   └── __init__.py
│   │
│   └── core/                   # Cross-Cutting Infrastructure
│       ├── config.py           # Pydantic Settings & JSON Loguru logger
│       ├── prompts.py          # Centralized System Prompts repository
│       ├── exceptions.py       # Hierarchical system exceptions
│       └── llm_client.py       # LLM provider factory (Chat, VLM, Embeddings)
│
├── tests/                      # Pytest suite
├── .vibeflow/skills/           # Agent procedures in SKILL.md format
├── requirements.txt            # Dependency manifest
├── .env.example                # Environment configuration template
└── README.md                   # Architecture and local runbook
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

### Web UI + BE integration (Zone 1 checkpoint)

The `web/` application is served against the FastAPI adapter in
`src/api/main.py`. Keep the two HTTP services on separate ports:

| Service | URL | Responsibility |
|---|---|---|
| MinerU API | `http://127.0.0.1:8000` | PDF/Office/image layout parsing (`pipeline` backend) |
| A-RAG API | `http://127.0.0.1:8010` | Auth, workspace, upload, Zone 1 ingestion, preview, metadata, query contract |
| React/Vite UI | `http://127.0.0.1:5173` | User-facing workspace and assistant |

Start the stack in three PowerShell terminals:

```powershell
# Terminal 1: MinerU native service (use its dedicated environment)
.\.venv-mineru\Scripts\Activate.ps1
$env:CUDA_VISIBLE_DEVICES = "0"
mineru-api --host 127.0.0.1 --port 8000

# Terminal 2: A-RAG backend
cd D:\Antigravity\A-RAG
.\.venv\Scripts\Activate.ps1
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8010

# Terminal 3: Web UI
cd D:\Antigravity\A-RAG\web
npm run dev -- --host 127.0.0.1 --port 5173
```

Verify both backend boundaries before opening the UI:

```powershell
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8010/health
```

The browser flow is:

```text
web upload
  → POST /v1/workspaces/{workspace_id}/documents
  → save source bytes under .runtime/web
  → uploaded response + ingestion job handle
  → user selects Start ingestion / Sync
  → POST /v1/documents/actions/sync-up
  → processing
  → ingestion_pipeline StateGraph
  → detect_type → extraction tool (MinerU/native)
  → quality_check → OCR retry only when supported
  → normalize → chunking subgraph: plan → build → validate
  → structural entity/relationship extraction
  → embedding branch + graph extraction branch
  → provenance validation
  → BGE-M3 dense embedding (1024 dimensions, L2 normalized)
  → Neo4j upsert: Document → Element/Section → Chunk + lineage edges
  → indexed, or failed with structured error
  → UI polls status and ordered LangGraph events for the runtime flow panel
```

The imported frontend contract is implemented for bootstrap/auth, workspaces,
documents, upload/download/rename/move/delete, ingestion status, preview,
metadata, graph view, chat sessions, query, assistant tools, answer feedback,
MCP credential screens, admin screens and the evaluation screen. The main
Zone 1 endpoints are:

```text
GET  /v1/ui/bootstrap
GET  /v1/auth/session
POST /v1/auth/signup | /v1/auth/signin | /v1/auth/signout
GET  /v1/workspaces
POST /v1/workspaces
GET  /v1/documents?workspace_id=...
POST /v1/workspaces/{workspace_id}/documents
POST /v1/documents/actions/sync-up
GET  /v1/ingestion/jobs/{job_id}
GET  /v1/ingestion/jobs/{job_id}/events?workspace_id=...&after=0
GET  /v1/ingestion/status?workspace_id=...&document_id=...
GET  /v1/documents/{document_id}/preview
GET  /v1/workspaces/{workspace_id}/documents/{document_id}/metadata
POST /api/chat/stream       # UI Agentic RAG realtime (SSE)
POST /v1/query              # synchronous/legacy client contract
GET  /v1/workspaces/{workspace_id}/assistant/tools
POST /v1/workspaces/{workspace_id}/assistant/tool-executions
```

The web source-byte preview store remains local under `.runtime/web`. The API
atomically checkpoints user/workspace membership, document and ingestion-job
metadata, parsed documents/chunks, chats, feedback and evaluation jobs to
`.runtime/web/service_state.json`; source bytes remain separate files under
`.runtime/web/workspaces/`. Neo4j stores auth principals and retrieval/index
data when enabled.
The JSON checkpoint is a single-process local persistence boundary, not a
multi-host job queue or shared database; an in-flight job is marked interrupted
after a process restart and must be explicitly restarted. Passwords are hashed
server-side with Argon2id; the API never returns password hashes. The browser keeps auth and UI state out of browser
storage; navigation is represented by non-sensitive URL paths/query parameters,
while the session cookie is HttpOnly and is not readable by frontend
JavaScript. With `NEO4J_ENABLED=true`, the explicit Start ingestion
command runs the parser, chunker, provenance gate, embedding provider, and
transactionally upserts to Neo4j; `/v1/query` invokes the compiled Zone 2
LangGraph through Neo4j-backed tools. With Neo4j disabled, the same graph runs
against a workspace-scoped local lexical tool adapter. The UI capability response
reports `bm25_enabled`, `vector_enabled` and `graph_enabled` from
configuration; no synthetic vector score is returned when embeddings are
disabled.

Document status has four user-visible states: `uploaded` means source bytes are
stored but not parsed, `processing` means the ingestion worker is executing,
`indexed` means parsing/chunking/provenance/indexing completed, and `failed`
contains a structured error and can be started again. The local web runtime
uses a bounded in-process executor controlled by `INGESTION_WORKER_COUNT`;
Redis/Celery settings remain reserved for a later multi-process worker
deployment.

### Neo4j local integration (selected backend)

Neo4j is the single storage layer for this project. The same `Chunk` node
carries immutable source text, an optional embedding and complete provenance
metadata; `Document`, `Element` and `Section` nodes make the source hierarchy
queryable. This avoids dual-writing to a vector database and a graph database.
The repository is implemented in
[`src/retrieval/neo4j_repository.py`](src/retrieval/neo4j_repository.py), while
the agent only sees the tool boundary in
[`src/mcp_server/tools/kb_retrieval_tools.py`](src/mcp_server/tools/kb_retrieval_tools.py).

On Windows, install Neo4j Desktop, create a local DBMS, set its password, and
start it. The default local endpoints are Bolt `bolt://127.0.0.1:7687` and the
browser `http://127.0.0.1:7474`. A standalone Neo4j distribution can instead be
started with `neo4j console`.

Set the A-RAG environment before starting the API:

```powershell
NEO4J_URI="bolt://127.0.0.1:7687"
NEO4J_USER="neo4j"
NEO4J_PASSWORD="<the-password-you-set-in-Neo4j>"
NEO4J_DATABASE="neo4j"
NEO4J_VECTOR_INDEX="chunk_embedding_bge_m3"
NEO4J_FULLTEXT_INDEX="chunk_content_fulltext"
NEO4J_VECTOR_QUERY_MODE="search"
VECTOR_DB_PROVIDER="neo4j"
NEO4J_ENABLED=true
NEO4J_AUTO_SCHEMA=true
EMBEDDING_ENABLED=true
EMBEDDING_PROVIDER="bge_m3"
EMBEDDING_MODEL_NAME="BAAI/bge-m3"
EMBEDDING_DIMENSION=1024
EMBEDDING_NORMALIZE=true
EMBEDDING_DEVICE="cuda"
EMBEDDING_CACHE_DIR=".models"
```

`NEO4J_ENABLED=true` is the switch that changes the Web API from the explicit
Zone 1 local development store to live Neo4j persistence/retrieval. The
password above is only a placeholder and must be replaced. The selected local
embedding path uses `sentence-transformers` to load `BAAI/bge-m3`, produces one
1024-dimensional dense vector per chunk/query, and L2-normalizes it before
Neo4j cosine search. BM25 remains the independent lexical retrieval channel;
the repository fuses both ranks with RRF. The application never creates
deterministic pseudo-vectors.

With `EMBEDDING_DEVICE=cuda`, the local BGE-M3 provider passes `device=cuda` to
`SentenceTransformer` and verifies both `torch.cuda.is_available()` and the
loaded model device. It raises a configuration error instead of silently
falling back to CPU. MinerU is a separate process; start its `pipeline` API
with `CUDA_VISIBLE_DEVICES=0` if its local GPU should be restricted to the
first NVIDIA device. The configured answer LLM is an OpenAI-compatible remote
endpoint, so its GPU placement is controlled by that server, not this machine.

Run this schema once in Neo4j Browser at `http://127.0.0.1:7474` (or let
`NEO4J_AUTO_SCHEMA=true` create it on API startup):

```cypher
CREATE CONSTRAINT chunk_scope_unique IF NOT EXISTS
FOR (c:Chunk) REQUIRE (c.workspace_id, c.chunk_id) IS UNIQUE;

CREATE CONSTRAINT document_scope_unique IF NOT EXISTS
FOR (d:Document) REQUIRE (d.workspace_id, d.document_id) IS UNIQUE;

CREATE CONSTRAINT user_id_unique IF NOT EXISTS
FOR (u:User) REQUIRE u.user_id IS UNIQUE;

CREATE CONSTRAINT user_email_unique IF NOT EXISTS
FOR (u:User) REQUIRE u.email IS UNIQUE;

CREATE INDEX chunk_workspace_index IF NOT EXISTS
FOR (c:Chunk) ON (c.workspace_id);

CREATE VECTOR INDEX chunk_embedding_bge_m3 IF NOT EXISTS
FOR (c:Chunk) ON c.embedding
OPTIONS {indexConfig: {
  `vector.dimensions`: 1024,
  `vector.similarity_function`: 'cosine'
}};

CREATE FULLTEXT INDEX chunk_content_fulltext IF NOT EXISTS
FOR (c:Chunk) ON EACH [c.content];

SHOW VECTOR INDEXES;
```

The expected vector index state is `ONLINE`; a `POPULATING` index is not ready
for retrieval. Composite scope keys are required because Zone 1 document and
chunk IDs are deterministic from source content and may repeat in different
workspaces. On an existing database created with the old global
`chunk_id_unique`/`document_id_unique` constraints, remove those obsolete
constraints during a controlled migration before enabling the new schema.

The write path is one transactional batch using Cypher `UNWIND`:

```text
ParsedDocument + validated ContentChunk[]
  → Document (workspace_id, document_id)
  → Element (exact parsed blocks, bbox/page/type)
  → Section (heading breadcrumb tree)
  → Chunk (raw content, embedding, page/element/section metadata)
  → HAS_ELEMENT / HAS_SECTION / HAS_CHUNK
  → SOURCED_FROM / IN_SECTION / PARENT_SECTION / PARENT_OF
```

On re-index, stale chunks/elements/sections and derived lineage edges for the
same document are removed inside the transaction before the current graph is
relinked. Content is never rewritten by the LLM or repository.

Retrieval has two independent channels: Neo4j full-text search provides the
lexical/BM25 signal; the vector index provides dense candidates when an
embedding provider is enabled. The repository fuses ranks with Reciprocal
Rank Fusion (RRF), applies workspace/file-path filters, and only then returns
the configured `RETRIEVAL_TOP_K`. Graph expansion starts from full-text seeds
and clamps traversal to `NEO4J_GRAPH_MAX_HOPS`, with a second workspace check
on the target node.

`NEO4J_VECTOR_QUERY_MODE=search` uses the current Cypher `SEARCH` vector
syntax. For an older Neo4j 5.x server that does not support `SEARCH`, set the
value to `procedure`; the repository then uses the compatibility procedure and
keeps the same filtering/RRF contract.

The Neo4j Python driver is created once per API process and closed by the
FastAPI lifespan hook. The driver is concurrency-safe; each retrieval task
uses its own session, while the Zone 2 retriever runs vector and graph calls
in parallel and sorts results deterministically before synthesis.

The network MCP gateway is available at the configured MCP port:

```powershell
python -m src.mcp_server.server
```

It exposes `search_knowledge_base`, `query_knowledge_graph` and `get_evidence`.
Internal LangGraph agents receive LangChain tool schemas backed by the same
domain functions, and execute their calls through a shared `ToolNode`; tools
inject the authenticated `workspace_id` from graph state rather than asking the
model to choose a tenant. External clients use the streamable HTTP MCP
transport. All three data tools require workspace scope.

### Zone 2 supervisor and agent handoffs

The query workflow follows a supervisor/team model rather than a fixed chain
of independent functions:

```text
START → agent_main
          ├─ tool call → tools → active agent (tool result loop)
          ├─ handoff_to_query_formulator → query_formulator → agent_main
          ├─ handoff_to_parallel_retriever → parallel_retriever → agent_main
          ├─ handoff_to_synthesizer → synthesizer → agent_main
          └─ handoff_to_critic_reflection → critic_reflection → agent_main

agent_main → critic_reflection when a new answer candidate needs review
critic_reflection → agent_main → END when passed or maximum attempts reached
critic_reflection → agent_main → specialist handoff when another attempt is needed
```

Each agent has a role-scoped tool allowlist from
`src/agents/tools/registry.py`. The LLM chooses whether to call a data tool or
delegate to another role; the graph executes calls in the shared `tools` node
and sends each result back to the active agent. Specialist transcripts are
kept in separate state channels, while evidence, trace, critique, and the best
answer candidate are shared in `AgentState`. Tool calls and specialist
handoffs have per-run limits. `max_retries` means total candidate review
attempts, including the first answer.

The default workflow guidance encourages Formulator → Retriever → Synthesizer
→ Critic, but Main can skip or repeat a specialist when state and tool results
justify it. Critic review is enforced as a graph gate for every new answer;
the best validated candidate is restored if the final attempt is weaker.

### Zone 3 Agentic SSE stream

`POST /api/chat/stream` streams live execution updates from the LangGraph
agent. The request contains `workspace_id`, `question`, and optional
`file_paths`/`conversation_history`/`chat_session_id`; the response uses
`text/event-stream`.
Each `data:` frame contains one validated payload with an `event` of
`agent_thought`, `tool_start`, `tool_result`, `message_chunk`, `final_response`,
or `error`. Planner metadata is sent as `agent_thought.details` and is never
rendered in the main answer bubble. Only `message_chunk.content` is rendered
as answer text.
The endpoint authenticates the session and workspace before starting the graph.
The `message_chunk` frames carry answer deltas; the completion frame also
contains the normalized `answer_id`, citations, confidence, chat-session
summary, retrieval trace and provenance validation. The stream ends with a
`final_response` where `done=true`. The React UI consumes this endpoint for
both chat surfaces; it does not issue a second `/v1/query` request.

For a local connectivity check after Neo4j is started:

```powershell
python -c "from neo4j import GraphDatabase; from src.core.config import settings; d=GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)); d.verify_connectivity(); print('Neo4j connectivity: OK'); d.close()"
```

This connectivity check only verifies Bolt authentication. A complete local
smoke test should then upload a Markdown file through the Web UI/API, inspect
`MATCH (c:Chunk {workspace_id: $workspace_id}) RETURN c`, and ask the same
question through `/api/chat/stream` to verify streamed events, citations and
`provenance_validation` in the completion payload.

To validate the integration locally:

```powershell
pytest -q
python -m compileall -q src tests
cd web
npm run build
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
