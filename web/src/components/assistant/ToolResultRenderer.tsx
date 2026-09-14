import type { AssistantToolExecution, Citation } from '../../types'

type SearchEvidenceUnit = {
  evidence_id?: string
  source?: string
  preview?: string
  score?: number | null
}

type ToolResultRendererProps = {
  execution: AssistantToolExecution
  onGetEvidence?: (evidenceId: string) => void
}

function asCitations(result: Record<string, unknown>): Citation[] {
  return Array.isArray(result.citations) ? result.citations as Citation[] : []
}

export function ToolResultRenderer({ execution, onGetEvidence }: ToolResultRendererProps) {
  const result = execution.result || {}
  if (execution.result_type === 'search_results') {
    const units = Array.isArray(result.evidence_units) ? result.evidence_units as SearchEvidenceUnit[] : []
    return (
      <div className="assistant-tool-result assistant-search-results">
        <div className="assistant-tool-result-heading">Search results <small>{units.length} evidence units</small></div>
        {!units.length && <p className="assistant-tool-empty">No matching evidence found.</p>}
        {units.map((unit, index) => {
          const evidenceId = String(unit.evidence_id || '')
          return (
            <article className="assistant-search-result" key={`${evidenceId}-${index}`}>
              <div className="assistant-search-result-meta">
                <strong>{unit.source || 'Evidence'}</strong>
                {typeof unit.score === 'number' && <span>{unit.score.toFixed(3)}</span>}
              </div>
              <p>{unit.preview || 'No preview available.'}</p>
              {evidenceId && <button type="button" onClick={() => onGetEvidence?.(evidenceId)}>Read full evidence</button>}
            </article>
          )
        })}
      </div>
    )
  }

  if (execution.result_type === 'evidence') {
    return (
      <div className="assistant-tool-result assistant-evidence-result">
        <div className="assistant-tool-result-heading">Evidence <small>{String(result.source || result.evidence_id || '')}</small></div>
        <p>{String(result.content || 'Evidence is empty.')}</p>
      </div>
    )
  }

  if (execution.result_type === 'answer') {
    return (
      <div className="assistant-tool-result assistant-answer-result">
        <p>{String(result.answer || 'Backend returned no answer.')}</p>
        {asCitations(result).length > 0 && <div className="assistant-answer-citations">{asCitations(result).length} sources</div>}
      </div>
    )
  }

  return (
    <div className="assistant-tool-result assistant-tool-error" role="alert">
      <p>Unsupported assistant result type: {execution.result_type}</p>
    </div>
  )
}
