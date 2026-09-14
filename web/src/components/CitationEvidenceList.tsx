import { useTranslation } from 'react-i18next'

import type { Citation, QueryConfidence } from '../types'

type CitationEvidenceListProps = {
  titleKey: string
  citations: Citation[]
  confidence?: QueryConfidence
}

function confidencePercent(score: number | null | undefined): string | null {
  if (typeof score !== 'number' || Number.isNaN(score)) return null
  return `${Math.round(Math.max(0, Math.min(score, 1)) * 100)}%`
}

function citationEvidence(citation: Citation): string {
  return citation.evidence || citation.snippet || citation.content || ''
}

function citationSource(citation: Citation, fallback: string): string {
  return citation.source_label || citation.file_path || citation.source_path || citation.document_id || citation.reference_id || fallback
}

function confidenceClass(label: string | undefined): string {
  return `is-${(label || 'none').toLowerCase()}`
}

export function CitationEvidenceList({ titleKey, citations, confidence }: CitationEvidenceListProps) {
  const { t } = useTranslation()
  const answerScore = confidencePercent(confidence?.score)
  if (!citations.length && !confidence) return null

  return (
    <div className="citation-list">
      <div className="citation-list-heading">
        <strong>{t(titleKey)}</strong>
        {confidence && (
          <span className={`answer-confidence ${confidenceClass(confidence.label)}`} title={confidence.rationale}>
            {t('confidence.answer')}: {answerScore
              ? `${answerScore} / ${t(`confidence.${confidence.label}`, { defaultValue: confidence.label })}`
              : t('confidence.unscored')}
          </span>
        )}
      </div>
      {citations.map((citation, index) => {
        const evidence = citationEvidence(citation)
        const score = typeof citation.confidence_score === 'number' ? citation.confidence_score : citation.confidence
        return (
          <article className="citation-card" key={`${citation.file_path || citation.source_path || citation.reference_id || index}-${index}`}>
            <div className="citation-card-source">
              <span>{citationSource(citation, t('citation.fallbackSource'))}</span>
              {confidencePercent(score) && (
                <i className={confidenceClass(citation.confidence_label)}>
                  {t('confidence.source')}: {confidencePercent(score)}
                </i>
              )}
            </div>
            {evidence && <blockquote>{evidence}</blockquote>}
          </article>
        )
      })}
    </div>
  )
}
