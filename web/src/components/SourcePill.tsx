import { useId, useState } from 'react'
import { FileText } from 'lucide-react'

type SourcePillProps = {
  fileName: string
  citationIndex?: number
  sourceName?: string
  retrievalScore?: number
  snippet?: string
  onClick?: () => void
}

function retrievalLabel(score: number | undefined): string {
  if (typeof score !== 'number' || Number.isNaN(score)) return 'Độ liên quan: Chưa có điểm'
  const bounded = Math.max(0, Math.min(score, 1))
  return `Độ liên quan: ${(bounded * 100).toFixed(2)}%`
}

/**
 * Compact source trigger for an answer citation.
 *
 * The pill keeps the answer surface scannable while its hover preview exposes
 * enough context to identify a source before the full detail modal is opened.
 */
export function SourcePill({ fileName, citationIndex, sourceName, retrievalScore, snippet, onClick }: SourcePillProps) {
  const [previewOpen, setPreviewOpen] = useState(false)
  const previewId = useId()

  return (
    <span
      className="source-pill-shell"
      onMouseEnter={() => setPreviewOpen(true)}
      onMouseLeave={() => setPreviewOpen(false)}
      onFocus={() => setPreviewOpen(true)}
      onBlur={() => setPreviewOpen(false)}
    >
      <button
        type="button"
        className="source-pill"
        title={sourceName || fileName}
        aria-label={`Mở nguồn ${sourceName || fileName}`}
        aria-describedby={previewOpen ? previewId : undefined}
        onClick={onClick}
      >
        <FileText className="source-pill-icon" size={11} strokeWidth={2.2} aria-hidden="true" />
        {citationIndex !== undefined && <span className="source-pill-index">[{citationIndex}]</span>}
        <span className="source-pill-label">{fileName}</span>
      </button>
      {previewOpen && (
        <span id={previewId} className="source-pill-preview" role="tooltip">
          <strong>{sourceName || fileName}</strong>
          <b>{retrievalLabel(retrievalScore)}</b>
          <span>{snippet?.trim() || 'Không có đoạn trích nguồn.'}</span>
        </span>
      )}
    </span>
  )
}
