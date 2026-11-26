import { waitForEvenAppBridge, type EvenAppBridge } from '@evenrealities/even_hub_sdk'

import { HubClient } from './api'
import { G2App, type Display } from './app'
import { GlassesDisplay } from './glasses'
import { toInput } from './input'
import { PhoneView } from './phone'
import { loadSettings, saveSettings, type Store } from './settings'

/** How often the event log is read while the plugin is open. */
const POLL_MS = 20_000

// Outside the Even app (a plain browser) there is no native bridge: draw on the
// phone mirror only, keep settings in the page's storage.
const noGlasses: Display = { render: async () => {}, exit: async () => {} }

function browserStore(): Store {
  return {
    get: async key => window.localStorage.getItem(key) ?? '',
    set: async (key, value) => window.localStorage.setItem(key, value),
  }
}

// The Even app's own storage when the native side answers, else the page's.
function evenStore(bridge: EvenAppBridge): Store {
  const fallback = browserStore()
  return {
    get: key => bridge.getLocalStorage(key).catch(() => fallback.get(key)),
    set: (key, value) => bridge.setLocalStorage(key, value).catch(() => fallback.set(key, value)),
  }
}

const BRIDGE_WAIT_MS = 3000
const withTimeout = <T,>(p: Promise<T>, ms: number) =>
  Promise.race([p, new Promise<null>(resolve => setTimeout(() => resolve(null), ms))])

async function boot() {
  // In a plain browser the SDK still reports ready but native calls fail; the
  // glasses display then marks itself unavailable and the phone mirror carries on.
  const bridge = await withTimeout(waitForEvenAppBridge(), BRIDGE_WAIT_MS).catch(() => null)
  const store = bridge ? evenStore(bridge) : browserStore()
  const settings = await loadSettings(store, window.location, window.history)
  const api = new HubClient(settings)
  const glasses = bridge ? new GlassesDisplay(bridge) : null

  let phone: PhoneView | null = null
  const app = new G2App({
    api,
    display: glasses ?? noGlasses,
    onChange: s => phone?.update(s),
  })
  phone = new PhoneView(
    document.querySelector<HTMLElement>('#app')!,
    {
      onSave: async next => {
        await saveSettings(store, next)
        api.configure(next)
        await app.start()
      },
      onInput: input => void app.input(input),
    },
    settings,
    () => (!glasses ? 'not in the Even app' : glasses.available ? 'connected' : 'not reachable'),
  )

  let timer: ReturnType<typeof setInterval> | null = null
  const stop = () => {
    if (timer) clearInterval(timer)
    timer = null
  }
  const run = () => {
    if (!timer) timer = setInterval(() => void app.poll(), POLL_MS)
  }
  let exited = false
  bridge?.onEvenHubEvent(event => {
    const input = toInput(event)
    if (input === 'exit') {
      exited = true
      stop()
    } else if (input === 'resume') {
      // Back in front after an exit: draw the page again and catch up at once.
      if (exited) glasses?.reset()
      exited = false
      run()
      void app.render().then(() => app.poll())
    } else if (input) void app.input(input)
  })
  window.addEventListener('beforeunload', stop)

  await app.start()
  run()
}

boot().catch(err => {
  console.error('Commonplace G2 failed to start', err)
  document.querySelector('#app')!.textContent = `Could not start: ${String(err)}`
})
