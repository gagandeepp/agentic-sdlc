import { useState } from 'react'
import { abortRun, errorMessage, resumeRun } from '../../lib/api'
import type { RunViewState } from '../../store/reducer'

const LIVE = new Set<RunViewState['status']>(['pending', 'running', 'waiting_gate'])
const BUTTON = 'rounded px-2 py-1 text-xs font-medium disabled:opacity-50'

interface Props {
  runId: string
  status: RunViewState['status']
}

export function RunControls({ runId, status }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function act(action: (runId: string) => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action(runId)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const interrupted = status === 'interrupted'
  if (!LIVE.has(status) && !interrupted) return null
  return (
    <div className="flex items-center gap-2">
      {interrupted && (
        <button type="button" disabled={busy} onClick={() => void act(resumeRun)} className={`${BUTTON} bg-indigo-600 hover:bg-indigo-500`}>
          Resume
        </button>
      )}
      <button type="button" disabled={busy} onClick={() => void act(abortRun)} className={`${BUTTON} border border-red-800 text-red-300 hover:bg-red-950`}>
        Abort
      </button>
      {error && (
        <span role="alert" className="text-xs text-red-300">
          {error}
        </span>
      )}
    </div>
  )
}
