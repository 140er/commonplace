import { defineConfig } from 'vite'

// Dev: the plugin calls same-origin /api and /bridge; Vite forwards them to the
// G2 bridge, which holds the Commonplace token. Nothing secret is bundled.
const bridge = process.env.G2_BRIDGE_URL ?? 'http://127.0.0.1:8790'

export default defineConfig({
  server: {
    host: true,
    port: 5173,
    proxy: { '/api': bridge, '/bridge': bridge },
  },
  build: { target: 'es2022' },
})
