# Going public: checklist

Gate: hub-core PR #13 reviewed, the integrated test run on feat/mvp green, and Jeffrey's go. Until then everything stays tailnet-only.

## Hosts

| Host | Serves | Proxied to the gateway |
|---|---|---|
| commonplace.run (+ www → 308) | Website (static, Caddy) | nothing |
| app.cmmn.dev | The app | `/app*`, `/api/*`, `/health*` |
| cmmn.dev | MCP, OAuth, short links, short-name redirects | `/mcp`, `/oauth/*`, `/.well-known/*`, `/s/*`, `/health*` |

Caddy file: `deploy/commonplace-public.caddy`, bound to 168.119.13.136 and 2a01:4f8:242:4404::2 (the tailnet site stays on 100.76.209.1). TLS: Caddy's automatic ACME; ports 80 and 443 are already open on edge1.

## DNS (Cloudflare, DNS only: grey cloud, so Cloudflare never sees plaintext)

| Zone | Name | Type | Value |
|---|---|---|---|
| commonplace.run | @ | A | 168.119.13.136 |
| commonplace.run | @ | AAAA | 2a01:4f8:242:4404::2 |
| commonplace.run | www | CNAME | commonplace.run |
| cmmn.dev | @ | A | 168.119.13.136 |
| cmmn.dev | @ | AAAA | 2a01:4f8:242:4404::2 |
| cmmn.dev | app | A | 168.119.13.136 |
| cmmn.dev | app | AAAA | 2a01:4f8:242:4404::2 |

Keep the existing `140er.cmmn.dev`. Add CAA `0 issue "letsencrypt.org"` on both zones (optional).

## edge1 `.env` at go time

Use `/srv/commonplace/.env`.

```
COMMONPLACE_SITE_URL=https://commonplace.run
COMMONPLACE_APP_URL=https://app.cmmn.dev
COMMONPLACE_AGENT_URL=https://cmmn.dev
COMMONPLACE_COOKIE_DOMAIN=cmmn.dev
COMMONPLACE_ALLOWED_HOSTS=app.cmmn.dev,cmmn.dev,edge1.tail6cee8c.ts.net,100.76.209.1,127.0.0.1:8765
# Optional social sign-in (set every variable for each enabled provider):
# COMMONPLACE_GOOGLE_CLIENT_ID, COMMONPLACE_GOOGLE_CLIENT_SECRET
# COMMONPLACE_GITHUB_CLIENT_ID, COMMONPLACE_GITHUB_CLIENT_SECRET
# COMMONPLACE_APPLE_CLIENT_ID, COMMONPLACE_APPLE_TEAM_ID,
# COMMONPLACE_APPLE_KEY_ID, COMMONPLACE_APPLE_PRIVATE_KEY
# COMMONPLACE_X_CLIENT_ID, COMMONPLACE_X_CLIENT_SECRET
# COMMONPLACE_DISCORD_CLIENT_ID, COMMONPLACE_DISCORD_CLIENT_SECRET
# COMMONPLACE_SIGNUP=closed turns off new-account sign-up.
```

Provider registration and signup policy: [social sign-in setup](../commonplace/SETUP.md). Apply optional variables through the gateway's environment and restart it; no provider variables means no change to sign-in.

OAuth issuer and every metadata URL then come from `https://cmmn.dev`; sign-in and app links from `https://app.cmmn.dev`. Existing tokens keep working. Short links made before the switch point at the tailnet host: they still resolve inside the tailnet only.

## Public surface

- [ ] Every gateway route answers 401 without credentials, except: `/health`, `/healthz`, `/app/` static files, `/app/login`, `/app/auth/*`, `/s/<code>`, `/.well-known/*`, `/oauth/register|authorize|token|revoke` (test: `tests/test_commonplace_public_surface.py`).
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
2. Add the `.env` lines, `COMPOSE_FILE=deploy/compose.edge1.yml /srv/commonplace/deploy.sh`.
3. `sudo cp deploy/commonplace-public.caddy /etc/caddy/sites/ && sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy`.
4. Check: `curl -I https://commonplace.run`, `curl https://cmmn.dev/.well-known/oauth-protected-resource`, `curl -i https://cmmn.dev/mcp` (401 with `resource_metadata`), sign in on `https://app.cmmn.dev/app/`.
5. claude.ai: Settings → Connectors → Add custom connector → `https://cmmn.dev/mcp`. ChatGPT: same URL as a developer-mode connector.

Rollback: remove `/etc/caddy/sites/commonplace-public.caddy`, reload Caddy. DNS can stay.
