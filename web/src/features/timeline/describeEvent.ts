import { formatTokens, formatUsd } from '../../lib/format'
import type { RunEvent } from '../../types/api'

export type Tone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

export interface EventSummary {
  title: string
  detail: string | null
  tone: Tone
}

const who = (e: RunEvent): string => e.agent_id ?? 'orchestrator'

export function describeEvent(e: RunEvent): EventSummary {
  switch (e.type) {
    case 'run.created':
      return { title: 'Run created', detail: e.payload.requirement, tone: 'info' }
    case 'run.status_changed':
      return { title: `Status → ${e.payload.status}`, detail: null, tone: 'neutral' }
    case 'run.completed':
      return { title: 'Run completed', detail: e.payload.pr_url, tone: 'success' }
    case 'run.failed':
      return { title: 'Run failed', detail: e.payload.reason, tone: 'danger' }
    case 'run.aborted':
      return { title: 'Run aborted', detail: e.payload.reason ?? `by ${e.payload.by}`, tone: 'warning' }
    case 'run.interrupted':
      return {
        title: 'Run interrupted',
        detail: e.payload.last_checkpoint ? `resumable from ${e.payload.last_checkpoint}` : null,
        tone: 'warning',
      }
    case 'phase.started':
      return {
        title: `Phase ${e.phase ?? '?'} started`,
        detail: e.payload.attempt > 1 ? `attempt ${e.payload.attempt}` : null,
        tone: 'info',
      }
    case 'phase.completed':
      return {
        title: `Phase ${e.phase ?? '?'} ${e.payload.outcome}`,
        detail: null,
        tone: e.payload.outcome === 'ok' ? 'success' : 'warning',
      }
    case 'agent.started':
      return { title: `${who(e)} started`, detail: e.payload.model, tone: 'info' }
    case 'agent.finished':
      return {
        title: `${who(e)} finished`,
        detail: `${e.payload.num_turns} turns · ${(e.payload.duration_ms / 1000).toFixed(1)}s`,
        tone: e.payload.ok ? 'success' : 'danger',
      }
    case 'agent.text':
      return { title: who(e), detail: e.payload.text, tone: 'neutral' }
    case 'agent.thinking':
      return { title: `${who(e)} thinking`, detail: e.payload.text, tone: 'neutral' }
    case 'agent.tool_call':
      return { title: `${who(e)} ⚙ ${e.payload.name}`, detail: JSON.stringify(e.payload.input), tone: 'neutral' }
    case 'agent.tool_result':
      return {
        title: `${who(e)} ${e.payload.is_error ? '✗' : '✓'} result`,
        detail: e.payload.content + (e.payload.truncated ? ' …(truncated)' : ''),
        tone: e.payload.is_error ? 'danger' : 'neutral',
      }
    case 'agent.usage': {
      const p = e.payload
      return {
        title: `${who(e)} tokens`,
        detail: `in ${formatTokens(p.input_tokens)} · out ${formatTokens(p.output_tokens)} · cache ${formatTokens(p.cache_read_input_tokens)} · ${formatUsd(p.cost_usd, 3)}`,
        tone: 'neutral',
      }
    }
    case 'handoff':
      return { title: `${e.payload.from_agent} → ${e.payload.to_agent}`, detail: e.payload.summary, tone: 'info' }
    case 'artifact.created':
      return { title: `Artifact: ${e.payload.title}`, detail: `${e.payload.kind} v${e.payload.version}`, tone: 'info' }
    case 'artifact.revised':
      return { title: `Artifact revised: ${e.payload.title}`, detail: `${e.payload.kind} v${e.payload.version}`, tone: 'info' }
    case 'gate.opened':
      return { title: `⏸ Gate: ${e.payload.gate}`, detail: e.payload.reason, tone: 'warning' }
    case 'gate.resolved':
      return {
        title: `Gate ${e.payload.action}`,
        detail: e.payload.feedback,
        tone: e.payload.action === 'abort' ? 'danger' : 'success',
      }
    case 'loop.iteration':
      return { title: `↺ ${e.payload.loop} ${e.payload.iteration}/${e.payload.limit}`, detail: null, tone: 'warning' }
    case 'guardrail.blocked':
      return { title: `⛔ Blocked: ${e.payload.rule}`, detail: `${e.payload.tool}: ${e.payload.detail}`, tone: 'danger' }
    case 'budget.updated':
      return {
        title: 'Budget',
        detail: `${formatUsd(e.payload.spent_usd)} / ${formatUsd(e.payload.budget_usd)}`,
        tone: 'neutral',
      }
    case 'merge.started':
      return { title: `Merge ${e.payload.branch} → ${e.payload.into}`, detail: null, tone: 'info' }
    case 'merge.conflict':
      return { title: `Merge conflict: ${e.payload.branch}`, detail: e.payload.files.join(', '), tone: 'danger' }
    case 'merge.completed':
      return { title: `Merged ${e.payload.branch}`, detail: e.payload.commit.slice(0, 7), tone: 'success' }
    case 'git.pushed':
      return { title: `Pushed ${e.payload.branch}`, detail: e.payload.commit.slice(0, 7), tone: 'success' }
    case 'pr.opened':
      return { title: `PR #${e.payload.number} opened${e.payload.draft ? ' (draft)' : ''}`, detail: e.payload.url, tone: 'success' }
    case 'pr.ready':
      return { title: `PR #${e.payload.number} ready for review`, detail: e.payload.url, tone: 'success' }
    case 'ci.status':
      return {
        title: `CI ${e.payload.state}`,
        detail: e.payload.summary,
        tone: e.payload.state === 'success' ? 'success' : e.payload.state === 'failure' ? 'danger' : 'neutral',
      }
  }
}

const NOISE = new Set<RunEvent['type']>(['agent.usage', 'budget.updated'])

export function filterEvents(
  events: RunEvent[],
  filter: { agentId: string | null; hideUsage: boolean },
): RunEvent[] {
  return events.filter(
    (e) => (!filter.hideUsage || !NOISE.has(e.type)) && (filter.agentId === null || e.agent_id === filter.agentId),
  )
}
