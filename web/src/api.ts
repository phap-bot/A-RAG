import i18n from './i18n'
import type {
  AdminOverview,
  AdminFeedbackRecord,
  AdminFeedbackSummary,
  AdminUserRecord,
  AdminWorkspaceRecord,
  AuthSession,
  AuthUser,
  ChatSessionDetail,
  ChatSessionSummary,
  Citation,
  DocumentApiRecord,
  DocumentPreview,
  DocumentReview,
  DocumentMetadata,
  DocumentRow,
  IssuedMcpCredential,
  McpCredential,
  QueryConfidence,
  QueryResponse,
  AnswerFeedbackRating,
  IngestionStatus,
  IngestionFlowEventsResponse,
  UiBootstrap,
  WorkspaceMember,
  WorkspaceOverview,
  KnowledgeGraph,
  WorkspaceRecord,
  WorkspaceSettings,
  UploadNotice,
  EvaluationJobSummary,
  EvaluationMetric,
  EvaluationRowsResponse,
  AssistantToolCatalog,
  AssistantToolExecution,
  AssistantToolName,
  AgentStreamEvent,
} from './types'

const configuredApiUrl = import.meta.env.VITE_API_BASE_URL?.trim()
const API_BASE_URL = (configuredApiUrl || window.location.origin).replace(/\/$/, '')
const APP_BASE_PATH = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '')

function withAppBasePath(path: string): string {
  if (!APP_BASE_PATH || APP_BASE_PATH === '/') return path
  return path.startsWith(`${APP_BASE_PATH}/`) ? path : `${APP_BASE_PATH}${path}`
}

export const API_ROUTES = {
  signUp: '/v1/auth/signup',
  signIn: '/v1/auth/signin',
  signOut: '/v1/auth/signout',
  session: '/v1/auth/session',
  forgotPassword: '/v1/auth/forgot-password',
  resetPassword: '/v1/auth/reset-password',
  bootstrap: '/v1/ui/bootstrap',  adminOverview: '/v1/admin/overview',
  adminUsers: '/v1/admin/users',
  adminUser: (userId: string) => `/v1/admin/users/${encodeURIComponent(userId)}`,
  adminWorkspaces: '/v1/admin/workspaces',
  adminFeedbackSummary: '/v1/admin/feedback/summary',
  adminFeedback: '/v1/admin/feedback',
  adminFeedbackItem: (feedbackId: string) => `/v1/admin/feedback/${encodeURIComponent(feedbackId)}`,
  adminWorkspaceRole: (userId: string, workspaceId: string) => `/v1/admin/users/${encodeURIComponent(userId)}/workspaces/${encodeURIComponent(workspaceId)}`,
  adminMcpCredentials: '/v1/admin/mcp/credentials',
  language: '/v1/preferences/language',
  mcpCredentials: '/v1/mcp/credentials',
  mcpCredential: (credentialId: string) => `/v1/mcp/credentials/${encodeURIComponent(credentialId)}`,
  workspaces: '/v1/workspaces',
  workspace: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}`,
  documents: '/v1/documents',
  workspaceOverview: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/overview`,
  workspaceGraph: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/graph`,
  workspaceGraphml: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/graph/graphml`,
  workspaceMembers: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/members`,
  workspaceSettings: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/settings`,
  chatSessions: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/sessions`,
  chatSession: (workspaceId: string, sessionId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/sessions/${encodeURIComponent(sessionId)}`,
  uploadDocument: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/documents`,
  ingestionJob: (jobId: string) => `/v1/ingestion/jobs/${encodeURIComponent(jobId)}`,
  document: (documentId: string) => `/v1/documents/${encodeURIComponent(documentId)}`,
  documentContent: (documentId: string) => `/v1/documents/${encodeURIComponent(documentId)}/content`,
  documentPreview: (documentId: string) => `/v1/documents/${encodeURIComponent(documentId)}/preview`,
  documentReview: (documentId: string) => `/v1/documents/${encodeURIComponent(documentId)}/review`,
  documentMetadata: (workspaceId: string, documentId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/documents/${encodeURIComponent(documentId)}/metadata`,
  syncDocuments: '/v1/documents/actions/sync-up',
  moveDocuments: '/v1/documents/actions/move',
  deleteDocuments: '/v1/documents/actions/delete',
  query: '/v1/query',
  chatStream: '/api/chat/stream',
  assistantTools: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/assistant/tools`,
  assistantToolExecutions: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/assistant/tool-executions`,
  ingestionStatus: '/v1/ingestion/status',
  ingestionEvents: (jobId: string, workspaceId: string, after: number) => `/v1/ingestion/jobs/${encodeURIComponent(jobId)}/events?workspace_id=${encodeURIComponent(workspaceId)}&after=${after}`,
  answerFeedback: '/v1/answers/feedback',
  evaluations: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/evaluations`,
  evaluationTemplate: (workspaceId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/evaluation-template`,
  evaluation: (workspaceId: string, jobId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/evaluations/${encodeURIComponent(jobId)}`,
  evaluationRows: (workspaceId: string, jobId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/evaluations/${encodeURIComponent(jobId)}/rows`,
  evaluationScore: (workspaceId: string, jobId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/evaluations/${encodeURIComponent(jobId)}/score`,
  evaluationDisplayMetrics: (workspaceId: string, jobId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/evaluations/${encodeURIComponent(jobId)}/display-metrics`,
  evaluationExport: (workspaceId: string, jobId: string) => `/v1/workspaces/${encodeURIComponent(workspaceId)}/evaluations/${encodeURIComponent(jobId)}/export`,
} as const

const encoder = new TextEncoder()
let csrfToken = ''

function bytesFromBody(body: BodyInit | null | undefined): Uint8Array {
  if (!body) return new Uint8Array()
  if (typeof body === 'string') return encoder.encode(body)
  if (body instanceof Uint8Array) return body
  if (body instanceof ArrayBuffer) return new Uint8Array(body)
  throw new Error('API request body phải là JSON hoặc bytes trước khi ký')
}

function toHex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function hashRequest(
  method: string,
  url: URL,
  timestamp: string,
  nonce: string,
  body: Uint8Array,
): Promise<string> {
  const canonical = encoder.encode(
    `${method.toUpperCase()}\n${url.pathname}\n${url.search.slice(1)}\n${timestamp}\n${nonce}\n`,
  )
  const payload = new Uint8Array(canonical.length + body.length)
  payload.set(canonical)
  payload.set(body, canonical.length)
  return toHex(await crypto.subtle.digest('SHA-256', payload))
}

function newNonce(): string {
  if (crypto.randomUUID) return crypto.randomUUID()
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

async function signedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const url = new URL(withAppBasePath(path), API_BASE_URL)
  // The reverse proxy strips the public `/docrag` prefix before the API
  // verifies the request envelope, so sign the backend-visible path.
  const signingUrl = new URL(path, API_BASE_URL)
  const method = (init.method || 'GET').toUpperCase()
  const body = bytesFromBody(init.body)
  const timestamp = Date.now().toString()
  const nonce = newNonce()
  const requestHash = await hashRequest(method, signingUrl, timestamp, nonce, body)
  const response = await fetch(url, {
    ...init,
    method,
    body: init.body,
    credentials: 'include',
    headers: {
      ...(init.headers || {}),
      'X-Request-Hash': requestHash,
      'X-Request-Timestamp': timestamp,
      'X-Request-Nonce': nonce,
      ...(csrfToken && !['GET', 'HEAD', 'OPTIONS'].includes(method)
        ? { 'X-CSRF-Token': csrfToken }
        : {}),
    },
  })
  if (!response.ok) {
    const responseBody = await response.json().catch(() => ({})) as { detail?: string | { message?: string } }
    if (typeof responseBody.detail === 'object') {
      throw new Error(responseBody.detail?.message || `Backend HTTP ${response.status}`)
    }
    throw new Error(responseBody.detail || `Backend trả về HTTP ${response.status}`)
  }
  return response
}

async function signedRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  return await (await signedFetch(path, init)).json() as T
}

function toDocumentRow(record: DocumentApiRecord): DocumentRow {
  return {
    id: record.id,
    name: record.name,
    workspace: record.workspace,
    page: record.page,
    status: record.status,
    jobId: record.job_id || null,
    uploadedAt: record.uploaded_at,
    sourcePath: record.source_path,
    sizeBytes: record.size_bytes,
    contentType: record.content_type,
    editable: record.editable,
  }
}

export function getUiBootstrap(): Promise<UiBootstrap> {
  return signedRequest<UiBootstrap>(API_ROUTES.bootstrap)
}

export async function getAuthSession(): Promise<AuthSession> {
  const session = await signedRequest<AuthSession>(API_ROUTES.session)
  csrfToken = session.csrf_token || ''
  return session
}

export async function signUp(payload: {
  display_name: string
  email: string
  password: string
  accept_terms: boolean
}): Promise<{ authenticated: true; user: AuthUser; csrf_token: string }> {
  const result = await signedRequest<{ authenticated: true; user: AuthUser; csrf_token: string }>(API_ROUTES.signUp, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  csrfToken = result.csrf_token
  return result
}

export async function signIn(payload: {
  email: string
  password: string
  remember: boolean
}): Promise<{ authenticated: true; user: AuthUser; csrf_token: string }> {
  const result = await signedRequest<{ authenticated: true; user: AuthUser; csrf_token: string }>(API_ROUTES.signIn, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  csrfToken = result.csrf_token
  return result
}

export async function signOut(): Promise<{ signed_out: boolean }> {
  const result = await signedRequest<{ signed_out: boolean }>(API_ROUTES.signOut, { method: 'POST' })
  csrfToken = ''
  return result
}

export function requestPasswordReset(email: string): Promise<{ message: string }> {
  return signedRequest(API_ROUTES.forgotPassword, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
}

export function resetPassword(token: string, newPassword: string): Promise<{ reset: boolean }> {
  return signedRequest(API_ROUTES.resetPassword, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, new_password: newPassword }),
  })
}

export function setLanguagePreference(locale: string): Promise<{ locale: string }> {
  return signedRequest(API_ROUTES.language, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ locale }),
  })
}

export function getAdminOverview(): Promise<AdminOverview> {
  return signedRequest<AdminOverview>(API_ROUTES.adminOverview)
}

export function getAdminUsers(): Promise<AdminUserRecord[]> {
  return signedRequest<AdminUserRecord[]>(API_ROUTES.adminUsers)
}

export function getAdminWorkspaces(): Promise<AdminWorkspaceRecord[]> {
  return signedRequest<AdminWorkspaceRecord[]>(API_ROUTES.adminWorkspaces)
}

export function getAdminFeedbackSummary(): Promise<AdminFeedbackSummary> {
  return signedRequest<AdminFeedbackSummary>(API_ROUTES.adminFeedbackSummary)
}

export function getAdminFeedback(): Promise<AdminFeedbackRecord[]> {
  return signedRequest<AdminFeedbackRecord[]>(API_ROUTES.adminFeedback)
}

export function triageAdminFeedback(
  feedbackId: string,
  payload: { status: AdminFeedbackRecord['triage_status']; admin_note?: string | null; expected_version: number },
): Promise<AdminFeedbackRecord> {
  return signedRequest<AdminFeedbackRecord>(API_ROUTES.adminFeedbackItem(feedbackId), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function updateAdminUser(
  userId: string,
  payload: { role?: 'admin' | 'member'; is_active?: boolean },
): Promise<AdminUserRecord> {
  return signedRequest<AdminUserRecord>(API_ROUTES.adminUser(userId), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function setAdminWorkspaceRole(
  userId: string,
  workspaceId: string,
  role: 'owner' | 'editor' | 'viewer',
): Promise<{ user_id: string; workspace_id: string; role: string }> {
  return signedRequest(API_ROUTES.adminWorkspaceRole(userId, workspaceId), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  })
}

export function revokeAdminWorkspaceRole(
  userId: string,
  workspaceId: string,
): Promise<{ revoked: boolean }> {
  return signedRequest(API_ROUTES.adminWorkspaceRole(userId, workspaceId), {
    method: 'DELETE',
  })
}
export function getWorkspaces(query = ''): Promise<WorkspaceRecord[]> {
  const search = new URLSearchParams()
  if (query.trim()) search.set('query', query.trim())
  const suffix = search.size ? `?${search}` : ''
  return signedRequest<WorkspaceRecord[]>(`${API_ROUTES.workspaces}${suffix}`)
}

export function createWorkspace(name: string): Promise<WorkspaceRecord> {
  return signedRequest<WorkspaceRecord>(API_ROUTES.workspaces, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

export function deleteWorkspace(
  workspaceId: string,
  confirmation: string,
): Promise<{ workspace_id: string; deleted: true }> {
  return signedRequest(API_ROUTES.workspace(workspaceId), {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirmation }),
  })
}

export type IngestionJobSummary = {
  job_id: string
  project_id: string
  document_id: string
  status: string
  started_at: string | null
  finished_at: string | null
  error?: Record<string, unknown> | null
}

export function getIngestionJob(jobId: string): Promise<IngestionJobSummary> {
  return signedRequest<IngestionJobSummary>(API_ROUTES.ingestionJob(jobId))
}

export type GetDocumentsOptions = {
  limit?: number
  offset?: number
}

export async function getDocuments(
  workspaceId: string,
  queryText = '',
  options: GetDocumentsOptions = {},
): Promise<DocumentRow[]> {
  const limit = Math.max(1, Math.min(options.limit ?? 200, 200))
  const offset = Math.max(0, options.offset ?? 0)
  const query = new URLSearchParams({
    workspace_id: workspaceId,
    limit: String(limit),
    offset: String(offset),
  })
  if (queryText.trim()) query.set('query', queryText.trim())
  const page = await signedRequest<DocumentApiRecord[]>(`${API_ROUTES.documents}?${query}`)
  return page.map(toDocumentRow)
}
export type ImportDocumentsOptions = {
  preserveRelativePath?: boolean
  targetRelativePaths?: Record<string, string>
  replaceDocumentIds?: Record<string, string>
}

export type ImportDocumentsResult = {
  accepted: DocumentRow[]
  errors: string[]
  notices: UploadNotice[]
}

export async function importDocuments(
  workspaceId: string,
  files: File[],
  options: ImportDocumentsOptions = {},
): Promise<ImportDocumentsResult> {
  const accepted: DocumentRow[] = []
  const errors: string[] = []
  const notices: UploadNotice[] = []
  for (const file of files) {
    try {
      const relativePath = (options.preserveRelativePath
        ? (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
        : file.name).replace(/\\/g, '/')
      const query = new URLSearchParams({
        filename: file.name,
        content_type: file.type || 'application/octet-stream',
      })
      const targetRelativePath = options.targetRelativePaths?.[relativePath]
      if (targetRelativePath || options.preserveRelativePath) {
        query.set('relative_path', targetRelativePath || relativePath)
      }
      const replacementId = options.replaceDocumentIds?.[relativePath]
      if (replacementId) query.set('replace_document_id', replacementId)
      const bytes = new Uint8Array(await file.arrayBuffer())
      const record = await signedRequest<DocumentApiRecord>(
        `${API_ROUTES.uploadDocument(workspaceId)}?${query}`,
        {
          method: 'POST',
          headers: { 'Content-Type': file.type || 'application/octet-stream' },
          body: bytes,
        },
      )
      const document = toDocumentRow(record)
      accepted.push(document)
      notices.push({
        document,
        action: record.upload_action || (replacementId ? 'updated' : 'created'),
        contentChanged: Boolean(record.content_changed),
        replacedDocumentId: record.replaced_document_id || replacementId || null,
      })
    } catch (reason) {
      errors.push(`${file.name}: ${reason instanceof Error ? reason.message : String(reason)}`)
    }
  }
  return { accepted, errors, notices }
}

export async function getWorkspaceOverview(workspaceId: string): Promise<WorkspaceOverview> {
  return signedRequest<WorkspaceOverview>(API_ROUTES.workspaceOverview(workspaceId))
}

export function getWorkspaceGraph(workspaceId: string): Promise<KnowledgeGraph> {
  const query = new URLSearchParams({ node_limit: '120', edge_limit: '240', include_attributes: 'true' })
  return signedRequest<KnowledgeGraph>(`${API_ROUTES.workspaceGraph(workspaceId)}?${query}`)
}

export async function downloadWorkspaceGraphml(workspaceId: string): Promise<void> {
  const response = await signedFetch(API_ROUTES.workspaceGraphml(workspaceId))
  const objectUrl = URL.createObjectURL(await response.blob())
  const anchor = window.document.createElement('a')
  anchor.href = objectUrl
  anchor.download = `${workspaceId}-graph.graphml`
  anchor.click()
  URL.revokeObjectURL(objectUrl)
}

export async function getWorkspaceMembers(workspaceId: string): Promise<WorkspaceMember[]> {
  const response = await signedRequest<{ members: WorkspaceMember[] }>(API_ROUTES.workspaceMembers(workspaceId))
  return response.members
}

export function getWorkspaceSettings(workspaceId: string): Promise<WorkspaceSettings> {
  return signedRequest<WorkspaceSettings>(API_ROUTES.workspaceSettings(workspaceId))
}

export async function renameDocument(
  workspaceId: string,
  documentId: string,
  name: string,
): Promise<DocumentRow> {
  const record = await signedRequest<DocumentApiRecord>(API_ROUTES.document(documentId), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace_id: workspaceId, name }),
  })
  return toDocumentRow(record)
}

export type SyncDocumentsResponse = {
  workspace_id: string
  accepted: string[]
  skipped: {
    unchanged: string[]
    already_active: string[]
  }
  jobs: Array<{
    job_id: string
    status: string
    document_id: string
    status_url: string
    files_url: string
  }>
}

export function syncDocuments(
  workspaceId: string,
  documentIds: string[],
): Promise<SyncDocumentsResponse> {
  return signedRequest(API_ROUTES.syncDocuments, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace_id: workspaceId, document_ids: documentIds }),
  })
}

export function deleteDocuments(
  workspaceId: string,
  documentIds: string[],
): Promise<{ workspace_id: string; deleted: string[] }> {
  return signedRequest(API_ROUTES.deleteDocuments, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ workspace_id: workspaceId, document_ids: documentIds }),
  })
}
export async function moveDocuments(
  workspaceId: string,
  destinationWorkspaceId: string,
  documentIds: string[],
): Promise<DocumentRow[]> {
  const records = await signedRequest<DocumentApiRecord[]>(API_ROUTES.moveDocuments, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      workspace_id: workspaceId,
      destination_workspace_id: destinationWorkspaceId,
      document_ids: documentIds,
    }),
  })
  return records.map(toDocumentRow)
}

export async function downloadDocument(workspaceId: string, document: DocumentRow): Promise<void> {
  const query = new URLSearchParams({ workspace_id: workspaceId })
  const response = await signedFetch(`${API_ROUTES.documentContent(document.id)}?${query}`)
  const objectUrl = URL.createObjectURL(await response.blob())
  const anchor = window.document.createElement('a')
  anchor.href = objectUrl
  anchor.download = document.name
  anchor.click()
  URL.revokeObjectURL(objectUrl)
}

export function getDocumentPreview(workspaceId: string, documentId: string): Promise<DocumentPreview> {
  const query = new URLSearchParams({ workspace_id: workspaceId })
  return signedRequest<DocumentPreview>(`${API_ROUTES.documentPreview(documentId)}?${query}`)
}

export function getDocumentMetadata(workspaceId: string, documentId: string): Promise<DocumentMetadata> {
  return signedRequest<DocumentMetadata>(API_ROUTES.documentMetadata(workspaceId, documentId))
}

type RawQueryResponse = {
  answer_id?: string
  answer?: string | null
  citations?: Citation[]
  confidence?: QueryConfidence
  chat_session?: ChatSessionSummary
  retrieval?: { references?: Citation[] }
}

type QueryOptions = {
  filePaths?: string[]
  conversationHistory?: Array<{ role: 'user' | 'assistant'; content: string }>
  chatSessionId?: string | null
  saveHistory?: boolean
  locale?: string
}

export async function askKnowledgeBase(
  question: string,
  workspaceId: string,
  options: QueryOptions = {},
): Promise<QueryResponse> {
  const response = await signedRequest<RawQueryResponse>(API_ROUTES.query, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      workspace_id: workspaceId,
      question,
      file_paths: options.filePaths || [],
      conversation_history: options.conversationHistory || [],
      chat_session_id: options.chatSessionId || null,
      save_history: options.saveHistory ?? true,
      locale: (options.locale || i18n.resolvedLanguage || 'vi').split('-')[0],
    }),
  })
  return {
    answer_id: response.answer_id || `legacy-${Date.now()}`,
    answer: response.answer || 'Backend chưa trả về nội dung câu trả lời.',
    citations: response.citations || response.retrieval?.references || [],
    confidence: response.confidence,
    chatSession: response.chat_session,
  }
}

export function getDocumentReview(workspaceId: string, documentId: string): Promise<DocumentReview> {
  const query = new URLSearchParams({ workspace_id: workspaceId })
  return signedRequest<DocumentReview>(`${API_ROUTES.documentReview(documentId)}?${query}`)
}

export async function fetchDocumentContent(workspaceId: string, documentId: string): Promise<Blob> {
  const query = new URLSearchParams({ workspace_id: workspaceId })
  return await (await signedFetch(`${API_ROUTES.documentContent(documentId)}?${query}`)).blob()
}

export async function streamKnowledgeBase(
  question: string,
  workspaceId: string,
  options: QueryOptions = {},
  onEvent: (event: AgentStreamEvent) => void = () => undefined,
): Promise<QueryResponse> {
  const response = await signedFetch(API_ROUTES.chatStream, {
    method: 'POST',
    headers: {
      'Accept': 'text/event-stream',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      workspace_id: workspaceId,
      question,
      file_paths: options.filePaths || [],
      conversation_history: options.conversationHistory || [],
      chat_session_id: options.chatSessionId || null,
      save_history: options.saveHistory ?? true,
    }),
  })
  if (!response.body) throw new Error('Backend không trả về luồng SSE')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let streamErrorMessage: string | null = null
  let result: QueryResponse = {
    answer_id: `stream-${Date.now()}`,
    answer: '',
    citations: [],
  }

  const consumeFrame = (frame: string) => {
    const data = frame
      .split(/\r?\n/)
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trim())
      .filter(Boolean)
    for (const rawPayload of data) {
      const event = JSON.parse(rawPayload) as AgentStreamEvent
      onEvent(event)
      if (event.event === 'error') {
        streamErrorMessage = event.message
        continue
      }
      if (event.event !== 'message_chunk' && event.event !== 'final_response') continue
      result = {
        answer_id: event.answer_id || result.answer_id,
        answer: result.answer + (event.event === 'message_chunk' ? event.content : ''),
        citations: event.citations || result.citations,
        confidence: event.confidence || result.confidence,
        chatSession: event.chat_session || result.chatSession,
      }
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const frames = buffer.split(/\r?\n\r?\n/)
      buffer = frames.pop() || ''
      frames.forEach(consumeFrame)
    }
    buffer += decoder.decode()
    if (buffer.trim()) consumeFrame(buffer)
  } finally {
    reader.releaseLock()
  }

  if (streamErrorMessage) throw new Error(streamErrorMessage)
  if (!result.answer) result.answer = 'Backend chưa trả về nội dung câu trả lời.'
  return result
}

export type AssistantToolExecutionPayload = {
  force_tool: AssistantToolName
  message?: string | null
  arguments?: Record<string, unknown>
  filters?: Record<string, unknown>
  chat_session_id?: string | null
  save_history?: boolean
}

export function getAssistantToolCatalog(workspaceId: string): Promise<AssistantToolCatalog> {
  return signedRequest<AssistantToolCatalog>(API_ROUTES.assistantTools(workspaceId))
}

export function executeAssistantTool(
  workspaceId: string,
  payload: AssistantToolExecutionPayload,
): Promise<AssistantToolExecution> {
  return signedRequest<AssistantToolExecution>(API_ROUTES.assistantToolExecutions(workspaceId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      force_tool: payload.force_tool,
      message: payload.message ?? null,
      arguments: payload.arguments || {},
      filters: payload.filters || {},
      chat_session_id: payload.chat_session_id || null,
      save_history: payload.save_history ?? true,
    }),
  })
}

export function getIngestionStatus(
  workspaceId: string,
  target: { documentId?: string; jobId?: string },
): Promise<IngestionStatus> {
  const query = new URLSearchParams({ workspace_id: workspaceId })
  if (target.documentId) query.set('document_id', target.documentId)
  if (target.jobId) query.set('job_id', target.jobId)
  return signedRequest<IngestionStatus>(`${API_ROUTES.ingestionStatus}?${query}`)
}

export function getIngestionEvents(
  workspaceId: string,
  jobId: string,
  after = 0,
): Promise<IngestionFlowEventsResponse> {
  return signedRequest<IngestionFlowEventsResponse>(API_ROUTES.ingestionEvents(jobId, workspaceId, after))
}

export function submitAnswerFeedback(
  projectId: string,
  answerId: string,
  rating: AnswerFeedbackRating,
): Promise<{ feedback_id: string; answer_id: string; created: boolean }> {
  return signedRequest(API_ROUTES.answerFeedback, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project_id: projectId,
      answer_id: answerId,
      rating,
      idempotency_key: `${answerId}:${rating}`,
    }),
  })
}

export async function createEvaluationJob(
  workspaceId: string,
  file: File,
  defaultLocale = 'vi',
): Promise<EvaluationJobSummary> {
  const query = new URLSearchParams({
    filename: file.name,
    default_locale: defaultLocale.split('-')[0],
  })
  const bytes = new Uint8Array(await file.arrayBuffer())
  return signedRequest<EvaluationJobSummary>(`${API_ROUTES.evaluations(workspaceId)}?${query}`, {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: bytes,
  })
}

export async function downloadEvaluationTemplate(workspaceId: string): Promise<void> {
  const response = await signedFetch(API_ROUTES.evaluationTemplate(workspaceId))
  const blob = await response.blob()
  const signature = new Uint8Array(await blob.slice(0, 2).arrayBuffer())
  if (!blob.size || signature[0] !== 0x50 || signature[1] !== 0x4b) {
    throw new Error('Backend không trả về file XLSX mẫu hợp lệ')
  }
  const objectUrl = URL.createObjectURL(blob)
  const anchor = window.document.createElement('a')
  anchor.href = objectUrl
  anchor.download = 'mau-cham-diem-cau-tra-loi.xlsx'
  anchor.style.display = 'none'
  window.document.body.appendChild(anchor)
  anchor.click()
  window.setTimeout(() => {
    URL.revokeObjectURL(objectUrl)
    anchor.remove()
  }, 1000)
}

export function getEvaluationJob(workspaceId: string, jobId: string): Promise<EvaluationJobSummary> {
  return signedRequest<EvaluationJobSummary>(API_ROUTES.evaluation(workspaceId, jobId))
}

export function getEvaluationRows(
  workspaceId: string,
  jobId: string,
  offset = 0,
  limit = 50,
): Promise<EvaluationRowsResponse> {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  return signedRequest<EvaluationRowsResponse>(`${API_ROUTES.evaluationRows(workspaceId, jobId)}?${query}`)
}

export function scoreEvaluationJob(
  workspaceId: string,
  jobId: string,
  displayMetrics: EvaluationMetric[],
  rowIds: string[] = [],
  failedOnly = false,
): Promise<EvaluationJobSummary> {
  return signedRequest<EvaluationJobSummary>(API_ROUTES.evaluationScore(workspaceId, jobId), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_metrics: displayMetrics, row_ids: rowIds, failed_only: failedOnly }),
  })
}

export function updateEvaluationDisplayMetrics(
  workspaceId: string,
  jobId: string,
  displayMetrics: EvaluationMetric[],
): Promise<EvaluationJobSummary> {
  return signedRequest<EvaluationJobSummary>(API_ROUTES.evaluationDisplayMetrics(workspaceId, jobId), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ display_metrics: displayMetrics }),
  })
}

export async function downloadEvaluationExport(
  workspaceId: string,
  jobId: string,
  format: 'xlsx' | 'csv',
): Promise<void> {
  const query = new URLSearchParams({ format })
  const response = await signedFetch(`${API_ROUTES.evaluationExport(workspaceId, jobId)}?${query}`)
  const blob = await response.blob()
  if (!blob.size) {
    throw new Error('Backend trả về file evaluation rỗng')
  }
  if (format === 'csv') {
    const signature = new Uint8Array(await blob.slice(0, 2).arrayBuffer())
    if (signature[0] !== 0x50 || signature[1] !== 0x4b) {
      throw new Error('Backend không trả về ZIP CSV hợp lệ')
    }
  }
  const objectUrl = URL.createObjectURL(blob)
  const anchor = window.document.createElement('a')
  anchor.href = objectUrl
  anchor.download = `${jobId}.${format === 'xlsx' ? 'xlsx' : 'zip'}`
  anchor.style.display = 'none'
  window.document.body.appendChild(anchor)
  anchor.click()
  window.setTimeout(() => {
    URL.revokeObjectURL(objectUrl)
    anchor.remove()
  }, 1000)
}

type RawChatSessionsResponse = {
  workspace_id: string
  sessions: ChatSessionSummary[]
}

export async function getChatSessions(workspaceId: string, limit = 30): Promise<ChatSessionSummary[]> {
  const query = new URLSearchParams({ limit: String(limit) })
  const response = await signedRequest<RawChatSessionsResponse>(`${API_ROUTES.chatSessions(workspaceId)}?${query}`)
  return response.sessions
}

export function getChatSession(workspaceId: string, sessionId: string): Promise<ChatSessionDetail> {
  return signedRequest<ChatSessionDetail>(API_ROUTES.chatSession(workspaceId, sessionId))
}

export { API_BASE_URL }
export function getMcpCredentials(): Promise<McpCredential[]> {
  return signedRequest<McpCredential[]>(API_ROUTES.mcpCredentials)
}

export function issueMcpCredential(payload: {
  name: string
  ttl_days: number
  workspace_id: string
}): Promise<IssuedMcpCredential> {
  return signedRequest<IssuedMcpCredential>(API_ROUTES.mcpCredentials, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function issueAdminMcpCredential(payload: {
  user_id: string
  name: string
  ttl_days: number
  workspace_id: string
}): Promise<IssuedMcpCredential> {
  return signedRequest<IssuedMcpCredential>(API_ROUTES.adminMcpCredentials, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function revokeMcpCredential(credentialId: string): Promise<{ revoked: boolean }> {
  return signedRequest<{ revoked: boolean }>(API_ROUTES.mcpCredential(credentialId), {
    method: 'DELETE',
  })
}
