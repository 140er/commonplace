# Commonplace on Even Realities G2

See what needs you on G2 glasses, read the card, and answer it with a press of the
temple. It runs as an Even Hub plugin: a small web app inside the Even Realities phone
app, which draws on the glasses over Bluetooth.

```
G2 glasses ──BLE── Even app (phone) ── plugin WebView ──HTTP── G2 bridge ──HTTPS + token── Commonplace hub
                                         (this repo: plugin/)      (bridge/)
```

- **plugin/**: Vite + TypeScript + `@evenrealities/even_hub_sdk`. Draws the glasses
  pages, maps gestures, shows connection state and settings on the phone. It holds no
  Commonplace token.
- **bridge/**: a dependency-free Node server (Node 20+). It keeps the Commonplace token
  server-side and forwards only an allowlist of routes. It also serves the built plugin,
  so the phone needs one address.

No hub change and no deploy is needed. The hub sends no CORS headers, and the bridge is
what makes the WebView's calls possible.

## What the glasses show

| Page | Content |
|---|---|
| Home | `Commonplace: N need you`, then the latest notice (`17:33 New ask #8: ...`) or the update time. Below that is a list of what needs you (`ASK #6 ...`, `REVIEW`, `MERGE`, `PLAN`, `NEW`, `STALE`), a today line, and an agents line. |
| Card | Number, board, status, title, who asks, the question and context, and the numbered options. It is paged at 9 lines. |
| Choose | The card's offered options, `Mark as seen`, and `Cancel`. |
| Confirm | `Answer #6 with: <option>`. It needs a second, separate press. |
| Result | `Answered #6: ...`, or why it was not sent (changed meanwhile, not allowed, offline). |

| Gesture (temple or R1 ring) | Effect |
|---|---|
| Swipe up / down | Move through the list, or page through a card |
| Press | Open the item. On a card it opens the answers. On Confirm it sends. |
| Double press | Back. On Confirm it cancels. On Home it exits through the system prompt. |
| Long press | Refresh, from anywhere |

The confirm step ignores a press within 0.7 s of appearing, so a bounced tap never
answers a card. Mark as seen adds the comment `Seen on glasses.`, because the hub has no
separate acknowledge route.

**Live updates.** While the plugin is open it polls every 20 s. With a connection that
may read the event log (`events:read` on a connection that reads every wing), it shows
new asks, decisions and comments, plus today's activity counts. A connection made in the
browser cannot read the event log. In that case the plugin compares Needs-me between
polls: new items still show up within about 20 s, and the today line shows open-card
counts instead.

## Security model

- The Commonplace token lives only in the bridge's token file. It goes upstream in
  `Authorization` and nowhere else. It is never logged or returned, and any copy of it in
  an upstream body is replaced by `[redacted]`.
- The bridge forwards these routes and refuses everything else before any request
  leaves: `GET needs-me`, `GET needs-me/digest`, `GET events`, `GET me`, `GET boards`,
  `GET boards/<b>/cards`, `GET cards/<n>`, `POST cards/<n>/decide`, and
  `POST cards/<n>/comments`. It forwards only the declared query parameters, checks body
  fields one by one, and caps bodies at 16 KiB.
- Beyond loopback the bridge requires a pairing key (`X-Bridge-Key`). The plugin gets it
  once from the pairing link (`?key=...`), stores it in the Even app's storage, and
  removes it from the address. The key only unlocks this bridge. It is not a Commonplace
  token.
- CORS headers go only to the origins in `BRIDGE_ALLOWED_ORIGINS`. They are not needed
  when the bridge serves the plugin itself.
- Voice is not included. `/api/voice/transcribe` accepts signed-in people only, not
  tokens.

## First run on real glasses

Steps 1, 2 and 5 are on Jeffrey's phone or browser. An agent does 3 and 4 on the bridge
host (for example a home server on Tailscale).

**1. Make the glasses connection (Jeffrey, browser, 2 minutes).**
In Commonplace, open Settings → Agents. Name it `g2-glasses`, set the client to `Other`,
and tick the consent that lets it answer Needs-me questions. Then press **Create token**.
The token is shown once. Hand it to the agent through a secret prompt that writes it to a
file on the bridge host (for example `~/.config/cmmn-g2/token`, mode 600), never through
chat. A connection made this way gets the Needs-me live-update mode described above.

**2. Turn on Developer Mode (Jeffrey, phone, once).**
Sign in at <https://hub.evenrealities.com> with the same account as the Even app, then
turn on Developer Mode and restart the Even app (Even app 2.2.10 or newer). The phone
must reach the bridge host, either on the same Wi-Fi or with Tailscale on the phone.

**3. Build and start the bridge (agent, bridge host).**

```bash
cd integrations/even-g2/plugin && npm ci && npm run build && cd ..
mkdir -p -m 700 ~/.config/cmmn-g2
node bridge/bin/cmmn-g2-bridge.mjs --make-key ~/.config/cmmn-g2/bridge-key
CMMN_HUB_URL=https://<your-hub> \
CMMN_TOKEN_FILE=~/.config/cmmn-g2/token \
BRIDGE_KEY_FILE=~/.config/cmmn-g2/bridge-key \
BRIDGE_HOST=<tailscale-or-lan-ip> BRIDGE_PORT=8790 \
BRIDGE_STATIC_DIR=plugin/dist \
node bridge/bin/cmmn-g2-bridge.mjs
```

Check it from another machine: `curl http://<ip>:8790/bridge/health` returns `{"ok": true}`.
Each request is logged as one line (method, route, status, time), with no headers,
bodies or token.

**4. Make the pairing QR code (agent).**

```bash
URL=$(BRIDGE_KEY_FILE=~/.config/cmmn-g2/bridge-key node bridge/bin/cmmn-g2-bridge.mjs --pair-url http://<ip>:8790/)
(cd plugin && npx evenhub qr --url "$URL")            # QR code in the terminal
npx -y qrcode -o /tmp/g2-pair.png "$URL"              # or as an image to send privately
```

The QR code carries the pairing key. Send it only privately and do not paste the URL in
chat.

**5. Scan and use it (Jeffrey, phone and glasses).**
In the Even app, open Developer Mode, choose **Scan QR**, and scan the code. The plugin
opens. The phone shows `Connection: Connected`, the hub address, the Needs-me count, and a
mirror of the glasses. The glasses show Home. Swipe to an `ASK`, press to read it, press
to pick an answer, then press once more to confirm. The card leaves Needs-me on the hub.
A new ask from an agent appears on the second line within about 20 s.

If something is off, check these first:

| Problem | Fix |
|---|---|
| `Offline: bridge key missing or wrong` | Scan the QR code again, or paste the key under Bridge on the phone page. |
| `Offline: bridge unreachable` | Make sure the phone reaches `<ip>:8790` (Wi-Fi or Tailscale). |
| `Answering is not allowed...` | In Commonplace → Agents, turn on answering for `g2-glasses`. |

## Develop without glasses

```bash
cd integrations/even-g2
scripts/local-hub.sh /tmp/g2-hub 8796       # throwaway hub with seeded asks (never a real hub)
CMMN_HUB_URL=http://127.0.0.1:8796 CMMN_TOKEN_FILE=/tmp/g2-hub/g2.token node bridge/bin/cmmn-g2-bridge.mjs &
(cd plugin && npm ci && npx vite &)          # dev server on :5173; /api and /bridge proxy to the bridge
scripts/sim.sh start http://127.0.0.1:5173  # Even Hub simulator, headless, automation API on :9898
scripts/sim.sh press down; scripts/sim.sh press click; scripts/sim.sh shot /tmp/card.png
python3 scripts/seed_local_hub.py http://127.0.0.1:8796 /tmp/g2-hub/agent.token late   # a new ask arrives
```

`local-hub.sh` runs from a Commonplace checkout with its Python environment (set
`PYTHON=` to choose one). Its glasses token may read the event log and has the decide
consent. To try the browser-connection mode, make a connection in the local app instead.
The simulator needs WebKitGTK 4.1 (`libwebkit2gtk-4.1-0`) and `xvfb-run` on Linux.

Tests:

```bash
(cd bridge && npm test)                     # allowlist, token never leaked, pairing key, CORS
(cd plugin && npm test && npx tsc --noEmit) # layout to 576x288, gestures, decide flow, live updates
```

## Not in this version

Voice replies are not included, because transcription accepts signed-in people only.
Images and logos, the dashboard widget, and the `.ehpk` package (`npm run pack`; set the
network whitelist in `app.json` to the bridge origin first) are also left out. The plugin
is not in the Even Hub store.
