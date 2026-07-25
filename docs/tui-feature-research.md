# TUI Feature Research — Comprehensive Report

> **Objective:** Web research into useful features to add to the Native GLM ACP
> standalone terminal frontend (`glm-acp chat`, built on Textual).
>
> **Method:** 9 `web_search` queries + 3 deep `web_reader` page reads + 2
> background `delegate_task` investigations (Codex/Gemini/Cursor;
> lazygit/k9s/helix/atuin/fzf). Sources cited inline and in §8.
>
> **Date:** 2026-07-25 (session after v2.7.0 cold-start perf wave).

---

## Contents

0. [Current state (don't recommend redundant work)](#0-current-state)
1. [Tier 1 — Quick wins (Textual built-ins, hours-to-days)](#1-tier-1-quick-wins)
2. [Tier 2 — High-impact feature gaps (days-to-weeks)](#2-tier-2-high-impact-feature-gaps)
3. [Tier 3 — Strategic differentiators (weeks)](#3-tier-3-strategic-differentiators)
4. [Tier 4 — Worth considering](#4-tier-4-worth-considering)
5. [What NOT to add](#5-what-not-to-add-anti-recommendations)
6. [Cross-cutting implementation notes](#6-cross-cutting-implementation-notes)
7. [Recommended sequencing](#7-recommended-sequencing)
8. [Sources](#8-sources)

---

## 0. Current state

The TUI already ships a mature feature set (per `AGENTS.md` and the v2.7.0 work):

- **F1–F7**: help, reasoning-panel toggle, settings, 4-view working-tree panel
  (session-changes / git-status / diff / file-browser), push-to-talk (Whisper),
  session-history browser, native-mouse mode.
- **Slash commands**: `/plan` `/thinking` `/model` `/search` `/export` `/undo`
  `/prompt` `/journey` `/usage` `/settings` `/clear-view` `/native-mouse`
  `/exit` `/reasoning-panel`.
- **Live widgets**: token meter (input↑ / output↓ / cache%), awareness
  indicator (mode / evidence / risk / contradictions), activity status line,
  FIFO composer queue.
- **Cross-cutting**: notification sounds (opt-in bell), desktop notifications,
  `SelectableStatic` for selection, `Ctrl+Y` clipboard, credential-redacted
  approvals, MCP servers, background workers with live transcripts, persistent
  goals + bounded completion judge.

This report addresses what's **missing** relative to the leaders.

---

## 1. Tier 1 — Quick wins

Leverage Textual built-ins or our existing session APIs. **Highest ROI.**

### 1.1 ⭐ Command Palette (`Ctrl+P`) — fuzzy command search

- **Why:** Textual ships a
  [Command Palette](https://textual.textualize.io/guide/command_palette/) with
  built-in fuzzy search. We expose `/` for slash commands but no global
  palette. Claude Code, OpenCode, and TUICommander all have one.
- **What:** Register every `/command`, every F-key view, and every
  model/plan/thinking toggle as palette providers. `Ctrl+P` → type "foc" →
  finds `/focus` or "Toggle focus mode".
- **Effort:** Low — Textual's `CommandPalette` widget + provider classes.
- **Differentiator:** none — table-stakes parity.

### 1.2 ⭐ Inline image rendering (kitty / sixel / iTerm2 protocols)

- **Why:** We ship **GLM-5V-Turbo, GLM-4.5V, GLM-4.6V** vision models — but the
  TUI can't display images users paste with `--image`, screenshots the agent
  captures, or vision-model outputs. They render as `[image]` or get hidden.
  Huge gap given our model lineup.
- **What:** Detect terminal capability (`TERM_PROGRAM=iTerm.app`, kitty's
  graphics-protocol handshake, `$TERM` containing `sixel` / `kitty` /
  `wezterm`), render attached images and vision outputs inline via the
  matching protocol. Fallback to a clickable
  `[image saved to /tmp/...]` link.
- **Effort:** Medium — detection is one function; rendering libraries exist
  (`term-image`, `textual-image`, `kitty-image`). Adds binary weight.
- **Differentiator:** strong — most agent TUIs (including Claude Code, per
  their open issue [#2266](https://github.com/anthropics/claude-code/issues/2266))
  don't have this yet.

### 1.3 ⭐ `/copy [N]` with code-block picker

- **Why:** We have `Ctrl+Y` for the last response. Claude Code's `/copy [N]`
  copies the Nth-latest response and shows a **picker** to grab an individual
  code block vs the whole message. Essential over SSH.
- **What:** `Ctrl+Y` → modal listing recent responses with code blocks
  enumerated; press a number to copy that block, or `w` to write to file.
- **Effort:** Low — Rich already segments code blocks.

### 1.4 ⭐ `/recap` — one-line session summary

- **Why:** Long sessions lose thread. Claude Code's `/recap` generates a
  one-line summary on demand.
- **What:** Auxiliary GLM (we already use the auxiliary path for
  titles/compression) generates a one-line summary injected into the status
  bar.
- **Effort:** Low — auxiliary GLM infrastructure already exists.

### 1.5 ⭐ Vim-mode composer (modal editing)

- **Why:** Heavily requested across OpenCode (separate `vimcode` plugin,
  326 upvotes), Codex, Crush, Smelt. Vim users hate single-line editors.
- **What:** Modal composer (Normal/Insert/Visual) with `hjkl` / `dw` / `yy` /
  `p`. Toggle via `/vim` or F-key. Default off.
- **Effort:** Medium — implement a `ModalInput` widget wrapping the composer,
  or embed a battle-tested component.
- **Differentiator:** parity, but high user demand.

### 1.6 ⭐ Status-bar customization (`/statusline`)

- **Why:** Our status line is fixed. Claude Code's `/statusline` lets you
  describe what you want in natural language and auto-configures from the
  shell prompt.
- **What:** Configurable segments (model, plan, branch, token meter,
  awareness, queue count) toggleable via `/settings` or a TOML block.
- **Effort:** Low–Medium.

---

## 2. Tier 2 — High-impact feature gaps

Real capability gaps vs Claude Code and TUICommander.

### 2.1 ⭐⭐ Interactive diff review with inline annotations (Revdiff / Hunk pattern)

- **Why:** We have a 4-view F4 panel showing diffs, but you can't **annotate**
  a line and send the annotation back to the agent.
  [Revdiff](https://news.ycombinator.com/item?id=47742437) and
  [modem-dev/Hunk](https://github.com/modem-dev/hunk) built entire products
  around this — drop an annotation on a hunk, quit, the annotations stream
  back to the agent which picks them up. Gold-standard human-in-the-loop
  pattern.
- **What:** In the F4 diff view, press `c` on any line → inline comment editor
  → on quit, comments become a structured follow-up prompt
  ("revise line 42 of foo.py: …") queued for the next turn.
- **Effort:** Medium–High — needs line-anchored comment storage and a
  comments→follow-up-prompt formatter.

### 2.2 ⭐⭐ `/context` — visualized context budget (grid + heatmap)

- **Why:** We have a token meter (input↑/output↓/cache%) but no view of
  **what** is consuming context — system prompt, tool results, MCP servers,
  recalled memory, prior turns. Claude Code's `/context` shows a colored grid
  with optimization suggestions. Token-budget research (multiple 2026 papers,
  [arxiv 2604.22750](https://arxiv.org/pdf/2604.22750)) shows agents waste ~80%
  on orientation — visibility is the first lever.
- **What:** Modal grid: rows = context segments (system prompt, `AGENTS.md`,
  tools, MCP, memory, prior turns, current turn), columns = bytes/tokens,
  color = compression candidacy. Press `c` to invoke `/compact` with focus on
  a segment.
- **Effort:** Medium — we already track usage deltas; need segment-level
  attribution.
- **Differentiator:** strong — pairs with our existing `/compact` and
  structured compaction.

### 2.3 ⭐⭐ `/compact [focus]` slash command

- **Why:** `AGENTS.md` says we have structured compaction but no slash command
  is exposed in the TUI. Claude's `/compact [instructions]` lets you guide the
  summary.
- **What:** `/compact focus on the bug fix and discard the exploration` —
  passes the focus to the existing compactor.
- **Effort:** Low — wire the existing capability to a command.

### 2.4 ⭐⭐ `/goal [condition]` — explicit persistent goal display

- **Why:** We already have persistent goals + a bounded completion judge (in
  the awareness machinery). But there's no slash command to **set** or **view**
  one mid-session. Claude's `/goal fix all type errors` makes Claude work
  across turns until the condition is met.
- **What:** `/goal <condition>` registers a persistent goal; status bar shows
  progress; `/goal clear` cancels. Reuses our existing goal/criterion
  infrastructure.
- **Effort:** Low–Medium — UI on top of existing engine.

### 2.5 ⭐⭐ `/btw [question]` — side question without polluting context

- **Why:** Asking a quick clarifying question mid-task forces the agent to
  context-switch and pollutes the working thread. Claude's `/btw` opens an
  overlay that doesn't add to the conversation.
- **What:** Overlay panel with its own short auxiliary-GLM exchange; answer
  shown but not injected into the main thread.
- **Effort:** Medium — separate auxiliary exchange + overlay widget.

### 2.6 ⭐⭐ MCP server management UI (`/mcp`)

- **Why:** We support MCP servers but the TUI has no `/mcp` to
  enable/disable/reconnect/list, no per-server tool count, no OAuth status.
  Claude's `/mcp [reconnect|enable|disable|all]` does this.
- **What:** `/mcp` → modal listing configured servers with connection state,
  tool count, last-error; actions: reconnect, enable, disable.
- **Effort:** Medium.

### 2.7 ⭐⭐ Background worker / subagent dashboard (`/tasks`)

- **Why:** We have `delegate_task(background=true)` workers with live
  transcripts, but no TUI to **see** them. Claude's `/tasks` lists
  running/completed background work; `/fork` `/subtask` spawn more.
- **What:** `/tasks` → table of active workers (id, goal, age, token usage,
  last tool call); click to tail the transcript file
  (`<config_dir>/workers/<session>-<uuid>.log`).
- **Effort:** Medium — we already write the transcripts, just need a viewer.

### 2.8 ⭐⭐ Themes: light/dark/colorblind/ANSI (`/theme`)

- **Why:** Accessibility + personalization. Claude's `/theme` includes `auto`
  (matches terminal bg), colorblind-accessible (daltonized), and ANSI (uses
  terminal palette). Our TUI assumes dark.
- **What:** Theme registry; `auto` queries `COLORFGBG` env or OSC 11 query;
  daltonized palette for deuteranopia/protanopia.
- **Effort:** Medium — Rich/Textual themes are well-supported.

---

## 3. Tier 3 — Strategic differentiators

Bigger bets that could set us apart, drawn mostly from TUICommander and the
orchestrator ecosystem.

### 3.1 ⭐⭐⭐ Vision-model UX (differentiator given our model lineup)

We ship **three vision models** (GLM-5V-Turbo, GLM-4.5V, GLM-4.6V) — possibly
the strongest vision lineup of any ACP-native agent. The TUI should lean in:

- **Inline image display** (Tier 1.2 above) — foundational.
- **Image paste from clipboard** — `Ctrl-V` already works for text; extend to
  images on macOS/Linux.
- **Screenshot tool** — `/screenshot` captures a region or window, attaches to
  next prompt.
- **PDF/page render** — `/attach <url>` fetches and renders a page as image
  for the vision model.

### 3.2 ⭐⭐⭐ Multi-session parallel worktrees

- **Why:** TUICommander's killer feature. Run N agent sessions in isolated git
  worktrees side-by-side. We have F6 for resuming past sessions but no
  parallelism.
- **What:** Tabbed sessions, each bound to a `git worktree add`-managed
  directory; sidebar shows all sessions with status dots
  (idle/working/awaiting/error); resume any. We already have session
  persistence + the `worktree_worker` infra.
- **Effort:** High — significant TUI restructuring. But `worktree_worker`
  already does isolated git worktrees for delegation; we could expose it for
  human-driven parallel sessions.

### 3.3 ⭐⭐⭐ CI Auto-Heal

- **Why:** TUICommander fetches CI failure logs and injects them into the
  agent for automatic fix. Close the feedback loop without leaving the TUI.
- **What:** `/ci` shows recent GitHub Actions runs for the current branch; on
  failure, button to inject logs as a follow-up prompt. We already have `gh`
  integration patterns.
- **Effort:** Medium — `gh run view --log-failed` + a "send to agent" action.

### 3.4 ⭐⭐ GitHub PR/Issues panel

- **Why:** TUICommander and Claude Code (`/review`, `/pr-comments`) integrate
  PRs. Our F4 panel shows local files but not GitHub state.
- **What:** Add a 5th view to F4: GitHub (open PRs for current branch, review
  state, CI ring, merge/approve actions, issue list filtered by assignee).
- **Effort:** Medium — `gh` CLI wrappers.

### 3.5 ⭐⭐ Mobile companion (PWA over LAN/Tailscale)

- **Why:** TUICommander's remote-control story is compelling — see agent
  status from your phone, approve with one tap. We already have rate-limit-aware
  `/usage`.
- **What:** Optional local HTTP server (default off, credential-safe) serving
  a PWA; QR code in TUI; one-tap approval forwarding to the session.
- **Effort:** High — security review needed (must not expose `ZAI_API_KEY`);
  use existing credential-redaction patterns.

### 3.6 ⭐⭐ Plugin system with hot reload

- **Why:** TUICommander's TUIC SDK lets plugins watch terminal output, add
  status widgets, register commands. We have a `plugin_cli` (signing/trust)
  but no in-TUI plugin runtime.
- **What:** Plugins register `/commands`, status-bar widgets, terminal-output
  watchers. Hot-reload on file change.
- **Effort:** High — needs careful sandboxing.

---

## 4. Tier 4 — Worth considering

| Feature | Source | Note |
|---|---|---|
| **Screen-reader mode** (linear text stream) | Claude Code, ["text mode lie"](https://news.ycombinator.com/item?id=48002938) HN thread | Real accessibility win; replace boxes/animations with linear output. Important for parity. |
| **Smart prompts** (parameterized templates with auto-resolved `{branch}`, `{diff}`, `{commit_log}`) | TUICommander | 29 built-ins; we could ship 5–10 GLM-specific ones. |
| **Sound effects / ambient soundscape** | EchoCoding | We have terminal bell only; hook-triggered SFX + ambient mode is niche but loved. |
| **Tab/session status dots** (idle/working/unseen/awaiting/error) | TUICommander | Trivial visual change, big legibility win — applies if we add multi-session. |
| **`/rewind` (code + conversation)** | Claude Code | We have `/undo` for turns; Claude rewinds **code** too via checkpoints. We have checkpoint infra. |
| **`/loop [interval] [prompt]`** | Claude Code | We have a `cron` subsystem; exposing `/loop` in TUI for ad-hoc iteration is cheap. |
| **`/insights` session analysis** | Claude Code | Auxiliary GLM analyzes past sessions for friction patterns. |
| **Customizable keybindings with chord support** | TUICommander, OpenCode (leader key) | Power-user feature; OpenCode's `Ctrl+X` leader-key pattern avoids terminal conflicts. |
| **`/security-review`** | Claude Code | We have a redactor; extending to a diff security scan is plausible. |

---

## 5. What NOT to add (anti-recommendations)

- **Agent marketplace / community registry** — TUICommander's plugin registry
  is a maintenance burden we don't need.
- **Built-in code editor (CodeMirror/Tree-sitter)** — TUICommander ships one;
  we should stay **editor-parity** oriented (per `AGENTS.md` user preference)
  and defer editing to `$EDITOR` via `/prompt`.
- **Cloud relay for mobile** — E2E-encrypted relay is complex; LAN-only is
  enough for v1.
- **Telemetry / usage analytics dashboard** — `AGENTS.md` is explicit:
  "no local quota estimates," credential-safe. Stay metadata-only.
- **Reimplementing what `gh` / `git` / `fzf` already do** — surface them, don't
  rebuild.

---

## 6. Cross-cutting implementation notes

- **Performance:** All Tier 1–2 features must follow the v2.7.0 cold-start
  discipline (deferred imports, regression test). The new
  `cold-start-import-deferral` skill applies.
- **Accessibility:** Any new visual feature should consider the screen-reader
  mode pattern (linear fallback). High-contrast + colorblind palettes are
  cheap wins.
- **Credential safety:** Mobile companion, MCP management, and CI integration
  all touch credentials — must reuse existing redaction patterns and never log
  `ZAI_API_KEY`.
- **Textual primitives to leverage:** `CommandPalette`, `TabbedContent`,
  `Tree`, `DataTable`, `Input(modal=True)`, screen-stack for overlays,
  `App.install_screen` for modals like `/btw` and `/copy`.
- **Testing:** Every new `/command` should get a test similar to the existing
  TUI test suite; every deferred import should keep the regression test
  passing.

---

## 7. Recommended sequencing

If we pick one quarter's worth of work, in priority order:

1. **Command Palette (Ctrl+P)** + **`/copy [N]` picker** + **`/recap`** +
   **`/compact [focus]`** + **`/goal`** + **`/btw`** — small, high-visibility
   wins that close most of the slash-command gap with Claude Code.
2. **Inline image rendering** — unlocks the full value of our vision models.
   Biggest single differentiator.
3. **`/context` visualization** + **`/tasks` worker dashboard** + **`/mcp`
   management** — surfaces infrastructure we already have but don't expose.
4. **Interactive diff annotation** (Revdiff pattern) — gold-standard review
   loop.
5. **Vim-mode composer** + **themes** (including colorblind) — accessibility +
   power-user parity.
6. Then strategic: **multi-session worktrees**, **CI Auto-Heal**, **GitHub
   panel**, **mobile companion**.

---

## 8. Sources

- [Claude Code Commands (official)](https://code.claude.com/docs/en/commands) —
  ~80-command reference
- [TUICommander](https://tuicommander.com/) — AI-native IDE feature set
- [awesome-cli-coding-agents](https://github.com/bradagi/awesome-cli-coding-agents) —
  90+ agent patterns
- [Textual Command Palette guide](https://textual.textualize.io/guide/command_palette/)
- [kitty graphics protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/)
  + [Sixel/Kitty/iTerm2 issue](https://github.com/anthropics/claude-code/issues/2266)
- [Revdiff](https://news.ycombinator.com/item?id=47742437) +
  [modem-dev/hunk](https://github.com/modem-dev/hunk) — diff annotation pattern
- [Aider git integration](https://aider.chat/docs/git.html)
- [OpenCode keybinds](https://opencode.ai/docs/keybinds/) — leader-key pattern
- [Claude Code screen reader mode](https://aicatchup.com/news/claude-code-screen-reader-mode-accessibility)
- [Token economics paper (arxiv 2604.22750)](https://arxiv.org/pdf/2604.22750) —
  context budget research

---

## Appendix — Pending supplementary investigations

Two background `delegate_task` workers were dispatched and may deliver
additional comparison tables as session messages:

- **`df770456`** — Codex CLI / Gemini CLI / Cursor terminal TUI feature
  matrix (slash-command catalog, keyboard shortcuts, diff rendering,
  permissions, unique features).
- **`805a199f`** — lazygit / lazydocker / k9s / helix / atuin / fzf / btop /
  gh-dash proven UX patterns (most-loved feature, navigation, diff rendering,
  fuzzy finding, virtualized lists).

Their findings will likely reinforce Tiers 1–2 (vim mode, command palette,
diff review) and may add depth on orchestrator patterns. Incorporate when they
arrive.
