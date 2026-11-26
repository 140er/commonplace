// Talks to the G2 bridge (never to the hub directly, never with a hub token).

import type { Board, Card, EventPage, Me, NeedsMe } from './types'

export class BridgeError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body: unknown = null,
  ) {
    super(message)
  }
}

export interface BridgeSettings {
  /** Bridge origin; '' means the page's own origin (bridge-served or Vite proxy). */
  url: string
  /** Pairing key for the bridge (not a Commonplace token). */
  key: string
}

/** Events newer than this many seqs are read once at start for today's activity. */
export const ACTIVITY_WINDOW = 400
const LATEST = '9223372036854775807'
/** A stalled network (Wi-Fi to mobile handover) must not leave a poll hanging. */
export const REQUEST_TIMEOUT_MS = 15_000

export class HubClient {
  constructor(
    private settings: BridgeSettings,
    private fetchImpl: typeof fetch = (...a) => fetch(...a),
  ) {}

  configure(settings: BridgeSettings) {
    this.settings = settings
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (this.settings.key) headers['X-Bridge-Key'] = this.settings.key
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    const base = this.settings.url.replace(/\/+$/, '')
    let res: Response
    try {
      res = await this.fetchImpl(base + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      })
    } catch {
      throw new BridgeError(0, 'bridge unreachable')
    }
    const text = await res.text()
    let data: unknown = text
    try {
      data = JSON.parse(text)
    } catch {
      /* plain text */
    }
    if (!res.ok) {
      const message = (data as { error?: string } | null)?.error ?? `HTTP ${res.status}`
      throw new BridgeError(res.status, message, data)
    }
    return data as T
  }

  info = () => this.request<{ hub: string; paired: boolean }>('GET', '/bridge/info')
  me = () => this.request<Me>('GET', '/api/me')
  needsMe = () => this.request<NeedsMe>('GET', '/api/needs-me')
  boards = () => this.request<{ boards: Board[] }>('GET', '/api/boards')
  card = (n: number) => this.request<Card>('GET', `/api/cards/${n}`)
  latestSeq = async () => (await this.request<EventPage>('GET', `/api/events?after=${LATEST}`)).seq
  eventsAfter = (seq: number) => this.request<EventPage>('GET', `/api/events?after=${Math.max(0, seq)}`)
  decide = (n: number, version: number, choice: string) =>
    this.request<Card>('POST', `/api/cards/${n}/decide`, { version, choice })
  comment = (n: number, text: string) => this.request<Card>('POST', `/api/cards/${n}/comments`, { text })
}
