// The glasses app as a state machine: hub data plus a gesture in, a view out.
// No SDK calls here, so the whole flow runs in node against a mock display.

import { ACTIVITY_WINDOW, BridgeError, type HubClient } from './api'
import {
  activityBlocks,
  addActivity,
  agentBlocks,
  agentStatus,
  cardBlocks,
  cardCountBlocks,
  emptyActivity,
  eventNotice,
  homeItems,
  isDecision,
  needsLine,
  needsMeCards,
  type Activity,
  type HomeItem,
} from './model'
import { LINE_H, MAX_ITEMS, SCREEN_W, fitItem, fitLine, paginate, wrap } from './text'
import type { Board, Card, NeedsMe } from './types'

export type Input =
  | { type: 'click'; index?: number; name?: string }
  | { type: 'double' }
  | { type: 'up' }
  | { type: 'down' }
  | { type: 'long' }

export type View =
  | { layout: 'list'; head: string; items: string[] }
  | { layout: 'text'; body: string; footer: string }

export interface Display {
  render(view: View): Promise<void>
  exit(): Promise<void>
}

type Choice = { kind: 'option'; label: string } | { kind: 'seen' } | { kind: 'cancel' }

type Screen =
  | { name: 'home' }
  | { name: 'pages'; title: string; pages: string[]; page: number; card?: Card }
  | { name: 'choose'; card: Card; choices: Choice[] }
  | { name: 'confirm'; card: Card; choice: Choice; since: number }
  | { name: 'result'; text: string; reopen?: number }

export const SEEN_TEXT = 'Seen on glasses.'
/** A confirm press this soon after the confirm screen appears is ignored (bounce guard). */
export const CONFIRM_GUARD_MS = 700

// Geometry shared with glasses.ts.
export const PAD = 4
export const HEAD_H = 2 * LINE_H + 2 * PAD
export const BODY_LINES = 9
export const BODY_H = BODY_LINES * LINE_H + 2 * PAD
export const TEXT_W = SCREEN_W - 2 * PAD - 8
export const ITEM_W = SCREEN_W - 2 * PAD - 40

export interface Snapshot {
  status: 'connecting' | 'ok' | 'error'
  error: string
  hub: string
  count: number | null
  notice: string
  updated: string
  view: View | null
}

export interface AppDeps {
  api: HubClient
  display: Display
  now?: () => Date
  onChange?: (s: Snapshot) => void
}

export class G2App {
  private api: HubClient
  private display: Display
  private now: () => Date
  private onChange: (s: Snapshot) => void

  needs: NeedsMe | null = null
  boards: Board[] = []
  people: Record<string, string> = {}
  activity: Activity = emptyActivity()
  cursor: number | null = null
  /**
   * 'log': read the event log (needs events:read on a connection that reads every wing).
   * 'diff': the connection may not read the log (a browser-made connection), so new
   * items are found by comparing needs-me between polls.
   */
  mode: 'log' | 'diff' = 'log'
  notice = ''
  screen: Screen = { name: 'home' }
  items: HomeItem[] = []
  status: Snapshot['status'] = 'connecting'
  error = ''
  hub = ''
  updated = ''
  view: View | null = null
  /** start() finished once; until then every poll retries it (phone not on the network yet). */
  ready = false
  private busy = false

  constructor(deps: AppDeps) {
    this.api = deps.api
    this.display = deps.display
    this.now = deps.now ?? (() => new Date())
    this.onChange = deps.onChange ?? (() => {})
  }

  clock = (iso?: string) => {
    const d = iso ? new Date(iso) : this.now()
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }

  snapshot(): Snapshot {
    return {
      status: this.status,
      error: this.error,
      hub: this.hub,
      count: this.needs?.count ?? null,
      notice: this.notice,
      updated: this.updated,
      view: this.view,
    }
  }

  /** First load: who is who, the event cursor, today's activity, then the home page. */
  async start(): Promise<void> {
    this.ready = false
    await this.render()
    try {
      const [info, me] = await Promise.all([this.api.info(), this.api.me()])
      this.hub = info.hub
      this.people = me.people ?? {}
      this.activity = emptyActivity()
      await this.startEvents()
      this.ready = true
      await this.refresh()
    } catch (err) {
      this.fail(err)
      await this.render()
    }
  }

  private async startEvents() {
    try {
      const latest = await this.api.latestSeq()
      this.mode = 'log'
      this.cursor = latest
      if (latest > 0) {
        const page = await this.api.eventsAfter(latest - ACTIVITY_WINDOW)
        this.activity = addActivity(emptyActivity(), page.events, this.now())
      }
    } catch (err) {
      if (!(err instanceof BridgeError) || err.status !== 403) throw err
      this.mode = 'diff'
      this.cursor = null
    }
  }

  private fail(err: unknown) {
    this.status = 'error'
    this.error = err instanceof BridgeError ? err.message : String(err)
  }

  /** Re-read needs-me and boards; re-render. */
  async refresh(): Promise<void> {
    try {
      const [needs, boards] = await Promise.all([this.api.needsMe(), this.api.boards()])
      this.needs = needs
      this.boards = boards.boards ?? []
      this.status = 'ok'
      this.error = ''
      this.updated = this.clock()
    } catch (err) {
      this.fail(err)
    }
    await this.render()
  }

  /** One poll of the event log: new notable events set the notice and refresh the counts. */
  async poll(): Promise<void> {
    if (!this.ready || (this.cursor === null && this.mode === 'log')) return this.start()
    if (this.mode === 'diff') return this.pollNeeds()
    try {
      const page = await this.api.eventsAfter(this.cursor!)
      // A cursor that moves backwards means the hub was restored: start over from there.
      this.cursor = page.seq
      if (!page.events.length) {
        // Back from an error on a quiet hub: re-read so Home stops saying offline.
        if (this.status === 'error') await this.refresh()
        return
      }
      this.activity = addActivity(this.activity, page.events, this.now())
      const notices = page.events.map(eventNotice).filter((n): n is string => n !== null)
      if (notices.length) this.notice = `${this.clock()} ${notices[notices.length - 1]}`
      await this.refresh()
    } catch (err) {
      this.fail(err)
      await this.render()
    }
  }

  /**
   * Diff mode: compare Needs-me with the previous poll. A card that newly needs
   * you, a card whose version moved (a comment or an edit), or a card that left
   * (decided or closed elsewhere) sets the notice; a new one wins.
   */
  private async pollNeeds(): Promise<void> {
    const before = this.needs ? needsMeCards(this.needs) : null
    await this.refresh()
    if (!before || !this.needs || this.status !== 'ok') return
    const notice = needsDiffNotice(before, needsMeCards(this.needs))
    if (!notice) return
    this.notice = `${this.clock()} ${notice}`
    await this.render()
  }

  async input(input: Input): Promise<void> {
    if (this.busy) return
    this.busy = true
    try {
      await this.handle(input)
    } finally {
      this.busy = false
    }
  }

  /**
   * The list row a click means. A poll may have rebuilt the list under the
   * wearer; when the firmware's row name no longer matches, find it by name,
   * and ignore the click if that row is gone.
   */
  private pick(input: { index?: number; name?: string }): number | null {
    const index = input.index ?? 0
    const rows = this.view?.layout === 'list' ? this.view.items : []
    if (input.name === undefined || rows[index] === input.name) return index
    const found = rows.indexOf(input.name)
    return found >= 0 ? found : null
  }

  private async handle(input: Input): Promise<void> {
    const screen = this.screen
    if (input.type === 'click' && (screen.name === 'home' || screen.name === 'choose')) {
      const index = this.pick(input)
      if (index === null) return
      input = { type: 'click', index }
    }
    if (input.type === 'long') {
      if (screen.name === 'pages' && screen.card) return this.openCard(screen.card.number)
      this.screen = { name: 'home' }
      return this.refresh()
    }
    switch (screen.name) {
      case 'home':
        if (input.type === 'double') return this.display.exit()
        if (input.type === 'click') return this.openItem(this.items[input.index ?? 0])
        return
      case 'pages':
        if (input.type === 'double') return this.go({ name: 'home' })
        if (input.type === 'up' && screen.page > 0) return this.go({ ...screen, page: screen.page - 1 })
        if (input.type === 'down' && screen.page < screen.pages.length - 1) {
          return this.go({ ...screen, page: screen.page + 1 })
        }
        if (input.type === 'click') {
          if (screen.card) return this.go({ name: 'choose', card: screen.card, choices: choicesFor(screen.card) })
          return this.go({ name: 'home' })
        }
        return
      case 'choose': {
        if (input.type === 'double') return this.openCard(screen.card.number)
        if (input.type !== 'click') return
        const choice = screen.choices[input.index ?? 0]
        if (!choice || choice.kind === 'cancel') return this.openCard(screen.card.number)
        return this.go({ name: 'confirm', card: screen.card, choice, since: this.now().getTime() })
      }
      case 'confirm':
        if (input.type === 'double') {
          return this.go({ name: 'choose', card: screen.card, choices: choicesFor(screen.card) })
        }
        if (input.type !== 'click') return
        if (this.now().getTime() - screen.since < CONFIRM_GUARD_MS) return
        return this.act(screen.card, screen.choice)
      case 'result':
        if (input.type === 'click' || input.type === 'double') {
          if (screen.reopen) return this.openCard(screen.reopen)
          this.screen = { name: 'home' }
          return this.refresh()
        }
        return
    }
  }

  private async openItem(item: HomeItem | undefined): Promise<void> {
    if (!item) return
    if (item.kind === 'card' && item.number) return this.openCard(item.number)
    if (item.kind === 'today') {
      if (this.mode === 'diff') return this.showPages('Cards', cardCountBlocks(this.boards))
      return this.showPages('Today', activityBlocks(this.activity, this.clock))
    }
    if (item.kind === 'agents') {
      return this.showPages('Agents', agentBlocks(agentStatus(this.boards, this.needs), this.boards))
    }
    this.screen = { name: 'home' }
    return this.refresh()
  }

  private showPages(title: string, blocks: string[], card?: Card) {
    const pages = paginate(blocks, TEXT_W, BODY_LINES)
    return this.go({ name: 'pages', title, pages, page: 0, card })
  }

  private async openCard(number: number): Promise<void> {
    await this.go({ name: 'result', text: `Opening #${number}…` })
    try {
      const card = await this.api.card(number)
      await this.showPages(`#${number}`, cardBlocks(card, this.people), card)
    } catch (err) {
      const why = err instanceof BridgeError ? err.message : 'error'
      await this.go({ name: 'result', text: `Could not open #${number}: ${why}.\n\nPress to go back.` })
    }
  }

  private async act(card: Card, choice: Choice): Promise<void> {
    await this.go({ name: 'result', text: 'Sending…' })
    try {
      if (choice.kind === 'option') {
        await this.api.decide(card.number, card.version, choice.label)
        await this.go({ name: 'result', text: `Answered #${card.number}:\n${choice.label}\n\nPress to go home.` })
      } else {
        await this.api.comment(card.number, SEEN_TEXT)
        await this.go({ name: 'result', text: `Marked #${card.number} as seen.\n\nPress to go home.` })
      }
    } catch (err) {
      await this.go(failure(card, err))
    }
  }

  private async go(screen: Screen): Promise<void> {
    this.screen = screen
    await this.render()
  }

  /** The view for the current screen. Exposed for tests and the phone mirror. */
  compose(): View {
    const s = this.screen
    switch (s.name) {
      case 'home': {
        this.items = homeItems(
          this.needs,
          this.mode === 'log' ? this.activity : null,
          agentStatus(this.boards, this.needs),
          this.boards,
        ).slice(0, MAX_ITEMS)
        const second =
          this.status === 'error'
            ? `Offline: ${this.error}`
            : this.notice || (this.updated ? `Updated ${this.updated} - hold to refresh` : 'Connecting…')
        return {
          layout: 'list',
          head: [fitLine(needsLine(this.needs), TEXT_W), fitLine(second, TEXT_W)].join('\n'),
          items: this.items.map(i => fitItem(i.label, ITEM_W)),
        }
      }
      case 'pages': {
        const hint = s.card ? (isDecision(s.card) ? 'press: answer' : 'press: act') : 'press: home'
        const pager = s.pages.length > 1 ? `${s.page + 1}/${s.pages.length} swipe - ` : ''
        return { layout: 'text', body: s.pages[s.page], footer: fitLine(`${pager}${hint} - 2x: back`, TEXT_W) }
      }
      case 'choose':
        return {
          layout: 'list',
          head: [fitLine(`#${s.card.number} ${s.card.title}`, TEXT_W), 'Pick one, then confirm'].join('\n'),
          items: s.choices.map(c => fitItem(choiceLabel(c), ITEM_W)),
        }
      case 'confirm': {
        const what =
          s.choice.kind === 'option'
            ? [`Answer #${s.card.number} with:`, '', clip(s.choice.label, 4)]
            : [`Mark #${s.card.number} as seen?`, '', `Adds the comment "${SEEN_TEXT}"`]
        const body = paginate([...what, '', 'Press again to confirm.'], TEXT_W, BODY_LINES)[0]
        return { layout: 'text', body, footer: '2x: cancel' }
      }
      case 'result':
        return { layout: 'text', body: paginate(s.text.split('\n'), TEXT_W, BODY_LINES)[0], footer: '' }
    }
  }

  async render(): Promise<void> {
    this.view = this.compose()
    await this.display.render(this.view)
    this.onChange(this.snapshot())
  }
}

type Listed = Array<{ card: Card; tag: string }>

export function needsDiffNotice(before: Listed, after: Listed): string | null {
  const old = new Map(before.map(x => [x.card.number, x.card]))
  const now = new Set(after.map(x => x.card.number))
  const fresh = after.filter(x => !old.has(x.card.number))
  if (fresh.length) {
    const { card, tag } = fresh[fresh.length - 1]
    return `${tag === 'ASK' ? 'New ask' : 'New'} #${card.number}: ${card.title}`
  }
  const moved = after.filter(x => (old.get(x.card.number)?.version ?? x.card.version) < x.card.version)
  if (moved.length) {
    const { card } = moved[moved.length - 1]
    return `Updated #${card.number}: ${card.title}`
  }
  const gone = before.filter(x => !now.has(x.card.number))
  if (gone.length) {
    const { card } = gone[gone.length - 1]
    return `Done elsewhere #${card.number}: ${card.title}`
  }
  return null
}

export function choicesFor(card: Card): Choice[] {
  // The firmware list holds 20 items: up to 18 options plus "seen" and "cancel".
  const labels = isDecision(card) ? card.ask_options!.slice(0, MAX_ITEMS - 2) : []
  const options: Choice[] = labels.map(label => ({ kind: 'option' as const, label }))
  return [...options, { kind: 'seen' }, { kind: 'cancel' }]
}

/** At most `lines` wrapped lines, so the confirm prompt always stays on screen. */
function clip(text: string, lines: number): string {
  const wrapped = wrap(text, TEXT_W)
  return wrapped.length <= lines ? text : `${wrapped.slice(0, lines).join(' ')}…`
}

function choiceLabel(c: Choice): string {
  if (c.kind === 'option') return c.label
  if (c.kind === 'seen') return 'Mark as seen'
  return 'Cancel'
}

function failure(card: Card, err: unknown): Screen {
  if (err instanceof BridgeError) {
    if (err.status === 409) {
      return { name: 'result', text: `#${card.number} changed meanwhile.\n\nPress to reload it.`, reopen: card.number }
    }
    if (err.status === 403 && /signed-in workspace owner/.test(err.message)) {
      return {
        name: 'result',
        text: 'Answering is not allowed for this connection yet.\n\nIn Commonplace, let the G2 connection answer Needs-me questions.',
      }
    }
    return { name: 'result', text: `Not sent: ${err.message}.\n\nPress to reload.`, reopen: card.number }
  }
  return { name: 'result', text: 'Not sent: bridge unreachable.\n\nPress to reload.', reopen: card.number }
}
