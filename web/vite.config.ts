import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

function normalizeBasePath(value: string): string {
  const trimmed = value.trim()
  if (!trimmed || trimmed === '/') return '/'
  return `/${trimmed.replace(/^\/+|\/+$/g, '')}/`
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    base: normalizeBasePath(env.VITE_APP_BASE_PATH || '/'),
    plugins: [react()],
    server: {
      host: '127.0.0.1',
      port: 5173,
    },
  }
})
