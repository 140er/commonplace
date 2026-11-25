import { describe, expect, it } from 'vitest'

import type { View } from '../src/app'
import { GlassesDisplay } from '../src/glasses'

function fakeBridge(upgradeOk = true) {
  const calls: string[] = []
  return {
    calls,
    createStartUpPageContainer: async () => (calls.push('create'), 0),
    rebuildPageContainer: async () => (calls.push('rebuild'), true),
    textContainerUpgrade: async () => (calls.push('upgrade'), upgradeOk),
    shutDownPageContainer: async () => (calls.push('exit'), true),
  }
}

const home = (head: string): View => ({ layout: 'list', head, items: ['ASK #6 Ship'] })

describe('glasses display', () => {
  it('creates once, updates text in place, rebuilds on new items', async () => {
    const b = fakeBridge()
    const d = new GlassesDisplay(b as any)
    await d.render(home('a'))
    await d.render(home('b'))
    await d.render({ layout: 'list', head: 'b', items: ['ASK #7 Other'] })
    expect(b.calls).toEqual(['create', 'upgrade', 'rebuild'])
    expect(d.available).toBe(true)
  })

  it('a refused text update is retried as a rebuild, and status recovers', async () => {
    const b = fakeBridge(false)
    const d = new GlassesDisplay(b as any)
    await d.render(home('a'))
    await d.render(home('b'))
    expect(d.available).toBe(false)
    await d.render(home('b'))
    expect(b.calls).toEqual(['create', 'upgrade', 'rebuild'])
    expect(d.available).toBe(true)
  })

  it('after reset (plugin exited) the page is created again', async () => {
    const b = fakeBridge()
    const d = new GlassesDisplay(b as any)
    await d.render(home('a'))
    d.reset()
    await d.render(home('a'))
    expect(b.calls).toEqual(['create', 'create'])
  })
})
