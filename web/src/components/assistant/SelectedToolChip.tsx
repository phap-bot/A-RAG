import { X } from 'lucide-react'

import type { AssistantToolDefinition } from '../../types'

type SelectedToolChipProps = {
  tool: AssistantToolDefinition
  onRemove: () => void
}

export function SelectedToolChip({ tool, onRemove }: SelectedToolChipProps) {
  return (
    <div className="assistant-tool-chip" title={tool.agent_description}>
      <span>{tool.command}</span>
      <strong>{tool.title}</strong>
      <button type="button" aria-label={`Remove ${tool.command}`} onClick={onRemove}>
        <X size={12} />
      </button>
    </div>
  )
}
