import { useEffect, useState } from 'react'
import { errorMessage, listArtifacts } from '../../lib/api'
import type { ArtifactRecord } from '../../types/api'

interface Props {
  runId: string
  /** Bumps whenever an artifact event arrives, so the list refetches. */
  version: number
}

export function ArtifactsList({ runId, version }: Props) {
  const [artifacts, setArtifacts] = useState<ArtifactRecord[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (version === 0) return
    let cancelled = false
    listArtifacts(runId).then(
      (list) => {
        if (cancelled) return
        setArtifacts(list)
        setError(null)
      },
      (e: unknown) => {
        if (!cancelled) setError(errorMessage(e))
      },
    )
    return () => {
      cancelled = true
    }
  }, [runId, version])

  if (artifacts.length === 0 && !error) return null
  const open = artifacts.find((a) => a.id === selected)
  return (
    <section aria-label="Artifacts" className="mt-6">
      <h2 className="mb-2 text-sm font-semibold text-slate-300">Artifacts</h2>
      {error && (
        <p role="alert" className="mb-2 text-xs text-red-300">
          Could not load artifacts: {error}
        </p>
      )}
      <ul className="flex flex-wrap gap-2 text-xs">
        {artifacts.map((a) => (
          <li key={a.id}>
            <button
              type="button"
              aria-pressed={a.id === selected}
              onClick={() => setSelected(a.id === selected ? null : a.id)}
              className={`rounded border px-2 py-1 ${a.id === selected ? 'border-sky-500 text-sky-200' : 'border-slate-700 text-slate-300'}`}
            >
              {a.title} v{a.version}
              {a.edited_by_user ? ' (edited)' : ''}
            </button>
          </li>
        ))}
      </ul>
      {open && (
        <pre aria-label="Artifact content" className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-3 text-xs">
          {open.markdown}
        </pre>
      )}
    </section>
  )
}
