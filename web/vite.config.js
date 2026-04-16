import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Prevent the browser from pinning stale optimized deps across dev-server restarts.
  server: {
    headers: {
      'Cache-Control': 'no-store',
    },
    proxy: {
      // Forward all backend API calls and WebSocket connections to the backend server.
      // This lets config.js fall back to window.location.origin (port 5173) in dev
      // without breaking anything — Vite proxies matched requests to port 8080.
      '/ws': {
        target: 'http://localhost:8080',
        ws: true,
        changeOrigin: true,
      },
      '/config': { target: 'http://localhost:8080', changeOrigin: true },
      '/health': { target: 'http://localhost:8080', changeOrigin: true },
      '/skills': { target: 'http://localhost:8080', changeOrigin: true },
      '/logs': { target: 'http://localhost:8080', changeOrigin: true },
      '/model-providers': { target: 'http://localhost:8080', changeOrigin: true },
      '/auth': { target: 'http://localhost:8080', changeOrigin: true },
      '/memory': { target: 'http://localhost:8080', changeOrigin: true },
      '/canvas': { target: 'http://localhost:8080', changeOrigin: true },
      '/cron': { target: 'http://localhost:8080', changeOrigin: true },
      '/personality': { target: 'http://localhost:8080', changeOrigin: true },
      '/persona': { target: 'http://localhost:8080', changeOrigin: true },
      '/feedback': { target: 'http://localhost:8080', changeOrigin: true },
      '/pairing': { target: 'http://localhost:8080', changeOrigin: true },
      '/mcp': { target: 'http://localhost:8080', changeOrigin: true },
      '/policy': { target: 'http://localhost:8080', changeOrigin: true },
      '/debug': { target: 'http://localhost:8080', changeOrigin: true },
      '/agents': { target: 'http://localhost:8080', changeOrigin: true },
      '/llm-router': { target: 'http://localhost:8080', changeOrigin: true },
      '/plugins': { target: 'http://localhost:8080', changeOrigin: true },
      '/release-gate': { target: 'http://localhost:8080', changeOrigin: true },
      '/training': { target: 'http://localhost:8080', changeOrigin: true },
      '/observability': { target: 'http://localhost:8080', changeOrigin: true },
      '/evolution': { target: 'http://localhost:8080', changeOrigin: true },
      '/git': { target: 'http://localhost:8080', changeOrigin: true },
      '/deployment': { target: 'http://localhost:8080', changeOrigin: true },
      '/audit': { target: 'http://localhost:8080', changeOrigin: true },
    },
  },
  resolve: {
    dedupe: ['react', 'react-dom'],
  },
  optimizeDeps: {
    force: true,
    include: [
      'react',
      'react-dom',
      'react-dom/client',
      'react/jsx-runtime',
      'react/jsx-dev-runtime',
      'react-router-dom',
      'axios',
      'lucide-react',
    ],
  },
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/three')) return 'vendor-three-core'
          if (id.includes('node_modules/react-force-graph-3d')) return 'vendor-force-graph'
          if (id.includes('node_modules/3d-force-graph')) return 'vendor-force-graph'
          if (id.includes('node_modules/force-graph')) return 'vendor-force-graph'
          if (id.includes('node_modules/three-spritetext')) return 'vendor-force-graph'
          return undefined
        },
      },
    },
  },
})
