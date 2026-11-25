// Pure view-model helpers: hub data in, short display strings out.

import type { Board, Card, HubEvent, NeedsMe } from './types'

export interface HomeItem {
  kind: 'card' | 'today' | 'agents' | 'empty'
  number?: number
  label: string
}

/** needs-me buckets, most urgent first, with the short tag shown on the glasses. */
const BUCKETS: Array<[keyof NeedsMe, string]> = [
  ['asks', 'ASK'],
  ['approvals', 'OK?'],
  ['done_review', 'REVIEW'],
  ['plans', 'PLAN'],
  ['ready_to_merge', 'MERGE'],
  ['backlog_from_agents', 'NEW'],
  ['stale', 'STALE'],
]

export function needsMeCards(needs: NeedsMe): Array<{ card: Card; tag: string }> {
  const seen = new Set<number>()
  const out: Array<{ card: Card; tag: string }> = []
  for (const [bucket, tag] of BUCKETS) {
    const list = needs[bucket]
    if (!Array.isArray(list)) continue
    for (const card of list as Card[]) {
      if (!card || typeof card.number !== 'number' || seen.has(card.number)) continue
      seen.add(card.number)
      out.push({ card, tag })
    }
  }
  return out
}

/** Display name for an actor id such as conn:coworker or user:usr_1. */
export function actorName(actor: string | null | undefined, people: Record<string, string> = {}): string {
  if (!actor) return 'someone'
  if (people[actor]) return people[actor]
  const [kind, rest] = actor.split(':', 2)
  if (kind === 'conn' && rest) return rest
  if (kind === 'user') return 'a person'
  return actor
}

export interface Activity {
  created: number
  asks: number
  decided: number
  comments: number
  done: number
  memories: number
  latest: HubEvent[]
}

export const emptyActivity = (): Activity => ({ created: 0, asks: 0, decided: 0, comments: 0, done: 0, memories: 0, latest: [] })

export function localDay(iso: string | Date): string {
  const d = typeof iso === 'string' ? new Date(iso) : iso
  return `${d.getFullYear()}-${d.getMonth() + 1}-${d.getDate()}`
}

/** Fold events into today's activity counts (events from other days are skipped). */
export function addActivity(activity: Activity, events: HubEvent[], today: Date): Activity {
  const day = localDay(today)
  const next = { ...activity, latest: [...activity.latest] }
  for (const e of events) {
    if (!e.at || localDay(e.at) !== day) continue
    switch (e.kind) {
      case 'card.created':
        next.created++
        break
      case 'card.review_needed':
        next.asks++
        break
      case 'card.decided':
        next.decided++
        break
      case 'card.commented':
        next.comments++
        break
      case 'card.moved':
        if (e.data?.status === 'done') next.done++
        break
      case 'memory.saved':
        next.memories++
        break
      default:
        continue
    }
    next.latest.push(e)
  }
  next.latest = next.latest.slice(-8)
  return next
}

export function activityLine(a: Activity): string {
  const parts: string[] = []
  if (a.asks) parts.push(`${a.asks} asked`)
  if (a.decided) parts.push(`${a.decided} decided`)
  if (a.created) parts.push(`${a.created} new`)
  if (a.done) parts.push(`${a.done} done`)
  if (a.comments) parts.push(`${a.comments} comments`)
  if (a.memories) parts.push(`${a.memories} saved`)
  return `Today: ${parts.length ? parts.join(', ') : 'quiet so far'}`
}

export interface AgentStatus {
  working: number
  waiting: number
  failing: number
  stalled: number
}

export function agentStatus(boards: Board[], needs: NeedsMe | null): AgentStatus {
  return {
    working: boards.reduce((n, b) => n + (b.agents_working ?? 0), 0),
    waiting: needs?.coordinator_waiting?.length ?? 0,
    failing: needs?.orchestrator_failing?.length ?? 0,
    stalled: needs?.orchestrator_stalled?.length ?? 0,
  }
}

export function agentLine(s: AgentStatus): string {
  const parts = [`${s.working} working`]
  if (s.waiting) parts.push(`${s.waiting} waiting`)
  if (s.failing) parts.push(`${s.failing} failing`)
  if (s.stalled) parts.push(`${s.stalled} stalled`)
  return `Agents: ${parts.join(', ')}`
}

/** Open cards across boards, for connections that cannot read the event log. */
export function cardCountLine(boards: Board[]): string {
  const total = (status: string) => boards.reduce((n, b) => n + (b.counts?.[status] ?? 0), 0)
  const parts = [`${total('doing')} doing`, `${total('review')} in review`, `${total('todo')} to do`]
  return `Cards: ${parts.join(', ')}`
}

export function cardCountBlocks(boards: Board[]): string[] {
  const blocks = [cardCountLine(boards), '']
  for (const b of boards) {
    const c = b.counts ?? {}
    blocks.push(`${b.name}: ${c.doing ?? 0} doing, ${c.review ?? 0} review, ${c.todo ?? 0} to do`)
  }
  blocks.push('', "Today's activity needs a connection that may read the event log (see README).")
  return blocks
}

/** `activity` is null when the connection cannot read the event log. */
export function homeItems(
  needs: NeedsMe | null,
  activity: Activity | null,
  agents: AgentStatus,
  boards: Board[] = [],
): HomeItem[] {
  const items: HomeItem[] = []
  const cards = needs ? needsMeCards(needs) : []
  for (const { card, tag } of cards) {
    items.push({ kind: 'card', number: card.number, label: `${tag} #${card.number} ${card.title}` })
  }
  if (!cards.length) items.push({ kind: 'empty', label: needs ? 'Nothing needs you right now' : 'Loading…' })
  items.push({ kind: 'today', label: activity ? activityLine(activity) : cardCountLine(boards) })
  items.push({ kind: 'agents', label: agentLine(agents) })
  return items
}

export function needsLine(needs: NeedsMe | null): string {
  if (!needs) return 'Commonplace'
  const n = needs.count
  return n === 0 ? 'Commonplace: all clear' : `Commonplace: ${n} need${n === 1 ? 's' : ''} you`
}

/** The short notification line for an event, or null when it is not worth a glance. */
export function eventNotice(e: HubEvent): string | null {
  const n = e.data?.number
  const title = e.data?.title ?? ''
  const ref = n ? `#${n}` : (e.subject ?? '')
  switch (e.kind) {
    case 'card.review_needed':
      return `New ask ${ref}: ${title}`
    case 'card.decided':
      return `Decided ${ref}: ${title}`
    case 'card.commented':
      return `Comment on ${ref}: ${title}`
    default:
      return null
  }
}

export function isDecision(card: Card): boolean {
  return card.status === 'review' && Array.isArray(card.ask_options) && card.ask_options.length > 0
}

/** Paragraph blocks for a card's detail page ('' is a blank line). */
export function cardBlocks(card: Card, people: Record<string, string> = {}): string[] {
  const blocks = [`#${card.number} on ${card.board} - ${card.status}`, card.title]
  const asker = card.return_to ?? card.created_by
  if (card.status === 'review') blocks.push(`From: ${actorName(asker, people)}`)
  const question = card.ask && card.ask !== card.title ? card.ask : ''
  const context = (card.review_context || card.body || '').trim()
  if (question) blocks.push('', question)
  if (context) blocks.push('', context.length > 600 ? `${context.slice(0, 600)}…` : context)
  if (isDecision(card)) {
    blocks.push('', 'Options:')
    card.ask_options!.forEach((o, i) => blocks.push(`${i + 1}. ${o}`))
  }
  return blocks
}

export function activityBlocks(a: Activity, clock: (iso: string) => string): string[] {
  const blocks = [activityLine(a), '']
  if (!a.latest.length) blocks.push('No card activity yet today.')
  for (const e of [...a.latest].reverse()) {
    const n = e.data?.number ? `#${e.data.number} ` : ''
    const what = e.kind.replace(/^card\.|^memory\./, '').replace('_', ' ')
    blocks.push(`${clock(e.at)} ${what} ${n}${e.data?.title ?? ''}`.trim())
  }
  return blocks
}

export function agentBlocks(s: AgentStatus, boards: Board[]): string[] {
  const blocks = [agentLine(s), '']
  const busy = boards.filter(b => (b.agents_working ?? 0) > 0)
  for (const b of busy) blocks.push(`${b.name}: ${b.agents_working} working`)
  if (!busy.length) blocks.push('No agent is working on a card right now.')
  if (s.failing || s.stalled) blocks.push('', 'Open Commonplace for the failing or stalled coordinators.')
  return blocks
}
