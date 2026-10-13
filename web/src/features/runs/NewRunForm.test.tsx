import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import { NewRunForm } from './NewRunForm'

type Fetch = (url: string, init?: RequestInit) => Promise<Response>

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

function renderForm() {
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<NewRunForm />} />
        <Route path="/runs/:runId" element={<p>run page</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

test('starts a live run against the sandbox by default and opens it', async () => {
  const fetchMock = vi.fn<Fetch>(async () => json({ id: 'run_new' }, 201))
  vi.stubGlobal('fetch', fetchMock)
  renderForm()
  const start = screen.getByRole('button', { name: 'Start run' })
  expect(start).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Requirement'), { target: { value: ' Add a /health endpoint ' } })
  fireEvent.click(start)
  await waitFor(() => expect(screen.getByText('run page')).toBeInTheDocument())
  const [url, init] = fetchMock.mock.calls[0]!
  expect(url).toBe('/api/runs')
  expect(JSON.parse(String(init?.body))).toEqual({
    requirement: 'Add a /health endpoint',
    repo: 'gagandeepp/agentic-sdlc-sandbox',
    base_branch: 'main',
    budget_usd: 5,
  })
})

test('shows why a run could not start', async () => {
  const detail = "live runs need AGENT_ANTHROPIC_KEY and AGENT_GITHUB_TOKEN in the API's .env"
  vi.stubGlobal('fetch', vi.fn(async () => json({ detail }, 400)))
  renderForm()
  fireEvent.change(screen.getByLabelText('Requirement'), { target: { value: 'x' } })
  fireEvent.click(screen.getByRole('button', { name: 'Start run' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('AGENT_ANTHROPIC_KEY')
})

test('the scripted demo needs no keys', async () => {
  const fetchMock = vi.fn<Fetch>(async () => json({ id: 'run_demo' }, 201))
  vi.stubGlobal('fetch', fetchMock)
  renderForm()
  fireEvent.change(screen.getByLabelText('Requirement'), { target: { value: 'Add a /health endpoint' } })
  fireEvent.click(screen.getByRole('button', { name: /Scripted demo/ }))
  await waitFor(() => expect(screen.getByText('run page')).toBeInTheDocument())
  expect(fetchMock.mock.calls[0]![0]).toBe('/api/dev/scripted-runs')
})
