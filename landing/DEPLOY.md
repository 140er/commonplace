# Commonplace site: build, check, deploy

The site is plain static files in `landing/public/`. There is no build step: the
folder you check is the folder nginx serves. One `commonplace-site` container on
zurvur serves both domains. `landing/nginx.conf` routes by host:

| Host | Answer |
|---|---|
| `commonplace.run` | the site, with a strict Content-Security-Policy |
| `www.commonplace.run` | 301 to `https://commonplace.run` |
| `cmmn.dev`, `www.cmmn.dev` | 302 short links (table below); unknown names go to the home page |

No GitHub Actions. Every check runs locally.

## Check

```bash
node landing/check.mjs                        # names, links, zero third-party requests
NGINX_BIN=/usr/sbin/nginx node landing/check.mjs  # also tests nginx.conf host routing and short links
node landing/check.mjs --shots /tmp/shots     # also saves desktop and phone screenshots
```

It needs Node 22+ and Chrome (`CHROME=/path` if it isn't on PATH) and installs nothing.
The request check opens each page at 1440 and 390 px in headless Chrome, scrolls it,
clicks every tab and row, and fails on any request to another host. That is milestone
W's done test.

## 1. The container on zurvur

```bash
mkdir -p /mnt/cache/appdata/commonplace-site
git clone --depth 1 --branch develop https://github.com/140er/mempalace.git \
  /mnt/cache/appdata/commonplace-site/repo

docker run -d --name commonplace-site --restart unless-stopped \
  --network <the network Nginx Proxy Manager is on> \
  -v /mnt/cache/appdata/commonplace-site/repo/landing/public:/usr/share/nginx/html:ro \
  -v /mnt/cache/appdata/commonplace-site/repo/landing:/etc/nginx/conf.d:ro \
  nginx:1.27-alpine
```

- Mount the `landing` folder as `conf.d`, not the single `nginx.conf` file.
  `git pull` replaces files, and a single-file bind mount keeps serving the old copy.
  nginx only includes `*.conf`, so `nginx.conf` is the one file it reads from there.
- The appdata share stays cache-only (PRODUCT-DESIGN §14).
- No port is published. NPM reaches the container as `commonplace-site:80` on the shared
  network. Find NPM's network with `docker inspect <npm container> -f '{{json .NetworkSettings.Networks}}'`.
- Smoke test from zurvur:
  ```bash
  docker run --rm --network <same network> curlimages/curl -sI -H 'Host: cmmn.dev' http://commonplace-site/gh
  # → HTTP/1.1 302, location: https://github.com/140er/mempalace
  ```

**Updating** is a pull. Pages change at once; a changed `nginx.conf` needs a reload.
For cron, use an Unraid User Scripts entry, every 15 minutes:

```bash
#!/bin/bash
cd /mnt/cache/appdata/commonplace-site/repo || exit 1
before=$(git rev-parse HEAD:landing/nginx.conf)
git pull --ff-only -q
[ "$(git rev-parse HEAD:landing/nginx.conf)" != "$before" ] && docker exec commonplace-site nginx -s reload
```

## 2. Nginx Proxy Manager

Add two proxy hosts, both forwarding to `http://commonplace-site:80`:

1. `commonplace.run`, `www.commonplace.run`
2. `cmmn.dev`, `www.cmmn.dev`

For each: SSL tab, request a Let's Encrypt certificate, then turn on Force SSL and HTTP/2.
Turn on HSTS once it has run cleanly for a week. `.dev` is on the browsers' HSTS preload
list, so `cmmn.dev` works over HTTPS only and does nothing until its certificate exists.

Use the DNS challenge with a Cloudflare API token (Zone, DNS, Edit, on both zones). It works
whether or not port 80 reaches zurvur, and whether or not the records are proxied.

## 3. Cloudflare: why commonplace.run fails today

Checked on 2026-10-30 from t3code-home: both zones use Cloudflare's nameservers
(`ed`, `sonia`), but neither has any A, AAAA or CNAME record, for the apex or for `www`.
`dig +short commonplace.run` and `dig +short @1.1.1.1 cmmn.dev` return nothing, so from
here the names don't resolve at all. A Cloudflare 500 can't come from a record that doesn't
exist. If a browser shows one, something else in the zone is answering: a Worker route, a
Pages custom domain, or a Redirect or Origin rule left over from registration. Remove it,
then add the records. Fix it with
one of these.

**A. Proxied records to zurvur (the PRODUCT-DESIGN §14 path).** Use this when the router
forwards 80/443 to NPM.

- DNS, both zones: `A @ <zurvur's public IP>` and `CNAME www @`, both **Proxied**. That
  keeps the home IP out of public DNS.
- SSL/TLS mode: **Full (strict)**. NPM holds a real certificate, so strict works. "Flexible"
  combined with NPM's Force SSL loops forever.
- A residential IP changes, so run a DDNS updater on zurvur (for example the
  `favonia/cloudflare-ddns` container) for `commonplace.run`, `www.commonplace.run`,
  `cmmn.dev` and `www.cmmn.dev`.

**B. Cloudflare Tunnel.** Use this if there is no port forwarding (CGNAT, or a router you
can't change). Run a `cloudflared` container on zurvur, and add public hostnames for all
four names pointing to `http://commonplace-site:80`. Cloudflare creates the DNS records and
handles TLS, so NPM isn't needed for the site.

**With either, turn these off in both zones,** or Cloudflare adds third-party requests
the site never made, and the done test fails:

- Web Analytics / Real User Measurements (it injects a `cloudflareinsights.com` beacon)
- Rocket Loader
- Email Address Obfuscation
- Zaraz
- Speed > Optimization extras that rewrite HTML

**Verify:**

```bash
dig +short commonplace.run                     # Cloudflare IPs (proxied) or the tunnel's
curl -sI https://commonplace.run/ | head -1    # HTTP/2 200
curl -sI https://www.commonplace.run/ | grep -i '^location'   # https://commonplace.run/
curl -sI https://cmmn.dev/gh | grep -i '^location'            # https://github.com/140er/mempalace
node landing/check.mjs --origin https://commonplace.run       # zero third-party requests, live
```

## Short links (cmmn.dev)

Defined in the `map` at the top of `landing/nginx.conf`. `check.mjs` fails if a target page
or anchor on commonplace.run doesn't exist.

| Short link | Goes to |
|---|---|
| `cmmn.dev` | `https://commonplace.run/` |
| `cmmn.dev/gh` | `https://github.com/140er/mempalace` |
| `cmmn.dev/mempalace` | `https://github.com/MemPalace/mempalace` |
| `cmmn.dev/install` | `https://commonplace.run/#install` |
| `cmmn.dev/connect` | `https://commonplace.run/connect/` |
| `cmmn.dev/claude`, `/codex`, `/cursor`, `/antigravity`, `/cloud`, `/local` | that client on the connect page |

To add one, add a line to the map, run `NGINX_BIN=… node landing/check.mjs`, and merge. The cron
pull picks it up and reloads nginx.

## Before pointing DNS at it

The page tells visitors to run `docker run … ghcr.io/140er/commonplace`. Publish that image
(milestone M0) first, or the first command a visitor tries fails. The GitHub links go to
`140er/mempalace` until the Commonplace repo and org exist (PRODUCT-DESIGN §16). Change them
in `public/index.html`, `public/connect/index.html` and the `gh` short link.
