import type { AssistantToolDefinition } from '../../types'

export function assistantCommandQuery(value: string): string | null {
  const trimmed = value.trimStart()
  if (!trimmed.startsWith('/')) return null
  return trimmed.split(/\s+/, 1)[0].toLowerCase()
}

export function filterAssistantTools(
  tools: AssistantToolDefinition[],
  value: string,
): AssistantToolDefinition[] {
  const query = assistantCommandQuery(value)
  if (!query) return []
  return tools.filter((tool) => Boolean(tool.command && tool.command.startsWith(query)))
}

export function commandRemainder(value: string, command: string | null): string {
  if (!command) return ''
  const trimmed = value.trimStart()
  return trimmed.startsWith(command) ? trimmed.slice(command.length).trimStart() : ''
}
