import { defineConfig } from 'vite'

// Dev: the plugin calls same-origin /api and /bridge; Vite forwards them to the
// G2 bridge, which holds the Commonplace token. Nothing secret is bundled.
// Vite listens on loopback only. To load it from a phone (G2_DEV_HOST=0.0.0.0),
// give the bridge a key too, or anyone on the network could answer cards.
const bridge = process.env.G2_BRIDGE_URL ?? 'http://127.0.0.1:8790'

export default defineConfig({
  server: {
    host: process.env.G2_DEV_HOST ?? '127.0.0.1',
    port: 5173,
    proxy: {
      // Keep the browser's Host and add X-Forwarded-For with the real client
      // address: a keyless bridge then refuses any LAN client of this server.
      '/api': { target: bridge, changeOrigin: false, xfwd: true },
      '/bridge': { target: bridge, changeOrigin: false, xfwd: true },
    },
  },
  build: { target: 'es2022' },
})
