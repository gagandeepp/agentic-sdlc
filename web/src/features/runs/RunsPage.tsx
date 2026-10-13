import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { formatUsd } from '../../lib/format'
import { listRuns, startFakeRun } from '../../lib/api'
import type { RunRecord } from '../../types/api'

export function RunsPage() {
  const [runs, setRuns] = useState<RunRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    listRuns()
      .then(setRuns)
      .catch((e: unknown) => setError(String(e)))
  }, [])

  async function startDemo() {
    try {
      const run = await startFakeRun()
      await navigate(`/runs/${run.id}`)
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <section className="mx-auto max-w-5xl p-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Runs</h1>
        <button
          type="button"
          onClick={() => void startDemo()}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium hover:bg-indigo-500"
        >
          Start demo run
        </button>
      </div>
      {error && (
        <p role="alert" className="mb-4 rounded-md border border-red-900 bg-red-950 p-3 text-sm text-red-300">
          {error}
        </p>
      )}
      {runs === null && !error && <p className="text-slate-400">Loading…</p>}
      {runs?.length === 0 && <p className="text-slate-400">No runs yet. Start a demo run to see the pipeline.</p>}
      {runs && runs.length > 0 && (
        <table className="w-full text-left text-sm">
          <thead className="text-slate-400">
            <tr>
              <th className="py-2 font-normal">Requirement</th>
              <th className="py-2 font-normal">Status</th>
              <th className="py-2 font-normal">Phase</th>
              <th className="py-2 font-normal">Spend</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} className="border-t border-slate-800">
                <td className="py-2">
                  <Link to={`/runs/${run.id}`} className="text-sky-300 hover:underline">
                    {run.requirement}
                  </Link>
                </td>
                <td className="py-2">{run.status}</td>
                <td className="py-2">{run.phase ?? '–'}</td>
                <td className="py-2 font-mono">
                  {formatUsd(run.spent_usd)} / {formatUsd(run.budget_usd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
