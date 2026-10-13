import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { expect, test } from 'vitest'
import App from './App'

test('renders the shell with the runs page at /', () => {
  render(
    <MemoryRouter initialEntries={['/']}>
      <App />
    </MemoryRouter>,
  )
  expect(screen.getByRole('link', { name: 'agentic-sdlc' })).toHaveAttribute('href', '/')
  expect(screen.getByRole('heading', { name: 'Runs' })).toBeInTheDocument()
})
