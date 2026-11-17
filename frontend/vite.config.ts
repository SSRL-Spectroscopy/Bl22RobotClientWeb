import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// During development the page is served by Vite and the API by the backend
// on port 8600. In production the backend serves the built page itself.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8600', ws: true },
    },
  },
})
