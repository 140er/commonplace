// The only hub routes the glasses may reach. Everything else is refused before
// any request leaves the bridge. Each entry also names the query parameters and
// JSON body fields it forwards; anything else is dropped (query) or refused (body).

const CARD = '([1-9][0-9]{0,8})'
const BOARD = '([a-z0-9][a-z0-9_-]{0,63})'

const DECIDE_THEN = new Set(['return', 'todo', 'done', 'backlog'])

export const ROUTES = [
  { name: 'needs-me', method: 'GET', pattern: /^\/api\/needs-me$/ },
  {
    name: 'digest',
    method: 'GET',
    pattern: /^\/api\/needs-me\/digest$/,
    query: { format: v => v === 'json' || v === 'text' },
  },
  {
    name: 'events',
    method: 'GET',
    pattern: /^\/api\/events$/,
    query: { after: v => /^[0-9]{1,19}$/.test(v) },
  },
  { name: 'me', method: 'GET', pattern: /^\/api\/me$/ },
  { name: 'boards', method: 'GET', pattern: /^\/api\/boards$/ },
  {
    name: 'board-cards',
    method: 'GET',
    pattern: new RegExp(`^/api/boards/${BOARD}/cards$`),
    query: { status: v => /^[a-z]{1,16}$/.test(v) },
  },
  { name: 'card', method: 'GET', pattern: new RegExp(`^/api/cards/${CARD}$`) },
  {
    name: 'decide',
    method: 'POST',
    pattern: new RegExp(`^/api/cards/${CARD}/decide$`),
    body: {
      version: { required: true, check: v => Number.isInteger(v) && v >= 0 },
      choice: { check: v => typeof v === 'string' && v.length >= 1 && v.length <= 120 },
      reply: { check: v => typeof v === 'string' && v.trim().length >= 1 && v.length <= 2000 },
      then: { check: v => DECIDE_THEN.has(v) },
    },
  },
  {
    name: 'comment',
    method: 'POST',
    pattern: new RegExp(`^/api/cards/${CARD}/comments$`),
    body: {
      text: { required: true, check: v => typeof v === 'string' && v.trim().length >= 1 && v.length <= 2000 },
      version: { check: v => Number.isInteger(v) && v >= 0 },
    },
  },
]

/** The allowlisted route for a method and path, or null. */
export function matchRoute(method, path) {
  for (const route of ROUTES) {
    if (route.method === method && route.pattern.test(path)) return route
  }
  return null
}

/** Only the declared query parameters, each validated; anything else is dropped. */
export function filterQuery(route, searchParams) {
  const out = new URLSearchParams()
  for (const [key, check] of Object.entries(route.query ?? {})) {
    const value = searchParams.get(key)
    if (value === null) continue
    if (!check(value)) return { error: `invalid ${key}` }
    out.set(key, value)
  }
  return { query: out.toString() }
}

/** A JSON body with only the declared fields, each validated. */
export function checkBody(route, raw) {
  let body
  try {
    body = JSON.parse(raw)
  } catch {
    return { error: 'invalid JSON' }
  }
  if (body === null || typeof body !== 'object' || Array.isArray(body)) return { error: 'JSON object required' }
  const spec = route.body ?? {}
  for (const key of Object.keys(body)) {
    if (!(key in spec)) return { error: `field not allowed: ${key}` }
  }
  for (const [key, rule] of Object.entries(spec)) {
    if (!(key in body)) {
      if (rule.required) return { error: `${key} is required` }
      continue
    }
    if (!rule.check(body[key])) return { error: `invalid ${key}` }
  }
  return { body }
}
