import { Link, Route, Routes } from 'react-router'
import { RunsPage } from './features/runs/RunsPage'

export default function App() {
  return (
    <div className="min-h-screen">
      <nav className="flex h-14 items-center border-b border-slate-800 px-6">
        <Link to="/" className="font-semibold tracking-tight">
          agentic-sdlc
        </Link>
      </nav>
      <Routes>
        <Route path="/" element={<RunsPage />} />
      </Routes>
    </div>
  )
}
