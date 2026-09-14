import { useEffect, useRef, useState } from 'react'

import type { AssistantToolDefinition } from '../../types'

type ToolCommandMenuProps = {
  tools: AssistantToolDefinition[]
  onSelect: (tool: AssistantToolDefinition) => void
}

export function ToolCommandMenu({ tools, onSelect }: ToolCommandMenuProps) {
  const [activeIndex, setActiveIndex] = useState(0)
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([])
  const toolKey = tools.map((tool) => tool.name).join('|')

  useEffect(() => {
    setActiveIndex(0)
    optionRefs.current[0]?.focus()
  }, [toolKey])

  if (!tools.length) return null
  return (
    <div className="assistant-tool-menu" role="listbox" aria-label="Assistant tools">
      {tools.map((tool, index) => (
        <button
          key={tool.name}
          type="button"
          className="assistant-tool-option"
          ref={(element) => { optionRefs.current[index] = element }}
          role="option"
          aria-selected={index === activeIndex}
          onMouseDown={(event) => event.preventDefault()}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault()
              const direction = event.key === 'ArrowDown' ? 1 : -1
              const nextIndex = (index + direction + tools.length) % tools.length
              setActiveIndex(nextIndex)
              optionRefs.current[nextIndex]?.focus()
            } else if (event.key === 'Enter') {
              event.preventDefault()
              onSelect(tool)
            }
          }}
          onClick={() => onSelect(tool)}
        >
          <span className="assistant-tool-command">{tool.command}</span>
          <span className="assistant-tool-option-copy">
            <strong>{tool.title}</strong>
            <small>{tool.description}</small>
          </span>
        </button>
      ))}
    </div>
  )
}
