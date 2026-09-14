import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeft,
  Bot,
  Download,
  FileSearch,
  FileText,
  FolderKanban,
  Gauge,
  Send,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from 'lucide-react'

import { askKnowledgeBase, downloadDocument, executeAssistantTool, getAssistantToolCatalog, getChatSession, getChatSessions, getDocumentMetadata, getDocumentPreview, submitAnswerFeedback } from '../api'
import { CitationEvidenceList } from '../components/CitationEvidenceList'
import { AssistantToolComposer } from '../components/assistant/AssistantToolComposer'
import { ToolResultRenderer } from '../components/assistant/ToolResultRenderer'
import { Button } from '../components/ui/Button'
import type { AssistantToolDefinition, AssistantToolExecution, AssistantToolName, DocumentMetadata, DocumentPreview, DocumentRow, QueryResponse, UiBootstrap, WorkspaceRecord } from '../types'
import { readSessionState, sessionStorageKey, writeSessionState } from '../sessionState'

type ChatTurn = {
  id: string
  question: string
  answer: QueryResponse | null
  toolExecution: AssistantToolExecution | null
  error: string | null
  pending: boolean
  feedback: 'positive' | 'negative' | null
}

type AssistantPageProps = {
  bootstrap: UiBootstrap
  workspace: WorkspaceRecord
  documents: DocumentRow[]
  initialDocument: DocumentRow | null
  onDocumentChange?: (document: DocumentRow | null) => void
  onClose: () => void
  onBack: () => void
}

type AssistantSessionState = {
  documentId: string | null
  question: string
  activeChatSessionId: string | null
  selectedToolName: string | null
  feedbackByAnswerId: Record<string, 'positive' | 'negative'>
}

export function AssistantPage({
  bootstrap,
  workspace,
  documents,
  initialDocument,
  onDocumentChange,
  onClose,
  onBack,
}: AssistantPageProps) {
  const { t } = useTranslation()
  const [document, setDocument] = useState<DocumentRow | null>(initialDocument || documents[0] || null)
  const [preview, setPreview] = useState<DocumentPreview | null>(null)
  const [metadata, setMetadata] = useState<DocumentMetadata | null>(null)
  const [loadingMetadata, setLoadingMetadata] = useState(false)
  const [question, setQuestion] = useState('')
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([])
  const [loadingPreview, setLoadingPreview] = useState(false)
  const [asking, setAsking] = useState(false)
  const [assistantTools, setAssistantTools] = useState<AssistantToolDefinition[]>([])
  const [selectedTool, setSelectedTool] = useState<AssistantToolDefinition | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [railCollapsed, setRailCollapsed] = useState(false)
  const [activeChatSessionId, setActiveChatSessionId] = useState<string | null>(null)
  const [feedbackByAnswerId, setFeedbackByAnswerId] = useState<Record<string, 'positive' | 'negative'>>({})
  const conversationEndRef = useRef<HTMLDivElement>(null)
  const currentWorkspaceIdRef = useRef(workspace.workspace_id)
  const workspaceRequestEpochRef = useRef(0)
  const sessionHydrationPendingRef = useRef(false)
  const assistantSessionKey = sessionStorageKey('assistant', `${bootstrap.session.email || bootstrap.session.display_name}:${workspace.workspace_id}`)
  if (currentWorkspaceIdRef.current !== workspace.workspace_id) {
    currentWorkspaceIdRef.current = workspace.workspace_id
    workspaceRequestEpochRef.current += 1
  }

  useEffect(() => {
    const saved = readSessionState<AssistantSessionState>(assistantSessionKey)
    const selected = initialDocument || documents.find((item) => item.id === saved?.documentId) || documents[0] || null
    sessionHydrationPendingRef.current = true
    setDocument(selected)
    setChatTurns([])
    setQuestion(saved?.question || '')
    setSelectedTool(null)
    setError(null)
    setActiveChatSessionId(saved?.activeChatSessionId || null)
    setFeedbackByAnswerId(saved?.feedbackByAnswerId || {})
    setAsking(false)
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
    let cancelled = false
    getChatSessions(workspace.workspace_id)
      .then(async (sessions) => {
        if (cancelled) return
        const sessionId = saved?.activeChatSessionId || sessions[0]?.id
        if (!sessionId) return
        const session = await getChatSession(workspace.workspace_id, sessionId)
        if (cancelled) return
        setActiveChatSessionId(session.id)
        setChatTurns(session.turns.map((turn) => ({
          id: turn.id,
          question: turn.question,
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
            feedback: turn.answer_id ? (saved?.feedbackByAnswerId || {})[turn.answer_id] || null : null,
        })))
      })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [assistantSessionKey, initialDocument?.id, workspace.workspace_id])

  useEffect(() => {
    if (sessionHydrationPendingRef.current) {
      return
    }
    writeSessionState<AssistantSessionState>(assistantSessionKey, {
      documentId: document?.id || null,
      question,
      activeChatSessionId,
      selectedToolName: selectedTool?.name || null,
      feedbackByAnswerId,
    })
  }, [activeChatSessionId, assistantSessionKey, document?.id, feedbackByAnswerId, question, selectedTool?.name])

  useEffect(() => {
    onDocumentChange?.(document)
  }, [document, onDocumentChange])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      conversationEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [chatTurns, asking])

  useEffect(() => {
    if (!document) {
      setPreview(null)
      setMetadata(null)
      return
    }
    setMetadata(null)
    setLoadingPreview(true)
    setError(null)
    getDocumentPreview(workspace.workspace_id, document.id)
      .then(setPreview)
      .catch((reason) => setError(reason instanceof Error ? reason.message : t('assistant.previewError')))
      .finally(() => setLoadingPreview(false))
  }, [document, workspace.workspace_id, t])

  async function loadMetadata() {
    if (!document || loadingMetadata) return
    setLoadingMetadata(true)
    setError(null)
    try {
      setMetadata(await getDocumentMetadata(workspace.workspace_id, document.id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Document metadata could not be loaded')
    } finally {
      setLoadingMetadata(false)
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
  ) {
    if (asking) return
    const turnId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const workspaceId = workspace.workspace_id
    const requestEpoch = workspaceRequestEpochRef.current
    setQuestion('')
    setSelectedTool(null)
    setAsking(true)
    setError(null)
    setChatTurns((current) => ([
      ...current,
      { id: turnId, question: displayQuestion, answer: null, toolExecution: null, error: null, pending: true, feedback: null },
    ]))
    try {
      const execution = await executeAssistantTool(workspaceId, {
        force_tool: tool.name as AssistantToolName,
        message,
        arguments: args,
        filters: { file_paths: document ? [document.sourcePath] : [] },
        chat_session_id: activeChatSessionId,
        save_history: true,
      })
      if (currentWorkspaceIdRef.current !== workspaceId || workspaceRequestEpochRef.current !== requestEpoch) return
      const answer = execution.result_type === 'answer' ? normalizeForcedAnswer(execution) : null
      if (answer?.chatSession?.id) setActiveChatSessionId(answer.chatSession.id)
      setChatTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, answer, toolExecution: execution, pending: false } : turn))
    } catch (reason) {
      if (currentWorkspaceIdRef.current !== workspaceId || workspaceRequestEpochRef.current !== requestEpoch) return
      setChatTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, error: reason instanceof Error ? reason.message : t('project.aiError'), pending: false } : turn))
    } finally {
      if (currentWorkspaceIdRef.current === workspaceId && workspaceRequestEpochRef.current === requestEpoch) setAsking(false)
    }
  }

  function selectAssistantTool(tool: AssistantToolDefinition) {
    const command = tool.command || ''
    const trimmed = question.trimStart()
    setQuestion(trimmed.startsWith(command) ? trimmed.slice(command.length).trimStart() : '')
    setSelectedTool(tool)
  }

  async function submitAssistantQuestion() {
    const rawQuestion = question.trim()
    if (!rawQuestion || asking) return
    const commandToken = rawQuestion.split(/\s+/, 1)[0].toLowerCase()
    const commandTool = assistantTools.find((tool) => tool.command === commandToken) || null
    const activeTool = selectedTool || commandTool
    if (rawQuestion.startsWith('/') && !activeTool) {
      setError('Unknown assistant command. Choose one of the available tools.')
      return
    }
    if (activeTool) {
      const input = selectedTool ? rawQuestion : rawQuestion.slice(commandToken.length).trim()
      if (!input) {
        setError(`Enter a request after ${activeTool.command}.`)
        return
      }
      await executeToolTurn(
        activeTool,
        activeTool.name === 'get_evidence' ? null : input,
        activeTool.name === 'get_evidence' ? { evidence_id: input } : {},
        `${activeTool.command} ${input}`.trim(),
      )
      return
    }
    await submitQuestion()
  }

  async function readEvidence(evidenceId: string) {
    const tool = assistantTools.find((item) => item.name === 'get_evidence')
    if (tool) await executeToolTurn(tool, null, { evidence_id: evidenceId }, `${tool.command} ${evidenceId}`)
  }

  async function sendFeedback(turnId: string, answerId: string, rating: 'positive' | 'negative') {
    try {
      await submitAnswerFeedback(workspace.workspace_id, answerId, rating)
      setFeedbackByAnswerId((current) => ({ ...current, [answerId]: rating }))
      setChatTurns((current) => current.map((turn) => turn.id === turnId ? { ...turn, feedback: rating } : turn))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Feedback could not be recorded')
    }
  }

  async function submitQuestion() {
    const rawQuestion = question.trim()
    if (!rawQuestion || asking) return

    const turnId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const workspaceId = workspace.workspace_id
    const requestEpoch = workspaceRequestEpochRef.current
    const sessionIdSnapshot = activeChatSessionId
    setQuestion('')
    setAsking(true)
    setError(null)
    setChatTurns((current) => ([
      ...current,
      { id: turnId, question: rawQuestion, answer: null, toolExecution: null, error: null, pending: true, feedback: null },
    ]))
    try {
      const result = await askKnowledgeBase(rawQuestion, workspaceId, {
        filePaths: document ? [document.sourcePath] : [],
        chatSessionId: sessionIdSnapshot,
      })
      if (
        currentWorkspaceIdRef.current !== workspaceId
        || workspaceRequestEpochRef.current !== requestEpoch
      ) return
      if (result.chatSession?.id) {
        setActiveChatSessionId(result.chatSession.id)
      }
      setChatTurns((current) => current.map((turn) => (
        turn.id === turnId
          ? { ...turn, answer: result, pending: false }
          : turn
      )))
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

  return (
    <section className="page page-analysis" aria-labelledby="analysis-title">
      <aside
        className="analysis-sidebar"
        data-rail-collapsed={railCollapsed ? 'true' : undefined}
        onMouseEnter={() => setRailCollapsed(false)}
        onMouseLeave={() => setRailCollapsed(true)}
        onClickCapture={() => setRailCollapsed(true)}
      >
        <div><strong>{workspace.name}</strong><span>{t('assistant.workspaceBackend')}</span></div>
        <Button variant="primary" fullWidth aria-label={t('assistant.backToFiles')} leadingIcon={<ArrowLeft />} onClick={onClose}><span className="rail-label">{t('assistant.backToFiles')}</span></Button>
        <nav>
          <Button variant="ghost" aria-label={t('assistant.currentDocument')} className="is-active" leadingIcon={<FileText />}><span className="rail-label">{t('assistant.currentDocument')}</span></Button>
          <Button variant="ghost" aria-label={t('assistant.allWorkspaces')} leadingIcon={<FolderKanban />} onClick={onBack}><span className="rail-label">{t('assistant.allWorkspaces')}</span></Button>
        </nav>
        <div className="analysis-document-list">
          {documents.slice(0, 8).map((item) => (
            <Button key={item.id} variant="unstyled" className={item.id === document?.id ? 'is-active' : ''} onClick={() => { setDocument(item); setChatTurns([]); setQuestion(''); setActiveChatSessionId(null); setSelectedTool(null); setError(null) }}>
              <FileText size={15} /><span className="rail-label">{item.name}</span>
            </Button>
          ))}
        </div>
      </aside>

      <main className="analysis-main">
        <div className="analysis-subheader">
          <div><span>{t('assistant.workspaces')}</span><b>/</b><span>{workspace.name}</span><b>/</b><strong>{document?.name || t('assistant.noDocument')}</strong></div>
          {document && <Button variant="primary" size="sm" leadingIcon={<Download />} onClick={() => void downloadDocument(workspace.workspace_id, document)}>{t('assistant.downloadDocument')}</Button>}
        </div>

        <div className="analysis-split">
          <section className="document-preview-panel">
            <div className="document-preview-inner">
              <span className="analysis-label">{t('assistant.previewLabel')}</span>
              <h1 id="analysis-title">{document?.name || t('assistant.noDocumentTitle')}</h1>
              {document && <div className="document-meta"><span>{document.status}</span><span>{document.contentType}</span><span>{(document.sizeBytes / 1024).toFixed(1)} KB</span><Button variant="ghost" size="sm" loading={loadingMetadata} onClick={() => void loadMetadata()}>Metadata</Button></div>}
              {metadata && <dl className="assistant-document-metadata"><div><dt>Status</dt><dd>{metadata.status}</dd></div><div><dt>Version</dt><dd>{String(metadata.version || '-')}</dd></div><div><dt>Chunks</dt><dd>{metadata.chunk_count ?? '-'}</dd></div><div><dt>Source</dt><dd title={metadata.source_path || metadata.source}>{metadata.source_path || metadata.source || '-'}</dd></div><div><dt>Hash</dt><dd title={metadata.hash || String(metadata.metadata?.source_hash || '')}>{metadata.hash || String(metadata.metadata?.source_hash || '-')}</dd></div></dl>}
              {loadingPreview && <div className="preview-state"><span className="system-loader" /> {t('assistant.loadingPreview')}</div>}
              {!loadingPreview && preview?.preview_available && <pre className="document-preview-text">{preview.preview}</pre>}
              {!loadingPreview && document && preview && !preview.preview_available && <div className="preview-placeholder"><FileSearch size={34} /><strong>{t('assistant.noPreviewTitle')}</strong><span>{t('assistant.noPreviewBody')}</span></div>}
              {!document && <div className="preview-placeholder"><FileText size={34} /><strong>{t('assistant.emptyWorkspace')}</strong></div>}
            </div>
          </section>

          <section className="analysis-insights">
            <header><i><Sparkles size={22} /></i><div><h2>{t('assistant.insightsTitle')}</h2><span>{t('assistant.workspaceSource')}</span></div></header>
            <div className="analysis-insight-body">
              <div className="analysis-runtime">
                <article><span>{t('assistant.workspaces')}</span><strong>{workspace.name}</strong></article>
                <article><span>{t('assistant.searchMode')}</span><strong>{bootstrap.capabilities.bm25_enabled ? t('assistant.keywordContent') : t('assistant.contentOnly')}</strong></article>
                <article><span>{t('project.documents')}</span><strong>{documents.length}</strong></article>
              </div>
              <div className="project-conversation project-conversation--analysis" aria-live="polite">
                {!chatTurns.length && !asking && <div className="analysis-empty"><Bot size={30} /><strong>{t('assistant.askTitle')}</strong><p>{t('assistant.askBody')}</p></div>}
                {chatTurns.map((turn) => (
                  <article className="chat-turn" key={turn.id}>
                    <div className="user-message-stack">
                      <div className="user-message">{turn.question}</div>
                    </div>
                    {turn.pending && <div className="ai-thinking"><span className="system-loader" /> {t('assistant.thinking')}</div>}
                    {turn.error && <p className="ai-error" role="alert">{turn.error}</p>}
                    {turn.toolExecution && turn.toolExecution.result_type !== 'answer' && <ToolResultRenderer execution={turn.toolExecution} onGetEvidence={(evidenceId) => { void readEvidence(evidenceId) }} />}
                    {turn.answer && <article className="analysis-answer"><span>{t('assistant.responseLabel')}</span><p>{turn.answer.answer}</p><CitationEvidenceList titleKey="assistant.references" citations={turn.answer.citations} confidence={turn.answer.confidence} /><div className="answer-feedback" aria-label="Answer feedback"><span>Helpful?</span><button type="button" className={turn.feedback === 'positive' ? 'is-selected' : ''} disabled={turn.feedback !== null} onClick={() => { void sendFeedback(turn.id, turn.answer!.answer_id, 'positive') }}><ThumbsUp size={13} /></button><button type="button" className={turn.feedback === 'negative' ? 'is-selected' : ''} disabled={turn.feedback !== null} onClick={() => { void sendFeedback(turn.id, turn.answer!.answer_id, 'negative') }}><ThumbsDown size={13} /></button></div></article>}
                  </article>
                ))}
                <div ref={conversationEndRef} />
              </div>
              <AssistantToolComposer
                className="analysis-composer"
                tools={assistantTools}
                value={question}
                selectedTool={selectedTool}
                asking={asking}
                textareaId="analysis-question"
                placeholder={t('assistant.questionPlaceholder')}
                topContent={<label htmlFor="analysis-question"><Gauge size={16} /> {t('assistant.questionLabel')}</label>}
                actions={<div className="assistant-composer-actions"><Button variant="primary" size="icon" type="submit" disabled={!question.trim()} aria-label={t('assistant.sendQuestion')} leadingIcon={<Send />} /></div>}
                onChange={setQuestion}
                onSubmit={submitAssistantQuestion}
                onSelectTool={selectAssistantTool}
                onRemoveTool={() => setSelectedTool(null)}
              />
            </div>
          </section>
        </div>
      </main>

    </section>
  )
}
