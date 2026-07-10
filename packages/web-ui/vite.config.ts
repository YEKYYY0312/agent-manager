import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

const webRoot = fileURLToPath(new URL('.', import.meta.url))
const publicTraces = fileURLToPath(new URL('./public/traces', import.meta.url))

export default defineConfig({
  plugins: [react()],
  server: {
    fs: {
      allow: [webRoot, publicTraces],
    },
  },
})
