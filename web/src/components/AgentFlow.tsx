import { Check, Circle, Database, FileSearch, GitBranch, LoaderCircle, Network, Sparkles } from 'lucide-react'
import type { IngestionFlowEvent } from '../types'

export type AgentFlowNodeState = 'idle' | 'running' | 'completed' | 'failed'

type FlowNode = {
  id: string
  label: string
  icon: typeof Circle
}

type AgentFlowProps = {
  ingestionStage?: string | null
  ingestionRunning?: boolean
  ingestionFailed?: boolean
  ingestionEvents?: IngestionFlowEvent[]
  queryActiveNode?: string | null
  queryRunning?: boolean
  queryCompleted?: boolean
  queryFailed?: boolean
}

const INGESTION_NODES: FlowNode[] = [
  { id: 'detect_type', label: 'Detect', icon: FileSearch },
  { id: 'extraction', label: 'Extract', icon: FileSearch },
  { id: 'quality_check', label: 'Quality', icon: Check },
  { id: 'normalize', label: 'Normalize', icon: Sparkles },
  { id: 'semantic_chunk', label: 'Chunk', icon: GitBranch },
  { id: 'entity_extraction', label: 'Entities', icon: Network },
  { id: 'embedding', label: 'Embed', icon: Sparkles },
  { id: 'graph_extraction', label: 'Graph', icon: Network },
  { id: 'validate', label: 'Validate', icon: Check },
  { id: 'neo4j_index', label: 'Neo4j', icon: Database },
]

const CHUNKING_NODES: FlowNode[] = [
  { id: 'chunking_plan', label: 'Plan', icon: Sparkles },
  { id: 'chunking_build', label: 'Build', icon: GitBranch },
  { id: 'chunking_validate', label: 'Validate', icon: Check },
]

const QUERY_NODES: FlowNode[] = [
  { id: 'main', label: 'Main', icon: Sparkles },
  { id: 'tools', label: 'Tools', icon: Network },
  { id: 'query_formulator', label: 'Formulator', icon: Sparkles },
  { id: 'parallel_retriever', label: 'Retriever', icon: Network },
  { id: 'synthesizer', label: 'Synthesizer', icon: Sparkles },
  { id: 'critic_reflection', label: 'Critic', icon: GitBranch },
  { id: 'final', label: 'Answer', icon: Check },
]

function ingestionState(
  nodeId: string,
  stage: string | null | undefined,
  running: boolean,
  failed: boolean,
  events: IngestionFlowEvent[],
): AgentFlowNodeState {
  const nodeEvents = events.filter((event) => event.node === nodeId)
  const latest = nodeEvents[nodeEvents.length - 1]
  if (latest?.event === 'node_completed') return latest.status === 'failed' ? 'failed' : 'completed'
  if (latest?.event === 'node_started') return 'running'
  if (failed && nodeId === stage) return 'failed'
  if (stage === 'completed' || (!running && stage === 'indexed')) return 'completed'
  return 'idle'
}

function queryState(
  nodeId: string,
  activeNode: string | null | undefined,
  running: boolean,
  completed: boolean,
  failed: boolean,
): AgentFlowNodeState {
  if (failed && nodeId === activeNode) return 'failed'
  if (completed) return 'completed'
  if (!running && !activeNode) return 'idle'
  if (nodeId === activeNode) return 'running'

  const order = QUERY_NODES.map((node) => node.id)
  const activeIndex = order.indexOf(activeNode || '')
  const nodeIndex = order.indexOf(nodeId)
  return activeIndex >= 0 && nodeIndex < activeIndex ? 'completed' : 'idle'
}

function FlowLane({
  label,
  nodes,
  getState,
  loop,
}: {
  label: string
  nodes: FlowNode[]
  getState: (nodeId: string) => AgentFlowNodeState
  loop?: boolean
}) {
  return (
    <section className="agent-flow-lane" aria-label={`${label} flow`}>
      <div className="agent-flow-lane-label">{label}</div>
      <div className="agent-flow-track">
        {nodes.map((node, index) => {
          const state = getState(node.id)
          const Icon = state === 'running' ? LoaderCircle : state === 'completed' ? Check : node.icon
          return (
            <div className="agent-flow-step" key={node.id}>
              <div className={`agent-flow-node is-${state}`}>
                <Icon size={14} className={state === 'running' ? 'agent-flow-spinner' : undefined} />
                <span>{node.label}</span>
              </div>
              {index < nodes.length - 1 && <div className={`agent-flow-arrow is-${state}`} aria-hidden="true"><i /></div>}
            </div>
          )
        })}
        {loop && <div className="agent-flow-loop" aria-hidden="true"><span>reflection loop</span></div>}
      </div>
    </section>
  )
}

export function AgentFlow({
  ingestionStage,
  ingestionRunning = false,
  ingestionFailed = false,
  ingestionEvents = [],
  queryActiveNode,
  queryRunning = false,
  queryCompleted = false,
  queryFailed = false,
}: AgentFlowProps) {
  return (
    <section className="agent-flow-panel" aria-label="Agent execution flow">
      <header className="agent-flow-header">
        <div>
          <span className="agent-flow-eyebrow">AGENT FLOW</span>
          <strong>Orchestrators</strong>
        </div>
        <span className="agent-flow-live"><Circle size={7} fill="currentColor" /> live</span>
      </header>
      <FlowLane
        label="Ingestion"
        nodes={INGESTION_NODES}
        getState={(nodeId) => ingestionState(nodeId, ingestionStage, ingestionRunning, ingestionFailed, ingestionEvents)}
      />
      <FlowLane
        label="Chunking"
        nodes={CHUNKING_NODES}
        getState={(nodeId) => {
          return ingestionState(nodeId, ingestionStage, ingestionRunning, ingestionFailed, ingestionEvents)
        }}
      />
      <FlowLane
        label="Agentic RAG"
        nodes={QUERY_NODES}
        loop
        getState={(nodeId) => queryState(nodeId, queryActiveNode, queryRunning, queryCompleted, queryFailed)}
      />
    </section>
  )
}
