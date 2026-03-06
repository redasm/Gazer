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
