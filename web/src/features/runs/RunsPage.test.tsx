import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { RunsPage } from './RunsPage'

const RUN = {
  id: 'run_1',
  requirement: 'Add CSV export to the orders page',
  repo: 'demo/orders-app',
  base_branch: 'main',
  budget_usd: 5,
  max_parallel_devs: 2,
  status: 'completed',
  phase: 'done',
  spent_usd: 0.88,
  tokens: { input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 },
  last_seq: 105,
  created_at: '2026-10-13T09:00:00Z',
  updated_at: '2026-10-13T09:01:00Z',
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<RunsPage />} />
        <Route path="/runs/:runId" element={<p>run page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

test('lists runs with status and spend', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json([RUN])))
  renderAt('/')
  const link = await screen.findByRole('link', { name: RUN.requirement })
  expect(link).toHaveAttribute('href', '/runs/run_1')
  expect(screen.getByText('completed')).toBeInTheDocument()
  expect(screen.getByText('$0.88 / $5.00')).toBeInTheDocument()
})

test('starting a demo run posts to the dev endpoint and opens the run', async () => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
    init?.method === 'POST' ? json({ ...RUN, id: 'run_new' }, 201) : json([]),
  )
  vi.stubGlobal('fetch', fetchMock)
  renderAt('/')
  fireEvent.click(await screen.findByRole('button', { name: 'Start demo run' }))
  await waitFor(() => expect(screen.getByText('run page')).toBeInTheDocument())
  expect(fetchMock).toHaveBeenCalledWith('/api/dev/fake-runs?speed=2', { method: 'POST' })
})

test('shows API errors', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'database unavailable' }, 503)))
  renderAt('/')
  expect(await screen.findByRole('alert')).toHaveTextContent('503')
})
