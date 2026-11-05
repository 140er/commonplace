# Direction B + chat: Workbench with the orchestrator chat (round 1)

**Owner decision (5 Nov 2026):** B Workbench is the base. One element comes from C (Conversation rail): the project chat with the orchestrator has to be visible inside B, and it may sit behind a button.

**Premise.** B as it passed review: projects and the Needs me count on the left, the board in the middle, and a context panel on the right (**Details**, **Memory**). ⌘K and single-key shortcuts appear next to the buttons they trigger. New in this round is one labelled **Chat** toggle in the board header. It opens the project chat in the right-hand slot, next to the board, so you can watch your request turn into cards.

**Serves best:** people who run several projects with several agents and check in many times a day. **Gives up:** the calm first look of a one-column design, and (new) seeing card details and chat at the same moment (see *What I traded away*).

Open `index.html` for the contact sheet: 49 screens and 54 PNGs, including 6 renders at other widths. All of them were re-rendered in this round, and I opened the chat, tablet and mobile ones. Screens are in `screens/`, screenshots in `shots/`. `_src/build.py` generates the HTML, `index.html` and the shot list. `_src/shoot.sh` takes the screenshots (`heavy --shot -- bash _src/shoot.sh`). `screens/wb.js` (local, no network) makes the keyboard behaviour work in a browser, including the Chat toggle. `_src/keytest.cjs` checks it live in local headless Chrome. Its last run, in `_src/keytest-results.txt`, is **ALL PASS**: 15 dialogs (initial focus, Tab trap, Esc, focus return) plus 10 chat checks.

## Where the chat toggle lives, and why

- **The control:** one button, **Chat** (`id="chat-toggle"`), in the board header right after the project name. It replaces B's "Orchestrator connected" pill, in the same place, so the board header keeps its width at 1440 and 1024 px. It is a native `<button>` with `aria-controls="chat"` and `aria-expanded`, a text label, and the shortcut shown on it (`/`). Open, it is drawn filled (white on near-black, 17.7:1), and the chat pane is visible next to it.
- **Status shows only by exception (attention is scarce).** It shows nothing extra when all is well. It shows **1 new** (screen readers hear "1 new reply") when the orchestrator answered while the chat was closed (`04`), and **no orchestrator yet** before an agent connects (`03`). Both are words, never only a dot or a colour. The full status sits in the pane header: "Orchestrator connected" or "No orchestrator yet".
- **Where the chat opens:** the right-hand slot that B's context panel uses, as a right rail (C's idea). It is not a third column. With chat open, the board keeps its 4 readable columns at 1440 px (as wide as with Details open). Closing the chat brings back whatever the context panel showed (`04` ↔ `06a`).
- **Why not a tab in the context panel (as in B before)?** The context panel shows *what you selected* (a card, a memory). Chat is project-wide and runs on its own time, so it needs its own control that says "1 new". B's old Chat tab sat among the Details tabs and was easy to miss, which is why the owner asked for it. Chat is therefore **no longer a tab**: the context panel has Details and Memory only, so there is one control for chat, not two.
- **Why not a permanent third column (C's layout)?** At 1440 px, rail + board + details + chat leaves about 450 px for 4 columns. The board is the default view and must stay readable, so chat sits behind the toggle, as the owner allowed.
- **Open and close:** the toggle (Enter or Space), `/` from anywhere outside a field (opens and puts the cursor in the message box), **Close chat** in the pane header, `Esc` while focus is in the chat (the draft is kept), or `]` outside a field. Focus goes into the message box on open and back to the control that opened it on close. All of this is checked in `keytest-results.txt`.
- **On cross-project screens** (Needs me, Memory) there is no board header. There, chat opens from the item's own button (**Ask a question R**, **Open in chat, ask about it ↵**) with the same pane. A strip at its top says what you are asking about, with **Back to the approval** or **Back to the source** (`07d`, `09f`).

## Words used in the product

- **Agent**: an AI helper that works on cards, such as a coding assistant on your computer, connected through MCP.
- **Orchestrator**: the project's lead agent. It reads what you ask in chat and turns it into cards. There is exactly one per project, and it is chosen automatically: the agent that has been connected longest (so the first one you connect). Nobody picks it by hand.
- **Chat** (*Atlas chat*): the project's conversation. You, teammates and agents can write there, and the orchestrator answers.
- **Needs me**: things only a person can do: approvals, decisions and reviews addressed to you.
- On cards, "Working on it: Otto" replaces "claimed by", "Available for an agent" means nobody is on it yet, and **From chat · 14:03** means the orchestrator made the card from the chat message sent at that time.

## Information architecture

```
Workspace (Northwind)
├─ Left rail: Search or do… ⌘K · Needs me [n] · Memory · Projects (+) · Shortcuts ? · Maya
├─ Centre
│  ├─ Board (default after sign-in): Todo · Doing · Review · Done
│  │   header: Atlas · [Chat ⟨1 new⟩ /] · filters · Settings · Add card (C)
│  ├─ Needs me queue · Memory search · Project settings
└─ Right-hand slot (one at a time)
   ├─ Context panel: Details (selected card / Needs me item) · Memory (verbatim source)
   └─ Chat pane (Chat toggle, /, Ask a question, Open in chat): Atlas chat with the orchestrator
Tablet 700–1099 px: context panel = modal sheet via Details (as B); chat = push pane, board scrolls sideways
Mobile: project switcher · column tabs · bottom tabs Board / Chat ⟨new⟩ / Needs me / Memory;
        Chat opens a full-screen sheet with "Back to board"
```

Rules the IA enforces (unchanged from B): the board is the default view and the place every flow returns to. There is at most one orchestrator per project, assigned automatically. Nothing files cards behind the user's back: with no agent, a chat message waits ("Waiting for an agent") and can become a card with one button. Needs me only holds what a person must do. Memory always shows exact words with author, source and time. **New:** chat never approves or declines anything. Approve and Decline stay as buttons in Needs me only (`07d` says so in its strip).

## Core flows

**1. Start: sign up, create the first project, connect an agent**
1. `01-signup` → `01b-check-email` (or `01c-link-expired`) → `02-create-project`, as B.
2. `03-board-empty`: a 3-step strip (Add card · Connect an agent · Ask for something). On a new project the chat starts **open** (there is no card to show in Details yet), and its callout teaches: "This is Atlas chat. You can already write here…", what the orchestrator is, and that none exists until you connect an agent, with **Connect an agent** (the connect path). The toggle reads **Chat · no orchestrator yet**.
3. `03b-chat-no-agent`: a request sent with no agent shows **Waiting for an agent**, nobody has read it, with **Connect an agent** and **Make it a card now**. A failed send shows **Not sent. This device is offline.** with Try again.
4. `03c-connect-agent` → `03c2-connect-waiting` → `03d-connect-done` or `03e-connect-failed`, as B. The success dialog now says where to talk to the orchestrator: "You talk to it in **Chat**, next to the project name above the board".

**2. Board (default view)**
- `04-board`: Iris, Otto and Juno are working on cards. A-121 is **Blocked**, and A-115 is tagged **Needs me**. The Details panel shows the blocker in Otto's exact words. **Closed chat:** the toggle reads **Chat 1 new**, because the orchestrator answered Maya's 12:30 question at 12:41.

**3. Add a card**: `04` → `05-add-card` → `05b-card-added`, as B (mobile `m4-add-card`).

**4. Talk to the orchestrator (the new path), one drawn path**
1. `04-board`: **Chat 1 new** (or press `/`).
2. `06a-chat-open`: the chat replaces the context panel, and the toggle is drawn pressed. A line marks **New since you last looked** above the 12:41 answer. That answer names A-121 in a **Cards in this answer** row (*select to show on the board*). Maya's request is typed in the focused message box.
3. `06b-chat-thinking`: sent. **Working on your request…** (`role="status"`): it read the message at 14:02, answers usually take under a minute, and new cards appear on the board when it answers. The board stays usable.
4. `06c-chat-answer`: the answer lists **From this request · on the board now**: 2 new cards and 1 linked card. On the board, A-134 and A-135 appear in Todo, tinted and tagged **From chat · 14:03**, the same time as the answer. **The visible link:** focusing or hovering a row in the chat outlines its card on the board with a dashed line (drawn: the A-134 row has focus, and A-134 is outlined). Selecting a row selects the card. A-135 shows the neutral "Starts after A-118 is done".
- States: `06d-chat-error` (**No answer yet.** It read the message but hasn't answered in 5 minutes, its agent was last active 14:03, the message is kept and nothing changed on the board, with **Ask again**, **Make it a card now** and **Check the agent in Settings**). `06e-chat-empty` (connected, nothing asked yet: what to ask, where cards will appear, that external actions still go to Needs me, and three starter chips). Not connected: `03`, `03b`. Offline send: `03b`.

**5. Handle a Needs me item, from notice to done**
1. Notice: the rail count (Needs me 3), the amber card tag on the board, and the mobile tab badge.
2. `07-needs-me`: the approval, with the orchestrator's request **in its exact words**, the 3 linked cards and the send fact. Buttons: **Approve and send…**, **Decline**, **Ask a question (R)**.
3. **New, `07d-needs-me-ask`:** Ask a question opens Atlas chat in the right-hand slot. The strip says "You are asking about *Send beta 0.9 to 40 testers?* Approve or decline it in Needs me", with **Back to the approval**. Maya's question carries an **Approval** label. The answer gives the facts and says that nothing has been sent. The approval stays selected in the list.
4. `07c-approve-confirm` → `08-needs-me-done` (the 60-second hold, Stop, the next decision), as B. `07b-needs-me-empty`.
5. Mobile: `m2` → `m3` → `m5` → `m6` → `m7` / `m8`, as B. The desktop "stopped" result is not drawn; it shows the same content as `m7`.

**6. Find a memory**
1. `09-memory`: search, results with source, author and date, and the full message between its neighbours in the Memory panel.
2. **New, `09f-memory-ask`:** **Open in chat, ask about it ↵** opens Atlas chat at Leon's 28 Oct message, marked **Found in memory**, with the messages around it and **Jump to today's messages**. The message box carries the quote ("Quoting Leon, Wed 28 Oct 2026, 16:12, exact words", removable). **Back to the source** returns to the result.
3. States: `09e`, `09b`, `09c`, `09d`, as B.

**Settings**: `10`, `10b`, `10e`, `10c`, `10d`, as B. The per-agent Disconnect… confirm is *not drawn* (it uses the `10c` pattern).

**Keyboard and ⌘K**: `11-command-palette` (behind it is the board after `06c`, so A-134 and A-135 match what is drawn), `11b-command-palette-card`, `12-shortcuts`. Shortcuts: ⌘K (Ctrl K on Windows and Linux) palette · C new card · **/ open the chat and put the cursor in it** · **] close the chat, or hide the side panel** · **Esc in the chat closes it** · G B / G N / G M go to · J/K move · Enter open · E edit · M move · R ask a question · Y copy exact words · L link to card · Z undo / stop · ? shortcuts. Approve and Decline: none, by design. Live in the demo (`wb.js`): ⌘/Ctrl K, C, /, R, ], Esc, the arrow keys, Tab, Enter, and the Chat toggle, Close chat, Ask a question, Open in chat and Back to…. G, E, M, Y, L, Z and J/K are design-only.

## Tablet and mobile

- **Tablet, 700–1099 px.** Closed (`r2-04-board-1024`): as in B, the board is full width and the header shows **Chat 1 new** next to **Details**. Open (`r-06c-chat-1024`): the chat **pushes** the board (220 | board | 340 px). The board does not get dimmed or covered. The board scrolls sideways with 190 px columns, so Todo, where chat cards land, and Doing stay in view, and the edge of Review shows there is more. Nothing is covered, so focus can't land under the chat (2.4.11). The Details sheet stays a modal over everything, as in B (`r2-04-board-1024-panel`).
- **Why not a modal sheet at tablet, as Details uses?** The chat's job is to show cards appearing next to it. A dimmed, inert board would hide exactly that. Details is a short look at one card, so it can stay a modal.
- **Mobile, 390×844.** `m1-board`: the **Chat** bottom tab carries a **new** label (screen readers hear "1 new reply"). `m10-chat`: a full-screen sheet (`role="dialog"`, `aria-modal`), with focus on **Back to board** (top left, 44 px). It holds the same answer, and each card row ends in **Todo · Show →** (48 px rows). The message box is 16 px text and 44 px targets. Esc or Back returns focus to the Chat tab (keytest). `m11-board-from-chat`: **Show** opens the board on the Todo tab, with A-134 selected and outlined, tagged **From chat · 14:03**. A bar says "Showing A-134, made in chat at 14:03" and has **Back to chat**.
- Below 700 px, the desktop markup (`r1-04-board-320`) stacks the chat pane under the board, like B's context panel.

## Screens

| File | Size | Shows |
|---|---|---|
| 01-signup · 01b-check-email · 01c-link-expired · 02-create-project | 1440×900 | Sign up, link, first project (B) |
| 03-board-empty · 03b-chat-no-agent | 1440×900 | Empty board with chat open, not connected; waiting and failed messages |
| 03c-connect-agent · 03c2-connect-waiting · 03d-connect-done · 03e-connect-failed | 1440×900 | Connect an agent (B; 03d names Chat) |
| 04-board | 1440×900 | Board + details, chat closed with **1 new** |
| 05-add-card · 05b-card-added | 1440×900 | Add a card (B) |
| 06a-chat-open · 06b-chat-thinking · 06c-chat-answer · 06d-chat-error · 06e-chat-empty | 1440×900 | Chat: open, working, answer → cards, no answer, empty |
| 07-needs-me · 07d-needs-me-ask · 07c-approve-confirm · 08-needs-me-done · 07b-needs-me-empty | 1440×900 | Needs me, **ask in chat**, confirm, held send, empty |
| 09-memory · 09f-memory-ask · 09e · 09b · 09c · 09d | 1440×900 | Memory, **ask in chat at the source**, states |
| 10-settings · 10b · 10e (1440×1180) · 10c · 10d (1440×900) | | Settings (B) |
| 11-command-palette · 11b-command-palette-card · 12-shortcuts | 1440×900 | Palette, card actions, shortcuts |
| m1-board · m10-chat · m11-board-from-chat · m9 · m4 · m2 · m3 · m5 · m6 · m7 · m8 | 390×844 @2x | Mobile; **chat sheet** and **back to the card** |
| r2-04-board-1024 · r-06c-chat-1024 · r2-04-board-1024-panel · r1-04-board-320 · r2-m1-board-320 · r1-m3-needs-me-item-360 | as named | Same markup at other widths |

## Accessibility notes (WCAG 2.2 AA)

Everything in B's notes still holds (same tokens, focus ring, dialogs, roving board, targets, motion, status messages). For this round:
- **Toggle state** (4.1.2): `aria-expanded` on every control that opens the chat (toggle, Ask a question, Open in chat), always in sync (`wb.js`, keytest). The open state is drawn filled, and the chat pane appears with it, so the state isn't shown by colour alone.
- **Chat pane:** `<aside aria-labelledby="chat-h">`, a complementary region, not modal on desktop or tablet. The message list is `role="log"`, and the message box has a label ("Message to Atlas chat"). The working state is `role="status"`; no answer, a failed send and a link problem are `role="alert"`. The log opens at the newest message, or at the found message (`09f`).
- **Not colour alone:** "1 new", "no orchestrator yet", **New since you last looked** (a line plus words), and **From chat · 14:03** on the card plus the same time on the message. The linked card gets a **dashed** 2 px outline (a shape distinct from the solid "selected" border), accent at 6.7:1 on the column well and 7.4:1 on the new-card tint.
- **Measured contrast of the new parts:** "1 new" chip 6.8:1 · "no orchestrator yet" chip 8.6:1 · toggle open 17.7:1 · chat subtitle 8.6:1 · New-since line 7.8:1 · context strip 17.0:1 · mobile "Showing…" bar 15.4:1 · toggle border 3.9:1 on the header.
- **Targets:** toggle 32 px high, Close chat 28 px, chat card rows 36 px (48 px on mobile), Back to board 44 px, mobile send 44×44.
- **Keyboard and focus:** described under *Where the chat toggle lives*. Single keys never fire while you type. `/` is the only key that opens chat, and Esc inside the chat closes it (the draft stays). On mobile the sheet traps Tab and returns focus to the Chat tab.
- **Fixes from B's last reviews, taken along:** column counts match the drawn cards ("Show 2 more" in `06c` and `11`, "Show 1 more" in `05`/`05b`). Toast buttons sit beside the `role="status"` text (`05b`, `08`). The single-key switch reads its saved state on load. The palette says Approve and Decline are not in it. Settings name the orchestrator row "(Maya's laptop)". The developer words "localhost" and "index" are gone (`10b`, `09d`).

## What I traded away

- **Details and chat at the same time.** They share the right-hand slot. With the chat open, you see a card's details by closing the chat, by selecting it on mobile, or through the dashed outline and the **From chat** tag. A permanent 3rd column would cost the board its readable columns at 1440.
- **Always-on chat (C's rail).** The chat stays behind one toggle, as the owner allowed. In exchange, it says **1 new** when there is something to read.
- **The "Orchestrator connected" pill** is gone from the header. Its place is now the toggle, which shows status only when something is wrong or new. The full status is in the chat header and in Settings.
- **One drawn path per flow:** the toggle's closed "answering…" and "no answer" chips are named here but not drawn (same chip as **1 new**).

## No ev0lve-theme visuals: hex check

`python3 -I _src/hexcheck.py . ~/work/ev0lve-themes` (run in this folder) collects every `#hex` (3, 4, 6 and 8 digits, lower-cased and expanded) from B-chat's CSS, HTML, JS and build script, and from every text file in `~/work/ev0lve-themes`. Result on 5 Nov 2026: B-chat has 37 colours and ev0lve-themes has 41. **The only shared value is `#ffffff` (plain white)**, the same result as B's earlier rounds. There are no shared accents, backgrounds or textures, and no images or wallpapers from it. The palette is B's own: accent #2E43C0, neutrals #F3F3EF / #FAFAF8 / #EDEDE7, and status colours.

## Open risks

- **Two things in one slot.** Some users may expect chat and details side by side. On screens wider than about 1700 px a third column could be offered later; it is not designed.
- **Tablet sideways board.** With the chat open, Review and Done need a sideways scroll. That is fine for watching chat cards land in Todo, less so for review work, so close the chat there.
- **The "1 new" chip** needs a rule for many unread messages ("3 new") and for messages from teammates rather than the orchestrator. Not designed.
- B's risks still stand: density on day one, single-key shortcuts with voice control, the 60-second send hold, dark theme not designed, system font.
