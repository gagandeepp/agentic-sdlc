import { useEffect } from 'react'
import { ApiError, eventsUrl, getRun } from '../lib/api'
import { useRunStore } from '../store/runStore'
import type { RunEvent } from '../types/api'

const TERMINAL = new Set<RunEvent['type']>(['run.completed', 'run.failed', 'run.aborted'])
const RETRY_MS = [500, 1_000, 2_000, 5_000, 10_000]

/**
 * Streams a run's events into the run store until the run completes, fails or is aborted (an
 * interrupted run can be resumed, so its stream stays open). EventSource reconnects on its own after network
 * drops (sending Last-Event-ID), but gives up for good on a non-200 reply (proxy error, API
 * restart, database outage); then this hook reopens from the last seen seq with backoff, or
 * reports a run that does not exist. The reducer ignores already-seen seqs, so resumes never
 * duplicate.
 */
export function useRunEvents(runId: string): void {
  const reset = useRunStore((s) => s.reset)
  const apply = useRunStore((s) => s.apply)
  const setConnection = useRunStore((s) => s.setConnection)

  useEffect(() => {
    reset(runId)
    let source: EventSource | null = null
    let buffer: RunEvent[] = []
    let frame = 0
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let lastSeq = 0
    let done = false

    const flush = () => {
      frame = 0
      const batch = buffer
      buffer = []
      apply(batch)
    }

    const scheduleRetry = () => {
      if (done) return
      retryTimer = setTimeout(connect, RETRY_MS[Math.min(attempt, RETRY_MS.length - 1)])
      attempt += 1
    }

    const recover = () => {
      getRun(runId).then(scheduleRetry, (error: unknown) => {
        if (done) return
        if (error instanceof ApiError && error.status === 404) {
          done = true
          setConnection('not_found')
        } else {
          scheduleRetry()
        }
      })
    }

    function connect() {
      const current = new EventSource(eventsUrl(runId, lastSeq))
      source = current
      current.onopen = () => {
        attempt = 0
        setConnection('live')
      }
      current.onmessage = (message: MessageEvent<string>) => {
        const event = JSON.parse(message.data) as RunEvent
        lastSeq = Math.max(lastSeq, event.seq)
        buffer.push(event)
        if (frame === 0) frame = requestAnimationFrame(flush) // one store update per frame
        if (TERMINAL.has(event.type)) {
          done = true
          current.close()
          setConnection('ended')
        }
      }
      current.onerror = () => {
        if (done) return
        setConnection('reconnecting')
        if (current.readyState === EventSource.CLOSED) recover()
      }
    }

    connect()
    return () => {
      done = true
      if (frame !== 0) cancelAnimationFrame(frame)
      clearTimeout(retryTimer)
      source?.close()
    }
  }, [runId, reset, apply, setConnection])
}
