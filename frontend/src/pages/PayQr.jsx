import { useState } from 'react'
import { apiRequest } from '../api.js'
import { Banner, Card } from '../components/ui.jsx'
import { formatMoney, formatTime } from '../format.js'

/**
 * Stands in for the customer wallet app.
 *
 * A real integration would be a phone camera decoding the QR payload; here the
 * merchant issues a signed invoice and a wallet pays it, which exercises the
 * same endpoints a scanner would hit.
 */
export default function PayQr({ session }) {
  const merchant = session.merchant
  const [amount, setAmount] = useState('250.00')
  const [invoice, setInvoice] = useState(null)
  const [walletId, setWalletId] = useState('')
  const [wallets, setWallets] = useState([])
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  // The idempotency key is what makes a retried scan safe. Reusing the same
  // key in this form is the easy way to demonstrate a replay.
  const [idemKey, setIdemKey] = useState(`ui-${Date.now()}`)

  async function issueInvoice() {
    setBusy(true)
    setError(null)
    try {
      setInvoice(
        await apiRequest(`/merchants/${merchant.id}/invoices`, {
          method: 'POST',
          token: session.access_token,
          body: { amount },
        }),
      )
      setResult(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function loadWallets() {
    setBusy(true)
    setError(null)
    try {
      // The wallet endpoint is unauthenticated by design; this helper just
      // lists wallets so the demo has something to pay from.
      const created = await apiRequest('/wallets', {
        method: 'POST',
        body: { customer_id: `ui-${Date.now()}`, initial_balance: '2000.00' },
      })
      setWallets((current) => [...current, created])
      setWalletId(String(created.id))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function pay() {
    setBusy(true)
    setError(null)
    try {
      setResult(
        await apiRequest('/transactions', {
          method: 'POST',
          headers: { 'Idempotency-Key': idemKey },
          body: {
            merchant_id: merchant.id,
            wallet_id: Number(walletId),
            amount: invoice.amount,
            invoice_token: invoice.qr_payload,
          },
        }),
      )
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="min-h-screen bg-slate-950 px-6 py-10">
      <div className="mx-auto max-w-3xl space-y-6">
        <header className="space-y-2">
          <p className="text-sm font-semibold uppercase tracking-widest text-emerald-400">
            Customer wallet simulator
          </p>
          <h1 className="text-3xl font-bold">Pay {merchant.name}</h1>
          <p className="text-sm text-slate-400">
            Issues a signed invoice, then pays it exactly as a phone scanning the QR would.
          </p>
        </header>

        <Banner tone="bad">{error}</Banner>

        <Card title="1. Issue an invoice">
          <div className="flex flex-wrap items-end gap-3">
            <label className="space-y-1">
              <span className="block text-sm text-slate-300">Amount</span>
              <input
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                inputMode="decimal"
                className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-emerald-500"
              />
            </label>
            <button
              type="button"
              onClick={issueInvoice}
              disabled={busy}
              className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
            >
              Issue invoice
            </button>
          </div>

          {invoice && (
            <div className="mt-4 space-y-1 rounded-lg border border-slate-800 bg-slate-950 p-4 text-xs">
              <p className="text-slate-400">
                Reference <span className="font-mono text-slate-200">{invoice.invoice_ref}</span>
              </p>
              <p className="text-slate-400">
                {formatMoney(invoice.amount)} {invoice.currency}, expires in {invoice.expires_in}s
              </p>
              <p className="break-all font-mono text-slate-500">{invoice.qr_payload}</p>
            </div>
          )}
        </Card>

        <Card title="2. Pay it from a wallet">
          <div className="flex flex-wrap items-end gap-3">
            <label className="space-y-1">
              <span className="block text-sm text-slate-300">Wallet</span>
              <input
                value={walletId}
                onChange={(event) => setWalletId(event.target.value)}
                inputMode="numeric"
                placeholder="wallet id"
                className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-emerald-500"
              />
            </label>
            <button
              type="button"
              onClick={loadWallets}
              disabled={busy}
              className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-slate-500 disabled:opacity-50"
            >
              Create a wallet
            </button>
            <label className="space-y-1">
              <span className="block text-sm text-slate-300">Idempotency key</span>
              <input
                value={idemKey}
                onChange={(event) => setIdemKey(event.target.value)}
                className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-emerald-500"
              />
            </label>
            <button
              type="button"
              onClick={pay}
              disabled={busy || !invoice || !walletId}
              className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-emerald-400 disabled:opacity-50"
            >
              Pay
            </button>
          </div>

          {wallets.length > 0 && (
            <p className="mt-3 text-xs text-slate-500">
              Wallets created in this session:{' '}
              {wallets.map((wallet) => `#${wallet.id} (${formatMoney(wallet.balance)})`).join(', ')}
            </p>
          )}
          <p className="mt-2 text-xs text-slate-500">
            Press Pay twice with the same key: the second call returns the original transaction
            with <code>idempotent_replay: true</code> instead of charging again.
          </p>
        </Card>

        {result && (
          <Card title="Payment result">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              <dt className="text-slate-400">Transaction</dt>
              <dd>#{result.id}</dd>
              <dt className="text-slate-400">Amount</dt>
              <dd className="font-semibold">{formatMoney(result.amount)}</dd>
              <dt className="text-slate-400">Status</dt>
              <dd>{result.status}</dd>
              <dt className="text-slate-400">Replayed</dt>
              <dd>{result.idempotent_replay ? 'yes (no new charge)' : 'no (first charge)'}</dd>
              <dt className="text-slate-400">Created</dt>
              <dd>{formatTime(result.created_at)}</dd>
            </dl>
          </Card>
        )}
      </div>
    </main>
  )
}
