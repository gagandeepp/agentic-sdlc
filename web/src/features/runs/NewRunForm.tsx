import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { errorMessage, startRun, startScriptedRun } from '../../lib/api'

export const DEFAULT_REPO = 'gagandeepp/agentic-sdlc-sandbox'

const field = 'rounded bg-slate-950 px-2 py-1.5 text-sm'

export function NewRunForm() {
  const navigate = useNavigate()
  const [requirement, setRequirement] = useState('')
  const [repo, setRepo] = useState(DEFAULT_REPO)
  const [baseBranch, setBaseBranch] = useState('main')
  const [budget, setBudget] = useState('5')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function launch(start: () => Promise<{ id: string }>) {
    setBusy(true)
    setError(null)
    try {
      const run = await start()
      await navigate(`/runs/${run.id}`)
    } catch (e) {
      setError(errorMessage(e))
      setBusy(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void launch(() =>
      startRun({
        requirement: requirement.trim(),
        repo: repo.trim(),
        base_branch: baseBranch.trim(),
        budget_usd: Number(budget),
      }),
    )
  }

  const empty = requirement.trim() === ''
  return (
    <form onSubmit={submit} aria-label="New run" className="mb-8 grid gap-3 rounded-lg border border-slate-800 bg-slate-900/40 p-4">
      <h2 className="text-lg font-semibold">New run</h2>
      <label className="grid gap-1 text-sm">
        <span className="text-slate-400">Requirement</span>
        <textarea
          value={requirement}
          onChange={(e) => setRequirement(e.target.value)}
          rows={3}
          maxLength={4000}
          placeholder="Add a /health endpoint with a test"
          className={field}
        />
      </label>
      <div className="grid grid-cols-[2fr_1fr_1fr] gap-3">
        <label className="grid gap-1 text-sm">
          <span className="text-slate-400">Repository</span>
          <input value={repo} onChange={(e) => setRepo(e.target.value)} className={field} />
        </label>
        <label className="grid gap-1 text-sm">
          <span className="text-slate-400">Base branch</span>
          <input value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)} className={field} />
        </label>
        <label className="grid gap-1 text-sm">
          <span className="text-slate-400">Budget (USD)</span>
          <input type="number" min={0.1} max={100} step="any" value={budget} onChange={(e) => setBudget(e.target.value)} className={field} />
        </label>
      </div>
      {error && (
        <p role="alert" className="rounded-md border border-red-900 bg-red-950 p-3 text-sm text-red-300">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-3">
        <button type="submit" disabled={busy || empty} className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium hover:bg-indigo-500 disabled:opacity-50">
          Start run
        </button>
        <button
          type="button"
          disabled={busy || empty}
          onClick={() => void launch(() => startScriptedRun(requirement.trim()))}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-sm hover:bg-slate-800 disabled:opacity-50"
        >
          Scripted demo (no keys)
        </button>
      </div>
    </form>
  )
}
