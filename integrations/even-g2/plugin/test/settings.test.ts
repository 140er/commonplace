import { describe, expect, it } from 'vitest'

import { cleanBridgeUrl, loadSettings, parseSettings } from '../src/settings'

function memoryStore(initial: Record<string, string> = {}) {
  const data = { ...initial }
  return { data, get: async (k: string) => data[k] ?? '', set: async (k: string, v: string) => void (data[k] = v) }
}

describe('bridge settings', () => {
  it('a pairing link fills the settings once and is removed from the address', async () => {
    const store = memoryStore()
    let replaced = ''
    const settings = await loadSettings(
      store,
      { search: '?key=abc&bridge=http://192.0.2.2:8790/x&v=1', pathname: '/', hash: '' } as Location,
      { replaceState: (_s: unknown, _t: string, url: string) => void (replaced = url) } as unknown as History,
    )
    expect(settings).toEqual({ url: 'http://192.0.2.2:8790', key: 'abc' })
    expect(replaced).toBe('/?v=1')
    expect(parseSettings(store.data['cmmn.g2.bridge'])).toEqual(settings)
  })

  it('keeps saved settings when the address carries none', async () => {
    const store = memoryStore({ 'cmmn.g2.bridge': JSON.stringify({ url: '', key: 'k' }) })
    const settings = await loadSettings(store, { search: '', pathname: '/', hash: '' } as Location, {} as History)
    expect(settings).toEqual({ url: '', key: 'k' })
  })

  it('a link to another bridge never carries the saved key there', async () => {
    const store = memoryStore({ 'cmmn.g2.bridge': JSON.stringify({ url: 'http://192.0.2.2:8790', key: 'paired' }) })
    const loc = (search: string) => ({ search, pathname: '/', hash: '' }) as Location
    const hist = { replaceState: () => {} } as unknown as History
    expect(await loadSettings(store, loc('?bridge=https://evil.example'), hist)).toEqual({ url: 'https://evil.example', key: '' })
    store.data['cmmn.g2.bridge'] = JSON.stringify({ url: 'http://192.0.2.2:8790', key: 'paired' })
    expect(await loadSettings(store, loc('?bridge=http://192.0.2.2:8790/'), hist)).toEqual({ url: 'http://192.0.2.2:8790', key: 'paired' })
  })

  it('a new pairing key without bridge= pairs with this page, not a saved other bridge', async () => {
    const store = memoryStore({ 'cmmn.g2.bridge': JSON.stringify({ url: 'http://192.0.2.2:8790', key: 'old' }) })
    const settings = await loadSettings(
      store,
      { search: '?key=NEW', pathname: '/', hash: '' } as Location,
      { replaceState: () => {} } as unknown as History,
    )
    expect(settings).toEqual({ url: '', key: 'NEW' })
  })

  it('accepts only http(s) bridge origins', () => {
    expect(cleanBridgeUrl('')).toBe('')
    expect(cleanBridgeUrl(' https://g2.example:8443/path ')).toBe('https://g2.example:8443')
    expect(() => cleanBridgeUrl('javascript:alert(1)')).toThrow()
    expect(parseSettings('not json')).toEqual({ url: '', key: '' })
  })
})
