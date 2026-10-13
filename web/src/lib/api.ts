import type { ArtifactRecord, RunRecord } from '../types/api'

export const API_BASE: string = import.meta.env.VITE_API_URL ?? ''

function parseDetail(body: string): string {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown }
    if (typeof parsed.detail === 'string') return parsed.detail
    if (Array.isArray(parsed.detail)) return 'the request was not valid'
  } catch {
    // not JSON: use the raw body
  }
  return body
}

export class ApiError extends Error {
  readonly detail: string

  constructor(
    readonly status: number,
    body: string,
  ) {
    super(`API ${status}: ${body}`)
    this.detail = parseDetail(body)
  }
}

/** A message for people: the API's `detail` when it sent one. */
export const errorMessage = (error: unknown): string =>
  error instanceof ApiError ? `${error.detail} (HTTP ${error.status})` : String(error)

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  if (!response.ok) throw new ApiError(response.status, await response.text())
  return (await response.json()) as T
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(
    path,
    body === undefined
      ? { method: 'POST' }
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) },
  )

const runPath = (runId: string) => `/api/runs/${encodeURIComponent(runId)}`

export type GateAction = 'approve' | 'revise' | 'edit' | 'abort'

export interface GateDecision {
  action: GateAction
  feedback?: string
  markdown?: string
}

export interface NewRunRequest {
  requirement: string
  repo: string
  base_branch: string
  budget_usd: number
}

export const listRuns = () => request<RunRecord[]>('/api/runs')

export const getRun = (runId: string) => request<RunRecord>(runPath(runId))

export const startFakeRun = (speed = 2) =>
  request<RunRecord>(`/api/dev/fake-runs?speed=${speed}`, { method: 'POST' })

export const startRun = (run: NewRunRequest) => post<RunRecord>('/api/runs', run)

export const startScriptedRun = (requirement: string) =>
  post<RunRecord>('/api/dev/scripted-runs', { requirement })

export const decideGate = (runId: string, gateId: string, decision: GateDecision) =>
  post<{ gate_id: string; action: GateAction }>(`${runPath(runId)}/gates/${encodeURIComponent(gateId)}`, decision)

export const abortRun = (runId: string) => post<RunRecord>(`${runPath(runId)}/abort`)

export const resumeRun = (runId: string) => post<RunRecord>(`${runPath(runId)}/resume`)

export const listArtifacts = (runId: string) => request<ArtifactRecord[]>(`${runPath(runId)}/artifacts`)

export const getArtifact = (runId: string, artifactId: string) =>
  request<ArtifactRecord>(`${runPath(runId)}/artifacts/${encodeURIComponent(artifactId)}`)

export const eventsUrl = (runId: string, after = 0) =>
  `${API_BASE}${runPath(runId)}/events${after > 0 ? `?after=${after}` : ''}`
