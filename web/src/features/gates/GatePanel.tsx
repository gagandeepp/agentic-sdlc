import { useEffect, useState } from 'react'
import { decideGate, errorMessage, getArtifact, type GateAction } from '../../lib/api'
import type { ArtifactRecord, GateOpenedEvent } from '../../types/api'

const TITLES: Record<GateOpenedEvent['payload']['gate'], string> = {
  requirements: 'Approve the requirements',
  design: 'Approve the design',
  release: 'Release',
  escalation: 'Needs your decision',
}

const BUTTON = 'rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50'

type Mode = 'view' | 'revise' | 'edit'

interface Props {
  runId: string
  gate: GateOpenedEvent
}

/** The open gate: its artifact and the decisions it allows. Render it with key={gate_id}. */
export function GatePanel({ runId, gate }: Props) {
  const { gate_id: gateId, actions, artifact_id: artifactId, reason } = gate.payload
  const [artifact, setArtifact] = useState<ArtifactRecord | null>(null)
  const [mode, setMode] = useState<Mode>('view')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!artifactId) return
    let cancelled = false
    getArtifact(runId, artifactId).then(
      (loaded) => {
        if (!cancelled) setArtifact(loaded)
      },
      (e: unknown) => {
        if (!cancelled) setError(`Could not load the artifact: ${errorMessage(e)}`)
      },
    )
    return () => {
      cancelled = true
    }
  }, [runId, artifactId])

  async function decide(action: GateAction) {
    setBusy(true)
    setError(null)
    try {
      if (action === 'revise') await decideGate(runId, gateId, { action, feedback: text })
      else if (action === 'edit') await decideGate(runId, gateId, { action, markdown: text })
      else await decideGate(runId, gateId, { action })
      // gate.resolved arrives on the event stream and closes this panel.
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  function begin(next: Mode) {
    setText(next === 'edit' ? (artifact?.markdown ?? '') : '')
    setMode(next)
  }

  return (
    <section aria-label="Gate" className="mb-6 rounded-lg border border-violet-700 bg-violet-950/30 p-4">
      <h2 className="font-semibold">{TITLES[gate.payload.gate]}</h2>
      {reason && <p className="mt-1 text-sm text-slate-300">{reason}</p>}
      {artifact && mode !== 'edit' && (
        <pre aria-label="Artifact" className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-3 text-xs">
          {artifact.markdown}
        </pre>
      )}
      {mode !== 'view' && (
        <textarea
          aria-label={mode === 'edit' ? 'Edited artifact' : 'Feedback'}
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={mode === 'edit' ? 16 : 4}
          className="mt-3 w-full rounded bg-slate-950 p-2 font-mono text-xs"
        />
      )}
      {error && (
        <p role="alert" className="mt-3 rounded border border-red-900 bg-red-950 p-2 text-sm text-red-300">
          {error}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {mode === 'view' ? (
          <>
            {actions.includes('approve') && (
              <button type="button" disabled={busy} onClick={() => void decide('approve')} className={`${BUTTON} bg-emerald-700 hover:bg-emerald-600`}>
                Approve
              </button>
            )}
            {actions.includes('revise') && (
              <button type="button" disabled={busy} onClick={() => begin('revise')} className={`${BUTTON} border border-slate-600 hover:bg-slate-800`}>
                Request changes
              </button>
            )}
            {actions.includes('edit') && artifact && (
              <button type="button" disabled={busy} onClick={() => begin('edit')} className={`${BUTTON} border border-slate-600 hover:bg-slate-800`}>
                Edit
              </button>
            )}
            {actions.includes('abort') && (
              <button type="button" disabled={busy} onClick={() => void decide('abort')} className={`${BUTTON} ml-auto border border-red-800 text-red-300 hover:bg-red-950`}>
                Abort run
              </button>
            )}
          </>
        ) : (
          <>
            <button type="button" disabled={busy || !text.trim()} onClick={() => void decide(mode)} className={`${BUTTON} bg-indigo-600 hover:bg-indigo-500`}>
              {mode === 'edit' ? 'Save and approve' : 'Send feedback'}
            </button>
            <button type="button" disabled={busy} onClick={() => setMode('view')} className={`${BUTTON} border border-slate-600 hover:bg-slate-800`}>
              Cancel
            </button>
          </>
        )}
      </div>
    </section>
  )
}
