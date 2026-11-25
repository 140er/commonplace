import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { createBridge, checkConfig } from '../src/server.mjs'
import { matchRoute, ROUTES } from '../src/allowlist.mjs'

const TOKEN = 'cmmn_' + 'T'.repeat(20) + 'x'.repeat(23)
const KEY = 'k'.repeat(32)

// A fake hub that records what reached it and can echo the token back.
const seen = []
let hub
let hubUrl

function listen(server) {
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server.address().port)))
}

before(async () => {
  hub = createServer((req, res) => {
    let body = ''
    req.on('data', c => (body += c))
    req.on('end', () => {
      seen.push({ method: req.method, url: req.url, auth: req.headers.authorization, cookie: req.headers.cookie, body })
      if (req.url.startsWith('/api/needs-me/digest')) {
        res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' })
        res.end('Two things need you.')
        return
      }
      if (req.url === '/api/me') {
        // Misbehaving upstream: echoes the credential it was sent.
        res.writeHead(200, { 'Content-Type': 'application/json', 'Set-Cookie': 'session=abc' })
        res.end(JSON.stringify({ echo: req.headers.authorization }))
        return
      }
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ ok: true, path: req.url }))
    })
  })
  hubUrl = `http://127.0.0.1:${await listen(hub)}`
})

after(() => hub.close())

async function withBridge(extra, fn) {
  const logs = []
  const bridge = createBridge({ hubUrl, token: TOKEN, log: l => logs.push(l), ...extra })
  const port = await listen(bridge)
  try {
    await fn(`http://127.0.0.1:${port}`, logs)
  } finally {
    bridge.close()
  }
}

test('every allowlisted route forwards with the token upstream only', async () => {
  await withBridge({}, async (base, logs) => {
    seen.length = 0
    for (const path of ['/api/needs-me', '/api/events?after=12', '/api/boards', '/api/me', '/api/cards/7']) {
      const r = await fetch(base + path)
      assert.equal(r.status, 200, path)
      const text = await r.text()
      assert.ok(!text.includes(TOKEN), path)
    }
    assert.equal(seen.length, 5)
    for (const s of seen) assert.equal(s.auth, `Bearer ${TOKEN}`)
    assert.ok(logs.every(l => !l.includes(TOKEN)))
  })
})

test('routes outside the allowlist are refused before reaching the hub', async () => {
  await withBridge({}, async base => {
    seen.length = 0
    const refused = [
      ['GET', '/api/memory/search?q=x'],
      ['POST', '/api/boards'],
      ['PATCH', '/api/cards/7'],
      ['DELETE', '/api/cards/7'],
      ['GET', '/api/connections'],
      ['POST', '/api/voice/transcribe'],
      ['GET', '/api/cards/7/../../connections'],
      ['GET', '/api/cards/abc'],
      ['GET', '/api/boards/../me/cards'],
      ['POST', '/api/cards/7/approve'],
      ['GET', '/api/needs-me/digest'],
      ['GET', '/api/boards/home/cards'],
      ['GET', '/mcp'],
    ]
    for (const [method, path] of refused) {
      const r = await fetch(base + path, { method, headers: { 'Content-Type': 'application/json' }, body: method === 'GET' ? undefined : '{}' })
      assert.equal(r.status, 404, `${method} ${path}`)
    }
    assert.equal(seen.length, 0, JSON.stringify(seen))
  })
})

test('the token never leaks into a response, even when the hub echoes it', async () => {
  await withBridge({}, async (base, logs) => {
    const r = await fetch(base + '/api/me')
    const text = await r.text()
    assert.ok(!text.includes(TOKEN))
    assert.match(text, /\[redacted\]/)
    assert.equal(r.headers.get('set-cookie'), null)
    assert.ok(logs.every(l => !l.includes(TOKEN)))
  })
})

test('query parameters are filtered and validated', async () => {
  await withBridge({}, async base => {
    seen.length = 0
    await fetch(base + '/api/needs-me?token=x&q=y')
    assert.equal(seen.at(-1).url, '/api/needs-me')
    const bad = await fetch(base + '/api/events?after=-1')
    assert.equal(bad.status, 400)
    seen.length = 0
    await fetch(base + '/api/events?after=5&limit=9999')
    assert.equal(seen.at(-1).url, '/api/events?after=5')
  })
})

test('decide and comment bodies are checked field by field', async () => {
  await withBridge({}, async base => {
    const post = (path, body, type = 'application/json') =>
      fetch(base + path, { method: 'POST', headers: { 'Content-Type': type }, body: typeof body === 'string' ? body : JSON.stringify(body) })
    seen.length = 0
    assert.equal((await post('/api/cards/7/decide', { version: 3, choice: 'Ship' })).status, 200)
    assert.deepEqual(JSON.parse(seen.at(-1).body), { version: 3, choice: 'Ship' })
    assert.equal((await post('/api/cards/7/decide', { choice: 'Ship' })).status, 400)
    assert.equal((await post('/api/cards/7/decide', { version: 3, choice: 'Ship', then: 'escalate' })).status, 400)
    assert.equal((await post('/api/cards/7/decide', { version: 3, choice: 'Ship', assignee: 'x' })).status, 400)
    assert.equal((await post('/api/cards/7/decide', { version: 3, reply: 'no choice' })).status, 400)
    // Inherited names are not declared fields: refused, never forwarded.
    for (const key of ['toString', 'constructor', '__proto__', 'hasOwnProperty']) {
      const raw = `{"version":3,"choice":"Ship","${key}":{"then":"done"}}`
      assert.equal((await post('/api/cards/7/decide', raw)).status, 400, key)
    }
    assert.equal((await post('/api/cards/7/comments', { text: 'hi', version: 2 })).status, 400)
    assert.equal((await post('/api/cards/7/comments', { text: 'Seen on glasses' })).status, 200)
    assert.equal((await post('/api/cards/7/comments', { text: '   ' })).status, 400)
    assert.equal((await post('/api/cards/7/comments', '[1]')).status, 400)
    assert.equal((await post('/api/cards/7/comments', 'text=hi', 'application/x-www-form-urlencoded')).status, 415)
    assert.equal((await post('/api/cards/7/comments', { text: 'x'.repeat(20000) })).status, 413)
    assert.equal(seen.length, 2)
  })
})

test('a bridge key, when set, is required on API calls', async () => {
  await withBridge({ key: KEY }, async base => {
    assert.equal((await fetch(base + '/api/needs-me')).status, 401)
    assert.equal((await fetch(base + '/api/needs-me', { headers: { 'X-Bridge-Key': 'wrong' } })).status, 401)
    assert.equal((await fetch(base + '/api/needs-me', { headers: { 'X-Bridge-Key': KEY } })).status, 200)
    assert.equal((await fetch(base + '/bridge/health')).status, 200)
    const info = await fetch(base + '/bridge/info', { headers: { 'X-Bridge-Key': KEY } })
    const body = await info.json()
    assert.deepEqual(body, { hub: hubUrl, paired: true })
  })
})

test('without a key only a loopback Host is served (DNS rebinding, LAN proxies)', async () => {
  const { request } = await import('node:http')
  const get = (port, host) =>
    new Promise((resolve, reject) => {
      const req = request({ host: '127.0.0.1', port, path: '/api/needs-me', headers: { Host: host } }, res => {
        res.resume()
        resolve(res.statusCode)
      })
      req.on('error', reject)
      req.end()
    })
  await withBridge({}, async base => {
    const port = new URL(base).port
    seen.length = 0
    assert.equal(await get(port, `evil.example:${port}`), 401)
    // A LAN client of a dev proxy that keeps its Host header (Vite, changeOrigin off).
    assert.equal(await get(port, `192.168.1.20:5173`), 401)
    assert.equal(await get(port, `localhost.evil.example:${port}`), 401)
    assert.equal(seen.length, 0)
    assert.equal(await get(port, `localhost:${port}`), 200)
    assert.equal(await get(port, `127.0.0.1:5173`), 200)
    assert.equal(await get(port, `[::1]:5173`), 200)
  })
  await withBridge({ key: KEY }, async base => {
    const r = await fetch(base + '/api/needs-me', { headers: { 'X-Bridge-Key': KEY } })
    assert.equal(r.status, 200)
  })
})

test('CORS headers only for configured origins', async () => {
  await withBridge({ allowedOrigins: ['http://localhost:5173'] }, async base => {
    const ok = await fetch(base + '/api/needs-me', { headers: { Origin: 'http://localhost:5173' } })
    assert.equal(ok.headers.get('access-control-allow-origin'), 'http://localhost:5173')
    const pre = await fetch(base + '/api/cards/1/decide', { method: 'OPTIONS', headers: { Origin: 'http://localhost:5173' } })
    assert.equal(pre.status, 204)
    assert.match(pre.headers.get('access-control-allow-headers'), /X-Bridge-Key/)
    const other = await fetch(base + '/api/needs-me', { headers: { Origin: 'http://evil.example' } })
    assert.equal(other.headers.get('access-control-allow-origin'), null)
  })
})

test('an unreachable hub gives 502 without details that could hold secrets', async () => {
  const logs = []
  const bridge = createBridge({ hubUrl: 'http://127.0.0.1:9', token: TOKEN, log: l => logs.push(l) })
  const port = await listen(bridge)
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/needs-me`)
    assert.equal(r.status, 502)
    const text = await r.text()
    assert.ok(!text.includes(TOKEN))
  } finally {
    bridge.close()
  }
})

test('serves the built plugin without escaping its directory', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'g2-static-'))
  writeFileSync(join(dir, 'index.html'), '<p>plugin</p>')
  await withBridge({ staticDir: dir }, async base => {
    assert.equal(await (await fetch(base + '/')).text(), '<p>plugin</p>')
    assert.equal((await fetch(base + '/%2e%2e/%2e%2e/etc/passwd')).status, 404)
  })
})

test('config is refused without a hub, a well-formed token, or a key beyond loopback', () => {
  assert.throws(() => checkConfig({ hubUrl: 'ftp://x', token: TOKEN }), /http/)
  assert.throws(() => checkConfig({ hubUrl: 'https://u:p@hub.example', token: TOKEN }), /credentials/)
  assert.throws(() => checkConfig({ hubUrl: hubUrl, token: 'nope' }), err => !String(err).includes('nope'))
  assert.throws(() => checkConfig({ hubUrl, token: TOKEN, host: '0.0.0.0' }), /bridge key/)
  assert.throws(() => checkConfig({ hubUrl, token: TOKEN, host: '0.0.0.0', key: 'short' }), /24/)
  assert.ok(checkConfig({ hubUrl, token: TOKEN, host: '192.0.2.1', key: KEY }))
})

test('the allowlist is exactly the read, decide and comment routes', () => {
  assert.deepEqual(
    ROUTES.map(r => `${r.method} ${r.name}`),
    ['GET needs-me', 'GET events', 'GET me', 'GET boards', 'GET card', 'POST decide', 'POST comment'],
  )
  assert.equal(matchRoute('GET', '/api/cards/0'), null)
  assert.equal(matchRoute('GET', '/api/cards/12/'), null)
})
