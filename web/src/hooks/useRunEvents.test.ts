import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { useRunStore } from '../store/runStore'
import { FakeEventSource } from '../test/fakeEventSource'
import { fixtureEvents } from '../test/fixture'
import { useRunEvents } from './useRunEvents'

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => vi.unstubAllGlobals())

test('streams events into the store and closes after the terminal event', async () => {
  renderHook(() => useRunEvents('run_demo0001'))
  const source = FakeEventSource.latest()
  expect(source.url).toBe('/api/runs/run_demo0001/events')
  for (const event of fixtureEvents) source.emit(event)
  await waitFor(() => expect(useRunStore.getState().state.lastSeq).toBe(fixtureEvents.length))
  expect(useRunStore.getState().state.status).toBe('completed')
  expect(source.closed).toBe(true)
})

test('closes the stream on unmount', () => {
  const { unmount } = renderHook(() => useRunEvents('run_x'))
  unmount()
  expect(FakeEventSource.latest().closed).toBe(true)
})
