// Text fitting for the G2 display (576x288 per eye, LVGL font, 27 px lines).
// Widths come from @evenrealities/pretext, which measures with the firmware's
// own glyph advances, so what fits here fits on the glasses.

import { getTextWidth, pxTruncate } from '@evenrealities/pretext'

export const SCREEN_W = 576
export const SCREEN_H = 288
export const LINE_H = 27
/** Firmware text containers take up to ~1000 bytes; stay well under. */
export const MAX_TEXT_BYTES = 900
/** Firmware list items take up to 63 bytes and 20 items. */
export const MAX_ITEM_BYTES = 63
export const MAX_ITEMS = 20

const encoder = new TextEncoder()

export const byteLength = (s: string) => encoder.encode(s).length

/** One line, cut with an ellipsis to fit `maxPx`. */
export function fitLine(text: string, maxPx: number): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  return getTextWidth(flat) <= maxPx ? flat : pxTruncate(flat, maxPx)
}

/** Cut a string to at most `max` UTF-8 bytes on a character boundary. */
export function fitBytes(text: string, max: number): string {
  if (byteLength(text) <= max) return text
  let out = ''
  for (const ch of text) {
    if (byteLength(out + ch + '…') > max) break
    out += ch
  }
  return out + '…'
}

/** A list item: one line wide, within the firmware byte cap. */
export function fitItem(text: string, maxPx: number): string {
  return fitBytes(fitLine(text, maxPx), MAX_ITEM_BYTES)
}

/** Word-wrap one paragraph to lines no wider than `maxPx`. Long words are hard-split. */
export function wrap(text: string, maxPx: number): string[] {
  const words = text.replace(/\s+/g, ' ').trim().split(' ').filter(Boolean)
  const lines: string[] = []
  let line = ''
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word
    if (getTextWidth(candidate) <= maxPx) {
      line = candidate
      continue
    }
    if (line) lines.push(line)
    line = word
    while (getTextWidth(line) > maxPx) {
      // Longest prefix that fits, by binary search (a URL can be thousands of chars).
      let lo = 1
      let hi = line.length - 1
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2)
        if (getTextWidth(line.slice(0, mid)) <= maxPx) lo = mid
        else hi = mid - 1
      }
      lines.push(line.slice(0, lo))
      line = line.slice(lo)
    }
  }
  if (line) lines.push(line)
  return lines
}

/**
 * Lay out blocks of text (paragraphs; '' for a blank line) into pages of at most
 * `linesPerPage` lines that each fit `maxPx` wide and the firmware byte cap.
 */
export function paginate(blocks: string[], maxPx: number, linesPerPage: number): string[] {
  const lines: string[] = []
  for (const block of blocks) {
    if (block === '') lines.push('')
    else lines.push(...wrap(block, maxPx))
  }
  const pages: string[] = []
  let page: string[] = []
  const flush = () => {
    while (page.length && page[page.length - 1] === '') page.pop()
    if (page.length) pages.push(page.join('\n'))
    page = []
  }
  for (const line of lines) {
    if (page.length === 0 && line === '') continue
    const next = [...page, line].join('\n')
    if (page.length >= linesPerPage || byteLength(next) > MAX_TEXT_BYTES) flush()
    if (page.length === 0 && line === '') continue
    page.push(line)
  }
  flush()
  return pages.length ? pages : ['']
}
