import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * During local development the backend runs separately on :8000 and the API is
 * proxied, so the browser only ever talks to one origin and CORS never enters
 * the picture. In the Docker image the built assets are served by nginx, which
 * proxies /api the same way (see docker/nginx.conf).
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': { target: process.env.BACKEND_URL ?? 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: process.env.BACKEND_URL ?? 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  preview: {
    host: '0.0.0.0',
    port: 4173,
    proxy: {
      '/api': { target: process.env.BACKEND_URL ?? 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: process.env.BACKEND_URL ?? 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: true, chunkSizeWarningLimit: 1500 },
})
