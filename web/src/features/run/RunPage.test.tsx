import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { FakeEventSource } from '../../test/fakeEventSource'
import { fixtureEvents } from '../../test/fixture'
import { RunPage } from './RunPage'

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})
// Panels on the page fetch artifacts; answer harmlessly unless a test says otherwise.
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.endsWith('/artifacts') ? [] : {})))
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
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

test('shows the gate panel while a gate is open and hides it once decided', async () => {
  const artifact = {
    id: 'art_req', run_id: 'run_demo0001', kind: 'requirements', version: 1, title: 'Requirements',
    data: {}, markdown: '# Requirements\n\nCSV export.\n', edited_by_user: false, created_at: '2026-10-13T09:00:00Z',
  }
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.endsWith('/artifacts') ? [artifact] : artifact)))
  renderRun('run_demo0001')
  const source = FakeEventSource.latest()
  const gateIndex = fixtureEvents.findIndex((e) => e.type === 'gate.opened')
  act(() => {
    for (const event of fixtureEvents.slice(0, gateIndex + 1)) source.emit(event)
  })
  const panel = await screen.findByRole('region', { name: 'Gate' })
  expect(within(panel).getByRole('button', { name: 'Approve' })).toBeInTheDocument()
  expect(await within(panel).findByLabelText('Artifact')).toHaveTextContent('CSV export.')
  fireEvent.click(await screen.findByRole('button', { name: 'Requirements v1' }))
  act(() => source.emit(fixtureEvents[gateIndex + 1]!))
  await waitFor(() => expect(screen.queryByRole('region', { name: 'Gate' })).toBeNull())
})
