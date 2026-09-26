import { useEffect, useState } from 'react'
import Dashboard from './pages/Dashboard.jsx'
import Login from './pages/Login.jsx'
import PayQr from './pages/PayQr.jsx'

const STORAGE_KEY = 'mqs.session'

function readSession() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    // A corrupt or unreadable entry should log the user out, not crash the app.
    return null
  }
}

export default function App() {
  const [session, setSession] = useState(readSession)
  const [view, setView] = useState('dashboard')

  useEffect(() => {
    if (session) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session))
    } else {
      window.localStorage.removeItem(STORAGE_KEY)
    }
  }, [session])

  if (!session) {
    return <Login onAuthenticated={setSession} />
  }

  return (
    <>
      <nav className="border-b border-slate-800 bg-slate-950 px-6 py-3">
        <div className="mx-auto flex max-w-6xl items-center gap-4">
          <button
            type="button"
            onClick={() => setView('dashboard')}
            className={`text-sm font-semibold ${
              view === 'dashboard' ? 'text-emerald-400' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Dashboard
          </button>
          <button
            type="button"
            onClick={() => setView('pay')}
            className={`text-sm font-semibold ${
              view === 'pay' ? 'text-emerald-400' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Simulate a customer paying
          </button>
        </div>
      </nav>
      {view === 'dashboard' ? (
        <Dashboard session={session} onLogout={() => setSession(null)} />
      ) : (
        <PayQr session={session} />
      )}
    </>
  )
}
