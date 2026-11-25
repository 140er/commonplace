// The phone side: what the Even app shows while the plugin runs. Connection
// state, hub, a mirror of the glasses, bridge settings, and tap-free controls.

import type { BridgeSettings } from './api'
import type { Input, Snapshot } from './app'
import { cleanBridgeUrl } from './settings'

export interface PhoneHooks {
  onSave(settings: BridgeSettings): Promise<void>
  onInput(input: Input): void
}

const esc = (s: string) => s.replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`)

export class PhoneView {
  constructor(
    private root: HTMLElement,
    private hooks: PhoneHooks,
    settings: BridgeSettings,
    private glasses: () => string,
  ) {
    this.root.innerHTML = `
      <h1>Commonplace for G2</h1>
      <div class="row"><span>Connection</span><span id="status" class="wait">Connecting…</span></div>
      <div class="row"><span>Hub</span><span id="hub">-</span></div>
      <div class="row"><span>Needs you</span><span id="count">-</span></div>
      <div class="row"><span>Glasses</span><span id="glasses">-</span></div>
      <div class="row"><span>Updated</span><span id="updated">-</span></div>
      <h2>On the glasses</h2>
      <pre id="mirror"></pre>
      <div class="buttons" id="controls">
        <button data-i="up">Swipe up</button><button data-i="down">Swipe down</button>
        <button data-i="click">Press</button><button data-i="double">Double press</button>
        <button data-i="long">Long press</button>
      </div>
      <h2>Bridge</h2>
      <form id="settings">
        <label for="url">Bridge URL (empty: this page's own address)</label>
        <input id="url" name="url" inputmode="url" autocomplete="off" placeholder="http://100.64.0.10:8790" />
        <label for="key" style="margin-top:10px">Pairing key (from the bridge, not a Commonplace token)</label>
        <input id="key" name="key" type="password" autocomplete="off" />
        <div class="buttons"><button type="submit">Save and reconnect</button></div>
        <div class="hint" id="form-hint">The Commonplace token stays on the bridge. This page never sees it.</div>
      </form>`
    ;(this.root.querySelector('#url') as HTMLInputElement).value = settings.url
    ;(this.root.querySelector('#key') as HTMLInputElement).value = settings.key
    this.root.querySelector('#controls')!.addEventListener('click', e => {
      const kind = (e.target as HTMLElement).dataset.i
      if (kind) this.hooks.onInput({ type: kind } as Input)
    })
    this.root.querySelector('#settings')!.addEventListener('submit', async e => {
      e.preventDefault()
      const hint = this.root.querySelector('#form-hint')!
      try {
        const url = cleanBridgeUrl((this.root.querySelector('#url') as HTMLInputElement).value)
        const key = (this.root.querySelector('#key') as HTMLInputElement).value.trim()
        await this.hooks.onSave({ url, key })
        hint.textContent = 'Saved. Reconnecting…'
      } catch (err) {
        hint.textContent = String((err as Error).message ?? err)
      }
    })
  }

  update(s: Snapshot) {
    const status = this.root.querySelector('#status')!
    status.className = s.status === 'ok' ? 'ok' : s.status === 'error' ? 'bad' : 'wait'
    status.textContent = s.status === 'ok' ? 'Connected' : s.status === 'error' ? `Offline: ${s.error}` : 'Connecting…'
    this.root.querySelector('#hub')!.textContent = s.hub || '-'
    this.root.querySelector('#count')!.textContent = s.count === null ? '-' : String(s.count)
    this.root.querySelector('#updated')!.textContent = s.notice || s.updated || '-'
    this.root.querySelector('#glasses')!.textContent = this.glasses()
    const mirror = this.root.querySelector('#mirror')!
    if (!s.view) mirror.textContent = ''
    else if (s.view.layout === 'list') mirror.innerHTML = esc(s.view.head) + '\n\n' + s.view.items.map(i => `  ${esc(i)}`).join('\n')
    else mirror.innerHTML = esc(s.view.body) + (s.view.footer ? `\n\n${esc(s.view.footer)}` : '')
  }
}
