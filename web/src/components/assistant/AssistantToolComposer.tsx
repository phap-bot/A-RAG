import { FormEvent, KeyboardEvent as ReactKeyboardEvent, ReactNode, useState } from 'react'

import type { AssistantToolDefinition } from '../../types'
import { SelectedToolChip } from './SelectedToolChip'
import { ToolCommandMenu } from './ToolCommandMenu'
import { filterAssistantTools } from './toolParser'

type AssistantToolComposerProps = {
  tools: AssistantToolDefinition[]
  value: string
  selectedTool: AssistantToolDefinition | null
  asking: boolean
  placeholder: string
  textareaId?: string
  className?: string
  topContent?: ReactNode
  actions: ReactNode
  onChange: (value: string) => void
  onSubmit: () => void | Promise<void>
  onSelectTool: (tool: AssistantToolDefinition) => void
  onRemoveTool: () => void
}

export function AssistantToolComposer({
  tools,
  value,
  selectedTool,
  asking,
  placeholder,
  textareaId,
  className = 'project-ai-composer',
  topContent,
  actions,
  onChange,
  onSubmit,
  onSelectTool,
  onRemoveTool,
}: AssistantToolComposerProps) {
  const [menuOpen, setMenuOpen] = useState(false)

  function submit(event: FormEvent) {
    event.preventDefault()
    void onSubmit()
  }

  function handleKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void onSubmit()
    }
  }

  function handleChange(nextValue: string) {
    onChange(nextValue)
    setMenuOpen(!selectedTool && filterAssistantTools(tools, nextValue).length > 0)
  }

  return (
    <form className={className} onSubmit={submit}>
      {selectedTool && <SelectedToolChip tool={selectedTool} onRemove={onRemoveTool} />}
      {menuOpen && !selectedTool && (
        <ToolCommandMenu
          tools={filterAssistantTools(tools, value)}
          onSelect={(tool) => { setMenuOpen(false); onSelectTool(tool) }}
        />
      )}
      {topContent}
      <textarea
        id={textareaId}
        value={value}
        onChange={(event) => handleChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={selectedTool?.name === 'get_evidence' ? 'Evidence ID...' : placeholder}
      />
      {actions}
      {asking && <span className="assistant-composer-status" role="status">Working...</span>}
    </form>
  )
}
