import { useEffect } from 'react'
import { eventsUrl } from '../lib/api'
import { useRunStore } from '../store/runStore'
import type { RunEvent } from '../types/api'

const TERMINAL = new Set<RunEvent['type']>(['run.completed', 'run.failed', 'run.aborted', 'run.interrupted'])

/**
 * Streams a run's events into the run store. EventSource reconnects on its own and sends
 * Last-Event-ID, and the reducer ignores already-seen seqs, so reconnects never duplicate.
 */
export function useRunEvents(runId: string): void {
  const reset = useRunStore((s) => s.reset)
  const apply = useRunStore((s) => s.apply)

  useEffect(() => {
    reset(runId)
    const source = new EventSource(eventsUrl(runId))
    let buffer: RunEvent[] = []
    let frame = 0
    const flush = () => {
      frame = 0
      const batch = buffer
      buffer = []
      apply(batch)
    }
    source.onmessage = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent
      buffer.push(event)
      if (frame === 0) frame = requestAnimationFrame(flush) // one store update per frame
      if (TERMINAL.has(event.type)) source.close()
    }
    return () => {
      if (frame !== 0) cancelAnimationFrame(frame)
      source.close()
    }
  }, [runId, reset, apply])
}
