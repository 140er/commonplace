# B + chat: responses to round 4

Both reviews returned CHANGES with no BLOCKING. All four MAJORs are fixed. All Haiku and Terra MINORs are fixed, except Haiku 5 and Haiku 7, which are written down instead of drawn (reasons below). All 78 PNGs were re-rendered. I opened the changed ones: `r-06c-chat-390`, `06d`, `r-06c-chat-1024`, `06f3`, `07`, `03`.

**Self-check pass, as asked.** Two new checkers run over every screen:
- `_src/layoutcheck.cjs` renders all 67 screens at 1440, 1024, 768 and 390 px (196 renders). Its first run found **85 issues** the reviews had not named. After the fixes it reports **0** (`_src/layoutcheck-results.txt`). What it found:
  - tags clipped on tablet cards;
  - "Write in chat /" clipped in the first-steps strip at 768 and 1024 px;
  - the Needs me filters wrapping under the title even at 1440 px;
  - Add card dropping to a second row at 1024 px.
- `_src/factcheck.py` reads every screen for contradicting state facts: connected vs not answering, last-active times, writer labels at 12:41 and 14:03, strip vs row source, counts on open controls or with no orchestrator, and project names in composer vs heading. Result: **no contradictions**.
- I also re-read the README against the screens. It still said the `m3`/`m8` ask paths were "not drawn" (drawn since round 3) and that `m7` says "Approve or decline…". Both are corrected.

The live keyboard test is **ALL PASS, 75 checks** (`_src/keytest-results.txt`).

## MAJOR

| # | Finding | Status | Where |
|---|---|---|---|
| Haiku M1 | The "not answering" state contradicts itself | **Fixed** | One set of facts. Chat header on `06d` and `m12b`: "Connected, not answering since 14:02". Body on both: "It read your message at 14:02 and has not answered. Its agent on “Maya's laptop” was last active at 14:03. Your message is kept, and nothing changed on the board." The guess "may be closed or asleep" is gone. `10f` agents table: "Last active 14:03", matching its amber card and `m13b`. `factcheck.py` guards it. |
| Haiku M2 | `r-06c-chat-390` overlaps its own text | **Fixed** | The phone-width rule that turned off the log's scrolling is now scoped away from the chat (`.shell:not(.chat-open):not(.ctx-pushed) .context .ctx-body`), and `.chatpane .ctx-body` keeps `overflow: auto` below 700 px. Keyboard hints are hidden below 700 px. Re-rendered and checked: the last message ends above the composer. Keytest at 390 px measures it (`lastBottom ≤ composerTop`, `overflow-y: auto`, hint hidden), and so does `layoutcheck`. |
| Haiku M3 | The quick-reply row opened with the orchestrator's 14:03 attribution | **Fixed** | Every chat row carries its own message source in `data-src` ("the 12:41 quick reply (local model)" or "the orchestrator's 14:03 answer"). The strip is built from it: "Opened from Atlas chat: …". The fixed string is gone from `wb.js`. Drawn: `06f3-chat-card-details-a121` (A-121 opened from the quick reply). Keytest: the `06a` A-121 row (names the 12:41 quick reply, never 14:03), the `06c` A-134 row (names the 14:03 answer) and `06f3`. |
| Terra M1 | Esc on `m13b` returned to the board | **Fixed** | Settings sheets opened from another page mark their Back link `data-esc="follow"`, and Esc does exactly what Back does. Keytest checks Esc separately for each origin: error (`m13b` → `m12b`, focus on `#restart-agent`), board (`m13` → the board, focus on Settings), Needs me (`m13c` → `m2`, focus on Settings) and Harbor (`m13c?from=harbor` → `m2b`). |

## Haiku MINOR

| # | Status | Where |
|---|---|---|
| 1 ⌘K hint overflows; header wraps below 1280 px | Fixed | Below 1280 px the key hints hide (they stay in `12` and on `?`), and Settings is an icon with a hidden label. With the chat or a chat card open, the filter group takes its own row, so the title, Chat and Add card stay on row one. `layoutcheck` asserts it at 1024 and 1440 px. The tablet Details button is hidden while the chat or a chat card fills the slot. |
| 2 Hide and `]` dead on chat-opened Details | Fixed | Both go back to the chat, with focus on the same row (keytest on `06f`). |
| 3 Phone status text not state-specific | Fixed | Chat sheets (`m10`, `m12`, `m12b`, `m12c`) no longer carry the "1 new reply" status behind them, and `factcheck` forbids a count where there is no orchestrator. |
| 4 "Back to Needs me" from Settings was script only | Fixed | Drawn: `m13c-settings-from-needs`, with Back to Needs me as static markup; `m2` links to it. From `m2b` it returns to the Harbor state (Terra MINOR 1). |
| 5 Recovery actions have no drawn result | Written down | README flow 4 states what Check again, Ask again and Make it a card now lead to. Each result is a state already drawn elsewhere (`06b` working, `05b` card added, the connected card in `10`). Not drawn, to keep one drawn path per flow. |
| 6 `m7` action reads as an action | Fixed | Renamed **Open the approval** (it goes to `m3`, where both decisions are). |
| 7 Phone Memory tab removed; README contradicted | Written down | The README no longer says there is no phone memory search. It says the phone palette offers "Search memory for…" and its results are not drawn, and it lists the removed tab under *What I traded away*. |
| 8 Phone chat tab depends on hidden state | Fixed | `m2` and `m2b` say under the title: "The chat tab opens the chat of the item you last opened, else your last project." |
| 9 `06e` had three sentences | Fixed | Cut to two; `06a` already explains quick replies. |

## Terra MINOR

| # | Status | Where |
|---|---|---|
| 1 Harbor's Settings return lost the Harbor state | Fixed | `m2b` opens `m13c-settings-from-needs.html?from=harbor`. Back and Esc return to `m2b-needs-me-harbor.html` with focus on Settings (keytest). |

## Hygiene

The mirrored review copies on the branch have home paths, scratch paths and local tool names replaced with generic wording. The originals in the card folder are unchanged.
