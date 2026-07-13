import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { stripCspForVite } from './src/devCsp.js'

const webRoot = fileURLToPath(new URL('.', import.meta.url))
const publicTraces = fileURLToPath(new URL('./public/traces', import.meta.url))

export default defineConfig(({ command }) => ({
  plugins: [
    react(),
    {
      name: 'agent-devtools-vite-dev-csp',
      transformIndexHtml(html: string) {
        return command === 'serve' ? stripCspForVite(html) : html
      },
    },
  ],
  server: {
    proxy: {
      '/api': {
        target: process.env.AGENT_DEVTOOLS_API_URL ?? 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
    fs: {
      allow: [webRoot, publicTraces],
    },
  },
}))
