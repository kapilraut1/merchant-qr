import { useCallback, useEffect, useState } from 'react'
import { apiRequest } from '../api.js'
import { Banner, Card, EmptyState, Stat, StatusPill } from '../components/ui.jsx'
import { formatMoney, formatPercent, formatTime, todayIso } from '../format.js'

export default function Dashboard({ session, onLogout }) {
  const merchant = session.merchant
  const [date, setDate] = useState(todayIso())
  const [day, setDay] = useState(null)
  const [settlement, setSettlement] = useState(null)
  const [reconciliation, setReconciliation] = useState(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)

  const loadDay = useCallback(async () => {
    try {
      setDay(await apiRequest(`/merchants/${merchant.id}/transactions?date=${date}`, {
        token: session.access_token,
      }))
    } catch (err) {
      setError(err.message)
    }
  }, [merchant.id, session.access_token, date])

  const loadSettlement = useCallback(async () => {
    try {
      setSettlement(
        await apiRequest(`/merchants/${merchant.id}/settlements?limit=25`, {
          token: session.access_token,
        }),
      )
    } catch (err) {
      setError(err.message)
    }
  }, [merchant.id, session.access_token])

  const loadReconciliation = useCallback(async () => {
    try {
      setReconciliation(
        await apiRequest(`/merchants/${merchant.id}/reconciliation`, {
          token: session.access_token,
        }),
      )
    } catch (err) {
      setError(err.message)
    }
  }, [merchant.id, session.access_token])

  useEffect(() => {
    setError(null)
    loadDay()
  }, [loadDay])
  useEffect(() => {
    loadSettlement()
    loadReconciliation()
  }, [loadSettlement, loadReconciliation])

  async function runSettlement() {
    setBusy(true)
    setNotice(null)
    setError(null)
    try {
      const result = await apiRequest(`/merchants/${merchant.id}/settle`, {
        method: 'POST',
        token: session.access_token,
        body: { batch_date: date },
      })
      setNotice(
        result.batch_id === null
          ? `Nothing to settle for ${date}: no unsettled completed payments.`
          : `Batch #${result.batch_id} settled ${result.transaction_count} payment(s). ` +
            `Commission ${formatMoney(result.commission_deducted)}, ` +
            `net payout ${formatMoney(result.net_payout)}.`,
      )
      await Promise.all([loadSettlement(), loadReconciliation(), loadDay()])
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function refreshReconciliation() {
    setBusy(true)
    setError(null)
    try {
      await loadReconciliation()
      setNotice('Reconciliation re-checked against the current ledger.')
    } finally {
      setBusy(false)
    }
  }

  const summary = reconciliation?.summary

  return (
    <div className="min-h-screen bg-slate-950 px-6 py-10">
      <div className="mx-auto max-w-6xl space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-sm font-semibold uppercase tracking-widest text-emerald-400">
              Merchant QR Settlement
            </p>
            <h1 className="text-3xl font-bold">{merchant.name}</h1>
            <p className="text-sm text-slate-400">
              {merchant.email} &middot; commission {formatPercent(merchant.commission_rate)}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-sm text-slate-400">
              Business date
              <input
                type="date"
                value={date}
                onChange={(event) => setDate(event.target.value)}
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100 outline-none focus:border-emerald-500"
              />
            </label>
            <button
              type="button"
              onClick={onLogout}
              className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-slate-500"
            >
              Sign out
            </button>
          </div>
        </header>

        <Banner tone="bad">{error}</Banner>
        {notice && <Banner tone="good">{notice}</Banner>}

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Stat
            label="Collected on this date"
            value={day ? formatMoney(day.total_amount) : '...'}
            hint="Completed payments only"
            tone="good"
          />
          <Stat
            label="Transactions"
            value={day ? day.count : '...'}
            hint={day && day.count > day.transactions.length ? 'List is truncated' : 'On this date'}
          />
          <Stat
            label="Batches settled"
            value={summary ? summary.settled_transaction_count : '...'}
            hint="Transactions covered by a batch"
          />
          <Stat
            label="Ledger status"
            value={summary ? (summary.is_balanced ? 'Balanced' : 'Mismatched') : '...'}
            hint={summary ? `${summary.orphaned_payment_count} orphaned` : ''}
            tone={summary ? (summary.is_balanced ? 'good' : 'bad') : 'default'}
          />
        </div>

        <Card
          title={`Transactions on ${date}`}
          action={
            <button
              type="button"
              onClick={runSettlement}
              disabled={busy}
              className="rounded-lg bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? 'Working...' : 'Run settlement'}
            </button>
          }
        >
          {!day ? (
            <EmptyState>Loading transactions...</EmptyState>
          ) : day.transactions.length === 0 ? (
            <EmptyState>
              No transactions on {date}. A payment appears here once a customer pays a QR invoice.
            </EmptyState>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="pb-2 pr-4">#</th>
                    <th className="pb-2 pr-4">Amount</th>
                    <th className="pb-2 pr-4">Status</th>
                    <th className="pb-2 pr-4">Wallet</th>
                    <th className="pb-2 pr-4">Time</th>
                    <th className="pb-2">Idempotency key</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {day.transactions.map((txn) => (
                    <tr key={txn.id}>
                      <td className="py-2 pr-4 text-slate-400">{txn.id}</td>
                      <td className="py-2 pr-4 font-semibold">{formatMoney(txn.amount)}</td>
                      <td className="py-2 pr-4">
                        <StatusPill status={txn.status} />
                      </td>
                      <td className="py-2 pr-4 text-slate-400">{txn.wallet_id}</td>
                      <td className="py-2 pr-4 text-slate-400">{formatTime(txn.created_at)}</td>
                      <td className="py-2 font-mono text-xs text-slate-500">{txn.idempotency_key}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Settlement history">
          {!settlement ? (
            <EmptyState>Loading batches...</EmptyState>
          ) : settlement.length === 0 ? (
            <EmptyState>
              No settlement batches yet. Use &ldquo;Run settlement&rdquo; to create one.
            </EmptyState>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="pb-2 pr-4">Batch</th>
                    <th className="pb-2 pr-4">Date</th>
                    <th className="pb-2 pr-4">Gross</th>
                    <th className="pb-2 pr-4">Commission</th>
                    <th className="pb-2">Net payout</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {settlement.map((batch) => (
                    <tr key={batch.id}>
                      <td className="py-2 pr-4 text-slate-400">#{batch.id}</td>
                      <td className="py-2 pr-4">{batch.batch_date}</td>
                      <td className="py-2 pr-4">{formatMoney(batch.total_amount)}</td>
                      <td className="py-2 pr-4 text-amber-400">
                        -{formatMoney(batch.commission_deducted)}
                      </td>
                      <td className="py-2 font-semibold text-emerald-400">
                        {formatMoney(batch.net_payout)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card
          title="Reconciliation"
          action={
            <button
              type="button"
              onClick={refreshReconciliation}
              disabled={busy}
              className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-slate-500 disabled:opacity-50"
            >
              Re-check
            </button>
          }
        >
          {!summary ? (
            <EmptyState>Loading report...</EmptyState>
          ) : (
            <div className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-lg border border-slate-800 px-4 py-3">
                  <p className="text-xs uppercase tracking-wider text-slate-500">
                    Orphaned payments
                  </p>
                  <p
                    className={`mt-1 text-xl font-bold ${
                      summary.orphaned_payment_count > 0 ? 'text-red-400' : 'text-emerald-400'
                    }`}
                  >
                    {summary.orphaned_payment_count}
                  </p>
                  <p className="text-xs text-slate-500">
                    {formatMoney(summary.total_orphaned_amount)} collected but never settled
                  </p>
                </div>
                <div className="rounded-lg border border-slate-800 px-4 py-3">
                  <p className="text-xs uppercase tracking-wider text-slate-500">Amount mismatches</p>
                  <p
                    className={`mt-1 text-xl font-bold ${
                      summary.amount_mismatch_count > 0 ? 'text-red-400' : 'text-emerald-400'
                    }`}
                  >
                    {summary.amount_mismatch_count}
                  </p>
                  <p className="text-xs text-slate-500">Batches whose items do not add up</p>
                </div>
                <div className="rounded-lg border border-slate-800 px-4 py-3">
                  <p className="text-xs uppercase tracking-wider text-slate-500">
                    Commission mismatches
                  </p>
                  <p
                    className={`mt-1 text-xl font-bold ${
                      summary.commission_mismatch_count > 0 ? 'text-red-400' : 'text-emerald-400'
                    }`}
                  >
                    {summary.commission_mismatch_count}
                  </p>
                  <p className="text-xs text-slate-500">Net payout disagrees with the maths</p>
                </div>
              </div>

              <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-slate-400">
                <span>Gross settled {formatMoney(summary.total_settled_gross)}</span>
                <span>Net paid {formatMoney(summary.total_net_paid)}</span>
                <span>{summary.batch_count} batches</span>
                {reconciliation.detail_truncated && (
                  <span className="text-amber-400">
                    Some details are truncated; counts above are complete.
                  </span>
                )}
              </div>

              {reconciliation.orphaned_payments.length > 0 && (
                <MismatchTable
                  title="Orphaned payments"
                  columns={['Transaction', 'Wallet', 'Amount', 'Created']}
                  rows={reconciliation.orphaned_payments.map((p) => [
                    `#${p.transaction_id}`,
                    p.wallet_id,
                    formatMoney(p.amount),
                    formatTime(p.created_at),
                  ])}
                />
              )}
              {reconciliation.amount_mismatches.length > 0 && (
                <MismatchTable
                  title="Amount mismatches"
                  columns={['Batch', 'Date', 'Recorded total', 'Items sum', 'Difference']}
                  rows={reconciliation.amount_mismatches.map((m) => [
                    `#${m.batch_id}`,
                    m.batch_date,
                    formatMoney(m.recorded_total),
                    formatMoney(m.item_sum),
                    formatMoney(m.difference),
                  ])}
                />
              )}
              {reconciliation.commission_mismatches.length > 0 && (
                <MismatchTable
                  title="Commission mismatches"
                  columns={['Batch', 'Date', 'Net paid', 'Expected net', 'Difference']}
                  rows={reconciliation.commission_mismatches.map((m) => [
                    `#${m.batch_id}`,
                    m.batch_date,
                    formatMoney(m.net_payout),
                    formatMoney(m.expected_net_payout),
                    formatMoney(m.difference),
                  ])}
                />
              )}

              {summary.is_balanced && (
                <p className="rounded-lg border border-emerald-900 bg-emerald-950/40 px-4 py-3 text-sm text-emerald-200">
                  Ledger is balanced: every completed payment is settled and every batch adds up.
                </p>
              )}
            </div>
          )}
        </Card>

        <footer className="text-xs text-slate-600">
          Generated {reconciliation ? formatTime(reconciliation.generated_at) : '...'} &middot; API docs
          at /docs
        </footer>
      </div>
    </div>
  )
}

function MismatchTable({ title, columns, rows }) {
  return (
    <div className="overflow-x-auto">
      <p className="mb-2 text-sm font-semibold text-red-300">{title}</p>
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wider text-slate-500">
          <tr>
            {columns.map((column) => (
              <th key={column} className="pb-2 pr-4">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {rows.map((row) => (
            <tr key={row.join('-')}>
              {row.map((cell, index) => (
                <td key={index} className="py-2 pr-4">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
