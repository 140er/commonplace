// The G2 bridge: a tiny HTTP server between the glasses plugin and a Commonplace hub.
//
// - Holds the Commonplace token. The token only ever goes upstream in the
//   Authorization header; it is never logged, never returned, and any copy of it
//   in an upstream body is redacted before the body reaches the plugin.
// - Forwards only the allowlisted routes in allowlist.mjs.
// - Adds CORS headers for configured plugin origins (the hub has none).
// - Optionally requires a bridge key (X-Bridge-Key) so only the paired plugin
//   can use it; the key is required whenever the bridge listens beyond loopback.
// - Optionally serves the built plugin, so one process is all the phone needs.

import { createServer } from 'node:http'
import { readFile, stat } from 'node:fs/promises'
import { extname, join, normalize, resolve, sep } from 'node:path'
import { timingSafeEqual, createHash } from 'node:crypto'

import { checkBody, filterQuery, matchRoute } from './allowlist.mjs'

const MAX_BODY = 16 * 1024
const MAX_UPSTREAM = 4 * 1024 * 1024
const UPSTREAM_TIMEOUT_MS = 15_000
const TOKEN_RE = /^cmmn_[A-Za-z0-9_-]{43}$/

const STATIC_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
}

/** Validate settings once, before anything listens. Throws with a reason, never the token. */
export function checkConfig(config) {
  let hub
  try {
    hub = new URL(config.hubUrl ?? '')
  } catch {
    throw new Error('CMMN_HUB_URL must be an http(s) URL')
  }
  if (!['http:', 'https:'].includes(hub.protocol) || hub.username || hub.password) {
    throw new Error('CMMN_HUB_URL must be an http(s) URL without credentials')
  }
  if (!TOKEN_RE.test(config.token ?? '')) {
    throw new Error('the Commonplace token is missing or malformed (expected cmmn_ + 43 characters)')
  }
  if (config.key !== undefined && config.key !== '' && config.key.length < 24) {
    throw new Error('the bridge key must be at least 24 characters')
  }
  if (!isLoopback(config.host ?? '127.0.0.1') && !config.key) {
    throw new Error('a bridge key is required when listening beyond loopback (set BRIDGE_KEY_FILE)')
  }
  return { ...config, hubOrigin: hub.origin }
}

/** The upstream body as text, or null once it passes MAX_UPSTREAM (reading stops there). */
async function readCapped(res) {
  const declared = Number(res.headers.get('content-length'))
  if (declared > MAX_UPSTREAM) {
    await res.body?.cancel()
    return null
  }
  const chunks = []
  let size = 0
  for await (const chunk of res.body ?? []) {
    size += chunk.length
    if (size > MAX_UPSTREAM) {
      await res.body.cancel().catch(() => {})
      return null
    }
    chunks.push(chunk)
  }
  return Buffer.concat(chunks).toString('utf8')
}

// Without a key the bridge serves loopback only. The Host must name loopback
// too (any port), which stops a DNS-rebound page. A proxy in front (the dev
// server, with xfwd) appends the real client to X-Forwarded-For; a client
// cannot remove that entry, so any non-loopback hop is refused. This stops a
// LAN client of a LAN-facing dev server even when it forges `Host: localhost`.
const LOOPBACK_ADDR = /^(127\.\d+\.\d+\.\d+|::1|::ffff:127\.\d+\.\d+\.\d+)$/
export function loopbackOnly(req) {
  const host = String(req.headers.host ?? '').toLowerCase()
  const name = host.startsWith('[') ? host.slice(0, host.indexOf(']') + 1) : host.split(':')[0]
  if (name !== '127.0.0.1' && name !== 'localhost' && name !== '[::1]') return false
  if (!LOOPBACK_ADDR.test(req.socket.remoteAddress ?? '')) return false
  const hops = String(req.headers['x-forwarded-for'] ?? '')
    .split(',')
    .map(s => s.trim())
    .filter(Boolean)
  return hops.every(hop => LOOPBACK_ADDR.test(hop))
}

/** A loopback listen address: an IP literal or `localhost`, never a host name like 127.example.net. */
export function isLoopback(host) {
  return host === 'localhost' || host === '::1' || /^127(\.\d{1,3}){3}$/.test(host)
}

function sameSecret(given, expected) {
  // Hash first so the comparison is constant-time regardless of length.
  const a = createHash('sha256').update(String(given)).digest()
  const b = createHash('sha256').update(String(expected)).digest()
  return timingSafeEqual(a, b)
}

/**
 * Build the bridge server (not yet listening).
 * config: { hubUrl, token, key?, host?, allowedOrigins?: string[], staticDir?, log?, fetchImpl? }
 */
export function createBridge(rawConfig) {
  const config = checkConfig(rawConfig)
  const { token, key } = config
  const origins = new Set((config.allowedOrigins ?? []).filter(Boolean))
  const log = config.log ?? (line => console.log(line))
  const doFetch = config.fetchImpl ?? fetch
  const staticRoot = config.staticDir ? resolve(config.staticDir) : null

  const redact = text => (text.includes(token) ? text.split(token).join('[redacted]') : text)

  function cors(req, res) {
    const origin = req.headers.origin
    if (origin && origins.has(origin)) {
      res.setHeader('Access-Control-Allow-Origin', origin)
      res.setHeader('Vary', 'Origin')
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
      res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Bridge-Key')
      res.setHeader('Access-Control-Max-Age', '600')
    }
  }

  function send(res, status, body, type = 'application/json; charset=utf-8') {
    const text = typeof body === 'string' ? body : JSON.stringify(body)
    res.writeHead(status, {
      'Content-Type': type,
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
    })
    res.end(text)
  }

  function readBody(req) {
    return new Promise((resolveBody, reject) => {
      let size = 0
      const chunks = []
      req.on('data', chunk => {
        size += chunk.length
        if (size > MAX_BODY) {
          // Answer 413 but keep draining, so the client reads the reply instead of a reset.
          req.removeAllListeners('data')
          req.resume()
          reject(Object.assign(new Error('too large'), { status: 413 }))
          return
        }
        chunks.push(chunk)
      })
      req.on('end', () => resolveBody(Buffer.concat(chunks).toString('utf8')))
      req.on('error', reject)
    })
  }


  function authorized(req) {
    if (!key) return loopbackOnly(req)
    const given = req.headers['x-bridge-key']
    return typeof given === 'string' && sameSecret(given, key)
  }

  async function forward(req, res, url) {
    const route = matchRoute(req.method, url.pathname)
    if (!route) return [404, { error: 'not allowed by the G2 bridge' }, 'refused']
    const q = filterQuery(route, url.searchParams)
    if (q.error) return [400, { error: q.error }, route.name]
    let payload
    if (route.method === 'POST') {
      const type = (req.headers['content-type'] ?? '').split(';')[0].trim().toLowerCase()
      if (type !== 'application/json') return [415, { error: 'send application/json' }, route.name]
      const checked = checkBody(route, await readBody(req))
      if (checked.error) return [400, { error: checked.error }, route.name]
      payload = JSON.stringify(checked.body)
    }
    const target = `${config.hubOrigin}${url.pathname}${q.query ? `?${q.query}` : ''}`
    let upstream
    try {
      upstream = await doFetch(target, {
        method: route.method,
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/json, text/plain',
          ...(payload ? { 'Content-Type': 'application/json' } : {}),
        },
        body: payload,
        redirect: 'manual',
        signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      })
    } catch (err) {
      const why = err?.name === 'TimeoutError' ? 'timeout' : (err?.cause?.code ?? err?.name ?? 'error')
      return [502, { error: 'hub unreachable', reason: String(why) }, route.name]
    }
    const type = (upstream.headers.get('content-type') ?? '').toLowerCase()
    const raw = await readCapped(upstream)
    if (raw === null) return [502, { error: 'hub reply too large' }, route.name]
    const text = redact(raw)
    if (upstream.status >= 300 && upstream.status < 400) {
      return [502, { error: 'hub redirected; check CMMN_HUB_URL' }, route.name]
    }
    if (type.startsWith('text/plain')) return [upstream.status, text, route.name, 'text/plain; charset=utf-8']
    return [upstream.status, text, route.name]
  }

  async function serveStatic(req, res, url) {
    if (!staticRoot || req.method !== 'GET') return false
    try {
      const rel = url.pathname === '/' ? '/index.html' : decodeURIComponent(url.pathname)
      const file = normalize(join(staticRoot, rel))
      if (file !== staticRoot && !file.startsWith(staticRoot + sep)) return false
      if (!(await stat(file)).isFile()) return false
      const body = await readFile(file)
      res.writeHead(200, {
        'Content-Type': STATIC_TYPES[extname(file)] ?? 'application/octet-stream',
        'Cache-Control': 'no-cache',
        'X-Content-Type-Options': 'nosniff',
      })
      res.end(body)
      return true
    } catch {
      return false
    }
  }

  return createServer(async (req, res) => {
    const started = Date.now()
    let label = 'static'
    let status = 200
    try {
      const url = new URL(req.url ?? '/', 'http://bridge.invalid')
      cors(req, res)
      if (req.method === 'OPTIONS') {
        label = 'preflight'
        status = 204
        res.writeHead(204)
        res.end()
      } else if (url.pathname === '/bridge/health') {
        label = 'health'
        send(res, 200, { ok: true })
      } else if (url.pathname === '/bridge/info' || url.pathname.startsWith('/api/')) {
        if (!authorized(req)) {
          label = 'unpaired'
          status = 401
          send(res, 401, { error: key ? 'bridge key missing or wrong' : 'loopback only without a bridge key' })
        } else if (url.pathname === '/bridge/info') {
          label = 'info'
          send(res, 200, { hub: config.hubOrigin, paired: Boolean(key) })
        } else {
          const [s, body, name, type] = await forward(req, res, url)
          label = name
          status = s
          send(res, s, body, type)
        }
      } else if (!(await serveStatic(req, res, url))) {
        status = 404
        send(res, 404, { error: 'not found' })
      }
    } catch (err) {
      status = err?.status ?? 500
      if (!res.headersSent) send(res, status, { error: status === 413 ? 'request too large' : 'bridge error' })
    } finally {
      // Method, route name, status and timing only: never headers, bodies or the token.
      log(`${req.method} ${label} ${status} ${Date.now() - started}ms`)
    }
  })
}
