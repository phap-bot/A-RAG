# Agentic RAG workflow and implementation status

This note records the current implementation after the Main-agent workflow,
evaluation path, and local state checkpoint were connected. The attached
workflow image is treated as a design reference: a supervisor routes to agents,
agents call tools through a shared node, and conditional edges control return,
handoff, and termination. It is not treated as source code or as a set of
instructions embedded in the image.

## What the system builds

A-RAG currently has four connected parts:

1. **Ingestion (Zone 1):** uploads become a parsed `ParsedDocument`, validated
   `ContentChunk[]`, provenance, embeddings when enabled, and Neo4j records.
2. **Agentic query (Zone 2):** Main coordinates role-specific agents and tools
   with one LangGraph `StateGraph` and one shared `ToolNode`. Retrieval uses
   Neo4j when enabled and workspace-scoped local lexical search otherwise.
3. **Knowledge service (Zone 3):** the FastAPI/MCP boundary exposes
   workspace-scoped search, graph expansion, and exact evidence lookup.
4. **Evaluation (Zone 4):** CSV/XLSX question sets run through the same query
   service, retain traces, and can be scored by the configured chat model.

The web app returns answer text, citations, confidence, retrieval trace,
provenance validation, critic attempt history, handoff count, and run status.
The SSE route streams safe agent progress and tool boundaries, then emits the
same structured result on completion.

## Query graph

```text
START
  ↓
agent_main ── conditional ──→ tools ──→ active agent
  ├─ handoff_to_query_formulator → query_formulator ─┐
  ├─ handoff_to_parallel_retriever → parallel_retriever ─┤
  ├─ handoff_to_synthesizer → synthesizer ────────────┤
  ├─ handoff_to_critic_reflection → critic_reflection ┤
  └─ candidate reviewed / limit reached → END        │
                                                       └→ agent_main

agent_main → critic_reflection for every new answer candidate
Critic pass → restore best candidate → END
Critic fail → Main chooses reformulation/retrieval/revision or best effort
```

The preferred path is Formulator → Retriever → Synthesizer → Critic, but this
is guidance rather than a hard-coded sequence. With a configured LLM, each
agent receives a role-specific tool schema using `bind_tools` and chooses
whether to issue data-tool calls or a handoff. LangGraph conditional edges
inspect tool calls and state to route execution. Without an LLM, deterministic
fallback turns still emit LangChain tool calls, so the shared `ToolNode` path
remains active, but the fallback itself chooses a fixed recovery sequence.

## Node responsibilities

| Node | Responsibility | Allowed tool behavior |
| --- | --- | --- |
| `agent_main` | Own the request, decide next step, coordinate specialists, decide recovery after Critic feedback | Search, graph, exact evidence, or hand off to any specialist |
| `query_formulator` | Build focused hybrid and graph queries; use Critic feedback for a retry | Search/graph probe; hand off to Main or Retriever |
| `parallel_retriever` | Find evidence with multiple search/graph calls and resolve exact passages | Search, graph, exact evidence; hand off to Main or Formulator |
| `synthesizer` | Draft a cited answer from retrieved evidence | Search/evidence verification; hand off to Main or Retriever |
| `critic_reflection` | Independently score the candidate and check source IDs | Search/evidence verification; hand off to Main, Retriever, or Synthesizer |
| `tools` | Execute the active role's pending LangChain tool calls | Dispatch only the active role's allowlist through `ToolNode` |

Tool schemas and adapters live in `src/agents/tools/registry.py`; graph
construction is in `src/agents/orchestrator/graph.py`; common role runtime,
state projections, tool execution, and conditional routing are in
`src/agents/agent_runtime.py`.

## Tool call and tenant-scope path

1. Each role binds its allowlisted tools to the chat model.
2. A model response contains one or more normal LangChain `tool_calls`.
3. The conditional edge routes the pending call to the shared `tools` node.
4. The tools node builds a `ToolNode` for the current role, executes its calls,
   and appends `ToolMessage` results to that role's transcript.
5. Tool effects update shared evidence, graph context, retrieval trace, errors,
   and (for a handoff tool) the next `active_agent`.
6. A conditional edge returns to the calling agent or follows the handoff.

`workspace_id` and selected `file_paths` are injected from the authenticated
request's graph state. They are not model-controlled tool arguments. Search
limits, graph hops, total handoffs and tool rounds are bounded. The same domain
retrieval functions back the HTTP/MCP service and internal tools; the
`parallel_retriever` node no longer directly runs retrieval calls itself.

## Context, loop, and best answer

- Each agent has a separate append-only message channel. A handoff starts a new
  role turn; a tool result resumes the same role turn.
- The complete user conversation history is supplied as bounded input context;
  the chat boundary supplies at most the latest six turns.
- The team shares a compact structured run state: user query, workspace/file
  scope, formulated search plan, retrieved chunks, graph context, tool trace,
  current candidate, Critic verdict, errors, and best candidate.
- Every new Synthesizer candidate increments a version and is routed through
  Critic. Critic checks numeric quality and a deterministic provenance gate.
- A failed Critic result returns control to Main. Main may reformulate and
  retrieve again, revise the response, or stop at the configured attempt cap.
- The best candidate prefers a passing candidate; among candidates with equal
  pass status it keeps the highest weighted score (faithfulness 0.5, relevance
  0.3, citation accuracy 0.2). The best candidate is restored at termination.
- `MAX_REFLECTION_RETRIES` is the total number of reviewed candidates,
  including the first. `MAX_AGENT_HANDOFFS` and
  `MAX_TOOL_ROUNDS_PER_AGENT` bound delegation and tool loops.
- `AGENT_LLM_ENABLED=false` keeps local/test runs offline and deterministic;
  set it to `true` together with a real `OPENAI_API_KEY` when Zone 2 agents
  should call the configured chat model. Tool selection and handoffs use the
  same LangGraph graph in either mode.

There is no durable LangGraph checkpointer yet. Per-run transcripts and loop
state live for the graph invocation; only the resulting chat turn and
evaluation/job projections are persisted by the API.

## Evaluation path

The evaluation screen accepts `.csv` and `.xlsx` with a `question` header and
optional `reference_answer` header, up to 200 rows and 10 MB. Each row invokes
`WebApplicationService.query(..., save_history=False)`, so it uses the same
workspace retrieval adapter and Main-agent graph without adding synthetic
chat turns.
The row stores the generated answer, duration, citations/context, graph search,
retrieval metadata, provenance, run status, and attempt history.

Rows with reference answers can be scored by the configured chat model as an
LLM judge on four 0–1 metrics: faithfulness, response relevancy, context
precision, and context recall. Rows without references stop after answer
generation for manual review. The current score is a model judgment, not a
RAGAs implementation or a statistically calibrated benchmark. Missing
retrieval context is recorded as `no_context`; model/JSON failures are
recorded per row and can be retried. CSV export is packaged as ZIP to match
the web contract; XLSX export is generated as a workbook.

Evaluation jobs live in the same local checkpoint as chats and ingestion jobs.
Their worker runs in-process, so a process restart marks queued/running work as
interrupted; it does not resume execution.

## Persistence boundary

| Data | Current location | Restart behavior |
| --- | --- | --- |
| Original uploaded source | `.runtime/web/workspaces/<workspace>/...` | Kept on disk |
| User/workspace membership, document metadata, parsed document/chunks, chat turns, feedback, evaluation rows, ingestion events | `.runtime/web/service_state.json`, atomically replaced | Loaded into the local API service |
| Vector index, chunk/lineage graph | Neo4j when enabled | Durable in Neo4j |
| Authentication principal | Neo4j when enabled and local JSON checkpoint | Repository-backed when configured; local record supports the API boundary |
| Active worker execution | In-process executors | Marked interrupted; user restarts it |
| LangGraph agent transcript/checkpoint | Current graph invocation | Not persisted across requests/restarts |

The local JSON boundary serves one API process and is not a multi-instance
database, transactional queue, or access-controlled production store. Use a
shared database/checkpointer and a durable queue before horizontal scaling.

## Task checklist

| Task | State | Notes |
| --- | --- | --- |
| Replace fixed retrieval node with Main + specialist conditional handoffs | Done | Model-selected role handoffs and role-scoped tools; offline fallback remains deterministic |
| Give agents actual tools and a shared executor | Done | LangChain `bind_tools` + shared LangGraph `ToolNode` |
| Inject workspace/file scope and bound loops | Done | Scope is state-injected; handoff and tool-round limits are configurable |
| Critic retry and best-answer policy | Done | Version gate, attempt history, provenance validation, best candidate restoration |
| Connect evaluation to real query runs and judge scoring | Done | CSV/XLSX upload, trace capture, async in-process judge scoring and rescore |
| Persist workspace/chat/job/evaluation state | Done for local checkpoint | Atomic JSON plus source files; not a distributed persistence layer |
| Surface agent/tool/handoff events in SSE and flow UI | Done | Shared `Tools` step and handoff target are shown |
| Run regression/integration coverage for the new workflow and fill gaps | Open | Not run as part of this implementation turn |
| Add durable LangGraph checkpoint and external worker queue | Open | Needed for restartable jobs, multi-process API, and long-running runs |
| Calibrate metrics with a labeled dataset and human agreement | Open | Current LLM judge scores are operational signals only |
