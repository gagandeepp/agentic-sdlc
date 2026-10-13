import { fireEvent, render, screen } from '@testing-library/react'
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

test('follows new events only while the reader is at the bottom', () => {
  vi.restoreAllMocks() // no layout stub: rows are not needed, only the scroll container
  const { rerender } = render(<Timeline events={fixtureEvents.slice(0, 3)} />)
  const log = screen.getByRole('log', { name: 'Run timeline' })
  let scrollTop = 1360
  const scrollRequests: number[] = []
  Object.defineProperty(log, 'scrollHeight', { configurable: true, get: () => 2000 })
  Object.defineProperty(log, 'clientHeight', { configurable: true, get: () => 640 })
  Object.defineProperty(log, 'scrollTop', {
    configurable: true,
    get: () => scrollTop,
    set: (value: number) => {
      scrollRequests.push(value)
      scrollTop = value
    },
  })
  log.scrollTo = ((options: ScrollToOptions) => scrollRequests.push(options.top ?? -1)) as typeof log.scrollTo

  rerender(<Timeline events={fixtureEvents.slice(0, 4)} />)
  expect(scrollRequests.length).toBeGreaterThan(0) // at the bottom: keeps following

  scrollTop = 200 // the reader scrolls up to read earlier messages
  fireEvent.scroll(log)
  scrollRequests.length = 0
  rerender(<Timeline events={fixtureEvents.slice(0, 5)} />)
  expect(scrollRequests).toEqual([]) // left where they are

  scrollTop = 1360 // back at the bottom
  fireEvent.scroll(log)
  rerender(<Timeline events={fixtureEvents.slice(0, 6)} />)
  expect(scrollRequests.length).toBeGreaterThan(0)
})

test('shows an empty state', () => {
  render(<Timeline events={[]} />)
  expect(screen.getByText('Waiting for events…')).toBeInTheDocument()
})
