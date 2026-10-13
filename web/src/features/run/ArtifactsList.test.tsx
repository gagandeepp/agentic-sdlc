import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { ArtifactRecord } from '../../types/api'
import { ArtifactsList } from './ArtifactsList'

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const DESIGN: ArtifactRecord = {
  id: 'art_1',
  run_id: 'run_1',
  kind: 'design',
  version: 1,
  title: 'Design',
  data: {},
  markdown: '# Design v1',
  edited_by_user: false,
  created_at: '2026-10-13T09:00:00Z',
}

afterEach(() => vi.unstubAllGlobals())

test('lists artifacts and shows one when clicked', async () => {
  const edited = { ...DESIGN, id: 'art_2', version: 2, edited_by_user: true, markdown: '# Design v2' }
  vi.stubGlobal('fetch', vi.fn(async () => json([DESIGN, edited])))
  render(<ArtifactsList runId="run_1" version={2} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Design v2 (edited)' }))
  expect(screen.getByLabelText('Artifact content')).toHaveTextContent('# Design v2')
})

test('renders nothing and fetches nothing before the first artifact', () => {
  const fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  const { container } = render(<ArtifactsList runId="run_1" version={0} />)
  expect(container).toBeEmptyDOMElement()
  expect(fetchMock).not.toHaveBeenCalled()
})
