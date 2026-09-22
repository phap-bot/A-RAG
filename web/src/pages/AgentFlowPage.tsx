import { FormEvent, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Play, RefreshCw } from 'lucide-react'

import { getDocuments, getIngestionEvents, getIngestionStatus, streamKnowledgeBase } from '../api'
import { AgentFlow } from '../components/AgentFlow'
import { Button } from '../components/ui/Button'
import type { AgentStreamEvent, DocumentRow, IngestionFlowEvent, IngestionStatus, UiBootstrap, WorkspaceRecord } from '../types'

type AgentFlowPageProps = {
  bootstrap: UiBootstrap
  workspace: WorkspaceRecord
  documents: DocumentRow[]
  onBack: () => void
}

function flowNodeFromEvent(event: AgentStreamEvent): string | null {
  if (event.event === 'agent_thought') return event.node === 'agent_main' ? 'main' : event.node
  if (event.event === 'tool_start' || event.event === 'tool_result') {
    const handoff = event.tool_name?.replace('handoff_to_', '')
    if (handoff === 'main') return 'main'
    if (handoff === 'query_formulator' || handoff === 'parallel_retriever' || handoff === 'synthesizer' || handoff === 'critic_reflection') {
      return handoff
    }
    return 'tools'
  }
  if (event.event === 'message_chunk') return 'synthesizer'
  if (event.event === 'final_response' && event.done) return 'final'
  return null
}

export function AgentFlowPage({ bootstrap, workspace, documents, onBack }: AgentFlowPageProps) {
  const [documentList, setDocumentList] = useState(documents)
  const [selectedDocumentId, setSelectedDocumentId] = useState(documents[0]?.id || '')
  const [ingestion, setIngestion] = useState<IngestionStatus | null>(null)
  const [ingestionEvents, setIngestionEvents] = useState<IngestionFlowEvent[]>([])
  const ingestionCursor = useRef(0)
  const [question, setQuestion] = useState('')
  const [queryActiveNode, setQueryActiveNode] = useState<string | null>(null)
  const [queryRunning, setQueryRunning] = useState(false)
  const [queryCompleted, setQueryCompleted] = useState(false)
  const [queryFailed, setQueryFailed] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setDocumentList(documents)
    if (!documents.some((document) => document.id === selectedDocumentId)) {
      setSelectedDocumentId(documents[0]?.id || '')
    }
  }, [documents, selectedDocumentId])

  useEffect(() => {
    if (!selectedDocumentId) {
      setIngestion(null)
      setIngestionEvents([])
      ingestionCursor.current = 0
      return
    }
    let cancelled = false
    const refresh = async () => {
      try {
        const next = await getIngestionStatus(workspace.workspace_id, { documentId: selectedDocumentId })
        if (cancelled) return
        setIngestion(next)
        if (next.job_id) {
          const eventResponse = await getIngestionEvents(workspace.workspace_id, next.job_id, ingestionCursor.current)
          if (!cancelled && eventResponse.events.length > 0) {
            setIngestionEvents((current) => [...current, ...eventResponse.events])
            ingestionCursor.current = eventResponse.next_after
          }
        }
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Không thể đọc trạng thái ingestion')
      }
    }
    void refresh()
    const interval = ingestion?.status === 'processing'
      ? window.setInterval(() => void refresh(), 2500)
      : null
    return () => {
      cancelled = true
      if (interval !== null) window.clearInterval(interval)
    }
  }, [ingestion?.status, selectedDocumentId, workspace.workspace_id])

  async function refreshDocuments() {
    try {
      setDocumentList(await getDocuments(workspace.workspace_id))
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Không thể tải tài liệu')
    }
  }

  async function runQuery(event: FormEvent) {
    event.preventDefault()
    const trimmed = question.trim()
    if (!trimmed || queryRunning) return
    setQueryRunning(true)
    setQueryCompleted(false)
    setQueryFailed(false)
    setQueryActiveNode('main')
    setError(null)
    try {
      const selectedDocument = documentList.find((document) => document.id === selectedDocumentId)
      await streamKnowledgeBase(
        trimmed,
        workspace.workspace_id,
        { filePaths: selectedDocument?.sourcePath ? [selectedDocument.sourcePath] : [] },
        (streamEvent) => {
          if (streamEvent.event === 'error') {
            setQueryFailed(true)
            return
          }
          const node = flowNodeFromEvent(streamEvent)
          if (node) setQueryActiveNode(node)
        },
      )
      setQueryActiveNode('final')
      setQueryCompleted(true)
    } catch (reason) {
      setQueryFailed(true)
      setError(reason instanceof Error ? reason.message : 'Agentic RAG query failed')
    } finally {
      setQueryRunning(false)
    }
  }

  const ingestionStage = ingestion?.flow_stage
    || (ingestion?.status === 'indexed' ? 'completed' : ingestion?.status === 'failed' ? 'failed' : null)

  return (
    <section className="page page-agent-flow" aria-labelledby="agent-flow-title">
      <div className="agent-flow-page-shell">
        <header className="agent-flow-page-header">
          <div>
            <Button variant="ghost" size="icon" aria-label="Quay lại workspace" leadingIcon={<ArrowLeft />} onClick={onBack} />
            <div>
              <span className="agent-flow-page-eyebrow">RUNTIME VISUALIZER</span>
              <h1 id="agent-flow-title">Agent execution flow</h1>
              <p>{workspace.name} · {bootstrap.brand.product}</p>
            </div>
          </div>
          <span className="agent-flow-page-status"><i /> Live flow</span>
        </header>

        <main className="agent-flow-page-content">
          <AgentFlow
            ingestionStage={ingestionStage}
            ingestionRunning={ingestion?.status === 'processing'}
            ingestionFailed={ingestion?.status === 'failed'}
            ingestionEvents={ingestionEvents}
            queryActiveNode={queryActiveNode}
            queryRunning={queryRunning}
            queryCompleted={queryCompleted}
            queryFailed={queryFailed}
          />

          <section className="agent-flow-controls" aria-label="Flow controls">
            <div className="agent-flow-control-heading">
              <div>
                <span>LIVE RUN</span>
                <strong>Chọn một tác vụ để xem node chạy</strong>
              </div>
              <Button variant="ghost" size="sm" leadingIcon={<RefreshCw />} onClick={() => void refreshDocuments()}>Refresh</Button>
            </div>
            <div className="agent-flow-control-grid">
              <label>
                <span>Document ingestion</span>
                <select value={selectedDocumentId} onChange={(event) => setSelectedDocumentId(event.target.value)}>
                  <option value="">Chưa có tài liệu</option>
                  {documentList.map((document) => <option key={document.id} value={document.id}>{document.name} · {document.status}</option>)}
                </select>
              </label>
              <form onSubmit={runQuery}>
                <label htmlFor="agent-flow-query">Agentic RAG query</label>
                <div className="agent-flow-query-input">
                  <input id="agent-flow-query" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Nhập câu hỏi để chạy Query Formulator..." />
                  <Button variant="primary" size="sm" type="submit" disabled={queryRunning || !question.trim()} loading={queryRunning} leadingIcon={<Play />}>Run</Button>
                </div>
              </form>
            </div>
            {ingestion && <p className="agent-flow-status-line">Ingestion: <strong>{ingestion.status}</strong>{ingestion.chunk_count ? ` · ${ingestion.chunk_count} chunks` : ''}{ingestion.flow_event_count ? ` · ${ingestion.flow_event_count} events` : ''}</p>}
            {error && <p className="agent-flow-error" role="alert">{error}</p>}
          </section>
        </main>
      </div>
    </section>
  )
}
