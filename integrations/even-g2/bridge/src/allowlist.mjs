// The only hub routes the glasses may reach: exactly what the plugin uses.
// Everything else is refused before any request leaves the bridge. Each entry
// also names the query parameters and JSON body fields it forwards; anything
// else is dropped (query) or refused (body).

const CARD = '([1-9][0-9]{0,8})'

/** A spec object with no prototype, so `toString` or `__proto__` never count as declared. */
const fields = spec => Object.assign(Object.create(null), spec)

export const ROUTES = [
  { name: 'needs-me', method: 'GET', pattern: /^\/api\/needs-me$/ },
  {
    name: 'events',
    method: 'GET',
    pattern: /^\/api\/events$/,
    query: fields({ after: v => /^[0-9]{1,19}$/.test(v) }),
  },
  { name: 'me', method: 'GET', pattern: /^\/api\/me$/ },
  { name: 'boards', method: 'GET', pattern: /^\/api\/boards$/ },
  { name: 'card', method: 'GET', pattern: new RegExp(`^/api/cards/${CARD}$`) },
  {
    // Pick one offered option; the card goes back to whoever asked (the hub default).
    name: 'decide',
    method: 'POST',
    pattern: new RegExp(`^/api/cards/${CARD}/decide$`),
    body: fields({
      version: { required: true, check: v => Number.isInteger(v) && v >= 0 },
      choice: { required: true, check: v => typeof v === 'string' && v.length >= 1 && v.length <= 120 },
    }),
  },
  {
    name: 'comment',
    method: 'POST',
    pattern: new RegExp(`^/api/cards/${CARD}/comments$`),
    body: fields({
      text: { required: true, check: v => typeof v === 'string' && v.trim().length >= 1 && v.length <= 2000 },
    }),
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
  const spec = route.body ?? fields({})
  for (const key of Object.keys(body)) {
    if (!Object.hasOwn(spec, key)) return { error: `field not allowed: ${key}` }
  }
  for (const [key, rule] of Object.entries(spec)) {
    if (!Object.hasOwn(body, key)) {
      if (rule.required) return { error: `${key} is required` }
      continue
    }
    if (!rule.check(body[key])) return { error: `invalid ${key}` }
  }
  return { body }
}
