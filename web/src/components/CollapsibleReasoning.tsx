import type { AgentStreamActivity } from '../types'

type CollapsibleReasoningProps = {
  activities: AgentStreamActivity[]
  pending: boolean
}

function jsonText(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? String(value)
  } catch {
    return String(value)
  }
}

export function CollapsibleReasoning({ activities, pending }: CollapsibleReasoningProps) {
  if (!pending && !activities.length) return null

  return (
    <details className="ai-reasoning-details">
      <summary>
        {pending && <span className="ai-reasoning-loader" aria-hidden="true" />}
        <span>{pending ? 'Đang phân tích dữ liệu...' : '⚙️ Quá trình suy luận (Click để xem chi tiết)'}</span>
      </summary>
      <div className="ai-reasoning-body">
        {activities.map((activity) => (
          <section className="ai-reasoning-entry" key={activity.id}>
            <strong>{activity.label}</strong>
            {activity.details !== undefined && (
              <pre><code>{jsonText(activity.details)}</code></pre>
            )}
          </section>
        ))}
        {!activities.length && <span className="ai-reasoning-empty">Đang chờ metadata từ agent...</span>}
      </div>
    </details>
  )
}
