import { beforeEach, expect, test } from 'vitest'
import { fixtureEvents } from '../test/fixture'
import { useRunStore } from './runStore'

beforeEach(() => useRunStore.getState().reset('run_demo0001'))

test('apply folds events into state and reset clears it', () => {
  useRunStore.getState().apply(fixtureEvents.slice(0, 5))
  expect(useRunStore.getState().state.lastSeq).toBe(5)
  useRunStore.getState().reset('run_other')
  expect(useRunStore.getState().state).toMatchObject({ runId: 'run_other', lastSeq: 0 })
})
