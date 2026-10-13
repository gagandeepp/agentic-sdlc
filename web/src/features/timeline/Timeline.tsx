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

/** How close to the bottom (px) still counts as "reading the latest events". */
const PIN_THRESHOLD = 48

export function Timeline({ events, follow = true }: { events: RunEvent[]; follow?: boolean }) {
  const parentRef = useRef<HTMLDivElement>(null)
  // Follow the tail only while the reader is at the bottom; scrolling up to read stops it.
  const pinnedRef = useRef(true)
  const virtualizer = useVirtualizer({
    count: events.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 52,
    overscan: 8,
    initialRect: { width: 380, height: 640 },
  })

  const onScroll = () => {
    const el = parentRef.current
    if (el) pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < PIN_THRESHOLD
  }

  useEffect(() => {
    const el = parentRef.current
    if (follow && pinnedRef.current && el) el.scrollTop = el.scrollHeight
  }, [follow, events.length])

  if (events.length === 0) {
    return <p className="p-4 text-sm text-slate-500">Waiting for events…</p>
  }

  return (
    <div ref={parentRef} onScroll={onScroll} role="log" aria-label="Run timeline" className="h-full overflow-auto">
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
