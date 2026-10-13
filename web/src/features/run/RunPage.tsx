import { useMemo, useState } from 'react'
import { useParams } from 'react-router'
import { useRunEvents } from '../../hooks/useRunEvents'
import { useRunStore } from '../../store/runStore'
import { filterEvents } from '../timeline/describeEvent'
import { Timeline } from '../timeline/Timeline'
import { AgentCards } from './AgentCards'
import { MetricsStrip } from './MetricsStrip'

export function RunPage() {
  const { runId = '' } = useParams()
  useRunEvents(runId)
  const run = useRunStore((s) => s.state)
  const connection = useRunStore((s) => s.connection)
  const [agentFilter, setAgentFilter] = useState<string | null>(null)
  const [hideUsage, setHideUsage] = useState(true)
  const events = useMemo(
    () => filterEvents(run.timeline, { agentId: agentFilter, hideUsage }),
    [run.timeline, agentFilter, hideUsage],
  )

  if (connection === 'not_found') {
    return (
      <section className="mx-auto max-w-5xl p-6">
        <h1 className="text-xl font-semibold">Run not found</h1>
        <p className="mt-2 text-slate-400">There is no run with id {runId}.</p>
      </section>
    )
  }

  return (
    <section className="grid h-[calc(100vh-3.5rem)] grid-rows-[auto_1fr_auto]">
      <header className="flex flex-wrap items-center gap-3 border-b border-slate-800 px-6 py-3">
        <h1 className="text-lg font-semibold">{run.requirement ?? runId}</h1>
        <span className="rounded bg-slate-800 px-2 py-0.5 text-xs">{run.status}</span>
        {run.phase && <span className="text-xs text-slate-400">phase: {run.phase}</span>}
        {connection === 'reconnecting' && (
          <span role="status" className="text-xs text-amber-300">
            reconnecting…
          </span>
        )}
        {run.prUrl && (
          <a href={run.prUrl} target="_blank" rel="noreferrer" className="ml-auto text-sm text-sky-300 hover:underline">
            Pull request
          </a>
        )}
      </header>
      <div className="grid min-h-0 grid-cols-[1fr_380px]">
        <div className="overflow-auto p-6">
          <AgentCards agents={run.agents} order={run.agentOrder} activeId={run.activeAgentId} />
        </div>
        <aside className="flex min-h-0 flex-col border-l border-slate-800">
          <div className="flex items-center gap-3 border-b border-slate-800 px-4 py-2 text-xs text-slate-400">
            <select
              aria-label="Filter by agent"
              value={agentFilter ?? ''}
              onChange={(e) => setAgentFilter(e.target.value || null)}
              className="rounded bg-slate-900 px-2 py-1"
            >
              <option value="">all agents</option>
              {run.agentOrder.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={!hideUsage} onChange={(e) => setHideUsage(!e.target.checked)} />
              token events
            </label>
            <span className="ml-auto">{events.length} events</span>
          </div>
          <div className="min-h-0 flex-1">
            <Timeline events={events} />
          </div>
        </aside>
      </div>
      <MetricsStrip run={run} />
    </section>
  )
}
