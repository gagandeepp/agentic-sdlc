import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useRef } from 'react'
import type { RunEvent } from '../../types/api'
import { describeEvent, type Tone } from './describeEvent'

const TONE: Record<Tone, string> = {
  neutral: 'text-slate-200',
  info: 'text-sky-300',
  success: 'text-emerald-300',
  warning: 'text-amber-300',
  danger: 'text-red-400',
}

const time = (ts: string) => new Date(ts).toLocaleTimeString([], { hour12: false })

export function Timeline({ events, follow = true }: { events: RunEvent[]; follow?: boolean }) {
  const parentRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: events.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 52,
    overscan: 8,
    initialRect: { width: 380, height: 640 },
  })

  useEffect(() => {
    if (follow && events.length > 0) virtualizer.scrollToIndex(events.length - 1, { align: 'end' })
  }, [follow, events.length, virtualizer])

  if (events.length === 0) {
    return <p className="p-4 text-sm text-slate-500">Waiting for events…</p>
  }

  return (
    <div ref={parentRef} role="log" aria-label="Run timeline" className="h-full overflow-auto">
      <ol className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
        {virtualizer.getVirtualItems().map((item) => {
          const event = events[item.index]!
          const summary = describeEvent(event)
          return (
            <li
              key={event.seq}
              data-index={item.index}
              ref={virtualizer.measureElement}
              className="absolute left-0 top-0 w-full border-b border-slate-900 px-4 py-2"
              style={{ transform: `translateY(${item.start}px)` }}
            >
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-[11px] text-slate-500">{time(event.ts)}</span>
                <span className={`text-sm font-medium ${TONE[summary.tone]}`}>{summary.title}</span>
              </div>
              {summary.detail && <p className="truncate text-xs text-slate-400">{summary.detail}</p>}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
