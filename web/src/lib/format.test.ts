import { expect, test } from 'vitest'
import { formatTokens, formatUsd } from './format'

test('formats token counts compactly', () => {
  expect(formatTokens(950)).toBe('950')
  expect(formatTokens(21_500)).toBe('21.5k')
  expect(formatTokens(1_250_000)).toBe('1.25M')
})

test('formats dollars', () => {
  expect(formatUsd(0.87612)).toBe('$0.88')
  expect(formatUsd(0.0121, 3)).toBe('$0.012')
})
