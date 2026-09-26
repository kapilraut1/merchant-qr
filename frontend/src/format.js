// The API returns money as a fixed-point string, never a float. Values are
// kept as strings through the UI and only parsed for display, so a shown total
// can never drift from the value the server computed.

const CURRENCY = 'NPR'

export function formatMoney(value) {
  const amount = typeof value === 'string' || typeof value === 'number' ? Number(value) : 0
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: CURRENCY,
    minimumFractionDigits: 2,
  }).format(amount)
}

export function formatPercent(rate) {
  const value = Number(rate)
  if (!Number.isFinite(value)) return '0.00%'
  return `${(value * 100).toFixed(2)}%`
}

export function formatTime(value) {
  if (!value) return ''
  return new Date(value).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function todayIso() {
  return new Date().toISOString().slice(0, 10)
}
