import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { SourceDetailsPopover } from './SourceDetailsPopover'
import { SourcePill } from './SourcePill'
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

function citationSource(citation: Citation, fallback: string): string {
  return citation.source_label || citation.file_path || citation.source_path || citation.document_id || citation.reference_id || fallback
}

function citationFileName(citation: Citation, fallback: string): string {
  const source = citationSource(citation, fallback).replace(/\\/g, '/')
  const parts = source.split('/').filter(Boolean)
  return parts[parts.length - 1] || fallback
}

function confidenceClass(label: string | undefined): string {
  return `is-${(label || 'none').toLowerCase()}`
}

export function CitationEvidenceList({ titleKey, citations, confidence }: CitationEvidenceListProps) {
  const { t } = useTranslation()
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null)
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
      {citations.length > 0 && (
        <div className="source-pill-row" aria-label={t(titleKey)}>
          {citations.map((citation, index) => {
            const sourceName = citationSource(citation, t('citation.fallbackSource'))
            const score = typeof citation.confidence_score === 'number' ? citation.confidence_score : citation.confidence
            const snippet = citation.evidence || citation.snippet || citation.content || ''
            return (
              <SourcePill
                key={`${citation.file_path || citation.source_path || citation.reference_id || index}-${index}`}
                citationIndex={index + 1}
                fileName={citationFileName(citation, t('citation.fallbackSource'))}
                sourceName={sourceName}
                retrievalScore={score}
                snippet={snippet}
                onClick={() => setSelectedCitation(citation)}
              />
            )
          })}
        </div>
      )}
      <SourceDetailsPopover
        citation={selectedCitation}
        open={selectedCitation !== null}
        onClose={() => setSelectedCitation(null)}
      />
    </div>
  )
}
