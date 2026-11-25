import { describe, expect, it } from 'vitest'

import { ACTIVITY_WINDOW } from '../src/api'
import { CONFIRM_GUARD_MS, G2App, SEEN_TEXT } from '../src/app'
import { card, FakeDisplay, fakeHub, needs } from './fakes'

function setup(over: { cards?: ReturnType<typeof card>[]; noLog?: boolean } = {}) {
  const cards = over.cards ?? [card()]
  const state = {
    needs: needs(cards),
    cards: Object.fromEntries(cards.map(c => [c.number, c])),
    events: [] as unknown[],
    seq: 40,
    noLog: over.noLog,
  }
  const hub = fakeHub(state)
  const display = new FakeDisplay()
  let now = new Date('2026-11-26T10:00:00')
  const app = new G2App({ api: hub.api, display, now: () => now })
  const tick = (ms: number) => (now = new Date(now.getTime() + ms))
  return { app, display, state, calls: hub.calls, tick }
}

describe('home', () => {
  it('shows the needs-me count, the items, today and agents', async () => {
    const { app, display } = setup()
    await app.start()
    const view = display.last
    expect(view.layout).toBe('list')
    if (view.layout !== 'list') return
    expect(view.head.split('\n')[0]).toBe('Commonplace: 1 needs you')
    expect(view.items[0]).toMatch(/^ASK #6 Ship the v2/)
    expect(view.items).toContain('Agents: 2 working')
    expect(view.items.some(i => i.startsWith('Today:'))).toBe(true)
  })

  it('never starts the event cursor at 0 and reads only a bounded window', async () => {
    const { app, calls } = setup()
    await app.start()
    const events = calls.filter(c => c.path.startsWith('/api/events')).map(c => c.path)
    expect(events[0]).toBe('/api/events?after=9223372036854775807')
    // seq is one hub-wide sequence, so after >= latest - window bounds the read.
    for (const path of events) expect(Number(path.split('=')[1])).toBeGreaterThanOrEqual(40 - ACTIVITY_WINDOW)
    expect(app.cursor).toBe(40)
  })

  it('sends the pairing key, never a hub token', async () => {
    const { app, calls } = setup()
    await app.start()
    for (const c of calls) {
      expect(c.headers['X-Bridge-Key']).toBe('pair-key')
      expect(JSON.stringify(c.headers)).not.toMatch(/Authorization|cmmn_/)
    }
  })

  it('double press on home exits through the system prompt', async () => {
    const { app, display } = setup()
    await app.start()
    await app.input({ type: 'double' })
    expect(display.exited).toBe(true)
  })
})

describe('card and decision', () => {
  it('press opens the item, swipes page, double press goes back', async () => {
    const long = card({ body: 'word '.repeat(300) })
    const { app, display } = setup({ cards: [long] })
    await app.start()
    await app.input({ type: 'click', index: 0 })
    let view = display.last
    expect(view.layout).toBe('text')
    if (view.layout !== 'text') return
    expect(view.body).toMatch(/^#6 on commonplace/)
    expect(view.footer).toMatch(/^1\/\d+ swipe/)
    await app.input({ type: 'down' })
    view = display.last
    expect(view.layout === 'text' && view.footer).toMatch(/^2\//)
    await app.input({ type: 'up' })
    await app.input({ type: 'up' })
    view = display.last
    expect(view.layout === 'text' && view.footer).toMatch(/^1\//)
    await app.input({ type: 'double' })
    expect(display.last.layout).toBe('list')
  })

  it('decides only after a second, deliberate press', async () => {
    const { app, display, calls, state, tick } = setup()
    await app.start()
    await app.input({ type: 'click', index: 0 }) // open card
    await app.input({ type: 'click' }) // choose
    const choose = display.last
    expect(choose.layout === 'list' && choose.items).toEqual(['Ship tonight', 'Wait', 'Mark as seen', 'Cancel'])
    await app.input({ type: 'click', index: 1 }) // pick "Wait"
    expect(display.last.layout === 'text' && display.last.body).toMatch(/Answer #6 with:\n\nWait/)
    await app.input({ type: 'click' }) // bounce: too soon, ignored
    expect(calls.some(c => c.path.endsWith('/decide'))).toBe(false)
    tick(CONFIRM_GUARD_MS + 1)
    await app.input({ type: 'click' })
    const decide = calls.find(c => c.path === '/api/cards/6/decide')
    expect(decide?.body).toEqual({ version: 3, choice: 'Wait' })
    expect(state.cards[6].status).toBe('todo')
    expect(display.last.layout === 'text' && display.last.body).toMatch(/Answered #6:\nWait/)
  })

  it('double press on confirm cancels without sending', async () => {
    const { app, display, calls, tick } = setup()
    await app.start()
    await app.input({ type: 'click', index: 0 })
    await app.input({ type: 'click' })
    await app.input({ type: 'click', index: 0 })
    tick(5000)
    await app.input({ type: 'double' })
    expect(display.last.layout).toBe('list')
    expect(calls.some(c => c.method === 'POST')).toBe(false)
  })

  it('mark as seen posts the glasses comment after confirm', async () => {
    const { app, calls, tick } = setup()
    await app.start()
    await app.input({ type: 'click', index: 0 })
    await app.input({ type: 'click' })
    await app.input({ type: 'click', index: 2 })
    tick(CONFIRM_GUARD_MS + 1)
    await app.input({ type: 'click' })
    expect(calls.find(c => c.path === '/api/cards/6/comments')?.body).toEqual({ text: SEEN_TEXT })
  })

  it('a stale version reports the change and reloads the card', async () => {
    const { app, display, state, tick } = setup()
    await app.start()
    await app.input({ type: 'click', index: 0 })
    state.cards[6] = { ...state.cards[6], version: 9 }
    await app.input({ type: 'click' })
    await app.input({ type: 'click', index: 0 })
    tick(CONFIRM_GUARD_MS + 1)
    await app.input({ type: 'click' })
    expect(display.last.layout === 'text' && display.last.body).toMatch(/changed meanwhile/)
    await app.input({ type: 'click' })
    expect(display.last.layout === 'text' && display.last.body).toMatch(/^#6 on commonplace/)
  })

  it('a card without options offers only mark as seen and cancel', async () => {
    const plain = card({ number: 8, status: 'review', review_kind: 'completion', ask_options: [] })
    const { app, display } = setup({ cards: [plain] })
    await app.start()
    await app.input({ type: 'click', index: 0 })
    await app.input({ type: 'click' })
    expect(display.last.layout === 'list' && display.last.items).toEqual(['Mark as seen', 'Cancel'])
  })
})

describe('live updates', () => {
  it('a new ask event sets the notice line and refreshes the count', async () => {
    const { app, display, state } = setup()
    await app.start()
    const next = card({ number: 9, title: 'Merge the pricing copy?' })
    state.needs = needs([card(), next])
    state.cards[9] = next
    state.events.push({
      seq: 41,
      at: '2026-11-26T10:00:05',
      kind: 'card.review_needed',
      data: { number: 9, title: 'Merge the pricing copy?' },
    })
    state.seq = 41
    await app.poll()
    const view = display.last
    expect(view.layout === 'list' && view.head).toBe('Commonplace: 2 need you\n10:00 New ask #9: Merge the pricing copy?')
    expect(app.cursor).toBe(41)
  })

  it('a quiet poll changes nothing', async () => {
    const { app, display } = setup()
    await app.start()
    const before = display.views.length
    await app.poll()
    expect(display.views.length).toBe(before)
  })

  it('long press refreshes from anywhere', async () => {
    const { app, display, calls } = setup()
    await app.start()
    await app.input({ type: 'click', index: 1 }) // Today page
    const reads = calls.filter(c => c.path === '/api/needs-me').length
    await app.input({ type: 'long' })
    expect(calls.filter(c => c.path === '/api/needs-me').length).toBe(reads + 1)
    expect(display.last.layout).toBe('list')
  })

  it('an unreachable bridge shows offline instead of failing', async () => {
    const display = new FakeDisplay()
    const { HubClient } = await import('../src/api')
    const api = new HubClient({ url: '', key: '' }, (async () => {
      throw new TypeError('network')
    }) as typeof fetch)
    const app = new G2App({ api, display })
    await app.start()
    expect(display.last.layout === 'list' && display.last.head).toMatch(/Offline: bridge unreachable/)
  })
})

describe('connections without event-log access', () => {
  it('falls back to comparing needs-me between polls', async () => {
    const { app, display, state, calls } = setup({ noLog: true })
    await app.start()
    expect(app.mode).toBe('diff')
    const home = display.last
    expect(home.layout === 'list' && home.items).toContain('Cards: 1 doing, 2 in review, 3 to do')
    const next = card({ number: 9, title: 'Merge the pricing copy?' })
    state.needs = needs([card(), next])
    await app.poll()
    const view = display.last
    expect(view.layout === 'list' && view.head).toBe('Commonplace: 2 need you\n10:00 New ask #9: Merge the pricing copy?')
    expect(calls.filter(c => c.path.startsWith('/api/events')).length).toBe(1)
  })
})
