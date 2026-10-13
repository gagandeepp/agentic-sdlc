import { formatTokens, formatUsd } from '../../lib/format'
import { totalInput, type AgentStatus, type AgentView } from '../../store/reducer'

const DOT: Record<AgentStatus, string> = {
  idle: 'bg-slate-500',
  thinking: 'bg-sky-400 animate-pulse',
  tool: 'bg-amber-400 animate-pulse',
  waiting: 'bg-violet-400',
  done: 'bg-emerald-400',
  failed: 'bg-red-500',
}

interface Props {
  agents: Record<string, AgentView>
  order: string[]
  activeId: string | null
}

export function AgentCards({ agents, order, activeId }: Props) {
  return (
    <ul className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-3">
      {order.map((id) => {
        const agent = agents[id]!
        const ctx = agent.contextTokens !== null && agent.contextMax ? agent.contextTokens / agent.contextMax : null
        return (
          <li
            key={id}
            aria-label={`agent ${id}`}
            className={`rounded-lg border bg-slate-900/60 p-3 ${id === activeId ? 'border-sky-500' : 'border-slate-800'}`}
          >
            <div className="flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${DOT[agent.status]}`} />
              <span className="font-medium">{id}</span>
              <span className="ml-auto font-mono text-[11px] text-slate-400">{agent.model}</span>
            </div>
            <dl className="mt-2 grid grid-cols-2 gap-x-2 gap-y-0.5 text-xs text-slate-400">
              <dt>status</dt>
              <dd>{agent.status}</dd>
              <dt>in / out</dt>
              <dd className="font-mono">
                {formatTokens(totalInput(agent.tokens))} / {formatTokens(agent.tokens.output)}
              </dd>
              <dt>cost</dt>
              <dd className="font-mono">{formatUsd(agent.costUsd, 3)}</dd>
              <dt>context</dt>
              <dd className="font-mono">{ctx === null ? '–' : `${Math.round(ctx * 100)}%`}</dd>
            </dl>
            {ctx !== null && (
              <div className="mt-2 h-1 rounded bg-slate-800" aria-hidden>
                <div className="h-1 rounded bg-sky-500" style={{ width: `${Math.min(100, ctx * 100)}%` }} />
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}
