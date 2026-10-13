import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { useRunStore } from '../store/runStore'
import { FakeEventSource } from '../test/fakeEventSource'
import { fixtureEvents } from '../test/fixture'
import { useRunEvents } from './useRunEvents'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

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
  expect(useRunStore.getState().connection).toBe('ended')
  expect(source.closed).toBe(true)
})

test('closes the stream on unmount', () => {
  const { unmount } = renderHook(() => useRunEvents('run_x'))
  unmount()
  expect(FakeEventSource.latest().closed).toBe(true)
})

test('reopens from the last seen seq when the browser gives up on the stream', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ id: 'run_demo0001' })))
  renderHook(() => useRunEvents('run_demo0001'))
  const first = FakeEventSource.latest()
  for (const event of fixtureEvents.slice(0, 5)) first.emit(event)
  first.fail() // e.g. a 502 from the proxy while the API restarts
  expect(useRunStore.getState().connection).toBe('reconnecting')
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2), { timeout: 3000 })
  expect(FakeEventSource.latest().url).toBe('/api/runs/run_demo0001/events?after=5')
})

test('reports a run that does not exist instead of waiting forever', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'run not found' }, 404)))
  renderHook(() => useRunEvents('run_missing'))
  FakeEventSource.latest().fail()
  await waitFor(() => expect(useRunStore.getState().connection).toBe('not_found'))
  expect(FakeEventSource.instances).toHaveLength(1)
})
