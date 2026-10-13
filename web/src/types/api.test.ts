import { expectTypeOf, test } from 'vitest'
import type { RunEvent, RunRecord } from './api'

test('RunEvent narrows on its type discriminator', () => {
  type Usage = Extract<RunEvent, { type: 'agent.usage' }>
  expectTypeOf<Usage['payload']['cache_read_input_tokens']>().toEqualTypeOf<number>()
  expectTypeOf<Usage['phase']>().toEqualTypeOf<
    'init' | 'ba' | 'architect' | 'dev' | 'integrate' | 'review' | 'qa' | 'devops' | 'done' | null
  >()
  type Handoff = Extract<RunEvent, { type: 'handoff' }>
  expectTypeOf<Handoff['payload']['to_agent']>().toEqualTypeOf<string>()
})

test('RunRecord carries status and token totals', () => {
  expectTypeOf<RunRecord['status']>().toEqualTypeOf<
    'pending' | 'running' | 'waiting_gate' | 'completed' | 'failed' | 'aborted' | 'interrupted'
  >()
  expectTypeOf<RunRecord['tokens']['input_tokens']>().toEqualTypeOf<number>()
})
