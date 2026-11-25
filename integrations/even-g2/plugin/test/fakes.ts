import { HubClient } from '../src/api'
import type { Display, View } from '../src/app'
import type { Card, NeedsMe } from '../src/types'

export function card(over: Partial<Card> = {}): Card {
  return {
    number: 6,
    board: 'commonplace',
    title: 'Ship the v2 build tonight or wait?',
    body: 'All checks are green.',
    status: 'review',
    ask: 'Ship the v2 build tonight or wait?',
    ask_options: ['Ship tonight', 'Wait'],
    return_to: 'conn:coworker',
    review_kind: 'decision',
    version: 3,
    ...over,
  }
}

export function needs(cards: Card[] = [card()]): NeedsMe {
  return {
    asks: cards,
    done_review: [],
    backlog_from_agents: [],
    ready_to_merge: [],
    stale: [],
    plans: [],
    approvals: [],
    count: cards.length,
    coordinator_waiting: [],
    orchestrator_failing: [],
    orchestrator_stalled: [],
  }
}

/** A fake bridge: routes to handlers, records every request. */
export function fakeHub(state: { needs: NeedsMe; cards: Record<number, Card>; events: unknown[]; seq: number; noLog?: boolean }) {
  const calls: Array<{ method: string; path: string; body?: unknown; headers: Record<string, string> }> = []
  const respond = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  const impl = async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, path, body, headers: (init?.headers ?? {}) as Record<string, string> })
    if (path === '/bridge/info') return respond(200, { hub: 'http://hub.test', paired: false })
    if (path === '/api/me') return respond(200, { actor: 'conn:g2', people: {} })
    if (path === '/api/needs-me') return respond(200, state.needs)
    if (path === '/api/boards') {
      return respond(200, {
        boards: [{ slug: 'commonplace', name: 'Commonplace', agents_working: 2, counts: { doing: 1, review: 2, todo: 3 } }],
      })
    }
    const ev = path.match(/^\/api\/events\?after=(\d+)$/)
    if (ev && state.noLog) return respond(403, { error: 'Workspace event access required' })
    if (ev) {
      const after = Number(ev[1])
      return respond(200, { events: state.events.filter((e: any) => e.seq > after), seq: state.seq })
    }
    const c = path.match(/^\/api\/cards\/(\d+)(\/(decide|comments))?$/)
    if (c) {
      const found = state.cards[Number(c[1])]
      if (!found) return respond(404, { error: 'card not found' })
      if (c[3] === 'decide') {
        if (body.version !== found.version) return respond(409, { error: 'card changed', current: found })
        state.cards[found.number] = { ...found, status: 'todo', version: found.version + 1, ask_options: null }
        return respond(200, state.cards[found.number])
      }
      if (c[3] === 'comments') return respond(200, found)
      return respond(200, found)
    }
    return respond(404, { error: 'not allowed by the G2 bridge' })
  }
  return { api: new HubClient({ url: '', key: 'pair-key' }, impl as typeof fetch), calls }
}

export class FakeDisplay implements Display {
  views: View[] = []
  exited = false
  async render(view: View) {
    this.views.push(view)
  }
  async exit() {
    this.exited = true
  }
  get last() {
    return this.views[this.views.length - 1]
  }
}
