import { useEffect, useState } from 'react'
import { apiRequest } from './api.js'

const PROBES = [
  { path: '/health', label: 'Liveness' },
  { path: '/health/ready', label: 'Readiness (PostgreSQL)' },
]

export default function App() {
  const [probes, setProbes] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false

    async function run() {
      const results = await Promise.all(
        PROBES.map(async ({ path, label }) => {
          try {
            const data = await apiRequest(path)
            return { label, path, ok: true, data }
          } catch (err) {
            return { label, path, ok: false, data: { detail: err.message } }
          }
        }),
      )
      if (!cancelled) {
        setProbes(results)
        const failed = results.filter((r) => !r.ok)
        setError(failed.length ? `${failed.length} probe(s) failed` : null)
      }
    }

    run()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-6 py-16">
      <header className="space-y-2">
        <p className="text-sm font-semibold uppercase tracking-widest text-emerald-400">
          Merchant QR Settlement
        </p>
        <h1 className="text-3xl font-bold">Stack scaffold online</h1>
        <p className="text-slate-400">
          The React frontend is talking to the FastAPI backend through docker-compose.
        </p>
      </header>

      <section className="space-y-3">
        {probes.length === 0 && <p className="text-slate-500">Contacting backend...</p>}
        {probes.map((probe) => (
          <div
            key={probe.path}
            className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3"
          >
            <div>
              <p className="font-medium">{probe.label}</p>
              <p className="text-xs text-slate-500">{probe.path}</p>
            </div>
            <span
              className={`rounded-full px-3 py-1 text-xs font-semibold ${
                probe.ok ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
              }`}
            >
              {probe.ok ? 'ok' : 'failed'}
            </span>
          </div>
        ))}
      </section>

      {error && (
        <p className="rounded-lg border border-red-900 bg-red-950/50 px-4 py-3 text-sm text-red-300">
          {error}
        </p>
      )}

      <footer className="text-sm text-slate-500">
        API docs at <span className="text-slate-300">http://localhost:8000/docs</span>
      </footer>
    </main>
  )
}
