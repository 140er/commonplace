# Going public: checklist

Enable public access only after the operator verifies authentication, backups, DNS and TLS.

## Hosts

| Host | Serves | Proxied to the gateway |
|---|---|---|
| commonplace.run (+ www → 308) | Website (static, Caddy) | nothing |
| app.cmmn.dev | The app | `/app*`, `/api/*`, `/health*` |
| cmmn.dev | MCP, OAuth, short links, short-name redirects | `/mcp`, `/oauth/*`, `/.well-known/*`, `/s/*`, `/health*` |

Caddy template: `deploy/commonplace-public.caddy.example`. Copy it to an operator-owned file, replace documentation bind addresses and the static root, and validate the host configuration before reloading Caddy. The private site is configured separately.

## DNS (Cloudflare, DNS only: grey cloud, so Cloudflare never sees plaintext)

| Zone | Name | Type | Value |
|---|---|---|---|
| commonplace.run | @ | A | 192.0.2.30 |
| commonplace.run | @ | AAAA | 2001:db8::30 |
| commonplace.run | www | CNAME | commonplace.run |
| cmmn.dev | @ | A | 192.0.2.30 |
| cmmn.dev | @ | AAAA | 2001:db8::30 |
| cmmn.dev | app | A | 192.0.2.30 |
| cmmn.dev | app | AAAA | 2001:db8::30 |

Preserve unrelated DNS records. Add CAA `0 issue "letsencrypt.org"` on both zones (optional).

## Operator environment

```
COMMONPLACE_SITE_URL=https://commonplace.run
COMMONPLACE_APP_URL=https://app.cmmn.dev
COMMONPLACE_AGENT_URL=https://cmmn.dev
COMMONPLACE_COOKIE_DOMAIN=cmmn.dev
COMMONPLACE_ALLOWED_HOSTS=app.cmmn.dev,cmmn.dev,hub.example.com,192.0.2.10,127.0.0.1:8765
```

OAuth issuer and every metadata URL then come from `https://cmmn.dev`; sign-in and app links from `https://app.cmmn.dev`. Existing tokens keep working. Short links made before the switch point at the tailnet host: they still resolve inside the tailnet only.

## Public surface

- [ ] Every gateway route answers 401 without credentials, except: `/health`, `/healthz`, `/app/` static files, `/app/login`, `/s/<code>`, `/.well-known/*`, `/oauth/register|authorize|token|revoke` (test: `tests/test_commonplace_public_surface.py`).
- [ ] Rate limits on (default): unauthenticated and failed auth 30/min per IP; login and OAuth endpoints 10/min per IP; 600/min per token; untrusted tokens 60/min and 120 writes/hour; short links 120/min per IP.
- [ ] Client IP from Caddy's `X-Forwarded-For` only (with no trusted proxies configured, Caddy replaces any client-sent value with the real peer).
- [ ] Host check on (`COMMONPLACE_ALLOWED_HOSTS`): other Host headers get 421.
- [ ] No debug endpoints, no tracebacks in responses, no tokens or memory text in logs.
- [ ] Request bodies capped (8 MiB MCP, 1 MiB REST).
- [ ] Secure, HttpOnly, SameSite=Lax session cookie for `.cmmn.dev`; login links are direct (never short links) and work once.
- [ ] `mine`, `sync`, `hook_settings`, `reconnect` are never served through the gateway (hub core).
- [ ] Grok Bot tokens: slim, untrusted, write only to `inbox`.
- [ ] Backups: nightly hook ran at least once and a restore was tried.

## Go steps

1. DNS records above (Cloudflare API token or by hand).
2. Add the environment lines to the operator-owned `.env`; deploy with the external `COMPOSE_FILE` and env paths described in `commonplace/DEPLOY.md`.
3. Install the filled-in Caddy site in the operator-owned configuration, validate the full configuration and reload the host service.
4. Check: `curl -I https://commonplace.run`, `curl https://cmmn.dev/.well-known/oauth-protected-resource`, `curl -i https://cmmn.dev/mcp` (401 with `resource_metadata`), sign in on `https://app.cmmn.dev/app/`.
5. claude.ai: Settings → Connectors → Add custom connector → `https://cmmn.dev/mcp`. ChatGPT: same URL as a developer-mode connector.

Rollback: restore the previous operator-owned public Caddy site and reload Caddy. DNS can stay.
