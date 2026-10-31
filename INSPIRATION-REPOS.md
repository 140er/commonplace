# Inspiration repos for Commonplace (researched 2026-10-29)
Source: https://x.com/roundtablespace/status/2115666184888537433 (6 repos, all < 1 week old). Stars as of 29 Oct 17:45 CET.

## 1. kanzo — https://github.com/emanuele-em/kanzo  (AGPL-3.0, Rust, 159★, created 25 Oct)
Kanban board in the browser + MCP server in one 4 MB binary (SQLite bundled). Solid for its age: CI, signed releases, crates.io, numbered migrations, ~2.3k lines (mcp.rs 560, store.rs 1371, sync.rs 390) plus app.js/index.html.
- Data model (schema.sql): projects(slug, name, repo unique) -> tasks(title, description, status, position REAL for drag ordering, timestamps) -> comments(author, body). Images attached to tasks/comments as files. A `meta.changes` counter bumped by triggers on every write; the web UI polls it, so agent edits show up live.
- MCP tools (~900 tokens total, replies 10-60 tokens plain text): list projects, create project, link repo, list tasks, show task (with comments + image paths), add task, update task, comment, take next (top todo -> doing), delete task. Project is auto-picked from the agent's git remote.
- Columns: todo/doing/done + optional Backlog that agents must ignore.
- Sync: data folder ~/kanzo is a git repo pushed to a private repo; manual `kanzo sync`, choose keep-local/keep-remote on conflict. For concurrent multi-machine use: one board on 0.0.0.0 with MCP over HTTP at /mcp. No auth at all.
- UI: single-page app, ⌘K search across projects, keyboard-first, light/dark.
- Verdict: BORROW THE DESIGN, NOT THE CODE. AGPL (we chose MIT) and Rust (MemPalace is Python), so reimplement. Copy: tiny tool surface + terse replies, take_next, repo-based project detection, Backlog-agents-ignore rule, change-counter polling, comments as progress log.

## 2. astro-conversion-kit — https://github.com/ethanplusai/astro-conversion-kit  (MIT, JS, 104★, created 22 Oct)
Agent skills that convert a WordPress/live site to static Astro with URL/SEO/pixel parity, ending in PR + staging deploy. Gated scripts, preflight checker. Results claims are author's own.
- Verdict: SKIP for the hub. Minor idea: `preflight.mjs` style "which tools are installed and logged in" check for our Docker self-host install.

## 3. bama — https://github.com/daveebbelaar/bama  (MIT, Swift, 727★, created 24 Oct)
21 skills for building native macOS apps (clone -> user stories -> one story per commit with snapshots). Needs macOS 26/Xcode 26.
- Verdict: SKIP for Commonplace. Maybe later for a native Mac client; its router skill pattern (`/bama` picks next skill) is a nice idea for a "commonplace" onboarding skill.

## 4. AgentFlux — https://github.com/davemessew/AgentFlux  (Apache-2.0, JS, 235★, created 27 Oct)
Self-hosted Docker platform for visual agent workflows, local/cloud models, "knowledge and learnings" (observations from runs reused as skills). No login; v0.2.0.
- Verdict: MOSTLY SKIP. Different product. Worth a look: its "observations from runs -> reviewable learnings" flow as a UI idea for memory review/approval. Apache code reusable if ever needed.

## 5. Flecha — https://github.com/lucianodiisouza/flecha-browser  (MIT, TS + Chromium fork, 107★, created 22 Oct)
Arc-style browser for devs with built-in local MCP server. Apple Silicon only, early.
- Verdict: SKIP. Unrelated to memory. Only as a possible future MCP client.

## 6. mcode-stack — https://github.com/MiniMax-AI/mcode-stack  (MIT, Python, 398★, created 28 Oct)
MiniMax Code team's daily skills (code quality bar, subagent decisions, long command waits, context per turn), packaged as Claude Code / Codex plugin marketplaces.
- Verdict: SMALL BORROW. Its packaging (one repo as Claude Code + Codex plugin marketplace, install.sh fallback to ~/.agents/skills) is a good model for shipping Commonplace's "check memory" skill/plugin cheaply. Its "what goes into context every turn" skill is relevant to keeping our seed prompt short.

## Kanzo-style board in Commonplace (sketch)
1. Tables next to memories in the same DB: boards(project, repo links), tasks(id, board, title, body, status todo|doing|done|backlog, position, assignee, created_by, updated_by, timestamps), task_comments(author, body).
2. Authorship: server stamps created_by/author from the caller's token (e.g. avery, grokbot, claude-code@vm), never from tool args. Same as memories.
3. MCP tools (~6, terse plain text): board_list, task_list, task_show, task_add, task_update(status/fields), task_comment, task_take_next. Project from git remote or explicit name.
4. Link tasks and memories: task_show returns related memories; finishing a task can file a memory ("decision/lesson") with a backlink.
5. Permissions: boards live in the same scopes as memories (private / shared / team); token scopes limit which boards an agent sees. Sealed tier does not apply to boards in v1.
6. Backlog column agents ignore; "take next" claims atomically so two agents never grab the same card.
7. Live updates: change counter or SSE from the hub; no git sync needed since the hub is the central service.
8. Web UI: kanban page in the Commonplace web app (same auth), drag-drop, ⌘K search across tasks and memories, comments thread showing which agent wrote what.
9. Self-host gets it free; hosted runs the same code.
10. Reimplement in our stack (MIT), no kanzo code copied because of AGPL.
