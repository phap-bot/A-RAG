export type ViewName = 'home' | 'signin' | 'signup' | 'forgot' | 'reset' | 'dashboard' | 'workspaces' | 'connections' | 'admin' | 'project' | 'assistant' | 'agent-flow'
export type WorkspaceSection = 'overview' | 'documents' | 'members' | 'graph' | 'settings' | 'evaluator' | 'mcp'

export type AuthUser = {
  id: string
  email: string
  display_name: string
  role: string
  created_at: number
}

export type AuthSession = {
  authenticated: boolean
  user: AuthUser | null
  csrf_token: string
}

export type UiBootstrap = {
  brand: { name: string; product: string }
  session: {
    authenticated: boolean
    display_name: string
    email: string
    role: string
    mode: string
  }
  locale: string
  locales: Array<{ code: string; label: string }>
  capabilities: {
    document_import: boolean
    document_actions: boolean
    assistant: boolean
    request_hash: string
    bm25_enabled: boolean
    rerank_enabled: boolean
    rerank_provider: string
  }
  landing: {
    eyebrow: string
    headline: string
    headline_accent: string
    description: string
    primary_action: string
    secondary_action: string
    features_title: string
    features_description: string
    features: Array<{ id: string; title: string; description: string }>
  }
}

export type AdminOverview = {
  total_users: number
  active_users: number
  global_admins: number
  total_workspaces: number
  total_memberships: number
}

export type ProjectRole = 'owner' | 'editor' | 'viewer'
export type GlobalRole = 'admin' | 'member'

export type AdminProjectMembership = {
  workspace_id: string
  role: ProjectRole
}

export type AdminUserRecord = AuthUser & {
  role: GlobalRole
  is_active: boolean
  workspace_count: number
  memberships: AdminProjectMembership[]
}

export type AdminWorkspaceRecord = {
  workspace_id: string
  name: string
  description: string
  status: 'ready' | 'draft' | 'syncing'
  document_count: number
  member_count: number
  role_counts: Record<ProjectRole, number>
}

export type AdminFeedbackSummary = {
  feedback_total: number
  negative_total: number
  unresolved_total: number
  reason_counts: Record<string, number>
}

export type AdminFeedbackRecord = {
  feedback_id: string
  answer_id: string
  project_id: string
  rating: 'positive' | 'negative'
  reason_codes: string[]
  comment: string | null
  triage_status: 'new' | 'reviewing' | 'resolved' | 'dismissed'
  admin_note: string | null
  version: number
  created_at: string
  updated_at: string
}
export type WorkspaceRecord = {
  workspace_id: string
  name: string
  description: string
  status: 'ready' | 'draft' | 'syncing'
  document_count: number
  access_role: 'owner' | 'editor' | 'viewer'
}

export type DocumentRow = {
  id: string
  name: string
  workspace: string
  page: string
  status: string
  jobId?: string | null
  uploadedAt: string
  sourcePath: string
  sizeBytes: number
  contentType: string
  editable: boolean
}

export type WorkspaceOverview = {
  workspace_id: string
  document_count: number
  indexed_count: number
  processing_count: number
  uploaded_count?: number
  failed_count?: number
  storage_bytes: number
  recent_documents: DocumentApiRecord[]
}

export type KnowledgeGraphNode = {
  id: string
  degree: number
  attributes: Record<string, unknown>
}

export type KnowledgeGraphEdge = {
  source: string
  target: string
  attributes: Record<string, unknown>
}

export type KnowledgeGraph = {
  workspace_id: string
  storage: string
  format: string
  graphml_path: string
  node_count: number
  edge_count: number
  is_directed: boolean
  is_multigraph: boolean
  density: number
  connected_components: number
  limits: { nodes: number; edges: number; include_attributes: boolean }
  top_degree_nodes: Array<{ id: string; degree: number }>
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
}

export type WorkspaceMember = {
  id: string
  display_name: string
  role: string
  status: string
}

export type WorkspaceSettings = {
  workspace_id: string
  retrieval: {
    top_k: number
    rerank_enabled: boolean
    rerank_provider: string
    rerank_top_n: number
    min_rerank_score: number
    bm25_enabled: boolean
  }
  storage: { source_root: string; upload_root: string }
}

export type DocumentApiRecord = {
  id: string
  name: string
  workspace: string
  page: string
  status: string
  job_id?: string | null
  uploaded_at: string
  source_path: string
  size_bytes: number
  content_type: string
  editable: boolean
  upload_action?: 'created' | 'updated' | 'unchanged' | 'none' | string
  content_changed?: boolean
  replaced_document_id?: string | null
}

export type UploadNotice = {
  document: DocumentRow
  action: 'created' | 'updated' | 'unchanged' | string
  contentChanged: boolean
  replacedDocumentId: string | null
}

export type ConfidenceLabel = 'high' | 'medium' | 'low' | 'none' | string

export type Citation = {
  reference_id?: string
  file_path?: string
  source_path?: string
  document_id?: string
  content?: string
  evidence?: string
  snippet?: string
  source_label?: string
  confidence?: number
  confidence_score?: number
  confidence_label?: ConfidenceLabel
  score_status?: 'measured' | 'unavailable' | string
  section_path?: string[]
  parent_header?: string | null
  page_numbers?: number[]
  element_ids?: string[]
  modality?: string
  metadata?: Record<string, unknown>
}

export type QueryConfidence = {
  score: number | null
  label: ConfidenceLabel
  source_count: number
  evidence_count: number
  rationale?: string
}

export type ChatHistoryTurn = {
  id: string
  answer_id?: string
  question: string
  answer: string
  citations: Citation[]
  confidence?: QueryConfidence
  attachment_paths: string[]
  created_at: string
}

export type ChatSessionSummary = {
  id: string
  workspace_id: string
  title: string
  created_at: string
  updated_at: string
  turn_count: number
  last_question: string
  last_answer_preview: string
  last_answer_id?: string | null
  attachment_paths: string[]
}

export type ChatSessionDetail = ChatSessionSummary & {
  turns: ChatHistoryTurn[]
}

export type QueryResponse = {
  answer_id: string
  answer: string
  citations: Citation[]
  confidence?: QueryConfidence
  chatSession?: ChatSessionSummary
  chat_session?: ChatSessionSummary | null
  retrieval_trace?: Record<string, unknown>
  provenance_validation?: Record<string, unknown>
  graph_context?: Array<Record<string, unknown>>
  attempt_history?: Array<Record<string, unknown>>
  agent_handoffs?: number
  run_status?: string
}

export type AgentStreamEvent =
  | {
      event: 'agent_thought'
      node: string
      message: string
      details?: unknown
      run_id?: string | null
    }
  | {
      event: 'tool_start'
      tool_name: string
      arguments: Record<string, unknown>
      tool_call_id?: string | null
      run_id?: string | null
    }
  | {
      event: 'tool_result'
      tool_name: string
      output: unknown
      tool_call_id?: string | null
      run_id?: string | null
    }
  | {
      event: 'message_chunk'
      content: string
      run_id?: string | null
      answer_id?: string | null
      citations?: Citation[]
      confidence?: QueryConfidence
      chat_session?: ChatSessionSummary | null
      retrieval_trace?: Record<string, unknown>
      provenance_validation?: Record<string, unknown>
      attempt_history?: Array<Record<string, unknown>>
      agent_handoffs?: number
      run_status?: string
    }
  | {
      event: 'final_response'
      content: string
      done: boolean
      run_id?: string | null
      answer_id?: string | null
      citations?: Citation[]
      confidence?: QueryConfidence
      chat_session?: ChatSessionSummary | null
      retrieval_trace?: Record<string, unknown>
      provenance_validation?: Record<string, unknown>
      attempt_history?: Array<Record<string, unknown>>
      agent_handoffs?: number
      run_status?: string
    }
  | {
      event: 'error'
      message: string
      error_type: string
      retryable: boolean
      run_id?: string | null
    }

export type AgentStreamActivity = {
  id: string
  kind: 'thought' | 'tool'
  label: string
  details?: unknown
}

export type AssistantToolName =
  | 'search_project_knowledge'
  | 'get_evidence'
  | 'answer_project_question'

export type AssistantResultType = 'answer' | 'search_results' | 'evidence'

export type AssistantToolDefinition = {
  name: string
  command: string | null
  title: string
  description: string
  agent_description: string
  use_when: string
  do_not_use_when: string
  required_arguments: string[]
  placement: string
  result_type: string
  composer_enabled: boolean
  priority: string
}

export type AssistantToolCatalog = {
  version: number
  tools: AssistantToolDefinition[]
  composer_tools: AssistantToolDefinition[]
}

export type AssistantToolExecution = {
  execution_id: string
  workspace_id: string
  command: string
  forced_tool: AssistantToolName
  result_type: AssistantResultType
  result: Record<string, unknown>
  duration_ms: number
}

export type IngestionStatus = {
  project_id: string
  target_type: 'document' | 'job'
  target_id: string
  readiness: 'ready' | 'partially_ready' | 'not_ready' | 'failed'
  ready_for_qa: boolean
  reason_code: string
  status: string
  stage: string | null
  flow_stage?: string | null
  flow_event_count?: number
  job_id: string | null
  chunk_count: number
  retryable: boolean
  updated_at: string | null
  error?: Record<string, unknown> | null
}

export type IngestionFlowEvent = {
  event: 'node_started' | 'node_completed' | 'progress' | 'state_update'
  node?: string
  namespace?: string[]
  status?: string
  kind?: string
  payload?: Record<string, unknown>
  duration_ms?: number
  sequence: number
  timestamp: string
}

export type IngestionFlowEventsResponse = {
  job_id: string
  document_id: string
  status: string
  flow_stage: string | null
  events: IngestionFlowEvent[]
  next_after: number
  completed: boolean
}

export type AnswerFeedbackRating = 'positive' | 'negative'

export type DocumentPreview = {
  document_id: string
  name: string
  source_path: string
  status: string
  content_type: string
  size_bytes: number
  preview: string
  preview_available: boolean
}

export type DocumentBoundingBox = {
  x1: number
  y1: number
  x2: number
  y2: number
}

export type DocumentReviewElement = {
  element_id: string
  content: string
  raw_content?: string | null
  page_number: number
  element_index: number
  element_type: string
  bounding_box?: DocumentBoundingBox | null
  confidence: number
  parent_header?: string | null
  header_level?: number | null
  section_path: string[]
  extra: Record<string, unknown>
  vlm_caption?: string | null
}

export type DocumentReviewPage = {
  page_number: number
  elements: DocumentReviewElement[]
}

export type DocumentReview = DocumentPreview & {
  total_pages: number
  elements: DocumentReviewElement[]
  pages: DocumentReviewPage[]
  markdown: string
  stats: Record<string, unknown>
  has_bounding_boxes: boolean
  review_available: boolean
}

export type DocumentMetadata = {
  project_id: string
  document_id: string
  name: string | null
  source: string
  source_path: string
  status: string
  content_type: string | null
  size_bytes: number | null
  version: string | number | null
  hash: string | null
  document_type: string | null
  development_stage: string | null
  chunk_count: number | null
  updated_at: string | null
  metadata: Record<string, unknown>
}
export type McpCredential = {
  id: string
  name: string
  scopes: string[]
  project_ids: string[]
  status: 'active' | 'expired' | 'revoked'
  created_at: string
  expires_at: string
  last_used_at: string | null
  workspace_role?: 'owner' | 'editor' | 'viewer'
}

export type McpConnectionExport = {
  server_name: string
  transport: 'streamable-http'
  url: string
  authorization: 'Bearer'
  api_key_env: string
  workspace_id: string
  workspace_role: 'owner' | 'editor' | 'viewer'
  sdk: {
    python_package: string
    python_module: string
    client_class: string
  }
  config: {
    mcpServers: Record<string, {
      command: string
      args: string[]
    }>
  }
}

export type IssuedMcpCredential = {
  api_key: string
  sdk_config: {
    base_url: string
    api_key: string
    project_id: string
  }
  credential: McpCredential
  connection: McpConnectionExport
  issued_for?: {
    user_id: string
    display_name: string
    email: string
    global_role: string
  }
}

export const EVALUATION_METRICS = [
  'faithfulness',
  'response_relevancy',
  'context_precision',
  'context_recall',
] as const

export type EvaluationMetric = typeof EVALUATION_METRICS[number]

export type EvaluationJobSummary = {
  job_id: string
  workspace_id: string
  filename: string
  status: 'queued' | 'running' | 'completed' | 'failed' | string
  score_status: 'not_started' | 'scoring' | 'completed' | 'completed_with_errors' | 'failed' | 'manual_review' | 'no_ground_truth' | 'no_scoreable_rows' | 'no_context' | string
  evaluation_mode: 'manual_review' | 'auto_score' | 'mixed' | string
  total_rows: number
  completed_rows: number
  failed_rows: number
  scored_rows: number
  trace_only_rows: number
  display_metrics: EvaluationMetric[]
  judge_model: string | null
  error_message: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
}

export type EvaluationTraceItem = {
  chunk_id?: string | null
  rank?: number
  content?: string
  document_id?: string | null
  source_path?: string | null
  dense_score?: number | null
  bm25_score?: number | null
  rrf_score?: number | null
  graph_score?: number | null
  rerank_score?: number | null
  selected_for_answer?: boolean
}

export type EvaluationRow = {
  case_id: string
  question: string
  reference_answer: string | null
  generated_answer: string | null
  answer_id: string | null
  status: string
  evaluation_mode: 'trace_only' | 'grounded' | string
  duration_ms: number | null
  trace: {
    vector_db: EvaluationTraceItem[]
    hybrid_retrieval: EvaluationTraceItem[]
    graph_search: EvaluationTraceItem[]
    reranking: EvaluationTraceItem[]
    final_contexts: EvaluationTraceItem[]
    availability?: Record<string, string>
    context_budget_cap?: Record<string, number | boolean | null>
    pipeline?: Record<string, unknown>
    trace_error?: string | null
  } | null
  scores: Record<EvaluationMetric, number | null>
  error_code: string | null
  error_message: string | null
  score_error?: string | null
}

export type EvaluationRowsResponse = {
  job_id: string
  workspace_id: string
  offset: number
  limit: number
  total: number
  display_metrics: EvaluationMetric[]
  rows: EvaluationRow[]
}
