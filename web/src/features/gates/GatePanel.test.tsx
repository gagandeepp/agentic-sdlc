import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { ArtifactRecord, GateOpenedEvent } from '../../types/api'
import { GatePanel } from './GatePanel'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const ARTIFACT: ArtifactRecord = {
  id: 'art_1',
  run_id: 'run_1',
  kind: 'design',
  version: 1,
  title: 'Design',
  data: {},
  markdown: '# Design\n\nUse the stock module.\n',
  edited_by_user: false,
  created_at: '2026-10-13T09:00:00Z',
}

function gate(payload: Partial<GateOpenedEvent['payload']> = {}): GateOpenedEvent {
  return {
    run_id: 'run_1',
    seq: 9,
    ts: '2026-10-13T09:00:00Z',
    type: 'gate.opened',
    phase: 'architect',
    agent_id: null,
    payload: {
      gate_id: 'gate_1',
      gate: 'design',
      actions: ['approve', 'revise', 'edit', 'abort'],
      artifact_id: 'art_1',
      reason: 'Approve the design to continue.',
      ...payload,
    },
  }
}

function stubApi(onPost: () => Response = () => json({ gate_id: 'gate_1', action: 'approve' })) {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
    init?.method === 'POST' ? onPost() : json(ARTIFACT),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const lastBody = (fetchMock: ReturnType<typeof stubApi>) =>
  JSON.parse(String(fetchMock.mock.calls.at(-1)![1]!.body)) as unknown

afterEach(() => vi.unstubAllGlobals())

test('shows the artifact and approves', async () => {
  const fetchMock = stubApi()
  render(<GatePanel runId="run_1" gate={gate()} />)
  expect(await screen.findByLabelText('Artifact')).toHaveTextContent('Use the stock module.')
  fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(fetchMock.mock.calls.at(-1)![0]).toBe('/api/runs/run_1/gates/gate_1'))
  expect(lastBody(fetchMock)).toEqual({ action: 'approve' })
})

test('request changes needs feedback, and a blocked decision shows why', async () => {
  stubApi(() => json({ detail: 'blocked by guardrails (self check input)' }, 422))
  render(<GatePanel runId="run_1" gate={gate()} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Request changes' }))
  const send = screen.getByRole('button', { name: 'Send feedback' })
  expect(send).toBeDisabled()
  fireEvent.change(screen.getByLabelText('Feedback'), { target: { value: 'Split task 2' } })
  fireEvent.click(send)
  expect(await screen.findByRole('alert')).toHaveTextContent('blocked by guardrails')
})

test('edit starts from the artifact markdown', async () => {
  const fetchMock = stubApi()
  render(<GatePanel runId="run_1" gate={gate()} />)
  await screen.findByLabelText('Artifact')
  fireEvent.click(screen.getByRole('button', { name: 'Edit' }))
  const editor = screen.getByLabelText('Edited artifact')
  expect(editor).toHaveValue(ARTIFACT.markdown)
  fireEvent.change(editor, { target: { value: '# Design\n\nEdited.\n' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save and approve' }))
  await waitFor(() => expect(lastBody(fetchMock)).toEqual({ action: 'edit', markdown: '# Design\n\nEdited.\n' }))
})

test('only the actions the gate offers are shown', () => {
  stubApi()
  const escalation = gate({
    gate: 'escalation',
    actions: ['approve', 'revise', 'abort'],
    artifact_id: null,
    reason: 'The architect agent failed.',
  })
  render(<GatePanel runId="run_1" gate={escalation} />)
  expect(screen.getByRole('heading', { name: 'Needs your decision' })).toBeInTheDocument()
  expect(screen.getByText('The architect agent failed.')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Abort run' })).toBeInTheDocument()
})
