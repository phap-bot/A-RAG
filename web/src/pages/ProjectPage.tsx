import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeft,
  ArrowRightLeft,
  Bot,
  CloudUpload,
  Download,
  FileText,
  FolderPlus,
  Gauge,
  History,
  MessageSquarePlus,
  House,
  Paperclip,
  Pencil,
  RefreshCw,
  PlugZap,
  Share2,
  Search,
  Send,
  Settings,
  Trash2,
  ThumbsDown,
  ThumbsUp,
  ShieldCheck,
  Upload,
  Users,
  X,
} from 'lucide-react'

import {
  askKnowledgeBase,
  deleteDocuments,
  downloadDocument,
  executeAssistantTool,
  getChatSession,
  getChatSessions,
  getAssistantToolCatalog,
  getDocuments,
  getIngestionStatus,
  getWorkspaceGraph,
  getWorkspaceMembers,
  getWorkspaceOverview,
  getWorkspaceSettings,

  importDocuments,
  moveDocuments,
  renameDocument,
  submitAnswerFeedback,
  syncDocuments,
  downloadWorkspaceGraphml,
} from '../api'
import { CitationEvidenceList } from '../components/CitationEvidenceList'
import { DocumentTable } from '../components/DocumentTable'
import { KnowledgeGraphPanel } from '../components/KnowledgeGraphPanel'
import { AssistantToolComposer } from '../components/assistant/AssistantToolComposer'
import { ToolResultRenderer } from '../components/assistant/ToolResultRenderer'
import { Button } from '../components/ui/Button'
import { Select } from '../components/ui/Select'
import { AgentConnectionsPage } from './AgentConnectionsPage'
import { EvaluationPage } from './EvaluationPage'
import { readSessionState, sessionStorageKey, writeSessionState } from '../sessionState'
import type {
  ChatSessionSummary,
  DocumentRow,
  AssistantToolDefinition,
  AssistantToolExecution,
  AssistantToolName,
  QueryResponse,
  UiBootstrap,
  WorkspaceMember,
  WorkspaceOverview,
  KnowledgeGraph,
  WorkspaceRecord,
  WorkspaceSection,
  WorkspaceSettings,
  UploadNotice,
} from '../types'

type ProjectPageProps = {
  bootstrap: UiBootstrap
  workspace: WorkspaceRecord
  workspaces: WorkspaceRecord[]
  documents: DocumentRow[]
  initialSection?: WorkspaceSection
  onSectionChange?: (section: WorkspaceSection) => void
  onDocumentsChange: (documents: DocumentRow[]) => void
  onBack: () => void
  onAnalyze: (document: DocumentRow) => void
}

const localeMap: Record<string, string> = { en: 'en-US', ja: 'ja-JP', vi: 'vi-VN' }
const MAX_FOLDER_UPLOAD_FILES = 300
const FOLDER_UPLOAD_ALLOWED_SUFFIXES = new Set([
  '.csv',
  '.docx',
  '.htm',
  '.html',
  '.jpeg',
  '.jpg',
  '.json',
  '.jsonl',
  '.md',
  '.pdf',
  '.png',
  '.pptx',
  '.txt',
  '.webp',
  '.xlsx',
  '.xml',
  '.yaml',
  '.yml',
])
const FOLDER_UPLOAD_EXCLUDED_PARTS = new Set([
  '.cache',
  '.git',
  '.kb',
  '.next',
  '.runtime',
  '.turbo',
  '.venv',
  '__pycache__',
  '__tests__',
  'build',
  'coverage',
  'dist',
  'node_modules',
  'playwright-report',
  'test-results',
])

type ChatAttachment = Pick<DocumentRow, 'id' | 'name' | 'sourcePath'>

type UploadFileOptions = {
  preserveRelativePath?: boolean
  targetRelativePaths?: Record<string, string>
}

type UploadCandidate = {
  key: string
  file: File
  relativePath: string
  candidates: DocumentRow[]
  selection: string | '__new__' | null
}

type PendingUpload = {
  files: File[]
  options: UploadFileOptions
  candidates: UploadCandidate[]
}

type UploadDiffNotice = {
  updated: UploadNotice[]
}

type ProjectSessionState = {
  search: string
  selectedIds: string[]
  question: string
  activeChatSessionId: string | null
  attachedDocumentIds: string[]
  historyOpen: boolean
  selectedToolName: string | null
  feedbackByAnswerId: Record<string, 'positive' | 'negative'>
}

function folderRelativePath(file: File): string {
  return ((file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name).replace(/\\/g, '/')
}

function suffixForUploadPath(path: string): string {
  const name = path.split('/').filter(Boolean).pop() || path
  const dotIndex = name.lastIndexOf('.')
  return dotIndex >= 0 ? name.slice(dotIndex).toLowerCase() : name.toLowerCase()
}

function shouldUploadFolderFile(file: File): boolean {
  const relativePath = folderRelativePath(file)
  const parts = relativePath.split('/').filter(Boolean)
  if (parts.some((part) => FOLDER_UPLOAD_EXCLUDED_PARTS.has(part.toLowerCase()))) return false
  if (parts.some((part) => part.startsWith('.') && part.toLowerCase() !== '.env.example')) return false
  return FOLDER_UPLOAD_ALLOWED_SUFFIXES.has(suffixForUploadPath(relativePath))
}

type ChatTurn = {
  id: string
  question: string
  attachments: ChatAttachment[]
  answer: QueryResponse | null
  toolExecution: AssistantToolExecution | null
  error: string | null
  pending: boolean
  feedback: 'positive' | 'negative' | null
}

function inlineAnswerText(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((part, index) => (
    part.startsWith('**') && part.endsWith('**')
      ? <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>
      : part
  ))
}

function AssistantAnswerText({ text }: { text: string }) {
  return (
    <div className="assistant-answer-copy">
      {text.split(/\r?\n/).map((line, index) => {
        const trimmed = line.trim()
        if (!trimmed) return <span className="assistant-answer-spacer" aria-hidden="true" key={`space-${index}`} />
        const heading = trimmed.match(/^#{1,3}\s+(.+)$/)
        if (heading) return <h3 key={`heading-${index}`}>{inlineAnswerText(heading[1])}</h3>
        const bullet = trimmed.match(/^[-*]\s+(.+)$/)
        if (bullet) return <div className="assistant-answer-list-item" key={`bullet-${index}`}><i aria-hidden="true" /> <p>{inlineAnswerText(bullet[1])}</p></div>
        const numbered = trimmed.match(/^(\d+)\.\s+(.+)$/)
        if (numbered) return <div className="assistant-answer-list-item" key={`number-${index}`}><b>{numbered[1]}</b><p>{inlineAnswerText(numbered[2])}</p></div>
        return <p key={`paragraph-${index}`}>{inlineAnswerText(trimmed)}</p>
      })}
    </div>
  )
}

export function ProjectPage({
  bootstrap,
  workspace,
  workspaces,
  documents,
  initialSection = 'documents',
  onSectionChange,
  onDocumentsChange,
  onBack,
  onAnalyze,
}: ProjectPageProps) {
  const { t, i18n } = useTranslation()
  const [section, setSection] = useState<WorkspaceSection>(initialSection)
  const [search, setSearch] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [overview, setOverview] = useState<WorkspaceOverview | null>(null)
  const [members, setMembers] = useState<WorkspaceMember[]>([])
  const [workspaceSettings, setWorkspaceSettings] = useState<WorkspaceSettings | null>(null)
  const [knowledgeGraph, setKnowledgeGraph] = useState<KnowledgeGraph | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [syncNotice, setSyncNotice] = useState<string | null>(null)
  const [pendingDeleteIds, setPendingDeleteIds] = useState<string[]>([])
  const [question, setQuestion] = useState('')
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([])
  const [chatSessions, setChatSessions] = useState<ChatSessionSummary[]>([])
  const [activeChatSessionId, setActiveChatSessionId] = useState<string | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [feedbackByAnswerId, setFeedbackByAnswerId] = useState<Record<string, 'positive' | 'negative'>>({})
  const [asking, setAsking] = useState(false)
  const [railCollapsed, setRailCollapsed] = useState(false)
  const [assistantTools, setAssistantTools] = useState<AssistantToolDefinition[]>([])
  const [selectedTool, setSelectedTool] = useState<AssistantToolDefinition | null>(null)
  const [readiness, setReadiness] = useState<import('../types').IngestionStatus | null>(null)
  const [attachmentPickerOpen, setAttachmentPickerOpen] = useState(false)
  const [attachedDocumentIds, setAttachedDocumentIds] = useState<string[]>([])
  const [pendingUpload, setPendingUpload] = useState<PendingUpload | null>(null)
  const [uploadDiffNotice, setUploadDiffNotice] = useState<UploadDiffNotice | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const deleteCancelRef = useRef<HTMLButtonElement>(null)
  const conversationRef = useRef<HTMLDivElement>(null)
  const currentWorkspaceIdRef = useRef(workspace.workspace_id)
  const workspaceRequestEpochRef = useRef(0)
  const sessionHydrationPendingRef = useRef(false)
  if (currentWorkspaceIdRef.current !== workspace.workspace_id) {
    currentWorkspaceIdRef.current = workspace.workspace_id
    workspaceRequestEpochRef.current += 1
  }
  const locale = localeMap[(i18n.resolvedLanguage || 'vi').split('-')[0]] || 'vi-VN'
  const projectSessionKey = sessionStorageKey('project', `${bootstrap.session.email || bootstrap.session.display_name}:${workspace.workspace_id}`)
  const attachedDocuments = useMemo(
    () => documents.filter((document) => attachedDocumentIds.includes(document.id)),
    [attachedDocumentIds, documents],
  )

  const attachmentFromPath = useCallback((sourcePath: string): ChatAttachment => {
    const match = documents.find((document) => document.sourcePath === sourcePath)
    if (match) return match
    const pathSegments = sourcePath.split(/[\\/]/).filter(Boolean)
    const name = pathSegments[pathSegments.length - 1] || sourcePath
    return { id: sourcePath, name, sourcePath }
  }, [documents])

  const refreshChatSessions = useCallback(async () => {
    const workspaceId = workspace.workspace_id
    const requestEpoch = workspaceRequestEpochRef.current
    const sessions = await getChatSessions(workspaceId)
    if (
      currentWorkspaceIdRef.current !== workspaceId
      || workspaceRequestEpochRef.current !== requestEpoch
    ) return []
    setChatSessions(sessions)
    return sessions
  }, [workspace.workspace_id])

  useEffect(() => {
    setSection(initialSection)
  }, [initialSection, workspace.workspace_id])

  useEffect(() => {
    if (initialSection !== 'graph') return
    let cancelled = false
    setBusy(true)
    setError(null)
    getWorkspaceGraph(workspace.workspace_id)
      .then((graph) => {
        if (!cancelled) setKnowledgeGraph(graph)
      })
      .catch((reason) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Không thể tải NetworkX graph')
      })
      .finally(() => {
        if (!cancelled) setBusy(false)
      })
    return () => {
      cancelled = true
    }
  }, [initialSection, workspace.workspace_id])

  useEffect(() => {
    const saved = readSessionState<ProjectSessionState>(projectSessionKey)
    sessionHydrationPendingRef.current = true
    setSelectedIds(new Set())
    setSearch('')
    setQuestion('')
    setSelectedTool(null)
    setChatTurns([])
    setChatSessions([])
    setActiveChatSessionId(null)
    setAsking(false)
    setHistoryLoading(false)
    setAttachmentPickerOpen(false)
    setAttachedDocumentIds([])
    setPendingUpload(null)
    setUploadDiffNotice(null)
    setSyncNotice(null)
    setKnowledgeGraph(null)
    setSearch(saved?.search || '')
    setSelectedIds(new Set(saved?.selectedIds || []))
    setQuestion(saved?.question || '')
    setActiveChatSessionId(saved?.activeChatSessionId || null)
    setAttachedDocumentIds(saved?.attachedDocumentIds || [])
    setHistoryOpen(Boolean(saved?.historyOpen))
    setFeedbackByAnswerId(saved?.feedbackByAnswerId || {})
    getWorkspaceSettings(workspace.workspace_id).then(setWorkspaceSettings).catch(() => undefined)
    getAssistantToolCatalog(workspace.workspace_id)
      .then((catalog) => {
        setAssistantTools(catalog.composer_tools)
        const restoredTool = saved?.selectedToolName
          ? catalog.composer_tools.find((tool) => tool.name === saved.selectedToolName) || null
          : null
        setSelectedTool(restoredTool)
        sessionHydrationPendingRef.current = false
      })
      .catch(() => {
        setAssistantTools([])
        sessionHydrationPendingRef.current = false
      })
  }, [projectSessionKey, workspace.workspace_id])

  useEffect(() => {
    if (sessionHydrationPendingRef.current) {
      return
    }
    writeSessionState<ProjectSessionState>(projectSessionKey, {
      search,
      selectedIds: Array.from(selectedIds),
      question,
      activeChatSessionId,
      attachedDocumentIds,
      historyOpen,
      selectedToolName: selectedTool?.name || null,
      feedbackByAnswerId,
    })
  }, [activeChatSessionId, attachedDocumentIds, feedbackByAnswerId, historyOpen, projectSessionKey, question, search, selectedIds, selectedTool?.name])

  useEffect(() => {
    const documentId = documents[0]?.id
    if (!documentId) {
      setReadiness(null)
      return
    }
    let cancelled = false
    getIngestionStatus(workspace.workspace_id, { documentId })
      .then((status) => { if (!cancelled) setReadiness(status) })
      .catch(() => { if (!cancelled) setReadiness(null) })
    return () => { cancelled = true }
  }, [documents, workspace.workspace_id])

  useEffect(() => {
    onSectionChange?.(section)
  }, [onSectionChange, section])

  useEffect(() => {
    let cancelled = false
    setHistoryLoading(true)
    getChatSessions(workspace.workspace_id)
      .then((sessions) => {
        if (cancelled) return
        setChatSessions(sessions)
        const saved = readSessionState<ProjectSessionState>(projectSessionKey)
        const sessionId = saved?.activeChatSessionId || sessions[0]?.id
        if (sessionId) void openChatSession(sessionId)
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setHistoryLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [projectSessionKey, workspace.workspace_id])

  useEffect(() => {
    setAttachedDocumentIds((current) => current.filter((documentId) => documents.some((document) => document.id === documentId)))
  }, [documents])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const conversation = conversationRef.current
      if (conversation) {
        conversation.scrollTo({ top: conversation.scrollHeight, behavior: 'smooth' })
      }
    })
    return () => window.cancelAnimationFrame(frame)
  }, [chatTurns, asking])

  useEffect(() => {
    if (!historyOpen) return
    function closeHistory(event: KeyboardEvent) {
      if (event.key === 'Escape') setHistoryOpen(false)
    }
    window.addEventListener('keydown', closeHistory)
    return () => window.removeEventListener('keydown', closeHistory)
  }, [historyOpen])

  useEffect(() => {
    const folderInput = folderInputRef.current
    if (!folderInput) return
    folderInput.setAttribute('webkitdirectory', '')
    folderInput.setAttribute('directory', '')
  }, [])

  useEffect(() => {
    if (!pendingDeleteIds.length) return
    const frame = window.requestAnimationFrame(() => deleteCancelRef.current?.focus())
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busy) setPendingDeleteIds([])
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [pendingDeleteIds.length, busy])

  async function refreshDocuments(): Promise<DocumentRow[]> {
    const refreshed = await getDocuments(workspace.workspace_id)
    onDocumentsChange(refreshed)
    return refreshed
  }

  async function loadSection(nextSection: WorkspaceSection) {
    setSection(nextSection)
    setBusy(true)
    setError(null)
    try {
      if (nextSection === 'documents') await refreshDocuments()
      if (nextSection === 'overview') setOverview(await getWorkspaceOverview(workspace.workspace_id))
      if (nextSection === 'members') setMembers(await getWorkspaceMembers(workspace.workspace_id))
      if (nextSection === 'settings') setWorkspaceSettings(await getWorkspaceSettings(workspace.workspace_id))
      if (nextSection === 'graph') setKnowledgeGraph(await getWorkspaceGraph(workspace.workspace_id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('project.loadError'))
    } finally {
      setBusy(false)
    }
  }

  async function refreshKnowledgeGraph() {
    setBusy(true)
    setError(null)
    try {
      setKnowledgeGraph(await getWorkspaceGraph(workspace.workspace_id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Không thể tải NetworkX graph')
    } finally {
      setBusy(false)
    }
  }

  async function searchDocuments(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onDocumentsChange(await getDocuments(workspace.workspace_id, search))
      setSelectedIds(new Set())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('project.searchError'))
    } finally {
      setBusy(false)
    }
  }

  function uploadRelativePath(file: File, options: UploadFileOptions): string {
    return options.preserveRelativePath
      ? (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name
      : file.name
  }

  function normalizedUploadPath(path: string): string {
    return path.replace(/\\/g, '/').replace(/^\/+/, '').toLocaleLowerCase()
  }

  function findUploadReplacements(files: File[], options: UploadFileOptions): PendingUpload | null {
    const candidates: UploadCandidate[] = []
    files.forEach((file, index) => {
      const relativePath = uploadRelativePath(file, options).replace(/\\/g, '/')
      const relativePathKey = normalizedUploadPath(relativePath)
      const pathMatches = documents.filter((document) => {
        const documentPathKey = normalizedUploadPath(document.sourcePath)
        return documentPathKey === relativePathKey || documentPathKey === `uploads/${relativePathKey}`
      })
      const fileNameKey = file.name.toLocaleLowerCase()
      const sameNameMatches = documents.filter((document) => document.name.toLocaleLowerCase() === fileNameKey)
      const matchingDocuments = pathMatches.length
        ? [...pathMatches, ...sameNameMatches.filter((document) => !pathMatches.some((match) => match.id === document.id))]
        : sameNameMatches
      if (!matchingDocuments.length) return

      const editableMatches = matchingDocuments.filter((document) => document.editable)
      const editablePathMatch = pathMatches.find((document) => document.editable)
      const selection = editablePathMatch
        ? editablePathMatch.id
        : pathMatches.length
          ? editableMatches.length === 0 ? '__new__' : null
          : matchingDocuments.length === 1 && editableMatches.length === 1
            ? editableMatches[0].id
            : editableMatches.length === 0
              ? '__new__'
              : null
      candidates.push({
        key: `${relativePath}:${index}`,
        file,
        relativePath,
        candidates: matchingDocuments,
        selection,
      })
    })
    return candidates.length ? { files, options, candidates } : null
  }

  async function uploadFiles(
    files: File[],
    options: UploadFileOptions = {},
    replacementIds?: Record<string, string>,
  ) {
    if (!files.length) return
    if (!replacementIds) {
      const pending = findUploadReplacements(files, options)
      if (pending) {
        setPendingUpload(pending)
        return
      }
    }
    setBusy(true)
    setError(null)
    try {
      const result = await importDocuments(workspace.workspace_id, files, {
        ...options,
        replaceDocumentIds: replacementIds,
      })
      if (result.accepted.length) {
        try {
          await refreshDocuments()
        } catch {
          onDocumentsChange([...result.accepted, ...documents])
        }
      }
      if (result.errors.length) {
        setError(result.errors.join('\n'))
      } else {
        setError(null)
      }
      const updated = result.notices.filter((notice) => notice.action === 'updated' && notice.contentChanged)
      if (updated.length) setUploadDiffNotice({ updated })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('project.uploadError'))
    } finally {
      setBusy(false)
    }
  }

  async function addLocalFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    await uploadFiles(files)
  }

  async function addFolderFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    const accepted = files.filter(shouldUploadFolderFile)
    const skipped = files.length - accepted.length
    if (!accepted.length) {
      setError('No supported document files found. Source code, tests, build/cache folders, and hidden files were skipped.')
      return
    }
    if (accepted.length > MAX_FOLDER_UPLOAD_FILES) {
      setError(`Folder still has ${accepted.length} document files after filtering. Please upload at most ${MAX_FOLDER_UPLOAD_FILES} files at a time to avoid a long-running backend batch.`)
      return
    }
    await uploadFiles(accepted, { preserveRelativePath: true })
    if (skipped > 0) {
      setError(`Uploaded ${accepted.length} files and skipped ${skipped} technical/source/test/cache files.`)
    }
  }

  function confirmPendingUpload() {
    if (!pendingUpload) return
    const replacementIds: Record<string, string> = {}
    const targetRelativePaths: Record<string, string> = {}
    for (const item of pendingUpload.candidates) {
      if (!item.selection || item.selection === '__new__') continue
      const selected = item.candidates.find((document) => document.id === item.selection)
      if (!selected?.editable) continue
      replacementIds[item.relativePath] = selected.id
      targetRelativePaths[item.relativePath] = selected.sourcePath.replace(/^uploads\//, '')
    }
    const options = { ...pendingUpload.options, targetRelativePaths }
    const { files } = pendingUpload
    setPendingUpload(null)
    void uploadFiles(files, options, replacementIds)
  }

  async function syncChangedUploads() {
    if (!uploadDiffNotice?.updated.length) return
    const documentIds = uploadDiffNotice.updated.map((notice) => notice.document.id)
    setUploadDiffNotice(null)
    await queueSync(documentIds)
  }

  async function queueSync(documentIds: string[]) {
    if (!documentIds.length) return
    setBusy(true)
    setError(null)
    setSyncNotice(null)
    try {
      const response = await syncDocuments(workspace.workspace_id, documentIds)
      const accepted = new Set(response.accepted)
      onDocumentsChange(documents.map((document) => accepted.has(document.id) ? { ...document, status: 'pending' } : document))
      setSyncNotice(t('project.syncSummary', {
        accepted: response.accepted.length,
        unchanged: response.skipped.unchanged.length,
        active: response.skipped.already_active.length,
      }))
      setSelectedIds(new Set())
      try {
        await refreshDocuments()
      } catch {
        // The optimistic pending state remains useful if the refresh is unavailable.
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('project.syncError'))
    } finally {
      setBusy(false)
    }
  }

  async function syncWorkspace() {
    await queueSync(documents.map((document) => document.id))
  }


  function startNewChatSession() {
    setHistoryOpen(false)
    setActiveChatSessionId(null)
    setChatTurns([])
    setQuestion('')
    setSelectedTool(null)
    setAttachedDocumentIds([])
    setAttachmentPickerOpen(false)
  }

  async function openChatSession(sessionId: string) {
    const workspaceId = workspace.workspace_id
    const requestEpoch = workspaceRequestEpochRef.current
    setHistoryLoading(true)
    setError(null)
    try {
      const session = await getChatSession(workspaceId, sessionId)
      if (
        currentWorkspaceIdRef.current !== workspaceId
        || workspaceRequestEpochRef.current !== requestEpoch
      ) return
      setActiveChatSessionId(session.id)
      setChatTurns(session.turns.map((turn) => ({
        id: turn.id,
        question: turn.question,
        attachments: turn.attachment_paths.map(attachmentFromPath),
        answer: {
          answer_id: turn.answer_id || `legacy-${turn.id}`,
          answer: turn.answer,
          citations: turn.citations || [],
          confidence: turn.confidence,
          chatSession: session,
        },
        toolExecution: null,
        error: null,
        pending: false,
        feedback: turn.answer_id ? feedbackByAnswerId[turn.answer_id] || null : null,
      })))
    } catch (reason) {
      if (
        currentWorkspaceIdRef.current !== workspaceId
        || workspaceRequestEpochRef.current !== requestEpoch
      ) return
      setError(reason instanceof Error ? reason.message : t('project.aiError'))
    } finally {
      if (
        currentWorkspaceIdRef.current === workspaceId
        && workspaceRequestEpochRef.current === requestEpoch
      ) setHistoryLoading(false)
    }
  }

  function normalizeForcedAnswer(execution: AssistantToolExecution): QueryResponse {
    const result = execution.result || {}
    return {
      answer_id: String(result.answer_id || `tool-${execution.execution_id}`),
      answer: String(result.answer || ''),
      citations: Array.isArray(result.citations) ? result.citations as QueryResponse['citations'] : [],
      confidence: result.confidence as QueryResponse['confidence'],
      chatSession: result.chat_session as QueryResponse['chatSession'],
    }
  }

  async function executeToolTurn(
    tool: AssistantToolDefinition,
    message: string | null,
    args: Record<string, unknown>,
    displayQuestion: string,
    attachmentsSnapshot: ChatAttachment[] = attachedDocuments,
  ) {
    if (asking) return
    const turnId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const workspaceId = workspace.workspace_id
    const requestEpoch = workspaceRequestEpochRef.current
    const sessionIdSnapshot = activeChatSessionId
    setQuestion('')
    setSelectedTool(null)
    setAsking(true)
    setError(null)
    setChatTurns((current) => ([
      ...current,
      {
        id: turnId,
        question: displayQuestion,
        attachments: attachmentsSnapshot,
        answer: null,
        toolExecution: null,
        error: null,
        pending: true,
        feedback: null,
      },
    ]))
    try {
      const execution = await executeAssistantTool(workspaceId, {
        force_tool: tool.name as AssistantToolName,
        message,
        arguments: args,
        filters: { file_paths: attachmentsSnapshot.map((document) => document.sourcePath) },
        chat_session_id: sessionIdSnapshot,
        save_history: true,
      })
      if (
        currentWorkspaceIdRef.current !== workspaceId
        || workspaceRequestEpochRef.current !== requestEpoch
      ) return
      const answer = execution.result_type === 'answer' ? normalizeForcedAnswer(execution) : null
      if (answer?.chatSession?.id) setActiveChatSessionId(answer.chatSession.id)
      setChatTurns((current) => current.map((turn) => (
        turn.id === turnId
          ? { ...turn, answer, toolExecution: execution, pending: false }
          : turn
      )))
      void refreshChatSessions()
    } catch (reason) {
      if (
        currentWorkspaceIdRef.current !== workspaceId
        || workspaceRequestEpochRef.current !== requestEpoch
      ) return
      const messageText = reason instanceof Error ? reason.message : t('project.aiError')
      setChatTurns((current) => current.map((turn) => (
        turn.id === turnId
          ? { ...turn, error: messageText, pending: false }
          : turn
      )))
    } finally {
      if (
        currentWorkspaceIdRef.current === workspaceId
        && workspaceRequestEpochRef.current === requestEpoch
      ) setAsking(false)
    }
  }

  async function submitAssistantQuestion() {
    const rawQuery = question.trim()
    if (!rawQuery || asking) return

    const commandToken = rawQuery.split(/\s+/, 1)[0].toLowerCase()
    const commandTool = assistantTools.find((tool) => tool.command === commandToken) || null
    const activeTool = selectedTool || commandTool
    if (rawQuery.startsWith('/') && !activeTool) {
      setError('Unknown assistant command. Choose one of the available tools.')
      return
    }
    if (activeTool) {
      const toolInput = selectedTool
        ? rawQuery
        : rawQuery.slice(commandToken.length).trim()
      if (activeTool.name !== 'get_evidence' && !toolInput) {
        setError(`Enter a request after ${activeTool.command}.`)
        return
      }
      if (activeTool.name === 'get_evidence' && !toolInput) {
        setError('Enter an evidence ID after /get_evidence.')
        return
      }
      await executeToolTurn(
        activeTool,
        activeTool.name === 'get_evidence' ? null : toolInput,
        activeTool.name === 'get_evidence' ? { evidence_id: toolInput } : {},
        `${activeTool.command} ${toolInput}`.trim(),
      )
      return
    }

    const query = rawQuery
    const turnId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const workspaceId = workspace.workspace_id
    const requestEpoch = workspaceRequestEpochRef.current
    const attachmentsSnapshot = [...attachedDocuments]
    const sessionIdSnapshot = activeChatSessionId
    setQuestion('')
    setAsking(true)
    setError(null)
    setChatTurns((current) => ([
      ...current,
      {
        id: turnId,
        question: query,
        attachments: attachmentsSnapshot,
        answer: null,
        toolExecution: null,
        error: null,
        pending: true,
        feedback: null,
      },
    ]))
    try {
      const result = await askKnowledgeBase(query, workspaceId, {
        filePaths: attachmentsSnapshot.map((document) => document.sourcePath),
        chatSessionId: sessionIdSnapshot,
      })
      if (
        currentWorkspaceIdRef.current !== workspaceId
        || workspaceRequestEpochRef.current !== requestEpoch
      ) return
      if (result.chatSession?.id) setActiveChatSessionId(result.chatSession.id)
      setChatTurns((current) => current.map((turn) => (
        turn.id === turnId
          ? { ...turn, answer: result, pending: false }
          : turn
      )))
      void refreshChatSessions()
    } catch (reason) {
      if (
        currentWorkspaceIdRef.current !== workspaceId
        || workspaceRequestEpochRef.current !== requestEpoch
      ) return
      const message = reason instanceof Error ? reason.message : t('project.aiError')
      setChatTurns((current) => current.map((turn) => (
        turn.id === turnId
          ? { ...turn, error: message, pending: false }
          : turn
      )))
    } finally {
      if (
        currentWorkspaceIdRef.current === workspaceId
        && workspaceRequestEpochRef.current === requestEpoch
      ) setAsking(false)
    }
  }

  async function sendFeedback(
    turnId: string,
    answerId: string,
    rating: 'positive' | 'negative',
  ) {
    try {
      await submitAnswerFeedback(workspace.workspace_id, answerId, rating)
      setFeedbackByAnswerId((current) => ({ ...current, [answerId]: rating }))
      setChatTurns((current) => current.map((turn) => (
        turn.id === turnId ? { ...turn, feedback: rating } : turn
      )))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Feedback could not be recorded')
    }
  }

  function selectAssistantTool(tool: AssistantToolDefinition) {
    const current = question.trimStart()
    const command = tool.command || ''
    const remainder = current.startsWith(command) ? current.slice(command.length).trimStart() : ''
    setQuestion(remainder)
    setSelectedTool(tool)
  }

  async function readEvidence(evidenceId: string) {
    const tool = assistantTools.find((item) => item.name === 'get_evidence')
    if (!tool) return
    await executeToolTurn(tool, null, { evidence_id: evidenceId }, `${tool.command} ${evidenceId}`.trim(), [])
  }

  function toggleAttachedDocument(documentId: string) {
    setAttachedDocumentIds((current) => current.includes(documentId)
      ? current.filter((item) => item !== documentId)
      : [...current, documentId])
  }

  function removeAttachedDocument(documentId: string) {
    setAttachedDocumentIds((current) => current.filter((item) => item !== documentId))
  }

  function toggleDocument(documentId: string) {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(documentId)) next.delete(documentId)
      else next.add(documentId)
      return next
    })
  }

  function toggleDocuments(documentIds: string[]) {
    setSelectedIds((current) => {
      const next = new Set(current)
      const allSelected = documentIds.every((documentId) => next.has(documentId))
      documentIds.forEach((documentId) => {
        if (allSelected) next.delete(documentId)
        else next.add(documentId)
      })
      return next
    })
  }

  function toggleAllDocuments() {
    setSelectedIds((current) => current.size === documents.length ? new Set() : new Set(documents.map((item) => item.id)))
  }

  async function renameSelected(document: DocumentRow) {
    if (!document.editable) return
    const name = window.prompt(t('project.renamePrompt'), document.name)?.trim()
    if (!name || name === document.name) return
    setBusy(true)
    setError(null)
    try {
      const renamed = await renameDocument(workspace.workspace_id, document.id, name)
      onDocumentsChange(documents.map((item) => item.id === document.id ? renamed : item))
      setSelectedIds(new Set())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('project.renameError'))
    } finally {
      setBusy(false)
    }
  }

  async function syncSelected() {
    const selected = documents.filter((document) => selectedIds.has(document.id))
    await queueSync(selected.map((document) => document.id))
  }

  async function moveSelected() {
    const destination = workspaces.find((item) => item.workspace_id !== workspace.workspace_id)
    const selected = documents.filter((document) => selectedIds.has(document.id))
    if (!destination || !selected.length || selected.some((document) => !document.editable)) return
    setBusy(true)
    try {
      await moveDocuments(workspace.workspace_id, destination.workspace_id, selected.map((document) => document.id))
      onDocumentsChange(documents.filter((document) => !selectedIds.has(document.id)))
      setSelectedIds(new Set())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('project.moveError'))
    } finally {
      setBusy(false)
    }
  }

  function requestDelete(documentIds: string[] = Array.from(selectedIds)) {
    const selected = documents.filter((document) => documentIds.includes(document.id))
    if (!selected.length || selected.some((document) => !document.editable)) return
    setPendingDeleteIds(selected.map((document) => document.id))
  }

  function closeDeleteDialog() {
    if (!busy) setPendingDeleteIds([])
  }

  async function confirmDelete() {
    const selected = documents.filter((document) => pendingDeleteIds.includes(document.id))
    if (!selected.length || selected.some((document) => !document.editable)) {
      setPendingDeleteIds([])
      return
    }
    setBusy(true)
    setError(null)
    try {
      const response = await deleteDocuments(workspace.workspace_id, selected.map((document) => document.id))
      const deletedIds = new Set(response.deleted)
      onDocumentsChange(documents.filter((document) => !deletedIds.has(document.id)))
      setSelectedIds(new Set())
      setPendingDeleteIds([])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('project.deleteError'))
      setPendingDeleteIds([])
      setSelectedIds(new Set())
      try {
        await refreshDocuments()
      } catch {
        // Keep the original deletion error visible while dropping stale selection state.
      }
    } finally {
      setBusy(false)
    }
  }

  async function downloadSelected() {
    setBusy(true)
    try {
      for (const document of documents.filter((item) => selectedIds.has(item.id))) {
        await downloadDocument(workspace.workspace_id, document)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('project.downloadError'))
    } finally {
      setBusy(false)
    }
  }

  const selectedDocuments = documents.filter((document) => selectedIds.has(document.id))
  const editableSelection = selectedDocuments.length > 0 && selectedDocuments.every((document) => document.editable)
  const moveDestination = workspaces.find((item) => item.workspace_id !== workspace.workspace_id)
  const sourceLabel = workspaceSettings?.retrieval.rerank_provider ? t('project.sourceReady') : t('project.usingWorkspaceData')

  return (
    <section className="page page-project" aria-labelledby="project-title">
      <aside
        className="project-sidebar"
        data-rail-collapsed={railCollapsed ? 'true' : undefined}
        onMouseEnter={() => setRailCollapsed(false)}
        onMouseLeave={() => setRailCollapsed(true)}
        onClickCapture={() => setRailCollapsed(true)}
      >
        <nav aria-label={t('project.navLabel')}>
          <Button variant="ghost" aria-label={t('project.overview')} className={section === 'overview' ? 'is-active' : ''} leadingIcon={<House />} onClick={() => void loadSection('overview')}><span className="rail-label">{t('project.overview')}</span></Button>
          <Button variant="ghost" aria-label={t('project.documents')} className={section === 'documents' ? 'is-active' : ''} leadingIcon={<FileText />} onClick={() => void loadSection('documents')}><span className="rail-label">{t('project.documents')}</span></Button>
          <Button variant="ghost" aria-label={t('project.members')} className={section === 'members' ? 'is-active' : ''} leadingIcon={<Users />} onClick={() => void loadSection('members')}><span className="rail-label">{t('project.members')}</span></Button>
          <Button variant="ghost" aria-label={t('project.graph')} className={section === 'graph' ? 'is-active' : ''} leadingIcon={<Share2 />} onClick={() => void loadSection('graph')}><span className="rail-label">{t('project.graph')}</span></Button>
          <Button variant="ghost" aria-label={t('project.evaluator')} className={section === 'evaluator' ? 'is-active' : ''} leadingIcon={<Gauge />} onClick={() => void loadSection('evaluator')}><span className="rail-label">{t('project.evaluator')}</span></Button>
          <Button variant="ghost" aria-label={t('project.agentConnection')} className={section === 'mcp' ? 'is-active' : ''} leadingIcon={<PlugZap />} onClick={() => void loadSection('mcp')}><span className="rail-label">{t('project.agentConnection')}</span></Button>
          <Button variant="ghost" aria-label={t('project.settings')} className={section === 'settings' ? 'is-active' : ''} leadingIcon={<Settings />} onClick={() => void loadSection('settings')}><span className="rail-label">{t('project.settings')}</span></Button>
        </nav>
        <div className="project-sidebar-bottom">
          <Button variant="primary" fullWidth aria-label={t('project.switchWorkspace')} leadingIcon={<FolderPlus />} onClick={onBack}><span className="rail-label">{t('project.switchWorkspace')}</span></Button>
          <div className="project-user"><span>{bootstrap.session.display_name.slice(0, 2).toUpperCase()}</span><div><strong>{bootstrap.session.display_name}</strong><small>{workspace.name}</small></div></div>
          <div className="signed-request"><ShieldCheck size={14} /> {t('project.signedRequest')}</div>
        </div>
      </aside>

      <main className="project-content">
        <header className="project-toolbar">
          <div>
            <Button variant="ghost" size="icon" aria-label={t('project.backToWorkspace')} leadingIcon={<ArrowLeft />} onClick={onBack} />
            <div><span>{t('project.workspaceLabel')}</span><h1 id="project-title">{section === 'documents' ? t('project.myDocuments') : workspace.name}</h1></div>
          </div>
          {section === 'documents' && (
            <form className="document-search" onSubmit={searchDocuments}>
              <Search size={16} />
              <label className="sr-only" htmlFor="document-search">{t('project.searchDocuments')}</label>
              <input id="document-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t('project.searchPlaceholder')} />
              <Button variant="ghost" size="icon" type="submit" loading={busy} aria-label={t('project.searchBackend')} />
            </form>
          )}
          <div className="project-toolbar-actions">
            <Button variant="primary" size="sm" leadingIcon={<Upload />} disabled={busy} onClick={() => fileInputRef.current?.click()}>{t('project.uploadFile')}</Button>
            <Button variant="secondary" size="sm" leadingIcon={<FolderPlus />} disabled={busy} onClick={() => folderInputRef.current?.click()}>{t('project.uploadFolder')}</Button>
            <Button variant="ghost" size="sm" leadingIcon={<CloudUpload />} disabled={busy || documents.length === 0} onClick={() => void syncWorkspace()}>{t('project.classifyWorkspace')}</Button>
          </div>
          <input ref={fileInputRef} className="sr-only" type="file" multiple onChange={addLocalFiles} />
          <input ref={folderInputRef} className="sr-only" type="file" multiple onChange={addFolderFiles} />
        </header>

        {error && <p className="workspace-error" role="alert">{error}</p>}
        {syncNotice && <p className="workspace-notice" role="status">{syncNotice}</p>}
        {busy && <div className="workspace-loading"><span className="system-loader" /> {t('project.loading')}</div>}

        {!busy && section === 'documents' && (
          <DocumentTable sessionKey={projectSessionKey} documents={documents} selectedIds={selectedIds} onToggle={toggleDocument} onToggleMany={toggleDocuments} onToggleAll={toggleAllDocuments} onRename={(document) => void renameSelected(document)} onDelete={(document) => requestDelete([document.id])} onDownload={(document) => void downloadDocument(workspace.workspace_id, document)} onAnalyze={onAnalyze} />
        )}

        {!busy && section === 'overview' && overview && (
          <div className="workspace-data-panel">
            <div className="panel-heading"><Gauge /><div><span>{t('project.overviewLabel')}</span><h2>{t('project.overviewTitle')}</h2></div></div>
            <div className="metric-grid">
              <article><span>{t('project.totalDocuments')}</span><strong>{overview.document_count.toLocaleString(locale)}</strong></article>
              <article><span>{t('project.indexedDocuments')}</span><strong>{overview.indexed_count.toLocaleString(locale)}</strong></article>
              <article><span>{t('project.processingDocuments')}</span><strong>{overview.processing_count.toLocaleString(locale)}</strong></article>
              <article><span>{t('project.storage')}</span><strong>{(overview.storage_bytes / 1024 / 1024).toFixed(1)} MB</strong></article>
            </div>
          </div>
        )}

        {!busy && section === 'members' && (
          <div className="workspace-data-panel">
            <div className="panel-heading"><Users /><div><span>{t('project.membersLabel')}</span><h2>{t('project.members')}</h2></div></div>
            <div className="members-list">
              {members.map((member) => <article key={member.id}><span>{member.display_name.slice(0, 2).toUpperCase()}</span><div><strong>{member.display_name}</strong><small>{member.role}</small></div><i>{member.status}</i></article>)}
            </div>
          </div>
        )}

        {!busy && section === 'settings' && workspaceSettings && (
          <div className="workspace-data-panel">
            <div className="panel-heading"><Settings /><div><span>{t('project.settingsLabel')}</span><h2>{t('project.settingsTitle')}</h2></div></div>
            <dl className="settings-grid">
              <div><dt>{t('project.keywordSearch')}</dt><dd>{workspaceSettings.retrieval.bm25_enabled ? t('project.enabled') : t('project.disabled')}</dd></div>
              <div><dt>{t('project.resultRanking')}</dt><dd>{workspaceSettings.retrieval.rerank_enabled ? t('project.enabled') : t('project.disabled')}</dd></div>
              <div><dt>{t('project.prioritySuggestions')}</dt><dd>{workspaceSettings.retrieval.rerank_top_n}</dd></div>
              <div><dt>{t('project.minimumScore')}</dt><dd>{workspaceSettings.retrieval.min_rerank_score}</dd></div>
              <div className="settings-wide"><dt>{t('project.sourceRoot')}</dt><dd>{workspaceSettings.storage.source_root}</dd></div>
              <div className="settings-wide"><dt>{t('project.uploadRoot')}</dt><dd>{workspaceSettings.storage.upload_root}</dd></div>
            </dl>
          </div>
        )}

        {!busy && section === 'evaluator' && (
          <EvaluationPage bootstrap={bootstrap} workspace={workspace} />
        )}

        {!busy && section === 'graph' && (
          <KnowledgeGraphPanel
            graph={knowledgeGraph}
            loading={busy}
            storageKey={projectSessionKey}
            onRefresh={() => void refreshKnowledgeGraph()}
            onDownload={() => void downloadWorkspaceGraphml(workspace.workspace_id).catch((reason) => setError(reason instanceof Error ? reason.message : 'Không thể tải GraphML'))}
          />
        )}

        {!busy && section === 'mcp' && (
          <AgentConnectionsPage bootstrap={bootstrap} workspaces={[workspace]} workspaceId={workspace.workspace_id} embedded />
        )}

        {section === 'documents' && selectedIds.size > 0 && (
          <div className="document-action-dock" aria-label={t('table.actions')}>
            <strong>{t('project.selectedFiles', { count: selectedIds.size })}</strong>
            <Button variant="primary" size="sm" leadingIcon={<CloudUpload />} disabled={busy} onClick={() => void syncSelected()}>{t('project.syncChanges')}</Button>
            <Button variant="ghost" size="sm" leadingIcon={<ArrowRightLeft />} disabled={!editableSelection || !moveDestination} onClick={() => void moveSelected()}>{t('project.move')}</Button>
            <Button variant="ghost" size="sm" leadingIcon={<Download />} onClick={() => void downloadSelected()}>{t('project.download')}</Button>
            <Button variant="ghost" size="sm" leadingIcon={<Pencil />} disabled={selectedDocuments.length !== 1 || !editableSelection} onClick={() => void renameSelected(selectedDocuments[0])}>{t('project.rename')}</Button>
            <Button variant="ghost" size="sm" leadingIcon={<Trash2 />} disabled={!editableSelection} onClick={() => requestDelete()}>{t('project.delete')}</Button>
            <Button variant="ghost" size="icon" aria-label={t('project.clearSelection')} leadingIcon={<X />} onClick={() => setSelectedIds(new Set())} />
          </div>
        )}
      </main>

      <aside className="project-ai-panel">
        <header className="project-ai-header">
          <div className="project-ai-identity">
            <i><Bot size={19} /></i>
            <div>
              <h2>{t('project.assistantTitle')}</h2>
              <span className="project-ai-status">
                <b className={readiness?.ready_for_qa === false ? 'is-waiting' : ''} aria-hidden="true" />
                {sourceLabel}
              </span>
            </div>
          </div>
          <div className="project-ai-header-actions">
            <button
              type="button"
              className={`project-ai-header-button${historyOpen ? ' is-active' : ''}`}
              aria-label={t('chatHistory.title')}
              aria-expanded={historyOpen}
              aria-controls="project-chat-history"
              title={t('chatHistory.title')}
              onClick={() => setHistoryOpen((current) => !current)}
            >
              <History size={17} />
              {chatSessions.length > 0 && <b>{chatSessions.length}</b>}
            </button>
            <button
              type="button"
              className="project-ai-header-button is-primary"
              aria-label={t('chatHistory.new')}
              title={t('chatHistory.new')}
              onClick={startNewChatSession}
            >
              <MessageSquarePlus size={17} />
            </button>
          </div>
        </header>
        {historyOpen && <button type="button" className="chat-history-scrim" aria-label={t('common.close')} onClick={() => setHistoryOpen(false)} />}
        {historyOpen && <section id="project-chat-history" className="chat-history-panel" aria-label={t('chatHistory.title')}>
          <div className="chat-history-header">
            <div>
              <strong>{t('chatHistory.title')}</strong>
              <span>{t('chatHistory.count', { count: chatSessions.length })}</span>
            </div>
            <button type="button" className="chat-history-close" onClick={() => setHistoryOpen(false)} title={t('common.close')} aria-label={t('common.close')}>
              <X size={15} />
            </button>
          </div>
          <div className="chat-history-list" aria-live="polite">
            {historyLoading && !chatSessions.length && <span className="chat-history-empty">{t('chatHistory.loading')}</span>}
            {!historyLoading && !chatSessions.length && <span className="chat-history-empty">{t('chatHistory.empty')}</span>}
            {chatSessions.map((session) => (
              <button
                key={session.id}
                type="button"
                className={`chat-history-item${session.id === activeChatSessionId ? ' is-active' : ''}`}
                onClick={() => { setHistoryOpen(false); void openChatSession(session.id) }}
              >
                <strong>{session.title}</strong>
                <small>
                  {t('chatHistory.turns', { count: session.turn_count })}
                  {' - '}
                  {session.updated_at ? new Date(session.updated_at).toLocaleDateString(locale) : '-'}
                </small>
                <span>{session.last_answer_preview || session.last_question}</span>
              </button>
            ))}
          </div>
        </section>}
        <div ref={conversationRef} className="project-conversation" aria-live="polite">
          {!chatTurns.length && !asking && <div className="ai-welcome"><Bot /><strong>{t('project.aiReady')}</strong><p>{t('project.aiReadyBody')}</p></div>}
          {chatTurns.map((turn) => (
            <article className="chat-turn" key={turn.id}>
              <div className="user-message-stack">
                <div className="user-message">{turn.question}</div>
                {turn.attachments.length > 0 && (
                  <div className="user-message-attachments">
                    {turn.attachments.map((document) => (
                      <span key={document.id}>{document.sourcePath}</span>
                    ))}
                  </div>
                )}
              </div>
              {turn.pending && <div className="ai-thinking"><span className="system-loader" /> {t('project.analyzing')}</div>}
              {turn.error && <p className="ai-error" role="alert">{turn.error}</p>}
              {turn.toolExecution && turn.toolExecution.result_type !== 'answer' && <ToolResultRenderer execution={turn.toolExecution} onGetEvidence={(evidenceId) => { void readEvidence(evidenceId) }} />}
              {turn.answer && <article className="assistant-message"><AssistantAnswerText text={turn.answer.answer} /><CitationEvidenceList titleKey="project.sources" citations={turn.answer.citations} confidence={turn.answer.confidence} /><div className="answer-feedback" aria-label={t('project.feedbackPrompt')}><button type="button" aria-label={t('project.feedbackPositive')} title={t('project.feedbackPositive')} className={turn.feedback === 'positive' ? 'is-selected' : ''} disabled={turn.feedback !== null} onClick={() => { void sendFeedback(turn.id, turn.answer!.answer_id, 'positive') }}><ThumbsUp size={13} /></button><button type="button" aria-label={t('project.feedbackNegative')} title={t('project.feedbackNegative')} className={turn.feedback === 'negative' ? 'is-selected' : ''} disabled={turn.feedback !== null} onClick={() => { void sendFeedback(turn.id, turn.answer!.answer_id, 'negative') }}><ThumbsDown size={13} /></button></div></article>}
            </article>
          ))}
        </div>
        <AssistantToolComposer
          tools={assistantTools}
          value={question}
          selectedTool={selectedTool}
          asking={asking}
          placeholder={t('project.askPlaceholder')}
          topContent={<>
          {attachedDocuments.length > 0 && (
            <div className="composer-attachment-strip" aria-live="polite">
              {attachedDocuments.map((document) => (
                <button key={document.id} type="button" className="composer-attachment-chip" onClick={() => removeAttachedDocument(document.id)} title={document.sourcePath}>
                  <Paperclip size={13} />
                  <span>{document.sourcePath}</span>
                  <X size={12} />
                </button>
              ))}
            </div>
          )}
          {attachmentPickerOpen && (
            <div className="workspace-attachment-panel" role="group" aria-label={t('project.attachDocuments')}>
              <div className="workspace-attachment-panel-header">
                <strong>{t('project.attachDocuments')}</strong>
                <span>{documents.length ? t('project.attachHint') : t('project.attachEmpty')}</span>
              </div>
              <div className="workspace-attachment-list">
                {documents.length ? documents.map((document) => {
                  const checked = attachedDocumentIds.includes(document.id)
                  return (
                    <label key={document.id} className={`workspace-attachment-option${checked ? ' is-selected' : ''}`}>
                      <input type="checkbox" checked={checked} onChange={() => toggleAttachedDocument(document.id)} />
                      <span>
                        <b>{document.name}</b>
                        <small>{document.sourcePath}</small>
                      </span>
                    </label>
                  )
                }) : <div className="workspace-attachment-empty">{t('project.attachEmpty')}</div>}
              </div>
            </div>
          )}
          </>}
          actions={<div className="project-ai-composer-actions">
            <button
              type="button"
              className="composer-attachment-trigger"
              aria-label={attachedDocuments.length ? t('project.attachSelectedCount', { count: attachedDocuments.length }) : t('project.attachFile')}
              title={attachedDocuments.length ? t('project.attachSelectedCount', { count: attachedDocuments.length }) : t('project.attachFile')}
              disabled={!documents.length}
              onClick={() => setAttachmentPickerOpen((current) => !current)}
            >
              <Paperclip size={17} />
            </button>
            <Button variant="primary" size="sm" type="submit" loading={asking} disabled={!question.trim()} trailingIcon={<Send size={15} />}>{t('project.send')}</Button>
          </div>}
          onChange={setQuestion}
          onSubmit={submitAssistantQuestion}
          onSelectTool={selectAssistantTool}
          onRemoveTool={() => setSelectedTool(null)}
        />
      </aside>

      {pendingUpload && (
        <div className="upload-update-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !busy) setPendingUpload(null)
        }}>
          <div className="upload-update-dialog" role="alertdialog" aria-modal="true" aria-labelledby="upload-update-title" aria-describedby="upload-update-description">
            <div className="upload-update-icon" aria-hidden="true"><Upload size={22} /></div>
            <span className="upload-update-eyebrow">FILE MATCH DETECTED</span>
            <h2 id="upload-update-title">Phát hiện tài liệu có thể trùng</h2>
            <p id="upload-update-description">
              Hệ thống ưu tiên relative path, sau đó đối chiếu tên file. Hãy chọn tài liệu upload editable cần cập nhật hoặc chọn tạo file mới; source read-only sẽ không bị ghi đè.
              {!pendingUpload.options.preserveRelativePath && ' Với Tải tệp, browser chỉ gửi tên file, không gửi thư mục local; hãy chọn đúng candidate nếu có nhiều file trùng tên.'}
            </p>
            <div className="upload-update-files">
              {pendingUpload.candidates.map((item) => {
                const selected = item.candidates.find((document) => document.id === item.selection)
                const hasEditableCandidate = item.candidates.some((document) => document.editable)
                return (
                  <div className="upload-update-candidate" key={item.key}>
                    <div className="upload-update-candidate-heading">
                      <FileText size={14} />
                      <span title={item.relativePath}>{item.relativePath}</span>
                    </div>
                    <Select
                      ariaLabel={`Chọn tài liệu thay thế cho ${item.relativePath}`}
                      value={item.selection || ''}
                      options={[
                        { value: '', label: 'Chọn tài liệu cần cập nhật', disabled: true },
                        { value: '__new__', label: 'Tạo file mới' },
                        ...item.candidates.map((document) => ({
                          value: document.id,
                          label: `${document.sourcePath} ${document.editable ? '(upload editable)' : '(source chỉ đọc)'}`,
                          disabled: !document.editable,
                        })),
                      ]}
                      onChange={(selectionValue) => {
                        const selection = selectionValue as UploadCandidate['selection']
                        setPendingUpload((current) => current ? {
                          ...current,
                          candidates: current.candidates.map((candidate) => candidate.key === item.key
                            ? { ...candidate, selection }
                          : candidate),
                        } : current)
                      }}
                    />
                    <b>{selected ? 'cập nhật' : hasEditableCandidate ? 'cần chọn' : 'tạo bản mới'}</b>
                  </div>
                )
              })}
            </div>
            {pendingUpload.candidates.some((item) => item.candidates.some((document) => !document.editable)) && (
              <div className="upload-update-readonly-warning">
                Một hoặc nhiều candidate là source chỉ đọc. Candidate này chỉ để tham chiếu; hãy chọn upload editable hoặc tạo file mới.
              </div>
            )}
            <div className="upload-update-actions">
              <Button variant="secondary" onClick={() => setPendingUpload(null)} disabled={busy}>Hủy</Button>
              <Button
                variant="primary"
                leadingIcon={<Upload size={15} />}
                onClick={confirmPendingUpload}
                disabled={busy || pendingUpload.candidates.some((item) => item.selection === null)}
              >
                Xác nhận upload
              </Button>
            </div>
          </div>
        </div>
      )}

      {uploadDiffNotice && (
        <div className="upload-update-backdrop" role="presentation">
          <div className="upload-update-dialog upload-update-dialog--changed" role="alertdialog" aria-modal="true" aria-labelledby="upload-diff-title" aria-describedby="upload-diff-description">
            <div className="upload-update-icon upload-update-icon--changed" aria-hidden="true"><RefreshCw size={22} /></div>
            <span className="upload-update-eyebrow">INCREMENTAL INGESTION</span>
            <h2 id="upload-diff-title">Đã phát hiện nội dung thay đổi</h2>
            <p id="upload-diff-description">
              File đã được cập nhật. Khi đồng bộ, hệ thống sẽ giữ lại chunk không đổi và chỉ embedding phần nội dung mới hoặc đã sửa, thay vì xử lý lại toàn bộ file.
            </p>
            <div className="upload-update-files">
              {uploadDiffNotice.updated.map((notice) => (
                <div key={notice.document.id}><FileText size={14} /><span>{notice.document.sourcePath}</span><b>cần đồng bộ</b></div>
              ))}
            </div>
            <div className="upload-update-actions">
              <Button variant="secondary" onClick={() => setUploadDiffNotice(null)}>Để sau</Button>
              <Button variant="primary" leadingIcon={<RefreshCw size={15} />} onClick={() => void syncChangedUploads()}>Đồng bộ phần thay đổi</Button>
            </div>
          </div>
        </div>
      )}

      {pendingDeleteIds.length > 0 && (
        <div className="workspace-create-backdrop delete-confirm-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) closeDeleteDialog()
        }}>
          <div className="delete-confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-confirm-title" aria-describedby="delete-confirm-description">
            <div className="delete-confirm-icon" aria-hidden="true"><Trash2 size={24} /></div>
            <div className="delete-confirm-copy">
              <span>{t('project.deleteEyebrow')}</span>
              <h2 id="delete-confirm-title">{t('project.deleteTitle', { count: pendingDeleteIds.length })}</h2>
              <p id="delete-confirm-description">{t('project.deleteDescription', { count: pendingDeleteIds.length })}</p>
            </div>
            <div className="delete-confirm-actions">
              <Button ref={deleteCancelRef} variant="secondary" onClick={closeDeleteDialog} disabled={busy}>{t('project.deleteCancel')}</Button>
              <Button variant="danger" leadingIcon={<Trash2 size={16} />} loading={busy} onClick={() => void confirmDelete()}>{t('project.deleteConfirmAction')}</Button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}


