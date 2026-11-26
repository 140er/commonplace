import { describe, expect, it } from 'vitest'

import config from '../vite.config'

describe('dev server', () => {
  it('listens on loopback and forwards the real client address to the bridge', () => {
    const server = (config as { server: { host: string; proxy: Record<string, { xfwd?: boolean; changeOrigin?: boolean }> } }).server
    expect(server.host).toBe('127.0.0.1')
    // Without xfwd a LAN client of a LAN-facing dev server could forge Host: localhost
    // past a keyless bridge; the bridge refuses any non-loopback forwarded hop.
    for (const path of ['/api', '/bridge']) {
      expect(server.proxy[path].xfwd).toBe(true)
      expect(server.proxy[path].changeOrigin).toBe(false)
    }
  })
})
