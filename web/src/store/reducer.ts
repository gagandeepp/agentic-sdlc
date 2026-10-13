import type { GateOpenedEvent, Phase, Role, RunEvent, RunStatus } from '../types/api'

export type AgentStatus = 'idle' | 'thinking' | 'tool' | 'waiting' | 'done' | 'failed'

export interface TokenTotals {
  input: number
  output: number
  cacheCreation: number
  cacheRead: number
}

export interface AgentView {
  id: string
  role: Role
  model: string
  status: AgentStatus
  tokens: TokenTotals
  costUsd: number
  turns: number
  contextTokens: number | null
  contextMax: number | null
}

export interface RunViewState {
  runId: string | null
  lastSeq: number
  status: RunStatus | 'unknown'
  phase: Phase | null
  requirement: string | null
  budgetUsd: number | null
  spentUsd: number
  agents: Record<string, AgentView>
  agentOrder: string[]
  tokens: TokenTotals
  timeline: RunEvent[]
  openGate: GateOpenedEvent | null
  activeAgentId: string | null
  prUrl: string | null
}

export const emptyTokens = (): TokenTotals => ({ input: 0, output: 0, cacheCreation: 0, cacheRead: 0 })

export function initialRunState(runId: string | null = null): RunViewState {
  return {
    runId,
    lastSeq: 0,
    status: 'unknown',
    phase: null,
    requirement: null,
    budgetUsd: null,
    spentUsd: 0,
    agents: {},
    agentOrder: [],
    tokens: emptyTokens(),
    timeline: [],
    openGate: null,
    activeAgentId: null,
    prUrl: null,
  }
}

/** All input tokens the model processed: fresh + written to cache + read from cache. */
export const totalInput = (t: TokenTotals): number => t.input + t.cacheCreation + t.cacheRead

export function cacheHitRatio(t: TokenTotals): number {
  const total = totalInput(t)
  return total === 0 ? 0 : t.cacheRead / total
}

const addTokens = (a: TokenTotals, b: TokenTotals): TokenTotals => ({
  input: a.input + b.input,
  output: a.output + b.output,
  cacheCreation: a.cacheCreation + b.cacheCreation,
  cacheRead: a.cacheRead + b.cacheRead,
})

function updateAgent(
  state: RunViewState,
  agentId: string | null,
  update: (agent: AgentView) => AgentView,
): RunViewState {
  const agent = agentId ? state.agents[agentId] : undefined
  if (!agentId || !agent) return state
  return { ...state, agents: { ...state.agents, [agentId]: update(agent) } }
}

const setStatus = (state: RunViewState, agentId: string | null, status: AgentStatus) =>
  updateAgent(state, agentId, (a) => ({ ...a, status }))

/** Agents still marked busy when a run stops are no longer working. */
function settleAgents(agents: Record<string, AgentView>): Record<string, AgentView> {
  const settled: Record<string, AgentView> = {}
  for (const [id, agent] of Object.entries(agents)) {
    settled[id] = agent.status === 'done' || agent.status === 'failed' ? agent : { ...agent, status: 'idle' }
  }
  return settled
}

const stopped = (state: RunViewState, status: RunStatus): RunViewState => ({
  ...state,
  status,
  activeAgentId: null,
  openGate: null,
  agents: settleAgents(state.agents),
})

export function reduceEvent(state: RunViewState, event: RunEvent): RunViewState {
  if (event.seq <= state.lastSeq) return state // already applied (reconnect overlap)
  const next: RunViewState = {
    ...state,
    runId: state.runId ?? event.run_id,
    lastSeq: event.seq,
    timeline: [...state.timeline, event],
  }
  switch (event.type) {
    case 'run.created':
      return {
        ...next,
        status: 'pending',
        requirement: event.payload.requirement,
        budgetUsd: event.payload.budget_usd,
      }
    case 'run.status_changed':
      return { ...next, status: event.payload.status }
    case 'run.completed':
      return { ...next, status: 'completed', prUrl: event.payload.pr_url ?? next.prUrl, activeAgentId: null }
    case 'run.failed':
      return stopped(next, 'failed')
    case 'run.aborted':
      return stopped(next, 'aborted')
    case 'run.interrupted':
      return stopped(next, 'interrupted')
    case 'phase.started':
      return { ...next, phase: event.phase ?? next.phase }
    case 'agent.started': {
      const id = event.agent_id
      if (!id) return next
      const existing = next.agents[id]
      const agent: AgentView = existing
        ? { ...existing, model: event.payload.model, status: 'thinking' }
        : {
            id,
            role: event.payload.role,
            model: event.payload.model,
            status: 'thinking',
            tokens: emptyTokens(),
            costUsd: 0,
            turns: 0,
            contextTokens: null,
            contextMax: null,
          }
      return {
        ...next,
        agents: { ...next.agents, [id]: agent },
        agentOrder: existing ? next.agentOrder : [...next.agentOrder, id],
        activeAgentId: id,
      }
    }
    case 'agent.text':
    case 'agent.thinking':
    case 'agent.tool_result':
      return setStatus(next, event.agent_id, 'thinking')
    case 'agent.tool_call':
      return setStatus(next, event.agent_id, 'tool')
    case 'agent.usage': {
      const p = event.payload
      const delta: TokenTotals = {
        input: p.input_tokens,
        output: p.output_tokens,
        cacheCreation: p.cache_creation_input_tokens,
        cacheRead: p.cache_read_input_tokens,
      }
      const totals = { ...next, tokens: addTokens(next.tokens, delta), spentUsd: next.spentUsd + p.cost_usd }
      return updateAgent(totals, event.agent_id, (a) => ({
        ...a,
        tokens: addTokens(a.tokens, delta),
        costUsd: a.costUsd + p.cost_usd,
        turns: a.turns + 1,
        contextTokens: p.context_tokens ?? a.contextTokens,
        contextMax: p.context_max ?? a.contextMax,
      }))
    }
    case 'agent.finished': {
      const finished = setStatus(next, event.agent_id, event.payload.ok ? 'done' : 'failed')
      return {
        ...finished,
        activeAgentId: finished.activeAgentId === event.agent_id ? null : finished.activeAgentId,
      }
    }
    case 'budget.updated':
      // Authoritative spend (from the credential proxy in later plans).
      return { ...next, spentUsd: event.payload.spent_usd, budgetUsd: event.payload.budget_usd }
    case 'gate.opened':
      return { ...next, status: 'waiting_gate', openGate: event }
    case 'gate.resolved':
      return { ...next, status: 'running', openGate: null }
    case 'pr.opened':
    case 'pr.ready':
      return { ...next, prUrl: event.payload.url }
    default:
      return next
  }
}

export const reduceEvents = (state: RunViewState, events: RunEvent[]): RunViewState =>
  events.reduce(reduceEvent, state)
