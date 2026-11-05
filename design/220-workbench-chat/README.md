# Direction B + chat: Workbench with the orchestrator chat (round 2)

**Owner decision (5 Nov 2026):** B Workbench is the base. One element comes from C (Conversation rail): the project chat with the orchestrator has to be visible inside B, and it may sit behind a button.

**Premise.** B as it passed review: projects and the Needs me count on the left, the board in the middle, and a context panel on the right (**Details**, **Memory**). ⌘K and single-key shortcuts appear next to the buttons they trigger. New is one labelled **Chat** control in the header of the board, Needs me and Memory. It opens the project chat in the right-hand slot, next to your work, so you can watch your request turn into cards and open any of those cards from the chat.

**Serves best:** people who run several projects with several agents and check in many times a day. **Gives up:** the calm first look of a one-column design, and seeing card details and chat side by side (one click apart instead; see *What I traded away*).

Open `index.html` for the contact sheet: 53 screens and 58 PNGs, including 6 renders at other widths. Round-2 answers are in `RESPONSES-r1.md`. `_src/build.py` generates the HTML, `index.html` and the shot list. `_src/shoot.sh` takes the screenshots with headless Chrome (`bash _src/shoot.sh`). `screens/wb.js` (local, no network) makes the keyboard behaviour, the Chat control and the chat-to-card paths work in a browser. `_src/keytest.cjs` checks them live in headless Chrome. Its last run, in `_src/keytest-results.txt`, is **ALL PASS** with 41 checks: 15 dialogs (initial focus, Tab trap, Esc, focus return), the chat open and close paths, chat row → details → Back to chat (also at 1024 px), the outline clearing on focusout and mouseout, the Needs me and Memory controls, R, and the mobile Show and Back paths.

## Where the chat control lives, and why

- **The control:** one button, `id="chat-toggle"`, with `aria-controls="chat"`, `aria-expanded`, a text label and its shortcut (`/`). On the board it reads **Chat**, right after the project name, in the place of B's "Orchestrator connected" label. On Needs me and Memory, which span projects, it names its project: **Atlas chat**. That is the project of the selected item, or the project you last had open. Open, it is drawn filled (17.6:1), and the chat pane appears with it.
- **Status shows only by exception (attention is scarce).** It shows nothing extra when all is well. It shows **1 new reply** (or "3 new replies") when the orchestrator or a quick reply answered while the chat was closed (`04`, `07`, `09`). It shows **no orchestrator yet** before an agent connects (`03`). Messages between teammates don't raise the count. They are in the chat when you open it, but they don't call for you.
- **Where the chat opens:** the right-hand slot that the context panel uses, as a right rail (C's idea). It is not a third column, so the board keeps its 4 readable columns at 1440 px. Closing the chat brings back whatever the context panel showed.
- **Why not a tab in the context panel (as in B before)?** The context panel shows *what you selected*. Chat is project-wide and runs on its own time, so it needs its own control that can say "1 new reply". The context panel has Details and Memory only, so there is one control for chat, not two.
- **Why not a permanent third column (C's layout)?** At 1440 px, rail + board + details + chat leaves about 450 px for 4 columns. The board is the default view and must stay readable.
- **Open and close:** the control (Enter or Space), `/` outside a field on the board, Needs me and Memory (it opens the chat and puts the cursor in the message box), **Close chat** in the pane header, `Esc` while focus is in the chat (the draft is kept), or `]` outside a field. Focus goes into the message box on open and back to the control that opened it on close. Settings has no chat control and `/` does nothing there.
- **Contextual openers:** on Needs me, **Ask a question (R)** opens the same chat with a strip "You are asking about…" and **Back to the approval** (`07d`). On Memory, **Open in chat, ask about it ↵** opens it at the found message with the quote attached and **Back to the source** (`09f`). Opened from the header control instead, the chat shows its newest messages without those strips. The keytest checks both.

## Words used in the product

- **Agent**: an AI helper that works on cards, such as a coding assistant on your computer, connected through MCP.
- **Orchestrator**: the project's lead agent. It reads what you ask in chat and turns it into cards. There is exactly one per project, and it is chosen automatically: the agent that has been connected longest. Nobody picks it by hand.
- **Chat** (*Atlas chat*): the project's conversation. You, teammates and agents write there, and the orchestrator answers.
- **Quick reply**: a short answer written by the model chosen in Settings → Chat answers, not by the orchestrator. Every message names its writer: **Atlas orchestrator** (agent tag), or **Quick reply · local model** / **Quick reply · hosted provider** (neutral chat icon, `06a`–`06d`). Memory results show the same source line (`09`). Settings says quick replies are saved under that name (`10`, `10e`).
- **Needs me**: things only a person can do: approvals, decisions and reviews addressed to you.
- On cards: "Working on it: Otto", "Available for an agent", **From chat · 14:03** (the orchestrator made the card from the chat message at that time) and **Linked in chat · 14:03** (an existing card it linked to that request).

## Information architecture

```
Workspace (Northwind)
├─ Left rail: Search or do… ⌘K · Needs me [n] · Memory · Projects (+) · Shortcuts ? · Maya
├─ Centre
│  ├─ Board (default after sign-in): header Atlas · [Chat ⟨1 new reply⟩ /] · filters · Settings · Add card (C)
│  ├─ Needs me: header Needs me · [Atlas chat ⟨1 new reply⟩ /] · filters
│  ├─ Memory: header Memory · [Atlas chat ⟨1 new reply⟩ /] · search
│  └─ Project settings (no chat control)
└─ Right-hand slot (one at a time)
   ├─ Context panel: Details (selected card or item; "Back to chat" strip when opened from chat) · Memory
   └─ Chat pane: the project chat
Tablet 700–1099 px: context panel = modal sheet via Details (as B); chat = push pane, with a column jump bar
Mobile: bottom tabs Board / Chat ⟨new⟩ / Needs me / Memory; Chat = full-screen sheet with "Back to board"
```

Rules (unchanged from B): the board is the default view and the place every flow returns to. There is at most one orchestrator per project, assigned automatically. Nothing files cards behind the user's back. Needs me only holds what a person must do. Memory always shows exact words with author, source and time. Chat never approves or declines anything: Approve and Decline stay as buttons in Needs me. **Details always match the selected card.** Selecting a card, on the board or from a chat row, rewrites the Details panel from the same data as the card (keytest: ID and title agree).

## Core flows

**1. Start: sign up, create the first project, connect an agent**
1. `01-signup` → `01b-check-email` (or `01c-link-expired`) → `02-create-project`, as B.
2. `03-board-empty`: a 3-step strip (Add card · Connect an agent · Ask for something). It is the only explanation of the orchestrator on this screen, with one Connect button. The chat starts **closed**. The control reads **Chat · no orchestrator yet**, and **Write in chat** or `/` opens it.
3. `03b-chat-no-agent`: a request sent with no agent shows **Waiting for an agent**, nobody has read it, with **Make it a card now** (Connect stays in the step strip). A failed send shows **Not sent. This device is offline.** with Try again.
4. `03c-connect-agent` → `03c2-connect-waiting` → `03d-connect-done` or `03e-connect-failed`, as B. Behind the dialog the waiting message stays in view. `03d` shows it **Delivered to the orchestrator at 10:11** and says "You talk to it in **Chat**, next to the project name above the board."

**2. Board (default view)**: `04-board`. Iris, Otto and Juno are working on cards. A-121 is **Blocked**, A-115 is **Needs me**, and Details shows A-121. The closed control reads **Chat 1 new reply**: a quick reply answered Maya's 12:30 question at 12:41.

**3. Add a card**: `04` → `05-add-card` → `05b-card-added`, as B (mobile `m4-add-card`).

**4. Talk to the orchestrator, one drawn path**
1. `04-board`: **Chat 1 new reply** (or `/`).
2. `06a-chat-open`: the chat replaces the context panel. **New since you last looked** marks the 12:41 **Quick reply · local model**, which names A-121 (*select one to open its details*). Maya's request is typed in the focused box.
3. `06b-chat-thinking`: **Working on your request…** (`role="status"`, with a turning ring that stops under reduced motion). The board stays usable.
4. `06c-chat-answer`: **Atlas orchestrator** answers. **From this request · on the board now** lists 2 new cards and 1 linked card. On the board, A-134 and A-135 are tagged **From chat · 14:03**, and A-118 shows **Linked in chat · 14:03**. Focusing or hovering a row outlines its card with a dashed line, which clears when focus or the pointer leaves.
5. `06f-chat-card-details`: selecting the A-134 row opens **A-134's Details** in the right-hand slot, and A-134 is selected on the board. The strip "Opened from Atlas chat, the 14:03 answer" has **Back to chat**, and Details show where the card came from (Maya's request, quoted exactly). **Back to chat** reopens the chat with focus on the same row. `06f2-chat-card-details-a118` is the same for the linked card A-118 in Doing. In both, ID and title match the selected card. Closing the chat later keeps that card in Details.
- States: `06d-chat-error` (**No answer yet.**, with **Ask again**, **Make it a card now** and a bordered **Check the agent in Settings**), `06e-chat-empty` (connected, nothing asked yet). Not connected: `03`, `03b`.

**5. Handle a Needs me item, from notice to done**
1. Notice: the rail count (Needs me 3), the amber card tag, and the mobile tab badge. The Needs me header also carries **Atlas chat 1 new reply**: the orchestrator's 14:09 message that it is asking for this approval.
2. `07-needs-me`: the approval, in its exact words, with the linked cards and the send fact. Buttons: **Approve and send…**, **Decline**, **Ask a question (R)**.
3. `07d-needs-me-ask`: **Ask a question** (or R) opens Atlas chat with "You are asking about *Send beta 0.9 to 40 testers?* Approve or decline it in Needs me" and **Back to the approval**. The answer says nothing has been sent.
4. `07c-approve-confirm` → `08-needs-me-done` (the 60-second hold, Stop, the next decision; the control reads **Harbor chat** for the selected Harbor item), as B. `07b-needs-me-empty`.
5. Mobile: `m2` → `m3` → `m5` → `m6` → `m7` / `m8`, as B. `m7`'s action now says **Approve or decline…**. *Not drawn:* **Ask a question** on `m3` and **Ask the orchestrator** on `m8` open the `m10` sheet with the same "You are asking about…" strip as `07d`. The desktop "stopped" result shows the `m7` content.

**6. Find a memory**
1. `09-memory`: search, 4 results with source, author and date. One is **Atlas chat · Quick reply · local model · today, 12:41**, so a model-written reply is never shown under the orchestrator's name. The Memory panel shows the full message between its neighbours. The header carries **Atlas chat 1 new reply**.
2. `09f-memory-ask`: **Open in chat, ask about it ↵** opens Atlas chat at Leon's 28 Oct message (**Found in memory**), with the quote attached to the message box ("Quoting Leon, Wed 28 Oct 2026, 16:12, exact words", removable) and **Back to the source**. Today's messages follow below.
3. States: `09e`, `09b`, `09c`, `09d`, as B.

**Settings**: `10`, `10b`, `10e`, `10c`, `10d`, as B. **Chat answers** now says that quick replies are saved in chat and memory under their own name, never the orchestrator's. `10e` adds that a hosted provider's replies are saved here labelled "Quick reply · hosted provider". The per-agent Disconnect… confirm is *not drawn* (it uses the `10c` pattern).

**Keyboard and ⌘K**: `11-command-palette` (it says "Every action except Approve and Decline, which stay in Needs me"), `11b-command-palette-card`, `12-shortcuts`. Shortcuts: ⌘K (Ctrl K) palette · C new card · **/ open the chat** · **] close the chat, or hide the side panel** · **Esc in the chat closes it** · G B / G N / G M go to · J/K move · Enter open · E edit · M move · **R ask a question (opens the chat)** · Y copy exact words · L link to card · Z undo / stop · ? shortcuts. Approve and Decline: none, by design. Live in the demo: ⌘/Ctrl K, C, /, R, ], Esc, arrows, Tab, Enter (on a board card it also updates Details) and every chat control. G, E, M, Y, L, Z and J/K are design-only.

## Tablet and mobile

- **Tablet, 700–1099 px.** Closed (`r2-04-board-1024`): the board is full width, and the header shows **Chat 1 new reply** next to **Details**. Open (`r-06c-chat-1024`): the chat **pushes** the board (220 | board | 340 px), so nothing is dimmed or covered (2.4.11). Columns are 190 px and scroll sideways. A **column jump bar** (Todo · Doing · Review · Done 14) puts Review and Done one tap away. A chat row opens that card's Details in the same pushed slot (not modal), with the same Back to chat (keytest at 1024 px). The **Details** button still opens B's modal sheet (`r2-04-board-1024-panel`).
- **Why not a modal at tablet?** The chat's job is to show cards appearing next to it, and a dimmed board would hide exactly that.
- **Mobile, 390×844.** `m1-board` and `m2-needs-me`: the **Chat** tab carries **new** ("1 new reply" for screen readers) on every screen with the tab bar, until the chat is opened. `m10-chat`: a full-screen sheet (`role="dialog"`, `aria-modal`), with focus on **Back to board**. Back and Esc both return focus to the Chat tab (keytest). Each card row ends in **Show →** (48 px) and opens **its own card**: `m11-board-from-chat` (A-134, Todo), `m11b-board-from-chat-a135` (A-135, Todo) and `m11c-board-from-chat-a118` (A-118 on the **Doing** tab). Each opens selected and outlined, under a bar "Showing A-…" with **Back to chat**, which returns to the same row with focus on it (keytest).
- Below 700 px, the desktop markup (`r1-04-board-320`) stacks the chat pane under the board, like B's context panel.

## Screens

| File | Size | Shows |
|---|---|---|
| 01-signup · 01b-check-email · 01c-link-expired · 02-create-project | 1440×900 | Sign up, link, first project (B) |
| 03-board-empty · 03b-chat-no-agent | 1440×900 | Empty board, chat closed; chat with waiting and failed messages |
| 03c-connect-agent · 03c2-connect-waiting · 03d-connect-done · 03e-connect-failed | 1440×900 | Connect an agent, chat behind (03d: delivered) |
| 04-board | 1440×900 | Board + details, chat closed with **1 new reply** |
| 05-add-card · 05b-card-added | 1440×900 | Add a card (B) |
| 06a-chat-open · 06b-chat-thinking · 06c-chat-answer · 06f-chat-card-details · 06f2-chat-card-details-a118 · 06d-chat-error · 06e-chat-empty | 1440×900 | Chat: open, working, answer → cards, **a chat card's details** (new and linked), no answer, empty |
| 07-needs-me · 07d-needs-me-ask · 07c-approve-confirm · 08-needs-me-done · 07b-needs-me-empty | 1440×900 | Needs me with **Atlas chat**, ask in chat, confirm, held send, empty |
| 09-memory · 09f-memory-ask · 09e · 09b · 09c · 09d | 1440×900 | Memory with **Atlas chat** and a quick-reply source, ask at the source, states |
| 10-settings · 10b · 10e (1440×1180) · 10c · 10d (1440×900) | | Settings |
| 11-command-palette · 11b-command-palette-card · 12-shortcuts | 1440×900 | Palette, card actions, shortcuts |
| m1-board · m10-chat · m11 · m11b · m11c · m9 · m4 · m2 · m3 · m5 · m6 · m7 · m8 | 390×844 @2x | Mobile; chat sheet; **each card from chat** |
| r2-04-board-1024 · r-06c-chat-1024 · r2-04-board-1024-panel · r1-04-board-320 · r2-m1-board-320 · r1-m3-needs-me-item-360 | as named | Same markup at other widths |

## Accessibility notes (WCAG 2.2 AA)

Everything in B's notes still holds (tokens, focus ring, dialogs, roving board, targets, status messages). For B + chat:
- **Surface colour** is now near-white `#FEFEFC` instead of `#FFFFFF` (see the hex check). Measured: body text 17.6:1, secondary 8.5:1, muted 5.8:1, accent and white-on-accent 7.8:1, control borders 4.1:1 on the surface.
- **Control state** (4.1.2): `aria-expanded` on every chat opener (header control, Ask a question, Open in chat), kept in sync. The open state is filled (17.6:1), and the pane appears with it, so the state isn't shown by colour alone.
- **Chat pane:** `<aside aria-labelledby="chat-h">`, not modal on desktop or tablet. The message list is `role="log"`, and the box has a label. Card lists in messages are `role="group"` named by their heading ("From this request · on the board now"). The working state is `role="status"`; no answer and a failed send are `role="alert"`.
- **Who wrote it:** every message names its writer in text, so the source never depends on the avatar.
- **Not colour alone:** "1 new reply", "no orchestrator yet", **New since you last looked** (a line plus words), **From chat · 14:03** and **Linked in chat · 14:03** on the cards, and a **dashed** outline for the card a row points at (accent 6.7:1 on the column well). The outline clears on focusout and mouseout (keytest).
- **Measured contrast of the new parts:** "1 new reply" chip 6.8:1 · "no orchestrator yet" chip 8.5:1 · open control 17.6:1 · chat subtitle 8.5:1 · New-since line 7.8:1 · context strip 17.0:1 · mobile "Showing…" bar 15.4:1 · control border 3.9:1 on the header.
- **Targets:** control 32 px, Close chat and Back to chat 28 px, chat rows 36 px (48 px on mobile), Back to board 44 px, mobile send 44×44, mobile "Show N more" 44 px, tablet column jump 32 px.
- **Motion:** the only motion is the "working" ring turning (`06b`, `03b`). `prefers-reduced-motion` stops it, and the text carries the state.
- **Focus:** opening the chat puts focus in the box; closing returns it to the opener. A chat row opens that card's details with focus on its title. Back to chat returns focus to the same row. On mobile, links that return carry the target in the address (`#m-chat-tab`, `#row-A-135`), and the page focuses it on load.

## What I traded away

- **Details and chat side by side.** They share the right-hand slot. A chat row now opens the card's details in one click, and Back to chat returns in one more. A permanent 3rd column would cost the board its readable columns at 1440 px.
- **Always-on chat (C's rail).** The chat stays behind one control, as the owner allowed. In exchange, the control says **1 new reply** on every screen that has it.
- **The "Orchestrator connected" pill** is gone from the board header. The control now shows status only when something is wrong or new, and the full status is in the chat header and in Settings.
- **One drawn path per flow:** the closed control's "answering…" and "no answer" states, and the mobile ask paths from `m3` and `m8`, are described here but not drawn.

## No ev0lve-theme visuals: hex check

`python3 -I _src/hexcheck.py . <ev0lve theme folder>`, run in this folder against the local ev0lve theme folder (it is not part of this repository). **Scope:** in this folder, every `.css`, `.html`, `.js` and `.py` file (58 files). In the theme folder, every file of any type, read as text, with `.git` skipped (93 files). Values are lower-cased, short forms are expanded, and the alpha part is ignored. Id selectors such as `#add-card` are not counted as colours.

Result on 5 Nov 2026, round 2: **36 colours here, 336 in the theme folder, shared: none.** In round 1 the only shared value was plain white `#ffffff`. It is now near-white `#FEFEFC` everywhere (CSS tokens, inline styles, the contact sheet), which is not in the theme set. There are no shared accents, backgrounds or textures, and no images from it. The palette is B's own: accent #2E43C0, neutrals #F3F3EF / #FAFAF8 / #EDEDE7, and status colours.

## Open risks

- **Two things in one slot.** Some users may want chat and details side by side. On screens wider than about 1700 px, a third column could be offered later; it is not designed.
- **Tablet sideways board.** With the chat open, Review and Done need the jump bar or a sideways scroll.
- **Quick replies vs the orchestrator.** Two writers in one chat must stay clearly named in every surface that quotes chat: cards, memory, exports.
- B's risks still stand: density on day one, single-key shortcuts with voice control, the 60-second send hold, dark theme not designed, system font.
