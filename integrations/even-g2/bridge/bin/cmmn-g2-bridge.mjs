#!/usr/bin/env node
// Start the G2 bridge from environment settings. See ../../README.md.
//
//   CMMN_HUB_URL            Commonplace hub, e.g. https://app.cmmn.dev (required)
//   CMMN_TOKEN_FILE         file holding the Commonplace token (preferred), or
//   CMMN_TOKEN              the token itself
//   BRIDGE_HOST             listen address (default 127.0.0.1)
//   BRIDGE_PORT             listen port (default 8790)
//   BRIDGE_KEY_FILE         file holding the pairing key; required beyond loopback
//   BRIDGE_ALLOWED_ORIGINS  comma-separated plugin origins allowed cross-origin (CORS)
//   BRIDGE_STATIC_DIR       serve the built plugin (plugin/dist) from this directory
//
// Helpers (they print nothing secret unless asked for the pairing URL):
//   cmmn-g2-bridge --make-key <file>     write a fresh random pairing key (mode 600)
//   cmmn-g2-bridge --pair-url <base-url> print <base-url>/?key=<key> for `evenhub qr`

import { randomBytes } from 'node:crypto'
import { readFileSync, writeFileSync, existsSync } from 'node:fs'

import { createBridge } from '../src/server.mjs'

function readSecret(fileVar, valueVar) {
  const file = process.env[fileVar]
  if (file) return readFileSync(file, 'utf8').trim()
  return (process.env[valueVar] ?? '').trim() || undefined
}

const [flag, arg] = process.argv.slice(2)

if (flag === '--make-key') {
  if (!arg) throw new Error('usage: --make-key <file>')
  if (existsSync(arg)) {
    console.error(`kept the existing key in ${arg}`)
  } else {
    writeFileSync(arg, randomBytes(24).toString('base64url') + '\n', { mode: 0o600 })
    console.error(`wrote a new pairing key to ${arg}`)
  }
  process.exit(0)
}

if (flag === '--pair-url') {
  const key = readSecret('BRIDGE_KEY_FILE', 'BRIDGE_KEY')
  if (!arg || !key) throw new Error('usage: BRIDGE_KEY_FILE=<file> --pair-url http://<host>:<port>')
  const url = new URL(arg)
  url.searchParams.set('key', key)
  process.stdout.write(url.toString() + '\n')
  process.exit(0)
}

const host = process.env.BRIDGE_HOST || '127.0.0.1'
const port = Number(process.env.BRIDGE_PORT || 8790)

let server
try {
  server = createBridge({
    hubUrl: process.env.CMMN_HUB_URL,
    token: readSecret('CMMN_TOKEN_FILE', 'CMMN_TOKEN'),
    key: readSecret('BRIDGE_KEY_FILE', 'BRIDGE_KEY'),
    host,
    allowedOrigins: (process.env.BRIDGE_ALLOWED_ORIGINS ?? '').split(',').map(s => s.trim()),
    staticDir: process.env.BRIDGE_STATIC_DIR || undefined,
  })
} catch (err) {
  console.error(`cmmn-g2-bridge: ${err.message}`)
  process.exit(2)
}

server.listen(port, host, () => {
  const hub = new URL(process.env.CMMN_HUB_URL).origin
  console.log(`cmmn-g2-bridge listening on http://${host}:${port} -> ${hub}`)
})
