# DOX framework

- DOX is highly performant AGENTS.md hierarchy installed here
- Agent must follow DOX instructions across any edits

## Core Contract

- AGENTS.md files are binding work contracts for their subtrees
- Work products, source materials, instructions, records, assets, and durable docs must stay understandable from the nearest applicable AGENTS.md plus every parent AGENTS.md above it

## Read Before Editing

1. Read the root AGENTS.md
2. Identify every file or folder you expect to touch
3. Walk from the repository root to each target path
4. Read every AGENTS.md found along each route
5. If a parent AGENTS.md lists a child AGENTS.md whose scope contains the path, read that child and continue from there
6. Use the nearest AGENTS.md as the local contract and parent docs for repo-wide rules
7. If docs conflict, the closer doc controls local work details, but no child doc may weaken DOX

Do not rely on memory. Re-read the applicable DOX chain in the current session before editing.

## Update After Editing

Every meaningful change requires a DOX pass before the task is done.

Update the closest owning AGENTS.md when a change affects:

- purpose, scope, ownership, or responsibilities
- durable structure, contracts, workflows, or operating rules
- required inputs, outputs, permissions, constraints, side effects, or artifacts
- user preferences about behavior, communication, process, organization, or quality
- AGENTS.md creation, deletion, move, rename, or index contents

Update parent docs when parent-level structure, ownership, workflow, or child index changes. Update child docs when parent changes alter local rules. Remove stale or contradictory text immediately. Small edits that do not change behavior or contracts may leave docs unchanged, but the DOX pass still must happen.

## Hierarchy

- Root AGENTS.md is the DOX rail: project-wide instructions, global preferences, durable workflow rules, and the top-level Child DOX Index
- Child AGENTS.md files own domain-specific instructions and their own Child DOX Index
- Each parent explains what its direct children cover and what stays owned by the parent
- The closer a doc is to the work, the more specific and practical it must be

## Child Doc Shape

- Create a child AGENTS.md when a folder becomes a durable boundary with its own purpose, rules, responsibilities, workflow, materials, or quality standards
- Work Guidance must reflect the current standards of the project or user instructions; if no specific standards or instructions yet, leave it empty
- Verification must reflect an existing check; if no verification framework exists yet, leave it empty and update it when one exists

Default section order:
- Purpose
- Ownership
- Local Contracts
- Work Guidance
- Verification
- Child DOX Index

## Style

- Keep docs concise, current, and operational
- Document stable contracts, not diary entries
- Put broad rules in parent docs and concrete details in child docs
- Prefer direct bullets with explicit names
- Do not duplicate rules across many files unless each scope needs a local version
- Delete stale notes instead of explaining history
- Trim obvious statements, repeated rules, misplaced detail, and warnings for risks that no longer exist

## Closeout

1. Re-check changed paths against the DOX chain
2. Update nearest owning docs and any affected parents or children
3. Refresh every affected Child DOX Index
4. Remove stale or contradictory text
5. Run existing verification when relevant
6. Report any docs intentionally left unchanged and why

## User Preferences

When the user requests a durable behavior change, record it here or in the relevant child AGENTS.md

- Public releases and ACP Registry metadata identify Aleksejs Kozlitins as author and use Apache-2.0.
- Registry installation uses version-pinned frozen binaries for Linux x86-64/ARM64, macOS Intel/Apple Silicon, and Windows x86-64.
- Public GitHub installation provides checksum-verifying, user-local installers that expose both `native-glm-acp` and `glm-acp` without requiring Python, Node.js, or administrator privileges.
- Public frozen installs provide `glm-acp --uninstall`; credentials are preserved unless the user explicitly adds `--purge`, and source or Registry-managed copies must not self-delete.
- Standalone terminal interaction is discoverable and editor-parity oriented: typing `/` opens the live agent command catalog with `/plan`, `/thinking`, and `/model` first; `/plan` names Coding Plan, Standard API, and BigModel (CN) directly; `/thinking` controls provider reasoning while F2 and `/reasoning-panel` control only its view; all model, plan, thinking, permission, mode, generation, auxiliary-model, and Mixture-of-Agents changes use the same session APIs as ACP editors.
- Standalone terminal composition accepts terminal-routed bracketed paste plus explicit Ctrl-V and Ctrl-Shift-V OS clipboard shortcuts without dropping a leading-newline prompt, presents multiline content safely in the single-line composer, and keeps the complete composer border above the footer. Optional platform clipboard readers run only on explicit paste, without a shell or credential environment, under a one-second timeout and one-million-character result bound. Linux users must install the clipboard helper binaries once before in-TUI shortcuts work: `xclip`/`xsel` on X11 (`sudo apt install xclip xsel` on Debian/Ubuntu) or `wl-clipboard` on Wayland (`sudo apt install wl-clipboard`); macOS uses built-in `pbcopy`/`pbpaste` and Windows uses built-in PowerShell `Get-Clipboard`/`Set-Clipboard`. The README documents apt/dnf/pacman/zypper commands for both display servers; native-mouse mode (F7) bypasses these helpers by deferring to the terminal emulator's own clipboard path.
- Standalone terminal selection is editor-parity oriented: the TUI does NOT draw a custom dropdown menu — the terminal emulator already knows how to copy and paste natively. F7 (or `/native-mouse`, or `GLM_ACP_NATIVE_MOUSE=1` at startup) releases Textual's X11/SGR mouse capture (`\x1b[?1000l`, `\x1b[?1003l`, `\x1b[?1015l`, `\x1b[?1006l`) back to the terminal so the user's native right-click context menu and click-drag text selection work just like in Codex and Claude Code; TUI mouse features (clickable widgets, mouse-wheel scrolling) are disabled while native mode is on, and F7 toggles them back. While Textual mouse capture is on (default), hold Shift while dragging or right-clicking to bypass the app the same way. Ctrl+Y copies the last agent response to the OS clipboard and Ctrl+Shift+C copies the current Textual selection (when not intercepted by the terminal), both via credential-safe OS clipboard helpers with a one-million-character bound. Agent-message widgets use a `SelectableStatic` subclass that exposes the rendered plain text for selection — Textual's default `Static.get_selection` returns `None` for Rich renderables (RichMarkdown), so without this subclass agent responses are invisible to selection.
- Standalone TUI activity is visible but presentation-only: a low-overhead status line animates startup, thinking, reasoning, tool work, and cancellation; shows static approval, completion, failure, and ready states; bounds streamed labels; and honors `GLM_ACP_TUI_ANIMATION=0` without changing ACP, plain, or JSON behavior.
- Terminal quit is delivery-safe and bounded: the visible/clickable footer uses Ctrl-X, F10 and `/exit` are equivalents, Ctrl-Q is hidden because POSIX flow control may swallow it, TUI lifecycle state never shadows Textual internals, provider telemetry DNS/HTTP cannot hold the UI event loop open, and shutdown waits at most three seconds for shared-resource cleanup.
- Provider limits remain authoritative and credential-safe: `/usage` and the TUI sidebar use Z.ai's official monitor endpoint for 5-hour, weekly, and MCP Coding Plan quota windows, make no local quota estimates, and never send credentials to a custom API host.
- Terminal authentication must never echo or log `ZAI_API_KEY`; environment credentials take precedence over the user-only stored credential file.
- Push-to-talk voice uses local Whisper exclusively: audio is captured via `arecord`/`afrecord`, transcribed on-device with `faster-whisper` (base model, 74 MB), never sent to any API, and the transcribed text is appended to the composer for review before sending; `GLM_ACP_WHISPER_MODEL` selects the model size.
- Notification sounds are opt-in and bounded: `GLM_ACP_SOUND=1` enables a terminal bell on turn completion or failure with a 5-second cooldown, suppressed during voice recording to prevent feedback loops; the default is off.
- Desktop notifications are smart and rate-limited: `GLM_ACP_NOTIFY=0` disables them; otherwise they fire only for turns exceeding 10 seconds, at most once per 30 seconds, via `notify-send` (Linux), `osascript` (macOS), or PowerShell (Windows).
- Agent learning is inspectable, permission-gated, secret-safe, and reversible: facts/skills stay project-local, while explicitly approved user preferences use private cross-project storage.
- Advanced learning remains evidence-gated: failed traces may produce drafts, but candidates require higher held-out pass rate with no per-case, median-latency, or token-cost regression and explicit promotion; delegation is read-only, depth-one, shared-budgeted, and permission-gated.
- Coding reliability prioritizes progressively scoped repository instructions, edit-fresh verification evidence, persistent judged goals and acceptance criteria, post-write syntax/semantic diagnostics, unchanged-read deduplication, opt-in reference-model aggregation, and result-aware loop stops.
- Awareness remains bounded and inspectable: typed epistemic records cite only harness-issued metadata evidence; relevant edits invalidate support; active contradictions and unsupported criteria block persistent-goal completion before the auxiliary judge; no chain-of-thought is stored.
- Metacognitive control remains deterministic and advisory: uncertainty classes and execution modes derive from inspectable runtime facts; aggregate capability profiles are metadata-only and profile-isolated; empirical history may raise assurance but never expand permissions, authorize workers, change trusted policy, or store reasoning.
- Grounded deliberation remains evidence-only and bounded: an isolated thinking-disabled critic receives only objectives, a credential-redacted diff, fresh harness evidence, hypothesis outcomes, and completion metadata; ambiguous diagnosis uses two or three falsifiable alternatives with fresh evidence-backed tests; value-of-information ranking is advisory and cannot bypass permissions or policy.
- Repository intelligence remains lazy and metadata-bounded: it never snapshots source bodies, direct small tasks incur no world-model overhead, pre-edit impact predictions freeze at mutation, high-risk pre-mortems are inspectable, and observed impact never substitutes for fresh verification.
- Safe metacognitive learning remains causal, inert by default, and evaluation-gated: fixed strategy drafts require two evidence-backed attributions, explicit promotion, gains on fresh time-split and transformed cases, and no quality, safety, calibration, latency, token, or small-task-overthinking regression; promotion never changes trusted authority.
- Advanced execution remains declarative and fail-closed: checkpoints are conflict-aware, secret-safe, and profile-configurable within hard bounds; context references are bounded and language-ranked; policy rules inspect nested workflow steps; worker promotion is verification- and digest-gated with transactional rollback; profiles isolate user state; plugin packages are permission-scoped, data-only, hash-pinned, and optionally require trusted Ed25519 publishers.
- Quality evidence remains private and reproducible: failure drafts contain metadata only until explicitly promoted into outcome-based cases; local observability never stores bodies or raw identities; fuzzing and fault injection run offline and deterministically.

## Project Purpose

Native GLM ACP is an open-source ACP-native coding agent runtime for Z.ai GLM models. It is a standalone Python package that Zed (or any ACP-compatible editor) launches as a subprocess over stdio. It wraps the Z.ai GLM Coding Plan API directly — not the generic openai_compatible wrapper — to unlock GLM's 1M context window, live reasoning traces, and long-running generation without stalls.

- Language: Python 3.10+
- Transport: ACP over stdio (JSON-RPC 2.0)
- APIs: Z.ai Coding Plan, Standard API, and BigModel (CN)
- Models: GLM-5.2, GLM-5-Turbo, GLM-4.7, GLM-5V-Turbo, GLM-4.5V, and GLM-4.6V according to the selected API plan
- Entry points: `glm-acp`, `python3 -m glm_acp`, and the frozen `native-glm-acp` executable; append `chat` for the standalone terminal frontend while bare invocation remains ACP stdio

## Current Project Status

- Package and ACP implementation version is `2.7.9` from `glm_acp.__version__`.
- GitHub release `v2.7.9` publishes the five supported frozen binaries, checksums, provenance attestations, Python distributions, Registry metadata, the icon, checksum-verifying Unix and Windows installers, and safe one-command uninstall support. **Cold-start performance wave:** `glm_acp.cli`, `glm_acp.terminal_cli`, `glm_acp.cron_cli`, and `glm_acp.plugin_cli` defer all heavy chains (`acp`/`acp.schema`, `glm_acp.agent`, `httpx`, `croniter`, `cryptography`, `rich`) to the code paths that actually use them, so `--version`, `--setup`, `--check-auth`, `--uninstall`, and the `cron`/`plugin`/`observe`/`harden`/`meta-*` commands no longer pay their startup cost; `glm-acp --version` cold start dropped from ~0.82 s to ~0.16 s (about 5× faster). `tests/test_cli.py::test_cli_module_does_not_eagerly_import_heavy_dependencies` guards against regressions. **TUI command palette:** the full-screen Textual frontend enables Textual's built-in `Ctrl+P` command palette (`NativeGlmTui.ENABLE_COMMAND_PALETTE = True`) and registers `GlmCommandProvider` on `NativeGlmTui.COMMANDS` so the palette surfaces every `/`-command and F-key action alongside Textual's system commands; slash commands insert into the composer for review (the user can add arguments and press Enter) while F-key actions run immediately via `call_later`. `tests/test_tui.py::test_tui_command_palette_is_enabled_and_provider_surfaces_all_commands` guards the wiring. **`/recap`:** `GlmAcpAgent.generate_recap(session_id)` produces a one-line session summary via the configured auxiliary GLM, falling back to a local first-user-turn heuristic when the auxiliary model is the default, the session is empty, or the auxiliary call fails; transcript input is wrapped with `wrap_untrusted_output` so recalled/session content cannot issue promptware against the summarizer. Wired into both the TUI (`/recap`) and the plain REPL. `tests/test_agent.py::TestSessionRecap` (6 tests) covers empty session, default-aux fallback, non-default aux success + usage accounting, aux failure fallback, unknown session id, and multi-block content extraction. **`/blocks`:** the `CodeBlockPickerScreen(ModalScreen)` modal lists fenced code blocks extracted from the last ~20 agent responses (language, line count, first-line preview); Enter copies the selected block to the clipboard, `w` writes it to a timestamped file in the workspace, Esc cancels. `tests/test_tui.py::test_tui_blocks_picker_extracts_code_blocks_and_lists_them` and `test_tui_blocks_command_routes_to_picker` guard the wiring. **`/statusline`:** `StatusLineScreen(ModalScreen)` exposes 9 toggleable sidebar segments (state, session id, model, API plan, mode/permissions, context, tokens, awareness, quota); Save persists the choice atomically to `config_dir()/statusline.json` (mirrors the `max-iterations.json` pattern) and `_refresh_session_panel` only renders enabled segments — defaults to all visible, corrupt or missing config falls back to all visible. `tests/test_config.py::TestStatuslineConfig` (6 tests) covers default-on, round-trip, unknown-id rejection, empty-set fallback, corrupt-file fallback, wrong-schema fallback; `tests/test_tui.py::test_tui_statusline_command_opens_modal_and_persists` and `test_tui_refresh_session_panel_hides_disabled_segments` cover the wiring. **Agent-side slash commands now discoverable in the TUI:** the `LOCAL_COMMANDS` registry grew from 33 to 58 entries so the `/`-menu and the `Ctrl+P` palette surface every command the shared runtime already implements — `/compact [focus]` (Tier 2.3), `/goal [objective|clear|pause|resume]` and `/subgoal` (Tier 2.4), `/mcp` (Tier 2.6), plus `/status`, `/diff`, `/clear-plan`, `/clear-history`, `/checkpoint`, `/rollback`, `/plugins`, `/awareness`, `/metacognition`, `/deliberation`, `/repository`, `/meta-learning`, `/observability`, `/memory`, `/skills`, `/profile`, `/curator`, `/sessions`, `/lineage`, `/ci`, `/version`, and `/help`. Each surfaces in the menu with a one-line description; the underlying behavior is unchanged (the agent's command dispatcher already handled them). **`/context`:** `GlmAcpAgent.context_breakdown(session_id)` groups the live `session.messages` by role (system/user/assistant/tool) and estimates tokens per group using the same heuristic as `_estimate_tokens`; `ContextBudgetScreen(ModalScreen)` renders a horizontal bar chart with per-segment token counts, message counts, percent-of-window, total used / context-window size, and remaining capacity. Press `c` to dismiss and run `/compact` (which preserves the most recent turns and summarizes the rest). `tests/test_agent.py::TestContextBreakdown` (4 tests) covers unknown session, populated session grouping, empty session, and unknown-role collapse; `tests/test_tui.py::test_tui_context_command_routes_to_breakdown_screen` covers the wiring. **`/btw`:** `GlmAcpAgent.ask_btw(session_id, question)` answers a quick side question via the auxiliary GLM with a short recent-conversation context; the answer is **NOT** added to `session.messages` — the main conversation thread stays clean. Transcript input is wrapped with `wrap_untrusted_output`. `BtwOverlayScreen(ModalScreen)` provides an Input + answer display; `/btw <question>` pre-fills and fires the query on mount, `/btw` (bare) opens an empty overlay. Wired into both the TUI (`/btw`) and the plain REPL. `tests/test_agent.py::TestBtwSideQuestion` (5 tests) covers empty question, unknown session, default-aux setup hint, non-default aux success + usage accounting + session-untouched assertion, aux failure fallback; `tests/test_tui.py::test_tui_btw_command_routes_to_overlay_screen` covers both the bare and pre-filled slash-command paths. **`/theme`:** `/theme` opens Textual's built-in theme-search modal (16 themes ship with Textual 6.x: textual-dark, textual-light, textual-ansi, catppuccin-latte/mocha, dracula, flexoki, gruvbox, monokai, nord, rose-pine/dawn/moon, solarized-dark/light, tokyo-night). The chosen theme is persisted atomically to `config_dir()/theme.json` via `watch_theme` and re-applied on next startup by `on_mount`. The newer modal widgets (`StatusLineScreen`, `CodeBlockPickerScreen`, `ContextBudgetScreen`, `BtwOverlayScreen`, `SearchScreen`, `HistoryScreen`) use Textual design tokens (`$accent`, `$text`, `$background`, `$surface`) so they respond to the chosen theme; the legacy sidebar/transcript CSS keeps its hand-tuned dark palette as a v1 baseline. `tests/test_config.py::TestThemeConfig` (5 tests) covers default-on, round-trip, empty/blank fallback, corrupt-file fallback, wrong-schema fallback; `tests/test_tui.py::test_tui_theme_command_opens_picker_and_persists` covers the slash-command routing and the persistence-on-reactive-change behavior. **`/tasks`:** `TasksScreen(ModalScreen)` is a read-only session dashboard that surfaces the current turn state (Running/Idle + elapsed time + activity label), the FIFO prompt queue with previews, and the session's model/mode/permission/tokens/context-percentage/iteration-cap — consolidating scattered sidebar and queue-status info into one view. `tests/test_tui.py::test_tui_tasks_command_opens_dashboard_with_session_state` covers the slash-command routing and snapshot correctness. **`/insights`:** `GlmAcpAgent.generate_insights(session_id)` analyzes the session via the auxiliary GLM for friction points and improvement opportunities (2-4 bullet points, deeper than `/recap`); falls back to a local heuristic when no auxiliary model is configured. Wired into both TUI and plain REPL. `tests/test_agent.py::TestSessionInsights` (5 tests) covers unknown session, empty session, default-aux fallback, non-default aux success + usage accounting, aux failure fallback; `tests/test_tui.py::test_tui_insights_command_appends_to_transcript` covers the wiring. **`/release` discoverability:** added to `LOCAL_COMMANDS` (was already implemented in the agent's command dispatcher but invisible in the slash menu).
- ACP Registry publication is tracked in `agentclientprotocol/registry#439` and remains pending until Registry maintainers merge it.
- Source installs, the `glm-acp` console script, module execution, and frozen binaries share `cli.main()`.
- `glm-acp chat` and `native-glm-acp chat` open a cross-platform full-screen Textual interface over the full existing `GlmAcpAgent` runtime without an editor; a live `/` completion menu consumes the same available-command updates as Zed, puts API-plan/thinking/model controls first, and exposes every session setting through the shared APIs. Reasoning starts collapsed; F2 and `/reasoning-panel` toggle only its view, while `/thinking` changes the actual provider level. **F4 cycles a four-view working-tree panel** on the left side (session changes, git status, diff, file browser) sharing one screen location. **F5 toggles push-to-talk** — records via `arecord`, transcribes with local `faster-whisper` (base model, offline, free), and appends to the composer; faster-whisper is bundled in the frozen binary (156 MB on Linux). **F6 opens the session history browser** — lists persisted sessions for the current workspace (most-recent first), resumes the selected one through the shared agent runtime, and falls back to all sessions when the workspace is empty. **Ctrl-F (or `/search`) greps the live in-memory conversation** — a modal lists every message whose text matches the query with role, ordinal, and a context snippet, and selecting one shows the full message in the transcript. **`/export [md|json] [file|clip]`** writes the full current session as a self-contained Markdown transcript or JSON dump to clipboard (default) or a timestamped file in the workspace, with a clipboard→file fallback for transcripts over the one-million-character bound. **`/undo [N]`** takes back the last N user turns (default 1) and prefills the composer with the most recent removed user message so you can edit and resend — advertised in the ACP command catalog so Zed forwards it too. **`/prompt`** opens `$VISUAL`/`$EDITOR` on a tempfile so you can compose a long multi-line prompt in real Markdown, then queues it as the next message — comment lines starting with `#` are stripped. **`/journey`** opens a modal timeline of everything the agent has learned: skills (with creation timestamps, use counts, pinned/archived markers), project memory entries, and approved user-profile preferences, sorted most-recent-first. The composer stays **always enabled during active turns** — Enter **queues prompts** that auto-drain FIFO when each turn completes, with a visible queue-status line showing count and preview. The session sidebar shows a **live session token meter** (cumulative input ↑, output ↓, and cache hit %) sourced from real GLM API `usage` deltas alongside the compact **awareness indicator** (execution mode, evidence count, risk score, active contradictions). **Notification sounds** (opt-in via `GLM_ACP_SOUND=1`, terminal bell, 5s cooldown) and **smart desktop notifications** (only for turns >10 s, rate-limited to 1/30 s, `notify-send`/`osascript`/PowerShell) fire on turn complete/fail. Agent output is rendered as **structured Markdown** (headers, bullets, code blocks) with streaming-safe debounce. **Native mouse mode** (F7 or `/native-mouse`, or `GLM_ACP_NATIVE_MOUSE=1` at startup) releases Textual's mouse capture back to the terminal emulator so the terminal's own right-click context menu and click-drag text selection work natively — this is the Codex/Claude-Code approach: the terminal already knows how to copy/paste, the TUI just gets out of the way. While Textual mouse capture is on (default), hold Shift while dragging or right-clicking to bypass the app the same way. `Ctrl+Y` copies the last agent response to the OS clipboard; agent-message widgets use a `SelectableStatic` subclass so they are visible to in-app selection. The composer retains terminal-routed or Ctrl-V OS clipboard content as a usable single-line prompt and stays fully above the footer. A bounded, low-overhead activity line animates startup, thinking, reasoning, tool work, and cancellation, then reports approval/completion/failure/ready states without becoming runtime truth; `GLM_ACP_TUI_ANIMATION=0` disables motion. Compact conversation/activity/plan/status panels, credential-redacted modal approvals, F1 help, F3 settings, `/settings`, and `/clear-view` remain presentation-only while persistence, tools, MCP/browser integration, workers, learning, awareness, repository intelligence, checkpoints, and verification stay shared with ACP clients. `--plain`, `--prompt`, `--stdin`, and `--json` preserve line and automation surfaces.
- `/usage` works in ACP editors and terminal frontends; the TUI performs one non-blocking startup refresh and shows live provider-reported 5-hour, weekly, and monthly MCP quota percentages in the session sidebar, while an explicit `/usage` refresh displays available used/limit/remaining/reset details.
- Public frozen binaries support one-command removal of installer-owned commands, PATH markers, and matching custom Zed configuration with an automatic settings backup.
- ACP initialization advertises Registry-compatible `zai-api-key-setup` Terminal Auth.
- Terminal setup stores credentials atomically without echoing or logging the key; environment credentials take precedence.
- GitHub Actions tests Python 3.10–3.13 and packages Linux x86-64/ARM64, macOS Intel/Apple Silicon, and Windows x86-64 binaries.
- Official Z.ai Web Search, Web Reader, and optional local Vision MCP capabilities are exposed alongside configurable MCP servers. The Z.ai tool names are **asymmetric** and **must not be normalized**: the search endpoint exposes `web_search_prime` (snake_case) while the reader endpoint exposes `webReader` (camelCase) — both confirmed against the live API via `tools/list`. Calling either endpoint with the wrong-case name returns HTTP 200 with JSON-RPC error `Tool not found: <name>`, which masks the bug behind a successful HTTP status. The high-level presets exposed to the model are the case-insensitive `web_search` and `web_reader` (mapped to the correct Z.ai names inside `glm_acp/mcp.py:invoke_preset`).
- Root-to-target `.hermes.md`/Hermes, AGENTS, Claude, GLM, and Cursor instructions plus permission-gated `.glm-acp/memory.md` knowledge are progressively loaded into model context; direct writes defer when they first reveal closer rules.
- Successfully verified tasks receive one bounded learning review; approved reusable procedures are progressively loaded, usage-tracked, refinable, pinnable, reversibly archivable, and forgettable.
- Private user-profile memory and redacted FTS5 session recall provide cross-project and cross-session learning without indexing system prompts or reasoning traces.
- Promptware scanning blocks suspicious stored context and delimits tool, MCP, embedded-resource, and recalled output as untrusted data.
- Structured compaction preserves decisions, fixes, unresolved work, plan/edit/verification evidence, and memory proposals; it accepts an optional focus, scores summary quality over time, reports retained categories and pressure at 60%/75%/85%, and may use a configurable auxiliary GLM model.
- The auxiliary GLM path covers titles, compression, recall ranking, skill evaluation, and bounded workers. Workers provide permission-gated read-only investigation/review under shared token/tool budgets, strict iteration/time limits, and no recursive delegation. **Each delegated worker writes a live transcript file** under the profile-scoped config dir (`<config_dir>/workers/<session>-<uuid>.log`) — every model response, tool call (name + args), and tool result is timestamped and appended; the transcript path is returned with the worker report so the model and user can `tail -f` the worker as it runs and review it after completion. **`delegate_task(background=true)`** fans out up to `MAX_BACKGROUND_WORKERS_PER_SESSION` (default 3) workers per session — the call returns immediately with a worker-id status, and each completed report is delivered as a new session message when the worker finishes (Hermes v0.18 fan-out parity). Background workers use isolated budgets, share the same transcript sink, and are cancelled when the session is invalidated or the agent shuts down.
- ACP forks persist parent/root lineage, while relevant skill metadata, bundles, and benchmark-gated candidate promotion extend learning without automatic replacement.
- Project facts and canonical checks are auto-detected; edit-fresh verification evidence persists, and post-write Python/JSON/TOML syntax plus optional Python/TypeScript/Go/Rust LSP diagnostics feed the acting model.
- Persistent goals and subgoal acceptance criteria use a bounded auxiliary completion judge. Opt-in Mixture-of-Agents runs cached parallel reference reviews while the primary GLM remains the aggregator and sole actor.
- A typed epistemic ledger tracks observations, assumptions, hypotheses, contradictions, unknowns, and capability limits with provenance and scope-aware freshness. `/awareness` shows the state and completion certificate; metadata-only observability reports evidence coverage and prevented unsupported completions.
- A bounded metacognitive controller separates ambiguity, knowledge, diagnostic, capability, verification, and permission uncertainty; selects direct, grounded, deliberate, or high-assurance posture; and uses redacted outcome aggregates by task family and coarse environment to escalate weak historical cases without overthinking trivial work.
- Deliberate diagnosis generates two or three falsifiable hypotheses and tracks tests against fresh evidence IDs; a separately prompted auxiliary critic reviews only goals, bounded redacted diffs, fresh evidence, and completion metadata, while deterministic value-of-information ranking prioritizes the cheapest reliable allowed evidence action.
- Lazy repository intelligence combines bounded LSP/tool paths, imports, tests, manifests, instructions, CODEOWNERS, current changes, and project-matched failure classes; it predicts files/checks/packaging/platforms before edits, compares observed impact afterward, and adds deterministic pre-mortems only for high-risk work.
- Safe metacognitive learning attributes corrected failures to fixed cause/intervention classes and drafts allowlisted strategies without activating them; `meta-cases` and `meta-eval` enforce overall, fresh, transformed, per-case, safety, calibration, evidence, latency, token, and restraint gates before explicit promotion.
- Repeated identical tool batches, repeated failures, and unchanged read-only results are interrupted before the iteration ceiling; unchanged reads are deduplicated, malformed JSON arguments receive corrective feedback, and shell tools do not inherit common credential environment variables. The per-turn tool-call iteration cap defaults to 50, is overridable per session via the `/max-iterations [N]` slash command (routed through `set_config_option("max_tool_iterations", N)`, clamped to `[1, 1000]`), and is also settable at startup via the `GLM_ACP_MAX_TOOL_ITERATIONS` env var. `/max-iterations` works in **all three** frontends: it is advertised in the ACP command catalog (so Zed and other editors forward it to the agent), handled inside `_handle_command` for the ACP editor path, and exposed in both terminal frontends (TUI via `tui.py`, plain mode via `_handle_plain_command` in `terminal_cli.py`). The cap **persists across sessions**: `/max-iterations N` writes a 0600 atomic JSON file at `config_dir()/max-iterations.json` as the new user default, so a fresh `glm-acp chat` launch or a new editor session starts with the saved cap. Resolution precedence is **env var `GLM_ACP_MAX_TOOL_ITERATIONS` > persisted file > constant 50** — the env var wins so CI/scripts/one-off runs are never silently overridden by a stored preference.
- Installed language servers provide read-only semantic navigation, transactional hash-pinned multi-file patches commit all-or-nothing, and bounded batch reads reduce tool round trips without arbitrary code execution.
- Stable managed-prompt prefixes expose cache-hit ratios; metadata-only redacted trajectories and hash-pinned lifecycle hooks add evidence and policy without storing prompts, outputs, commands, reasoning, credentials, or raw session IDs.
- Permission-gated isolated Playwright MCP supplies accessibility, console, network, screenshot, and interaction evidence without arbitrary browser JavaScript evaluation or inherited credentials.
- Bounded secret-safe checkpoints precede workspace mutations only when auto-checkpoint is explicitly enabled (default **off** via `/checkpoint auto on` or `GLM_ACP_AUTO_CHECKPOINT=1`); compressed Git-compatible content-addressed objects deduplicate file bodies across projects, per-project history/age and a global ceiling prune automatically, large files are excluded, verified legacy copies can be migrated, and exact post-agent hashes make `/rollback` stop on later conflicts instead of overwriting them.
- Explicit `@file:`, `@folder:`, `@symbol:`, and `@diff` references stay workspace-contained, bounded, secret-aware, and delimited as untrusted context.
- Ordered repository policy rules, static dependency workflows, optional Bubblewrap isolation, detached worktree implementation workers, named user profiles, and permission-scoped hash-pinned data-only plugin packages provide safe extensibility without arbitrary orchestration code or automatic merges.
- Cross-platform containment capability-detects Linux Bubblewrap and macOS Seatbelt, adds Windows process-tree Job Objects without treating them as filesystem isolation, and keeps required mode fail-closed.
- Detached workers support exact-digest inspection, required isolated verification, conflict-aware transactional promotion, rollback-on-fault, and reviewed discard while preserving the worker after promotion.
- Explicit folder/symbol references spend their fixed budget on language-aware definitions, references, task terms, tests, manifests, and current changes.
- Metadata-only failure drafts can be permission-gated into runnable project-local regression cases; a local observability dashboard and deterministic offline hardening command expose reliability evidence without prompts, outputs, commands, paths, reasoning, credentials, or raw session IDs.
- Data-only plugin packages support explicit Ed25519 publisher trust, CLI-only private-key signing, signature enforcement policy, and exact manifest verification in addition to content hashes.
- Expired MCP HTTP sessions and restarted stdio servers reinitialize automatically with per-server initialization locking.
- The opt-in quality harness provides 11 outcome-based Python, TypeScript, Go, and Rust cases plus a credential-safe one-command runner with single-run locking, visible progress, and incremental JSON/Markdown handoff reports; live runs remain outside ordinary CI.
- Persistent scheduled automation supports relative one-shots, intervals, timezone-aware five-field cron, and aware ISO timestamps; permission-gated management, fresh non-persisted runs, skills/bundles, script prechecks, script-only mode, `[SILENT]`, renewable cross-process claims, and bounded redacted artifacts are available through ACP and `glm-acp cron`.

## Install and distribution (binding)

Source checkouts MUST install the package into the venv that Zed launches:

```bash
cd /path/to/glm-acp
uv pip install -e .
```

Without this, `python3 -m glm_acp` only resolves when run from this repo's
directory (Python puts the cwd on `sys.path`). Zed sets the subprocess cwd
to whatever project is open, so an uninstalled package crashes with
`ModuleNotFoundError` (exit 1) in any other repository. A bare `git clone`
is not enough for source-based launches. Public Registry installs use the
frozen `native-glm-acp` executable and do not require Python or a
repository-specific virtualenv.

Verify the install:

```bash
ls .venv/lib/*/site-packages/ | grep glm_acp
# expect: editable glm_acp metadata and glm_acp-2.7.9.dist-info
```

## Verification

```bash
uv sync --frozen --extra dev
uv run --frozen pytest tests/ -q
uv run --frozen pip-audit
uv build
uv run --frozen pyinstaller --noconfirm --clean --onefile --name native-glm-acp --collect-all acp glm_acp/launcher.py
dist/native-glm-acp --version
```

Before Registry submission, also run the official Registry schema builder and
authentication verifier against the published version-pinned archives.

## Child DOX Index

| Path | Purpose | Ownership |
|------|---------|-----------|
| `glm_acp/` | Python ACP agent, GLM client, tools, configuration, and CLI | Python implementation |
| `tests/` | Offline behavioral, security, packaging, and Registry verification | Python implementation |
| `registry/` | ACP Registry manifest template and icon | Release engineering |
| `.github/` | Cross-platform CI and release automation | Release engineering |
| `pyproject.toml` | Package metadata, dependencies, entry point, and build configuration | Python implementation |
| `uv.lock` | Reproducible dependency resolution | Python implementation |
| `README.md` | Installation, operation, security, and release guide | Project maintainers |
| `benchmarks/` | Opt-in native/external coding-agent quality evaluation | Quality engineering |
| `scripts/` | Runtime-free public installers for published frozen binaries | Release engineering |
