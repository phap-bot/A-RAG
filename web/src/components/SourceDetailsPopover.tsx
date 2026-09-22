import { useEffect } from 'react'
import { X } from 'lucide-react'

import type { Citation } from '../types'

type SourceDetailsPopoverProps = {
  citation: Citation | null
  open: boolean
  onClose: () => void
}

function evidenceFor(citation: Citation): string {
  return citation.evidence || citation.content || citation.snippet || 'Không có nội dung nguồn.'
}

function sourceFor(citation: Citation): string {
  return citation.source_label || citation.file_path || citation.source_path || citation.document_id || citation.reference_id || 'Nguồn được truy xuất'
}

function scoreFor(citation: Citation): string {
  const score = typeof citation.confidence_score === 'number' ? citation.confidence_score : citation.confidence
  if (typeof score !== 'number' || Number.isNaN(score)) return 'Chưa có điểm'
  const bounded = Math.max(0, Math.min(score, 1))
  return `${score} (${(bounded * 100).toFixed(2)}%)`
}

function sectionPathFor(citation: Citation): string[] {
  if (citation.section_path?.length) return citation.section_path
  const metadataPath = citation.metadata?.section_path
  return Array.isArray(metadataPath) ? metadataPath.filter((item): item is string => typeof item === 'string') : []
}

export function SourceDetailsPopover({ citation, open, onClose }: SourceDetailsPopoverProps) {
  useEffect(() => {
    if (!open) return undefined
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose, open])

  if (!open || !citation) return null

  const sectionPath = sectionPathFor(citation)
  const parentHeader = citation.parent_header || (typeof citation.metadata?.parent_header === 'string' ? citation.metadata.parent_header : '')
  const pages = citation.page_numbers?.length ? citation.page_numbers.join(', ') : null

  return (
    <div className="source-details-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="source-details-popover" role="dialog" aria-modal="true" aria-labelledby="source-details-title">
        <header className="source-details-header">
          <div className="source-details-heading">
            <span className="source-details-kicker">Nguồn tham chiếu</span>
            <h2 id="source-details-title" title={sourceFor(citation)}>{sourceFor(citation)}</h2>
          </div>
          <button type="button" className="source-details-close" aria-label="Đóng chi tiết nguồn" onClick={onClose}>
            <X size={16} aria-hidden="true" />
          </button>
        </header>

        <dl className="source-details-meta">
          <div>
            <dt>Điểm retrieval</dt>
            <dd>{scoreFor(citation)}</dd>
          </div>
          {sectionPath.length > 0 && (
            <div>
              <dt>Section</dt>
              <dd>{sectionPath.join(' › ')}</dd>
            </div>
          )}
          {parentHeader && (
            <div>
              <dt>Header cha</dt>
              <dd>{parentHeader}</dd>
            </div>
          )}
          {pages && (
            <div>
              <dt>Trang</dt>
              <dd>{pages}</dd>
            </div>
          )}
          {citation.modality && (
            <div>
              <dt>Loại nội dung</dt>
              <dd>{citation.modality}</dd>
            </div>
          )}
        </dl>

        <div className="source-details-content">
          <span className="source-details-kicker">Nội dung đầy đủ</span>
          <pre><code>{evidenceFor(citation)}</code></pre>
        </div>
      </section>
    </div>
  )
}
