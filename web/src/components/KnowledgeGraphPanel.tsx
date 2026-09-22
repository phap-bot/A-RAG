import { useEffect, useMemo, useRef, useState, type PointerEvent, type WheelEvent } from 'react'
import { Database, Download, GitBranch, RefreshCw, RotateCcw, Share2, ZoomIn, ZoomOut } from 'lucide-react'

import { Button } from './ui/Button'
import type { KnowledgeGraph } from '../types'

type KnowledgeGraphPanelProps = {
  graph: KnowledgeGraph | null
  loading: boolean
  onRefresh: () => void
  onDownload: () => void
}

type GraphPosition = { x: number; y: number }
type PanPosition = { x: number; y: number }
type PanGesture = {
  pointerId: number
  startX: number
  startY: number
  originX: number
  originY: number
  active: boolean
}

const GRAPH_WIDTH = 820
const GRAPH_HEIGHT = 430

function displayLabel(value: string, maxLength = 25): string {
  return value.length > maxLength ? `${value.slice(0, maxLength - 1)}...` : value
}

function graphPositions(graph: KnowledgeGraph | null): Map<string, GraphPosition> {
  const positions = new Map<string, GraphPosition>()
  const nodes = graph?.nodes || []
  if (!nodes.length) return positions

  const centerX = GRAPH_WIDTH / 2
  const centerY = GRAPH_HEIGHT / 2
  const radius = Math.min(158, Math.max(85, 34 + nodes.length * 7))
  nodes.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / nodes.length - Math.PI / 2
    positions.set(node.id, {
      x: centerX + Math.cos(angle) * radius,
      y: centerY + Math.sin(angle) * radius,
    })
  })
  return positions
}

function attributeText(attributes: Record<string, unknown>, key: string): string {
  const value = attributes[key]
  return typeof value === 'string' ? value : ''
}

export function KnowledgeGraphPanel({
  graph,
  loading,
  onRefresh,
  onDownload,
}: KnowledgeGraphPanelProps) {
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState<PanPosition>({ x: 0, y: 0 })
  const [isPanning, setIsPanning] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const panGesture = useRef<PanGesture | null>(null)
  const didPan = useRef(false)
  const canvasRef = useRef<SVGSVGElement | null>(null)
  const positions = useMemo(() => graphPositions(graph), [graph])
  const visibleEdges = (graph?.edges || []).filter((edge) => positions.has(edge.source) && positions.has(edge.target))
  const hasGraph = Boolean(graph && graph.node_count > 0)
  const selectedNode = graph?.nodes.find((node) => node.id === selectedNodeId) || null
  const relatedEdges = selectedNodeId
    ? visibleEdges.filter((edge) => edge.source === selectedNodeId || edge.target === selectedNodeId)
    : []
  const relatedNodeIds = new Set<string>(selectedNodeId ? [selectedNodeId] : [])
  relatedEdges.forEach((edge) => {
    relatedNodeIds.add(edge.source)
    relatedNodeIds.add(edge.target)
  })

  useEffect(() => {
    setZoom(1)
    setPan({ x: 0, y: 0 })
    setSelectedNodeId(null)
  }, [graph?.edge_count, graph?.node_count, graph?.workspace_id])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !hasGraph) return
    const lockCanvasWheel = (event: Event) => {
      event.preventDefault()
      event.stopPropagation()
    }
    canvas.addEventListener('wheel', lockCanvasWheel, { passive: false })
    return () => canvas.removeEventListener('wheel', lockCanvasWheel)
  }, [hasGraph])

  function clampPan(position: PanPosition, zoomValue = zoom): PanPosition {
    const horizontalLimit = Math.max(70, GRAPH_WIDTH * Math.max(0, zoomValue - 1) / 2 + 70)
    const verticalLimit = Math.max(50, GRAPH_HEIGHT * Math.max(0, zoomValue - 1) / 2 + 50)
    return {
      x: Math.min(horizontalLimit, Math.max(-horizontalLimit, position.x)),
      y: Math.min(verticalLimit, Math.max(-verticalLimit, position.y)),
    }
  }

  function updateZoom(delta: number) {
    const next = Math.min(2.4, Math.max(.55, Number((zoom + delta).toFixed(2))))
    setZoom(next)
    setPan((currentPan) => clampPan(currentPan, next))
  }

  function handleCanvasWheel(event: WheelEvent<SVGSVGElement>) {
    event.preventDefault()
    event.stopPropagation()
    updateZoom(event.deltaY < 0 ? .12 : -.12)
  }

  function handlePointerDown(event: PointerEvent<SVGSVGElement>) {
    if (event.button !== 0) return
    const target = event.target instanceof Element ? event.target : null
    if (target?.closest('.knowledge-graph-node')) return
    event.preventDefault()
    event.stopPropagation()
    panGesture.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: pan.x,
      originY: pan.y,
      active: false,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function handlePointerMove(event: PointerEvent<SVGSVGElement>) {
    const gesture = panGesture.current
    if (!gesture || gesture.pointerId !== event.pointerId) return
    const bounds = event.currentTarget.getBoundingClientRect()
    const scaleX = GRAPH_WIDTH / Math.max(1, bounds.width)
    const scaleY = GRAPH_HEIGHT / Math.max(1, bounds.height)
    const deltaX = (event.clientX - gesture.startX) * scaleX
    const deltaY = (event.clientY - gesture.startY) * scaleY
    if (!gesture.active && Math.hypot(deltaX, deltaY) < 4) return
    gesture.active = true
    didPan.current = true
    setIsPanning(true)
    setPan(clampPan({ x: gesture.originX + deltaX, y: gesture.originY + deltaY }))
  }

  function handlePointerUp(event: PointerEvent<SVGSVGElement>) {
    const gesture = panGesture.current
    if (!gesture || gesture.pointerId !== event.pointerId) return
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    panGesture.current = null
    setIsPanning(false)
  }

  function handleCanvasClick() {
    if (didPan.current) {
      didPan.current = false
      return
    }
    setSelectedNodeId(null)
  }

  function toggleNode(nodeId: string) {
    setSelectedNodeId((current) => current === nodeId ? null : nodeId)
  }

  return (
    <section className="knowledge-graph-panel" aria-labelledby="knowledge-graph-title">
      <header className="knowledge-graph-header">
        <div className="knowledge-graph-heading">
          <span className="knowledge-graph-eyebrow"><Share2 size={14} /> NETWORKX GRAPH</span>
          <h2 id="knowledge-graph-title">Knowledge Graph trực tiếp</h2>
          <p>Hiển thị node và edge đọc trực tiếp từ GraphML của workspace, không phải dữ liệu mock từ frontend.</p>
        </div>
        <div className="knowledge-graph-actions">
          <Button variant="secondary" size="sm" leadingIcon={<RefreshCw size={14} />} loading={loading} onClick={onRefresh}>Làm mới</Button>
          <Button variant="primary" size="sm" leadingIcon={<Download size={14} />} disabled={!graph} onClick={onDownload}>Tải GraphML</Button>
        </div>
      </header>

      {graph && (
        <>
          <div className="knowledge-graph-proof-banner">
            <Database size={16} />
            <div>
              <strong>{graph.storage} · {graph.format}</strong>
              <span>{hasGraph ? 'Graph đã có dữ liệu thật từ backend.' : 'Graph chưa có node. Hãy ingest thành công trước khi kiểm tra.'}</span>
            </div>
            <code title={graph.graphml_path}>{graph.graphml_path}</code>
          </div>

          <div className="knowledge-graph-metrics" aria-label="Graph statistics">
            <article><span>Nodes</span><strong>{graph.node_count}</strong><small>Entity trong NetworkX</small></article>
            <article><span>Edges</span><strong>{graph.edge_count}</strong><small>Quan hệ đã trích xuất</small></article>
            <article><span>Components</span><strong>{graph.connected_components}</strong><small>Cụm liên kết</small></article>
            <article><span>Density</span><strong>{graph.density.toFixed(4)}</strong><small>Mật độ graph</small></article>
          </div>

          <div className="knowledge-graph-workbench">
            <div className="knowledge-graph-canvas-card">
              <div className="knowledge-graph-card-heading">
                <div><span>NETWORK VIEW</span><h3>Node và quan hệ</h3></div>
                <div className="knowledge-graph-controls" aria-label="Điều khiển thu phóng graph">
                  <button type="button" aria-label="Thu nhỏ graph" title="Thu nhỏ" onClick={() => updateZoom(-.15)} disabled={zoom <= .55}><ZoomOut size={14} /></button>
                  <span>{Math.round(zoom * 100)}%</span>
                  <button type="button" aria-label="Phóng to graph" title="Phóng to" onClick={() => updateZoom(.15)} disabled={zoom >= 2.4}><ZoomIn size={14} /></button>
                  <button type="button" aria-label="Đặt lại graph" title="Đặt lại" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }) }} disabled={zoom === 1 && pan.x === 0 && pan.y === 0}><RotateCcw size={13} /></button>
                </div>
              </div>
              {hasGraph ? (
                <svg ref={canvasRef} className={`knowledge-graph-canvas${isPanning ? ' is-panning' : ''}`} viewBox={`0 0 ${GRAPH_WIDTH} ${GRAPH_HEIGHT}`} role="img" aria-label="NetworkX knowledge graph visualization" onWheelCapture={handleCanvasWheel} onPointerDown={handlePointerDown} onPointerMove={handlePointerMove} onPointerUp={handlePointerUp} onPointerCancel={handlePointerUp} onDragStart={(event) => event.preventDefault()} onClick={handleCanvasClick}>
                  <defs>
                    <marker id="knowledge-graph-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
                      <path d="M0,0 L0,6 L7,3 z" fill="rgba(93, 190, 241, .78)" />
                    </marker>
                    <clipPath id="knowledge-graph-viewport">
                      <rect x="0" y="0" width={GRAPH_WIDTH} height={GRAPH_HEIGHT} rx="11" />
                    </clipPath>
                  </defs>
                  <g clipPath="url(#knowledge-graph-viewport)" transform={`translate(${GRAPH_WIDTH / 2} ${GRAPH_HEIGHT / 2}) scale(${zoom}) translate(${-GRAPH_WIDTH / 2} ${-GRAPH_HEIGHT / 2})`}>
                    <circle className="knowledge-graph-orbit" cx={GRAPH_WIDTH / 2} cy={GRAPH_HEIGHT / 2} r="170" />
                    {visibleEdges.map((edge, index) => {
                      const source = positions.get(edge.source)
                      const target = positions.get(edge.target)
                      if (!source || !target) return null
                      const related = !selectedNodeId || edge.source === selectedNodeId || edge.target === selectedNodeId
                      return <line key={`${edge.source}-${edge.target}-${index}`} className={`knowledge-graph-edge ${selectedNodeId ? (related ? 'is-related' : 'is-muted') : ''}`} x1={source.x} y1={source.y} x2={target.x} y2={target.y} markerEnd={graph.is_directed ? 'url(#knowledge-graph-arrow)' : undefined} />
                    })}
                    {graph.nodes.map((node, index) => {
                      const position = positions.get(node.id)
                      if (!position) return null
                      const nodeSize = Math.min(18, 9 + node.degree * 2)
                      const selected = selectedNodeId === node.id
                      const related = relatedNodeIds.has(node.id)
                      return (
                        <g
                          className={`knowledge-graph-node ${selectedNodeId ? (selected ? 'is-selected' : related ? 'is-connected' : 'is-muted') : ''}`}
                          key={node.id}
                          role="button"
                          tabIndex={0}
                          aria-label={`Chọn entity ${node.id}, ${node.degree} liên kết`}
                          aria-pressed={selected}
                          onClick={(event) => {
                            event.stopPropagation()
                            if (didPan.current) {
                              didPan.current = false
                              return
                            }
                            toggleNode(node.id)
                          }}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault()
                              toggleNode(node.id)
                            }
                          }}
                        >
                          <title>{`${node.id} · degree ${node.degree}`}</title>
                          <circle cx={position.x} cy={position.y} r={nodeSize} style={{ animationDelay: `${index * 45}ms` }} />
                          <text x={position.x} y={position.y + nodeSize + 18} textAnchor="middle">{displayLabel(node.id)}</text>
                        </g>
                      )
                    })}
                  </g>
                </svg>
              ) : (
                <div className="knowledge-graph-empty"><Share2 size={28} /><strong>Chưa có node hoặc edge</strong><span>GraphML đã được kiểm tra nhưng chưa có entity để vẽ.</span></div>
              )}
              {selectedNode && (
                <div className="knowledge-graph-selection" role="status" aria-live="polite">
                  <div>
                    <span>ENTITY ĐANG CHỌN</span>
                    <strong title={selectedNode.id}>{selectedNode.id}</strong>
                    <small>{attributeText(selectedNode.attributes, 'entity_type') || 'entity'} · degree {selectedNode.degree}</small>
                  </div>
                  <b>{relatedEdges.length}<small>liên kết trực tiếp</small></b>
                  <button type="button" onClick={() => setSelectedNodeId(null)}>Bỏ chọn</button>
                </div>
              )}
            </div>

            <aside className="knowledge-graph-detail-card">
              <div className="knowledge-graph-card-heading"><div><span>RELATION PROOF</span><h3>Edges đã lưu</h3></div><span className="knowledge-graph-count">{visibleEdges.length}</span></div>
              {visibleEdges.length ? (
                <div className="knowledge-graph-edge-list">
                  {visibleEdges.map((edge, index) => {
                    const related = !selectedNodeId || edge.source === selectedNodeId || edge.target === selectedNodeId
                    return <article className={selectedNodeId ? (related ? 'is-related' : 'is-muted') : ''} key={`${edge.source}-${edge.target}-${index}`}>
                      <strong>{edge.source}</strong><i aria-hidden="true">{graph.is_directed ? '→' : '↔'}</i><strong>{edge.target}</strong>
                      {(attributeText(edge.attributes, 'description') || attributeText(edge.attributes, 'keywords')) && <small>{attributeText(edge.attributes, 'description') || attributeText(edge.attributes, 'keywords')}</small>}
                    </article>
                  })}
                </div>
              ) : <p className="knowledge-graph-muted">Chưa có edge để hiển thị.</p>}
              {graph.top_degree_nodes.length > 0 && <div className="knowledge-graph-top-nodes"><span>ENTITY NỔI BẬT</span>{graph.top_degree_nodes.slice(0, 5).map((node) => <b key={node.id}>{node.id}<em>{node.degree}</em></b>)}</div>}
            </aside>
          </div>
        </>
      )}

      {!graph && !loading && <div className="knowledge-graph-empty knowledge-graph-empty--page"><Share2 size={30} /><strong>Chưa tải dữ liệu NetworkX</strong><span>Nhấn làm mới để đọc GraphML từ backend.</span><Button variant="primary" size="sm" leadingIcon={<RefreshCw size={14} />} onClick={onRefresh}>Đọc graph</Button></div>}
    </section>
  )
}
