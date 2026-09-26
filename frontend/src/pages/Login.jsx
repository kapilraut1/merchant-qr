import { useState } from 'react'
import { apiRequest } from '../api.js'

export default function Login({ onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [form, setForm] = useState({ name: '', email: '', password: '', commission_rate: '0.02' })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const isRegister = mode === 'register'
  const canSubmit = form.email.trim() && form.password && (!isRegister || form.name.trim())

  function update(field) {
    return (event) => setForm({ ...form, [field]: event.target.value })
  }

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const path = isRegister ? '/merchants/register' : '/merchants/login'
      const body = isRegister
        ? {
            name: form.name.trim(),
            email: form.email.trim(),
            password: form.password,
            commission_rate: form.commission_rate || '0.02',
          }
        : { email: form.email.trim(), password: form.password }
      const data = await apiRequest(path, { method: 'POST', body })
      onAuthenticated(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-16">
      <header className="mb-8 space-y-2">
        <p className="text-sm font-semibold uppercase tracking-widest text-emerald-400">
          Merchant QR Settlement
        </p>
        <h1 className="text-3xl font-bold">{isRegister ? 'Create a merchant' : 'Merchant sign in'}</h1>
        <p className="text-sm text-slate-400">
          {isRegister
            ? 'Register to get a commission rate and a settlement history.'
            : 'Use the email and password you registered with.'}
        </p>
      </header>

      <form onSubmit={submit} className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/60 p-6">
        {isRegister && (
          <label className="block space-y-1">
            <span className="text-sm font-medium text-slate-300">Business name</span>
            <input
              value={form.name}
              onChange={update('name')}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-emerald-500"
              placeholder="Everest Coffee House"
              autoComplete="organization"
            />
          </label>
        )}

        <label className="block space-y-1">
          <span className="text-sm font-medium text-slate-300">Email</span>
          <input
            type="email"
            value={form.email}
            onChange={update('email')}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-emerald-500"
            placeholder="ops@everestcoffee.example"
            autoComplete="email"
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm font-medium text-slate-300">Password</span>
          <input
            type="password"
            value={form.password}
            onChange={update('password')}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-emerald-500"
            placeholder="At least 8 characters"
            autoComplete={isRegister ? 'new-password' : 'current-password'}
          />
        </label>

        {isRegister && (
          <label className="block space-y-1">
            <span className="text-sm font-medium text-slate-300">Commission rate</span>
            <input
              value={form.commission_rate}
              onChange={update('commission_rate')}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-emerald-500"
              placeholder="0.02"
              inputMode="decimal"
            />
            <span className="text-xs text-slate-500">
              A fraction: 0.02 is 2%. Frozen into each batch at settlement time.
            </span>
          </label>
        )}

        {error && (
          <p className="rounded-lg border border-red-900 bg-red-950/50 px-3 py-2 text-sm text-red-300">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={!canSubmit || busy}
          className="w-full rounded-lg bg-emerald-500 px-4 py-2 font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? 'Working...' : isRegister ? 'Create account' : 'Sign in'}
        </button>

        <button
          type="button"
          onClick={() => {
            setMode(isRegister ? 'login' : 'register')
            setError(null)
          }}
          className="w-full text-sm text-slate-400 underline-offset-2 hover:text-slate-200 hover:underline"
        >
          {isRegister ? 'Already registered? Sign in' : 'No account yet? Register one'}
        </button>
      </form>
    </main>
  )
}
