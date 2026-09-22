import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Copy, FileJson, FileText, Maximize2, Minus, Plus, ScanSearch } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import * as pdfjsLib from 'pdfjs-dist'

import { fetchDocumentContent, getDocumentReview } from '../api'
import type { DocumentReview, DocumentReviewElement, DocumentRow } from '../types'

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString()

type DocumentReviewWorkbenchProps = {
  workspaceId: string
  document: DocumentRow
  onClose: () => void
}

type OriginalContent = {
  blob: Blob
  text: string | null
}

const elementColors: Record<string, string> = {
  title: '#60a5fa',
  header: '#60a5fa',
  paragraph: '#34d399',
  text: '#34d399',
  table: '#fbbf24',
  image: '#f472b6',
  list: '#c084fc',
  code: '#fb923c',
}

function elementColor(type: string): string {
  return elementColors[type.toLowerCase()] || '#38bdf8'
}

function safeJson(review: DocumentReview): string {
  return JSON.stringify({
    document_id: review.document_id,
    file_name: review.name,
    file_type: review.content_type,
    total_pages: review.total_pages,
    stats: review.stats,
    elements: review.elements,
  }, null, 2)
}

export function DocumentReviewWorkbench({ workspaceId, document, onClose }: DocumentReviewWorkbenchProps) {
  const { t } = useTranslation()
  const [review, setReview] = useState<DocumentReview | null>(null)
  const [content, setContent] = useState<OriginalContent | null>(null)
  const [page, setPage] = useState(1)
  const [zoom, setZoom] = useState(1)
  const [activeElementId, setActiveElementId] = useState<string | null>(null)
  const [showBoxes, setShowBoxes] = useState(true)
  const [parsedTab, setParsedTab] = useState<'markdown' | 'json'>('markdown')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const parsedElementRefs = useRef(new Map<string, HTMLButtonElement>())
  const [pdfPageCount, setPdfPageCount] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setReview(null)
    setContent(null)
    setPage(1)
    setZoom(1)
    setActiveElementId(null)
    Promise.all([
      getDocumentReview(workspaceId, document.id),
      fetchDocumentContent(workspaceId, document.id).then(async (blob) => ({ blob, text: blob.type.includes('text') || blob.type.includes('json') || blob.type.includes('csv') ? await blob.text() : null })),
    ]).then(([nextReview, nextContent]) => {
      if (cancelled) return
      setReview(nextReview)
      setContent(nextContent)
    }).catch((reason) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : t('review.loadError'))
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [document.id, t, workspaceId])

  const isPdf = document.contentType === 'application/pdf' || document.name.toLowerCase().endsWith('.pdf')
  const isImage = document.contentType.startsWith('image/')
  const pageElements = useMemo(
    () => review?.pages.find((item) => item.page_number === page)?.elements || [],
    [page, review],
  )
  const totalPages = Math.max(review?.total_pages || 1, pdfPageCount || 1)

  useEffect(() => {
    if (!content || !isPdf || !canvasRef.current) return
    let cancelled = false
    const render = async () => {
      try {
        const pdf = await pdfjsLib.getDocument({ data: new Uint8Array(await content.blob.arrayBuffer()) }).promise
        if (cancelled) return
        setPdfPageCount(pdf.numPages)
        const pdfPage = await pdf.getPage(page)
        const viewport = pdfPage.getViewport({ scale: 1.05 * zoom })
        const canvas = canvasRef.current
        if (!canvas || cancelled) return
        const context = canvas.getContext('2d')
        if (!context) return
        canvas.width = viewport.width
        canvas.height = viewport.height
        await pdfPage.render({ canvasContext: context, viewport }).promise
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : t('review.originalError'))
      }
    }
    void render()
    return () => { cancelled = true }
  }, [content, isPdf, page, t, zoom])

  useEffect(() => {
    if (!activeElementId) return
    parsedElementRefs.current.get(activeElementId)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [activeElementId, parsedTab])

  const imageUrl = useMemo(() => content && isImage ? URL.createObjectURL(content.blob) : null, [content, isImage])
  useEffect(() => () => { if (imageUrl) URL.revokeObjectURL(imageUrl) }, [imageUrl])

  function selectElement(element: DocumentReviewElement) {
    setActiveElementId(element.element_id)
    if (element.page_number !== page) setPage(element.page_number)
  }

  async function copyParsed() {
    if (!review) return
    const value = parsedTab === 'markdown' ? review.markdown : safeJson(review)
    await navigator.clipboard?.writeText(value)
  }

  function renderOriginal() {
    if (isPdf) return <canvas ref={canvasRef} className="review-original-canvas" aria-label={t('review.originalCanvas')} />
    if (isImage && imageUrl) return <img className="review-original-image" src={imageUrl} alt={document.name} />
    return <pre className="review-original-text">{content?.text || t('review.originalFallback')}</pre>
  }

  return (
    <section className="document-review-workbench" aria-label={t('review.workbenchLabel')}>
      <header className="review-workbench-header">
        <div className="review-file-heading">
          <FileText size={18} />
          <div><strong>{document.name}</strong><span>{document.contentType} · {document.status}</span></div>
        </div>
        <div className="review-header-actions">
          <button type="button" className="review-close-button" onClick={onClose}>{t('review.backToDocuments')}</button>
        </div>
      </header>

      {loading && <div className="review-state"><span className="system-loader" /> {t('review.loading')}</div>}
      {error && <div className="review-state review-state--error" role="alert">{error}</div>}
      {!loading && review && (
        <div className="review-workbench-grid">
          <section className="review-pane review-pane--original">
            <header className="review-pane-header">
              <div><span>{t('review.originalLabel')}</span><strong>{t('review.originalTitle')}</strong></div>
              <div className="review-pane-controls">
                <button type="button" onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={page <= 1} aria-label={t('review.previousPage')}><ChevronLeft size={15} /></button>
                <span>{page} / {totalPages}</span>
                <button type="button" onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={page >= totalPages} aria-label={t('review.nextPage')}><ChevronRight size={15} /></button>
                <button type="button" onClick={() => setZoom((current) => Math.max(.65, Number((current - .1).toFixed(2))))} aria-label={t('review.zoomOut')}><Minus size={14} /></button>
                <span>{Math.round(zoom * 100)}%</span>
                <button type="button" onClick={() => setZoom((current) => Math.min(2, Number((current + .1).toFixed(2))))} aria-label={t('review.zoomIn')}><Plus size={14} /></button>
              </div>
            </header>
            <div className="review-original-scroll">
              <div className="review-original-stage" style={{ transform: `scale(${isPdf ? 1 : zoom})` }}>
                {renderOriginal()}
                {showBoxes && pageElements.length > 0 && (
                  <div className="review-box-layer" aria-label={t('review.boundingBoxes')}>
                    {pageElements.map((element) => element.bounding_box && (
                      <button
                        key={element.element_id}
                        type="button"
                        className={`review-bounding-box${activeElementId === element.element_id ? ' is-active' : ''}`}
                        style={{ left: `${element.bounding_box.x1 * 100}%`, top: `${element.bounding_box.y1 * 100}%`, width: `${(element.bounding_box.x2 - element.bounding_box.x1) * 100}%`, height: `${(element.bounding_box.y2 - element.bounding_box.y1) * 100}%`, borderColor: elementColor(element.element_type), color: elementColor(element.element_type) }}
                        onClick={() => selectElement(element)}
                        onMouseEnter={() => setActiveElementId(element.element_id)}
                        aria-label={`${element.element_type}: ${element.content.slice(0, 80)}`}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
            <footer className="review-pane-footer">
              <label><input type="checkbox" checked={showBoxes} onChange={(event) => setShowBoxes(event.target.checked)} /> {t('review.showBoxes')}</label>
              <span><ScanSearch size={13} /> {review.has_bounding_boxes ? t('review.boxesDetected', { count: pageElements.filter((item) => item.bounding_box).length }) : t('review.noBoxes')}</span>
            </footer>
          </section>

          <section className="review-pane review-pane--parsed">
            <header className="review-pane-header">
              <div><span>{t('review.parsedLabel')}</span><strong>{t('review.parsedTitle')}</strong></div>
              <div className="review-parsed-actions">
                <div className="review-tabs" role="tablist">
                  <button type="button" className={parsedTab === 'markdown' ? 'is-active' : ''} onClick={() => setParsedTab('markdown')}><FileText size={13} /> Markdown</button>
                  <button type="button" className={parsedTab === 'json' ? 'is-active' : ''} onClick={() => setParsedTab('json')}><FileJson size={13} /> JSON</button>
                </div>
                <button type="button" className="review-icon-button" onClick={() => void copyParsed()} aria-label={t('review.copyParsed')} title={t('review.copyParsed')}><Copy size={15} /></button>
              </div>
            </header>
            <div className="review-parsed-scroll">
              {parsedTab === 'json' ? (
                <pre className="review-json">{safeJson(review)}</pre>
              ) : (
                <div className="review-markdown">
                  {review.elements.length ? review.elements.map((element) => (
                    <button
                      key={element.element_id}
                      type="button"
                      ref={(node) => { if (node) parsedElementRefs.current.set(element.element_id, node); else parsedElementRefs.current.delete(element.element_id) }}
                      className={`review-parsed-element review-parsed-element--${element.element_type}${activeElementId === element.element_id ? ' is-active' : ''}`}
                      onClick={() => selectElement(element)}
                      onMouseEnter={() => setActiveElementId(element.element_id)}
                    >
                      <span className="review-element-meta"><b style={{ color: elementColor(element.element_type) }}>{element.element_type}</b><span>p.{element.page_number}</span><span>{Math.round(element.confidence * 100)}%</span></span>
                      <span className="review-element-content">{element.content}</span>
                    </button>
                  )) : <pre className="review-json">{review.markdown || t('review.originalFallback')}</pre>}
                </div>
              )}
            </div>
          </section>
        </div>
      )}
    </section>
  )
}
