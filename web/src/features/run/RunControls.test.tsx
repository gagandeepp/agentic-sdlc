import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { RunControls } from './RunControls'

type Fetch = (url: string, init?: RequestInit) => Promise<Response>

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

test('abort for live runs, resume and abort for interrupted ones, nothing once finished', async () => {
  const fetchMock = vi.fn<Fetch>(async () => json({ id: 'run_1' }))
  vi.stubGlobal('fetch', fetchMock)
  const { rerender } = render(<RunControls runId="run_1" status="waiting_gate" />)
  expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Abort' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/runs/run_1/abort', { method: 'POST' }))
  rerender(<RunControls runId="run_1" status="interrupted" />)
  expect(screen.getByRole('button', { name: 'Abort' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/runs/run_1/resume', { method: 'POST' }))
  rerender(<RunControls runId="run_1" status="completed" />)
  expect(screen.queryByRole('button')).toBeNull()
})

test('shows why a resume was refused', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'another run is in progress; try again when it finishes' }, 429)))
  render(<RunControls runId="run_1" status="interrupted" />)
  fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('another run is in progress')
})
