// Bridge settings: where the bridge is and its pairing key. Never a hub token.
// A pairing link (…/?key=…&bridge=…) fills them once, then is cleaned from the URL.

import type { BridgeSettings } from './api'

const STORE_KEY = 'cmmn.g2.bridge'

export interface Store {
  get(key: string): Promise<string>
  set(key: string, value: string): Promise<unknown>
}

export function parseSettings(raw: string | null | undefined): BridgeSettings {
  try {
    const v = JSON.parse(raw ?? '')
    return { url: typeof v.url === 'string' ? v.url : '', key: typeof v.key === 'string' ? v.key : '' }
  } catch {
    return { url: '', key: '' }
  }
}

/** Only an http(s) origin, or '' for the page's own origin. */
export function cleanBridgeUrl(value: string): string {
  const v = value.trim()
  if (!v) return ''
  const url = new URL(v)
  if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new Error('Bridge URL must start with http:// or https://')
  return url.origin
}

export async function loadSettings(store: Store, location: Location, history: History): Promise<BridgeSettings> {
  const saved = parseSettings(await store.get(STORE_KEY).catch(() => ''))
  const params = new URLSearchParams(location.search)
  const key = params.get('key')
  const bridge = params.get('bridge')
  if (key === null && bridge === null) return saved
  const url = bridge !== null ? cleanBridgeUrl(bridge) : saved.url
  // A saved key only ever goes back to the bridge it was paired with.
  const next = { url, key: key ?? (url === saved.url ? saved.key : '') }
  await saveSettings(store, next)
  params.delete('key')
  params.delete('bridge')
  const rest = params.toString()
  history.replaceState(null, '', location.pathname + (rest ? `?${rest}` : '') + location.hash)
  return next
}

export function saveSettings(store: Store, settings: BridgeSettings) {
  return store.set(STORE_KEY, JSON.stringify(settings))
}
