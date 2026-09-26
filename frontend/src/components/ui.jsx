const STATUS_STYLES = {
  completed: 'bg-emerald-500/15 text-emerald-400',
  pending: 'bg-amber-500/15 text-amber-400',
  failed: 'bg-red-500/15 text-red-400',
  reversed: 'bg-slate-500/15 text-slate-400',
}

export function StatusPill({ status }) {
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
        STATUS_STYLES[status] || 'bg-slate-500/15 text-slate-400'
      }`}
    >
      {status}
    </span>
  )
}

export function Card({ title, action, children, className = '' }) {
  return (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/60 ${className}`}>
      {(title || action) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-5 py-4">
          <h2 className="font-semibold">{title}</h2>
          {action}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  )
}

export function Stat({ label, value, hint, tone = 'default' }) {
  const toneClass =
    tone === 'good' ? 'text-emerald-400' : tone === 'bad' ? 'text-red-400' : 'text-slate-100'
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 px-5 py-4">
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-bold ${toneClass}`}>{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

export function Banner({ tone = 'info', children }) {
  if (!children) return null
  const tones = {
    info: 'border-slate-700 bg-slate-800/60 text-slate-200',
    good: 'border-emerald-900 bg-emerald-950/40 text-emerald-200',
    bad: 'border-red-900 bg-red-950/50 text-red-300',
  }
  return <p className={`rounded-lg border px-4 py-3 text-sm ${tones[tone]}`}>{children}</p>
}

export function EmptyState({ children }) {
  return <p className="py-6 text-center text-sm text-slate-500">{children}</p>
}
