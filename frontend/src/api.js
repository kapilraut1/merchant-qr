// Single place where the API origin is resolved. Vite replaces
// __API_BASE_URL__ at build time (see vite.config.js).
export const API_BASE_URL = __API_BASE_URL__

export async function apiRequest(path, { method = 'GET', body, token, headers = {} } = {}) {
  const requestHeaders = { ...headers }
  if (body !== undefined) {
    requestHeaders['Content-Type'] = 'application/json'
  }
  if (token) {
    requestHeaders.Authorization = `Bearer ${token}`
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  const text = await response.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    const detail =
      (payload && payload.detail && JSON.stringify(payload.detail)) || response.statusText
    const error = new Error(detail)
    error.status = response.status
    error.payload = payload
    throw error
  }

  return payload
}
