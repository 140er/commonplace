import { getTextWidth } from '@evenrealities/pretext'
import { describe, expect, it } from 'vitest'

import { BODY_LINES, TEXT_W } from '../src/app'
import { activityLine, addActivity, emptyActivity, eventNotice, homeItems, needsMeCards, actorName, agentStatus } from '../src/model'
import { MAX_ITEM_BYTES, MAX_TEXT_BYTES, byteLength, fitItem, paginate, wrap } from '../src/text'
import { card, needs } from './fakes'

describe('text fits 576x288', () => {
  it('wraps to the measured width, splitting overlong words', () => {
    const lines = wrap('a '.repeat(200) + 'x'.repeat(300), TEXT_W)
    for (const line of lines) expect(getTextWidth(line)).toBeLessThanOrEqual(TEXT_W)
    expect(lines.join('')).toContain('xxxx')
  })

  it('paginates into pages of at most nine lines and under the byte cap', () => {
    const blocks = ['Title', '', 'é'.repeat(5000), '', 'end']
    const pages = paginate(blocks, TEXT_W, BODY_LINES)
    expect(pages.length).toBeGreaterThan(1)
    for (const page of pages) {
      expect(page.split('\n').length).toBeLessThanOrEqual(BODY_LINES)
      expect(byteLength(page)).toBeLessThanOrEqual(MAX_TEXT_BYTES)
      expect(page.startsWith('\n')).toBe(false)
    }
    expect(pages[pages.length - 1]).toMatch(/end$/)
  })

  it('list items stay one line and within 63 bytes', () => {
    const item = fitItem('ASK #12 ' + 'Überprüfung '.repeat(20), 500)
    expect(byteLength(item)).toBeLessThanOrEqual(MAX_ITEM_BYTES)
    expect(getTextWidth(item)).toBeLessThanOrEqual(500)
  })
})

describe('model', () => {
  it('orders needs-me buckets and drops duplicates', () => {
    const n = needs([card({ number: 1 })])
    n.done_review = [card({ number: 2, status: 'review' }), card({ number: 1 })]
    expect(needsMeCards(n).map(x => `${x.tag}${x.card.number}`)).toEqual(['ASK1', 'REVIEW2'])
  })

  it('keeps Today and Agents within the 20-item list', () => {
    const many = needs(Array.from({ length: 30 }, (_, i) => card({ number: i + 1 })))
    const items = homeItems(many, emptyActivity(), agentStatus([], null))
    expect(items.length).toBe(20)
    expect(items.slice(-2).map(i => i.kind)).toEqual(['today', 'agents'])
  })

  it('home says so when nothing needs you', () => {
    const items = homeItems(needs([]), emptyActivity(), agentStatus([], null))
    expect(items.map(i => i.label)).toEqual(['Nothing needs you right now', 'Today: quiet so far', 'Agents: 0 working'])
  })

  it('maps events to notification lines', () => {
    const e = (kind: string) => ({ seq: 1, at: '', kind, data: { number: 4, title: 'Tiles' } })
    expect(eventNotice(e('card.review_needed'))).toBe('New ask #4: Tiles')
    expect(eventNotice(e('card.decided'))).toBe('Decided #4: Tiles')
    expect(eventNotice(e('card.commented'))).toBe('Comment on #4: Tiles')
    expect(eventNotice(e('card.updated'))).toBeNull()
    expect(eventNotice(e('memory.saved'))).toBeNull()
  })

  it('counts only today in the activity line', () => {
    const today = new Date('2026-11-26T12:00:00')
    const a = addActivity(
      emptyActivity(),
      [
        { seq: 1, at: '2026-11-25T12:00:00', kind: 'card.decided' },
        { seq: 2, at: '2026-11-26T09:00:00', kind: 'card.decided' },
        { seq: 3, at: '2026-11-26T09:01:00', kind: 'card.review_needed' },
        { seq: 4, at: '2026-11-26T09:02:00', kind: 'card.moved', data: { status: 'done' } },
      ],
      today,
    )
    expect(activityLine(a)).toBe('Today: 1 asked, 1 decided, 1 done')
  })

  it('names who asks', () => {
    expect(actorName('conn:coworker')).toBe('coworker')
    expect(actorName('user:usr_1', { 'user:usr_1': 'Avery' })).toBe('Avery')
    expect(actorName(null)).toBe('someone')
  })
})
