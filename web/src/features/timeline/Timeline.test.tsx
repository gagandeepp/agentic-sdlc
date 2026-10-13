import { render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { fixtureEvents } from '../../test/fixture'
import { Timeline } from './Timeline'

// jsdom has no layout (offsetHeight is always 0), so the virtualizer would see an empty viewport.
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(640)
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(380)
})
afterEach(() => vi.restoreAllMocks())

test('renders a row per event with its summary', () => {
  render(<Timeline events={fixtureEvents.slice(0, 6)} follow={false} />)
  const log = screen.getByRole('log', { name: 'Run timeline' })
  expect(log).toBeInTheDocument()
  expect(screen.getByText('Run created')).toBeInTheDocument()
  expect(screen.getByText('ba ⚙ Grep')).toBeInTheDocument()
})

test('shows an empty state', () => {
  render(<Timeline events={[]} />)
  expect(screen.getByText('Waiting for events…')).toBeInTheDocument()
})
