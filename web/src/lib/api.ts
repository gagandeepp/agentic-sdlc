import type { RunRecord } from '../types/api'

export const API_BASE: string = import.meta.env.VITE_API_URL ?? ''

export class ApiError extends Error {
  constructor(
    readonly status: number,
    body: string,
  ) {
    super(`API ${status}: ${body}`)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  if (!response.ok) throw new ApiError(response.status, await response.text())
  return (await response.json()) as T
}

export const listRuns = () => request<RunRecord[]>('/api/runs')

export const getRun = (runId: string) => request<RunRecord>(`/api/runs/${encodeURIComponent(runId)}`)

export const startFakeRun = (speed = 2) =>
  request<RunRecord>(`/api/dev/fake-runs?speed=${speed}`, { method: 'POST' })

export const eventsUrl = (runId: string, after = 0) =>
  `${API_BASE}/api/runs/${encodeURIComponent(runId)}/events${after > 0 ? `?after=${after}` : ''}`
