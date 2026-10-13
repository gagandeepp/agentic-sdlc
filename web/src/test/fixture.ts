import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { RunEvent } from '../types/api'

// Vitest runs from web/; under jsdom, import.meta.url is not a file: URL that fs accepts.
export const fixtureEvents: RunEvent[] = readFileSync(
  resolve(process.cwd(), '../fixtures/demo-runs/fake-basic.jsonl'),
  'utf8',
)
  .trim()
  .split('\n')
  .map((line) => JSON.parse(line) as RunEvent)
