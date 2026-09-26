import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The API base URL is injected at build time so the same bundle can point at a
// local docker-compose backend or a deployed one.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  return {
    plugins: [react()],
    server: {
      host: '0.0.0.0',
      port: 5173,
    },
    preview: {
      host: '0.0.0.0',
      port: 5173,
    },
    define: {
      __API_BASE_URL__: JSON.stringify(env.VITE_API_BASE_URL || 'http://localhost:8000'),
    },
  }
})
