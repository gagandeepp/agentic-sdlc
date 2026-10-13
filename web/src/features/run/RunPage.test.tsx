import { act, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { FakeEventSource } from '../../test/fakeEventSource'
import { fixtureEvents } from '../../test/fixture'
import { RunPage } from './RunPage'

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => vi.unstubAllGlobals())

function renderRun(runId: string) {
  render(
    <MemoryRouter initialEntries={[`/runs/${runId}`]}>
      <Routes>
        <Route path="/runs/:runId" element={<RunPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

test('shows "Run not found" for an unknown run', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('{"detail":"run not found"}', { status: 404 })),
  )
  renderRun('run_missing')
  act(() => FakeEventSource.latest().fail())
  expect(await screen.findByRole('heading', { name: 'Run not found' })).toBeInTheDocument()
})

test('renders a live run: header, agent cards, metrics, and timeline count', async () => {
  renderRun('run_demo0001')
  const source = FakeEventSource.latest()
  act(() => {
    for (const event of fixtureEvents) source.emit(event)
  })

  expect(await screen.findByText('completed')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Add CSV export to the orders page' })).toBeInTheDocument()
  expect(screen.getAllByLabelText(/^agent /)).toHaveLength(7)
  const metrics = screen.getByLabelText('Run metrics')
  expect(within(metrics).getByText('$0.88 / $5.00')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Pull request' })).toHaveAttribute(
    'href',
    'https://github.com/demo/orders-app/pull/42',
  )
  expect(screen.getByText(/^\d+ events$/)).toBeInTheDocument()
})
