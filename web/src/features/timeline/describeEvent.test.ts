import { expect, test } from 'vitest'
import { fixtureEvents } from '../../test/fixture'
import type { RunEvent } from '../../types/api'
import { describeEvent, filterEvents } from './describeEvent'

const first = <T extends RunEvent['type']>(type: T) =>
  fixtureEvents.find((e): e is Extract<RunEvent, { type: T }> => e.type === type)!

test('every fixture event gets a non-empty title', () => {
  for (const event of fixtureEvents) expect(describeEvent(event).title.length).toBeGreaterThan(0)
})

test('guardrail blocks are danger and name the rule', () => {
  expect(describeEvent(first('guardrail.blocked'))).toEqual({
    title: '⛔ Blocked: no-git-push',
    detail: 'Bash: git push origin HEAD',
    tone: 'danger',
  })
})

test('handoffs read as from → to', () => {
  expect(describeEvent(first('handoff')).title).toBe('ba → architect')
})

test('truncated tool output is labelled', () => {
  const result = first('agent.tool_result')
  const truncated = { ...result, payload: { ...result.payload, truncated: true, original_size: 99_999 } }
  expect(describeEvent(truncated).detail).toMatch(/…\(truncated\)$/)
})

test('filterEvents hides token noise and narrows to one agent', () => {
  const visible = filterEvents(fixtureEvents, { agentId: null, hideUsage: true })
  expect(visible.some((e) => e.type === 'agent.usage' || e.type === 'budget.updated')).toBe(false)
  const dev2 = filterEvents(fixtureEvents, { agentId: 'dev-2', hideUsage: false })
  expect(dev2.length).toBeGreaterThan(0)
  expect(dev2.every((e) => e.agent_id === 'dev-2')).toBe(true)
})
