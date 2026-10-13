import { describe, expect, test } from 'vitest'
import { fixtureEvents } from '../test/fixture'
import { cacheHitRatio, initialRunState, reduceEvent, reduceEvents, totalInput } from './reducer'

const final = reduceEvents(initialRunState(), fixtureEvents)
const usage = fixtureEvents.flatMap((e) => (e.type === 'agent.usage' ? [e.payload] : []))
const sum = (pick: (u: (typeof usage)[number]) => number) => usage.reduce((acc, u) => acc + pick(u), 0)

describe('reduceEvents over the fake run', () => {
  test('ends completed with the PR link and nothing pending', () => {
    expect(final.status).toBe('completed')
    expect(final.prUrl).toBe('https://github.com/demo/orders-app/pull/42')
    expect(final.openGate).toBeNull()
    expect(final.activeAgentId).toBeNull()
    expect(final.lastSeq).toBe(fixtureEvents.length)
    expect(final.timeline).toHaveLength(fixtureEvents.length)
  })

  test('token and cost totals match the fixture usage events', () => {
    expect(final.tokens.input).toBe(sum((u) => u.input_tokens))
    expect(final.tokens.output).toBe(sum((u) => u.output_tokens))
    expect(final.tokens.cacheRead).toBe(sum((u) => u.cache_read_input_tokens))
    expect(final.spentUsd).toBeCloseTo(sum((u) => u.cost_usd), 6)
    const perAgent = Object.values(final.agents).reduce((acc, a) => acc + a.costUsd, 0)
    expect(perAgent).toBeCloseTo(final.spentUsd, 6)
  })

  test('agents appear in first-start order and all finish done', () => {
    expect(final.agentOrder).toEqual(['ba', 'architect', 'dev-1', 'dev-2', 'reviewer', 'qa', 'devops'])
    expect(Object.values(final.agents).every((a) => a.status === 'done')).toBe(true)
    expect(final.agents['dev-1']?.turns).toBe(2)
    expect(final.agents['dev-1']?.contextTokens).toBe(40_500)
  })
})

describe('reduceEvent', () => {
  test('a stopped run drops its open gate and idles agents that were still working', () => {
    const callIndex = fixtureEvents.findIndex((e) => e.type === 'agent.tool_call')
    const busy = reduceEvents(initialRunState(), fixtureEvents.slice(0, callIndex + 1))
    const gate = fixtureEvents.find((e) => e.type === 'gate.opened')!
    const waiting = reduceEvent(busy, { ...gate, seq: busy.lastSeq + 1 })
    expect(waiting.openGate).not.toBeNull()
    const interrupted = reduceEvent(waiting, {
      run_id: waiting.runId!,
      seq: waiting.lastSeq + 1,
      ts: '2026-10-13T10:00:00Z',
      type: 'run.interrupted',
      phase: null,
      agent_id: null,
      payload: { last_checkpoint: 'requirements_drafted' },
    })
    expect(interrupted.status).toBe('interrupted')
    expect(interrupted.openGate).toBeNull()
    expect(interrupted.agents.ba?.status).toBe('idle')
  })

  test('is idempotent: replaying already-seen events returns the same state object', () => {
    expect(reduceEvents(final, fixtureEvents)).toBe(final)
  })

  test('resuming with an overlapping window equals one clean pass', () => {
    const partial = reduceEvents(initialRunState(), fixtureEvents.slice(0, 40))
    expect(reduceEvents(partial, fixtureEvents.slice(25))).toEqual(final)
  })

  test('tracks gate open and resolve', () => {
    const gateIndex = fixtureEvents.findIndex((e) => e.type === 'gate.opened')
    const waiting = reduceEvents(initialRunState(), fixtureEvents.slice(0, gateIndex + 1))
    expect(waiting.status).toBe('waiting_gate')
    expect(waiting.openGate?.payload.gate).toBe('requirements')
    const resolved = reduceEvent(waiting, fixtureEvents[gateIndex + 1]!)
    expect(resolved.openGate).toBeNull()
    expect(resolved.status).toBe('running')
  })

  test('an agent calling a tool is in the tool state', () => {
    const callIndex = fixtureEvents.findIndex((e) => e.type === 'agent.tool_call')
    const state = reduceEvents(initialRunState(), fixtureEvents.slice(0, callIndex + 1))
    expect(state.agents.ba?.status).toBe('tool')
    expect(state.activeAgentId).toBe('ba')
  })
})

test('cache hit ratio is cache reads over all input tokens', () => {
  const t = { input: 100, output: 7, cacheCreation: 50, cacheRead: 350 }
  expect(totalInput(t)).toBe(500)
  expect(cacheHitRatio(t)).toBeCloseTo(0.7)
  expect(cacheHitRatio({ input: 0, output: 0, cacheCreation: 0, cacheRead: 0 })).toBe(0)
})
