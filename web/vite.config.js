import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
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
