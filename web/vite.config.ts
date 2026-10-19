import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, the API runs separately (`classifiles serve`); in production FastAPI serves web/dist.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8765' } },
})
