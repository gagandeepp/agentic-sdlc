import { formatTokens, formatUsd } from '../../lib/format'
import { cacheHitRatio, totalInput, type RunViewState } from '../../store/reducer'

export function MetricsStrip({ run }: { run: RunViewState }) {
  const active = run.activeAgentId ? run.agents[run.activeAgentId] : undefined
  const ctx =
    active && active.contextTokens !== null && active.contextMax ? active.contextTokens / active.contextMax : null
  const items: [string, string][] = [
    ['active context', ctx === null ? '–' : `${Math.round(ctx * 100)}%`],
    ['input', formatTokens(totalInput(run.tokens))],
    ['output', formatTokens(run.tokens.output)],
    ['cache hit', `${Math.round(cacheHitRatio(run.tokens) * 100)}%`],
    ['cost', `${formatUsd(run.spentUsd)} / ${formatUsd(run.budgetUsd ?? 0)}`],
  ]
  return (
    <footer aria-label="Run metrics" className="flex flex-wrap gap-6 border-t border-slate-800 px-6 py-3 text-sm">
      {items.map(([label, value]) => (
        <div key={label} className="flex items-baseline gap-2">
          <span className="text-xs uppercase tracking-wide text-slate-500">{label}</span>
          <span className="font-mono">{value}</span>
        </div>
      ))}
    </footer>
  )
}
