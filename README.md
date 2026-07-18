# Native GLM ACP

[![CI](https://github.com/99percentgrip/Native-GLM-5.2-Provider/actions/workflows/ci.yml/badge.svg)](https://github.com/99percentgrip/Native-GLM-5.2-Provider/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/99percentgrip/Native-GLM-5.2-Provider)](https://github.com/99percentgrip/Native-GLM-5.2-Provider/releases)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

A native Agent Client Protocol (ACP) server for Z.ai GLM models. Runs as a
subprocess inside Zed's Agent Panel — no Zed recompilation required.

## Features

### Core

- **GLM-5.2 1M context window** — model-aware compaction uses documented limits for every model
- **Live reasoning traces** — `reasoning_content` streams into Zed's thinking view
- **No mid-generation stalls** — auto-continues on `finish_reason=length`
- **Usage reporting** — live token usage in Zed's context bar
- **Structured context compaction** — preserves plans, edits, verification evidence, and an optional user focus
- **Context-pressure diagnostics** — one-time 60%/75%/85% warnings explain when compaction approaches
- **Persistent session lineage** — conversations survive restarts; forks retain parent/root rollback paths
- **Persistent goals and criteria** — `/goal` plus `/subgoal` continue across restarts and use a bounded auxiliary completion judge
- **Multi-root workspaces** — full support for additional workspace directories
- **Progressive repository rules** — scoped AGENTS, Claude, Hermes, and Cursor instructions load before affected edits
- **Evidence-led verification** — canonical project checks are recorded with scope and invalidated by later edits
- **Post-write diagnostics** — syntax checks always run for supported formats; installed language servers add semantic diagnostics
- **Optional Mixture of Agents** — two independent GLM references advise the acting model once per user turn
- **Measured learning loop** — relevant skills, bundles, and benchmark-gated candidates improve without silent promotion
- **Promptware defense** — stored context and untrusted tool/MCP/recall output are scanned and delimited
- **Bounded delegation** — permission-gated read-only GLM workers investigate or review with strict budgets
- **Persistent scheduled automation** — one-shot, interval, timezone-aware cron, and ISO jobs run in isolated sessions with durable history
- **Semantic code navigation** — installed language servers provide symbols, definitions, references, hover types, implementations, rename preparation, and call hierarchy
- **Transactional multi-file patches** — content hashes, pre-commit syntax checks, and rollback keep coordinated refactors all-or-nothing
- **Context-efficient batch reads** — bounded concurrent file/search operations return one reduced JSON result
- **Cache-aware prompt layout** — volatile context stays behind a stable prefix; `/status` reports its hash and cache-hit ratio
- **Redacted trajectory telemetry** — metadata-only events support tuning without prompts, outputs, commands, reasoning, credentials, or raw session IDs
- **Playwright UI testing** — permission-gated isolated browser automation returns accessibility, console, network, interaction, and screenshot evidence
- **Lifecycle hooks** — user-owned hash-pinned hooks can block tools, observe results, or request bounded pre-verification follow-up
- **Conflict-aware checkpoints** — automatic pre-mutation snapshots and `/rollback` restore only exact agent-produced hashes
- **Explicit context references** — bounded `@file:`, `@folder:`, `@symbol:`, and `@diff` expansion is workspace-contained and secret-aware
- **Declarative policy and workflows** — ordered allow/ask/deny rules and static DAGs add control without arbitrary orchestration code
- **Optional OS sandboxing** — Bubblewrap isolates command filesystems on Linux; required mode fails closed when unavailable
- **Worktree implementation workers** — opt-in editing workers operate in detached locked worktrees and return unmerged diffs
- **Isolated profiles** — named profiles separate credentials, sessions, telemetry, hooks, cron jobs, plugins, and user memory
- **Hash-pinned plugin packages** — permission-scoped data-only packages reject executable content and hash drift

### API Resilience

- **Automatic retry** — honors `Retry-After`, then uses capped jittered backoff on transient failures
- **Cost and cache tracking** — cumulative input/output/cached tokens per session, shown in `/status`
- **Real cancellation** — the Cancel button actually aborts in-flight API requests
- **Token estimation** — calibrated 3.5 chars/token heuristic, handles vision content blocks
- **Tool-loop recovery** — repeated identical tool batches are interrupted with corrective feedback
- **Result-aware loop recovery** — repeated failures and unchanged read-only results warn, then halt boundedly
- **Unchanged-read deduplication** — repeated identical file/search results are replaced by a compact content fingerprint
- **MCP recovery** — expired HTTP sessions and restarted stdio servers reinitialize automatically

### Chat Dropdown Config Options

All configurable from the Zed agent panel — no restart needed:

| Option | Values | Description |
|---|---|---|
| **Model** | GLM-5.2, GLM-5-Turbo, GLM-4.7 (+ GLM-5V-Turbo and vision models on Standard/BigModel) | Model list syncs to the selected API plan |
| **Reasoning** | Off, Standard, Deep · High, Deep · Max | Deep levels are GLM-5.2 exclusive |
| **API Plan** | Coding Plan, Standard API, BigModel (CN) | Switch endpoints; vision models appear on Standard/BigModel |
| **Permissions** | Ask, Read Only, Bypass | Gate destructive tools (write/edit/run) |
| **Generation Style** | Balanced, Precise, Exploratory | Provider defaults, lower-temperature precision, or broader nucleus sampling |
| **Auxiliary Model** | Main model, GLM-5.2, GLM-5-Turbo, GLM-4.7 | Optional model for titles, compaction, recall ranking, skill evaluation, and delegated analysis |
| **Mixture of Agents** | Off, Reference review | Optionally run up to two independent non-vision GLM references; the acting model aggregates their private advice |

### Slash Commands

Type these in the chat input:

| Command | Description |
|---|---|
| `/compact [focus]` | Trigger structured compaction, optionally prioritizing a topic |
| `/clear-plan` | Clear the current task plan / todo list |
| `/clear-history` | Wipe conversation history (keeps settings) |
| `/diff` | Show git diff of all uncommitted changes |
| `/export` | Export the conversation as a Markdown file |
| `/status` | Show model, project facts, goal, fresh verification evidence, context usage, and cost |
| `/memory` | Show approved durable project facts |
| `/skills` | List skills learned from verified project work |
| `/profile` | Show the active isolated profile and its approved private preferences |
| `/curator` | Show skill usage, stale/archive candidates, and lifecycle state |
| `/sessions [words]` | Browse or search persisted conversations |
| `/lineage` | Show the current session's parent, branch root, and direct children |
| `/goal [objective\|pause\|resume\|clear]` | Set, inspect, pause, resume, or clear a persistent coding goal |
| `/subgoal [criterion\|remove N\|clear]` | Manage persistent acceptance criteria for the active goal |
| `/checkpoint [label\|list]` | Create or list a bounded secret-safe workspace checkpoint |
| `/rollback [checkpoint-id]` | Restore recorded agent changes unless a later conflicting edit is detected |
| `/plugins` | List installed declarative plugins and their integrity state |

### Task Plans

For any task with 3+ steps, the model automatically creates a live todo list
visible as a checklist in the panel. Each task shows pending / in-progress /
completed status with priority indicators.

### Safe orchestration, rollback, and isolation

Before the first workspace-mutating tool in each user turn, Native GLM ACP
captures a bounded checkpoint outside the repository. Common credential,
private-key, SSH, and `.env` files are never copied. After each mutation, the
checkpoint records the exact resulting hashes. `/rollback` restores only those
paths; if any current hash differs, rollback stops without overwriting the later
change. Use `/checkpoint list` to inspect recent checkpoints.

Prompts may explicitly include `@file:path`, `@folder:path`, `@symbol:name`, or
`@diff`. Expansion stays inside workspace roots, has file/character limits,
omits common secret files, and enters model context inside an untrusted-data
boundary.

Repositories may define `.glm-acp/policy.json` with ordered rules:

```json
{
  "version": 1,
  "rules": [
    {"effect": "deny", "tools": ["run_command"], "command_regex": "rm\\s", "reason": "No shell removal"},
    {"effect": "ask", "tools": ["run_workflow", "worktree_worker"]}
  ]
}
```

Invalid policies fail closed. Policy `allow` never bypasses the session's
permission mode, Read Only remains absolute, and workflow rules are also
evaluated against every nested step. `run_workflow` accepts at most 12
static dependency-ordered steps from the existing tool allowlist; it cannot
generate steps or execute orchestration code.

Command OS isolation is opt-in with `GLM_ACP_OS_SANDBOX=auto` or fail-closed
with `GLM_ACP_OS_SANDBOX=required`. On Linux, Bubblewrap exposes system runtime
files read-only, mounts only declared workspace roots writable, hides the user
home, and can disable networking with `GLM_ACP_SANDBOX_NETWORK=0`. Unsupported
platforms retain the existing workspace path sandbox in `auto` mode and reject
commands in `required` mode. Worktree implementation workers always request
required OS isolation with networking disabled for their command tools, preserve
dirty worktrees, and never merge automatically.

Set `GLM_ACP_PROFILE=client-a` before launch to isolate user state under a
validated named profile. `default` preserves all legacy paths. Plugin packages
are installed from a workspace `plugin.json` through the permission-gated
`plugin_package` tool. Manifests declare `schema: 1`, an id, permission scopes,
and SHA-256 for every data file; only JSON, Markdown, TOML, and YAML are allowed.
The installed manifest receives its own integrity pin. Executable plugin code is
intentionally unsupported.

### Scheduled Automation

Native GLM ACP includes Hermes-style persistent cron jobs. The agent can create,
list, update, pause, resume, run, and remove them through one permission-gated
`cronjob` tool. Scheduled runs use a fresh, non-persisted conversation, load the
target project's instructions and optional learned skills/bundles, and cannot
create more scheduled jobs recursively.

Supported schedules are relative one-shots (`30m` or `in 30m`), recurring
intervals (`every 2h`), strict five-field cron expressions, and timezone-aware
ISO timestamps. Named timezones use their daylight-saving rules. Missed
recurring slots collapse to one run instead of producing a catch-up storm.

```bash
# Create and inspect jobs
glm-acp cron create --schedule "0 9 * * 1-5" --timezone Asia/Manila \
  --workdir /path/to/project --prompt "Review failures and report only new findings"
glm-acp cron list
glm-acp cron status

# Manage or test one job
glm-acp cron update JOB_ID --schedule "every 2h"
glm-acp cron pause JOB_ID
glm-acp cron resume JOB_ID
glm-acp cron run JOB_ID
glm-acp cron remove JOB_ID

# Script precheck, or script-only monitoring without an API call
glm-acp cron create --schedule "every 10m" --workdir /path/to/project \
  --script scripts/healthcheck.py --no-agent
```

The scheduler runs automatically while a Native GLM ACP process is active.
For a dedicated long-lived process, use `glm-acp cron daemon`; `cron tick` runs
one due-job scan for external service managers. Cross-process locks and renewable
claims prevent duplicate execution. A crashed claim becomes recoverable, while
long healthy runs refresh ownership. Jobs may return `[SILENT]` to suppress live
delivery; every result still receives a bounded, redacted local artifact.

Job prompts reject credential-shaped and promptware content. Workdirs and scripts
must remain inside the recorded workspace, precheck scripts receive a scrubbed
environment and a 60-second limit, agent runs default to a 600-second inactivity
limit (`GLM_ACP_CRON_TIMEOUT=0` disables it), and running jobs cannot be updated
or removed. State is stored with user-only permissions in the platform's Native
GLM ACP configuration directory. This plugin intentionally delivers to local ACP
sessions and artifacts; Hermes messaging-channel routing is outside an editor
ACP server's scope.

### Project Context

The managed prompt detects the project root, manifests, package managers, git
branch/dirty state, and repository-defined verification commands. Facts refresh
after edits instead of relying on model guesses.

- **Languages:** Python, JavaScript/TypeScript, Rust, Go, Ruby, Java
- **Frameworks:** Next.js, React, Vue
- **Package managers:** uv, Poetry, npm, Yarn, pnpm
- **VCS:** git detection
- **Instructions:** `.hermes.md`, `HERMES.md`, `AGENTS.md`, `CLAUDE.md`, `GLM.md`, `.cursorrules`, and `.cursor/rules/*.mdc` are discovered from the project root toward accessed paths; newly applicable scoped rules are loaded before mutation
- **Memory:** approved reusable facts can be stored in `.glm-acp/memory.md`
- **Learned skills:** concise verified procedures are indexed from `.glm-acp/skills/*/SKILL.md` and loaded only when relevant
- **Relevance gates:** optional platform, project-environment, and required-tool metadata keeps unrelated skills out of context
- **Skill bundles:** related learned procedures can be progressively loaded as one bounded bundle
- **User profile:** explicitly approved preferences are stored privately in the user configuration directory and loaded across projects
- **Past work:** local FTS5 search recalls relevant user/assistant messages without indexing reasoning traces or credential-like values
- **Untrusted boundaries:** project context is scanned before prompt injection; tool, MCP, resource, and recalled text is explicitly delimited as data

### Coding Reliability

Every successful write is read back immediately. Python, JSON, and TOML receive
deterministic syntax checks; Python, JavaScript/TypeScript, Go, and Rust also use
`pyright-langserver`, `typescript-language-server`, `gopls`, or `rust-analyzer`
when that executable is already on `PATH`. Missing or failed language servers do
not block the syntax fallback and are never installed automatically.

Verification evidence is project-scoped and persistent. Only commands matching
auto-detected canonical checks are ledgered; output text such as `echo pytest
passed`, non-executing flags, and status-masking pipelines, fallbacks, or command
sequences cannot satisfy the edit guard. Each later edit invalidates prior passing evidence,
and `/status` exposes the latest event plus whether a fresh pass exists.

Repeated unchanged file/search results are fingerprinted instead of re-injected.
The result-aware loop guard separately tracks identical failures, same-tool
failure streaks, and unchanged read-only outcomes, warning before a bounded stop.
Mixture of Agents is opt-in because it spends extra API tokens: reference reviews
run in parallel once per user turn, while the selected primary model remains the
only acting model and aggregates the private advice.

### Verified Learning

After a task changes files and passes a recognized test, build, lint, audit, or
verification command, the agent performs one learning review. It may propose a
concise reusable `SKILL.md` only when the task revealed a non-obvious procedure
or corrected pitfall likely to recur. Creating, refining, or removing a learned
skill uses the normal ACP permission dialog in Ask mode.

Learned skills are project-local under `.glm-acp/skills/`. Their metadata is
included in project context, while full instructions are loaded on demand to
avoid wasting tokens. Reading and refining skills records usage and revision
metadata. A deterministic curator marks unused skills stale after 30 days and
reversibly archives unpinned skills after 90 days; pinning is enforced in code.
Curation mutations use the normal permission dialog and never auto-delete.
Content hashes flag manually changed skills for review, and cheap description
overlap detection surfaces consolidation candidates without auto-merging them.

The model surveys existing skills before creating one and prefers refining a
skill used during the successful task. Credential-like content is rejected,
raw reasoning is never stored, and `forget_skill` can remove only agent-owned
learned skills—not user-authored `.agents` or `.codex` skills.

Skill refinement can also use an evaluator-gated candidate workflow. `evolve_skill`
can first create a non-promotable draft directly from bounded failed benchmark
traces. Promotion then requires compatible completed held-out reports, a higher
pass rate, no per-case regression, no worse median latency, and no increase in
input-plus-output token cost. Passing creates a separate integrity-checked
candidate file; promotion still uses the normal permission gate and is never
automatic. An auxiliary model may provide an advisory critique, but cannot
override the objective gates or replace the original rollback point.

Skill metadata may declare `environments: [python, git]`, `platforms: [linux]`,
`requires_tools: [run_command]`, or semantic `tasks: [security review]` tags.
Task-tagged skills enter the managed prompt only when the current request matches.
Irrelevant skills remain inspectable but are not loaded. Project-local bundles
group up to 12 relevant learned skills and an optional shared instruction without
duplicating their bodies.

Private cross-project facts are stored only after permission in the platform's
GLM ACP configuration directory as `user.md`. Project facts remain in
`.glm-acp/memory.md`. Both support exact, explicit forgetting.

### Search Quality

`search_files` and `grep` use ripgrep when available, respect `.gitignore`, bound
their output, and always skip
`.git`, `node_modules`, `__pycache__`, `.venv`, `dist`, `build`.

### Z.ai MCP

The agent includes stable tools for the official Coding Plan Web Search and Web
Reader remote MCP services. It also supports the local Z.ai Vision MCP server
and arbitrary user-configured Streamable HTTP or stdio MCP servers.

Vision MCP is optional and requires Node.js 22+ with `npx` on `PATH`; the first
call asks for permission before starting `@z_ai/mcp-server@latest`. Custom MCP
servers can be added to the user-only `mcp.json` beside `credentials.json`:

```json
{
  "servers": {
    "docs": {
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer ${DOCS_MCP_TOKEN}"}
    }
  }
}
```

Keep credentials in environment variables, not in this file. The built-in Z.ai
servers reuse the existing API key without printing or persisting it.

## Install

### One-command release install

Linux and macOS:

```bash
curl -fsSL https://github.com/99percentgrip/Native-GLM-5.2-Provider/releases/latest/download/install.sh | sh
glm-acp --setup
```

Windows PowerShell:

```powershell
$installer = Join-Path $env:TEMP "install-glm-acp.ps1"
Invoke-WebRequest https://github.com/99percentgrip/Native-GLM-5.2-Provider/releases/latest/download/install.ps1 -OutFile $installer
& $installer
Remove-Item $installer
glm-acp --setup
```

The installers select the correct frozen binary, verify its published SHA-256
checksum, install without administrator privileges, and expose both `glm-acp`
and `native-glm-acp`. No Python or Node.js runtime is required. Open a new
terminal after installation if `glm-acp` is not immediately found.

To pin a release, set `GLM_ACP_VERSION=v1.2.0` before running the Unix
installer, or pass `-Version v1.2.0` to the downloaded PowerShell script.
The current release and manual-download fallback is
[v1.2.0](https://github.com/99percentgrip/Native-GLM-5.2-Provider/releases/tag/v1.2.0).

The setup prompts without echoing the API key and stores it in a user-only
configuration file. You can also keep using `ZAI_API_KEY` or `Z_AI_API_KEY`;
environment variables take precedence over stored credentials.

Default credential locations:

- Linux: `~/.config/glm-acp/credentials.json`
- macOS: `~/Library/Application Support/glm-acp/credentials.json`
- Windows: `%APPDATA%\glm-acp\credentials.json`

Set `GLM_ACP_CONFIG_DIR` to override the configuration directory. The key is
never printed or written to logs.

### One-command uninstall

For a public frozen-binary installation, run:

```bash
glm-acp --uninstall
```

This removes both installed command names, the installer-owned PATH entry, and
a matching custom `glm-acp` entry from Zed settings. Zed settings are backed up
before editing. Restart Zed afterward.

Credentials are preserved so reinstalling does not require entering the API key
again. To remove the stored credential too, use:

```bash
glm-acp --uninstall --purge
```

The command refuses to self-delete source installations and Registry-managed
copies. Remove Registry installations through Zed; remove development packages
with the package manager that installed them.

Configure the installed command as a custom Zed agent. ACP Registry publication
is tracked in
[agentclientprotocol/registry#439](https://github.com/agentclientprotocol/registry/pull/439);
Registry installation becomes public after the Registry maintainers merge it.

### Development installation

The agent must be **installed into its virtualenv** so the `glm_acp`
Python module resolves regardless of which directory Zed launches the
subprocess from. A bare `git clone` is not enough — Zed sets the
subprocess `cwd` to whatever project you have open, and without an
install `python -m glm_acp` will fail with `ModuleNotFoundError` (exit
code 1) in any repo other than this one.

```bash
cd /path/to/glm-acp
uv pip install -e .
```

> ⚠️ If the agent crashes on startup in other repos with no visible
> error, re-run the install command above — the package is missing from
> the venv's `site-packages`.

Get your API key at https://z.ai/

## Configure Zed

Open Zed → Settings → Agent Settings → Add Agent → Add Custom Agent, then
add to `settings.json`:

```json
{
  "agent_servers": {
    "glm-acp": {
      "type": "custom",
      "command": "glm-acp",
      "args": []
    }
  }
}
```

Restart Zed, open the Agent Panel, and select **Z.ai GLM** from the agent
dropdown. If Zed was already open during installation and does not inherit the
updated PATH, restart it or use the absolute installed command (`~/.local/bin/glm-acp`
on Linux and macOS).

If you use the development installation instead, keep the Python command,
`-m glm_acp` argument, working directory, and optional `ZAI_API_KEY` environment
entry from the earlier setup style.

## Models

| Model | Context | Plans | Use case |
|---|---|---|---|
| GLM-5.2 (Flagship) | 1M | All | Maximum reasoning, coding, agentic tasks (default) |
| GLM-5-Turbo | 200K | All | Flagship optimized for speed |
| GLM-4.7 | 200K | All | Balanced daily development |
| GLM-5V-Turbo | 200K | Standard, BigModel | Multimodal coding and agent workflows |
| GLM-4.5V (Vision) | 64K | Standard, BigModel | Screenshots, diagrams, charts |
| GLM-4.6V (Vision) | 128K | Standard, BigModel | Newer vision model with improved OCR |

## API Plans

| Plan | Endpoint | Notes |
|---|---|---|
| Coding Plan (default) | `api.z.ai/api/coding/paas/v4` | Subscription — text models only |
| Standard API | `api.z.ai/api/paas/v4` | Pay-as-you-go — text + vision models |
| BigModel (CN) | `open.bigmodel.cn/api/paas/v4` | Chinese mainland endpoint |

Direct vision model support requires Standard API or BigModel with sufficient
balance. Coding Plan users can use the separate official Vision MCP capability.

Z.ai's published supported-tool list does not currently name this Zed ACP
integration. Coding Plan availability is therefore subject to Z.ai's current
tool eligibility and usage policy; Standard API remains available independently.

## Architecture

```
glm_acp/
├── __main__.py      # Module entry point — routes through cli.main()
├── cli.py           # Console entry point and terminal credential setup
├── cron.py          # Persistent jobs, schedule parsing, claims, and artifacts
├── cron_cli.py      # Cron management CLI
├── cron_scheduler.py # Isolated execution, delivery, ticking, and daemon
├── launcher.py      # Frozen-executable entry point
├── agent.py         # ACP agent: session lifecycle, prompt loop, slash commands
├── config.py        # Model registry, API endpoints, constants
├── glm_client.py    # Streaming Z.ai API client (SSE, retry, reasoning, tools)
├── mcp.py           # Z.ai and user-configured MCP transports
├── memory.py        # Memory, relevant skills/bundles, and evaluated candidates
├── project_context.py # Progressive instructions and detected project facts
├── verification.py # Persistent, edit-fresh verification evidence ledger
├── diagnostics.py  # Syntax checks and optional LSP semantic diagnostics
├── guardrails.py    # Result-aware repeated-failure/no-progress detection
├── security.py      # Promptware scanning and untrusted-context delimiters
├── session_store.py # Persistent JSON session storage (~/.glm-acp/sessions/)
└── tools.py         # File/shell/search tools sandboxed to workspace roots
```

### Token flow

```
Z.ai API ──SSE──> glm_client.py ──callbacks──> agent.py ──session_update──> Zed
  │                    │                                       │
  │ reasoning_content  │ on_reasoning()                        │ agent_thought_chunk
  │ content            │ on_content()                          │ agent_message_chunk
  │ tool_calls         │ on_tool_call_started()                │ tool_call / tool_call_update
  │ usage              │ StreamResult.usage                    │ usage_update (context bar)
  │                    │                                       │
  │                    │ ◄── summarize_messages() ─────────────┘ (compaction)
```

### Context compaction

At 60%, 75%, and 85% usage the agent emits a single pressure diagnostic. When
usage exceeds **85%** of the context window:

1. System prompt preserved verbatim
2. 4 most recent messages kept (boundary adjusted to never split tool-call pairs)
3. Decisions, fixes, edited paths, command outcomes, unresolved work, plan state, and session lineage extracted deterministically
4. Older messages summarized via the selected auxiliary model, with optional `/compact <focus>` guidance
5. Verified decisions/fixes surfaced as permission-required memory proposals
6. Summary wrapped in `<conversation_summary>` tags with exact retained evidence
7. A persisted quality score is compared with prior compactions; declines trigger a warning and retained categories are reported

### Session persistence

- State saved to `~/.glm-acp/sessions/<session-id>.json` after every turn
- Session directories/files are created with user-only permissions on POSIX
- On restart, `load_session` / `resume_session` replays history + plan + config
- Sessions listed in Zed's history sidebar via `session/list`
- Fork support: duplicate a session to experiment while persisting parent and branch-root lineage
- `/lineage` identifies direct children and the parent session to resume for rollback
- Goals, subgoal acceptance criteria, judge budget, Mixture-of-Agents selection, and verification evidence persist with the session
- Closing an ACP session releases runtime resources without deleting searchable history
- A user-only SQLite FTS5 index enables recent-session browsing, keyword search, and contextual scrolling
- System prompts and `reasoning_content` are excluded from search; credential-like values are redacted before indexing

Set `GLM_ACP_SESSION_PERSISTENCE=0` to keep sessions process-local. Set
`GLM_ACP_PERSIST_REASONING=0` to persist messages without exact reasoning
traces; active Coding Plan turns still retain them in memory as required for
preserved-thinking requests.

## Tools

| Tool | Description |
|---|---|
| `read_file` | Read file contents (with optional line range) |
| `write_file` | Create or overwrite a file |
| `edit_file` | Find-and-replace a unique text block |
| `apply_patch` | Atomically apply validated unified-diff hunks |
| `apply_patch_set` | Transactionally apply hash-pinned hunks across up to 20 files |
| `list_directory` | List directory entries |
| `search_files` | Glob pattern search (`.gitignore`-aware) |
| `grep` | Regex content search (`.gitignore`-aware) |
| `batch_read` | Concurrently run and reduce up to 20 read/list/search operations |
| `semantic_code` | Query installed LSP servers for symbols and semantic navigation |
| `run_command` | Live, bounded shell output; timeouts kill the process tree; inherited credentials are removed |
| `update_plan` | Create/update the task plan checklist |
| `recall_memory` / `store_memory` | Read or permission-gate durable project knowledge |
| `recall_user_profile` / `store_user_profile` | Read or permission-gate private cross-project preferences |
| `forget_memory` | Remove an exact approved project or user-memory entry |
| `session_search` | Browse/search previous sessions and scroll around a matching message |
| `list_skills` / `read_skill` | Discover and progressively load learned project procedures |
| `learn_skill` / `forget_skill` | Permission-gate verified learning and removal of agent-owned skills |
| `manage_skill` / `curate_skills` | Pin, archive, restore, and maintain learned skills without automatic deletion |
| `list_skill_bundles` / `read_skill_bundle` | Discover and progressively load related relevant skills |
| `manage_skill_bundle` | Permission-gate creation or removal of project-local bundles |
| `evolve_skill` | Stage, promote, or discard objectively benchmarked skill candidates |
| `delegate_task` | Run one permission-gated, read-only auxiliary GLM investigation or review |
| `cronjob` | Permission-gated persistent scheduled automation and manual runs |
| `web_search` / `web_reader` | Official Z.ai Coding Plan MCP services |
| `vision_analyze` | Optional official local Z.ai Vision MCP |
| `browser_ui` | Permission-gated Playwright MCP inspection and interaction |
| `mcp_list_tools` / `mcp_call` | Generic configured MCP access |

After changing files, the agent requires a successful auto-detected canonical
build, test, lint, or other verification command before normal completion. A failed command or an
attempt to finish without verification triggers one focused recovery turn.
After successful verification of an edited task, one bounded learning review
decides whether a reusable skill is warranted; routine work stores nothing.

All file paths are validated against workspace roots. `update_plan` is
available in both Ask and Code modes.

Delegated workers cannot edit files, execute commands, call MCP, access
credentials, or delegate again, fixing delegation depth at one. Each worker is
limited to six read/search iterations and 180 seconds. One parent turn shares a
three-worker, 24-tool-call, 120K-input-token, and 16K-output-token budget across
all delegates. Their API usage is added to the parent session totals. Promptware
scanning is defense in depth: destructive operations still rely on ACP
permissions and workspace sandboxing.

### Local trajectory telemetry and lifecycle hooks

Metadata-only trajectory events are appended with user-only permissions to
`trajectory.jsonl` in the Native GLM ACP configuration directory. Set
`GLM_ACP_TELEMETRY=0` to disable them. Records include model/tool names,
durations, token counts, cache hits, changed-path counts, and verification state;
they exclude prompts, tool arguments and bodies, commands, reasoning, credentials,
and raw session IDs.

Lifecycle hooks are opt-in through user-only `hooks.json` (or
`GLM_ACP_HOOKS_CONFIG`). Each entry declares an event, an argv-form command, the
exact SHA-256 of its executable, an optional exact workspace scope, and a timeout
of at most 10 seconds. Supported events are `pre_tool_call`, `post_tool_call`,
`pre_verify`, and `post_llm_call`. Hash drift disables a hook, failures are
isolated, child environments are credential-scrubbed, and pre-verification
continuations are capped at three.

Playwright uses the optional `playwright` stdio MCP preset
(`npx -y @playwright/mcp@latest --headless --isolated`). Starting or calling it
is permission-gated, inherited credentials are removed, and the stable adapter
does not expose Playwright MCP's arbitrary JavaScript evaluation tools.

## Testing

```bash
cd /path/to/glm-acp
.venv/bin/python3 -m pytest tests/ -v
```

Release verification also builds the wheel, source distribution, and frozen
PyInstaller executable. GitHub Actions runs the test suite and frozen-binary
smoke test on Linux x86-64/ARM64, macOS Intel/Apple Silicon, and Windows x86-64.

Opt-in live quality evaluation uses isolated fixtures and objective test results:

```bash
# Safe preflight: validates the catalog and credential without an API request.
.venv/bin/python3 benchmarks/run_live.py --check

# Full handoff run: three attempts for each of the 11 cases.
.venv/bin/python3 benchmarks/run_live.py
```

The runner creates a timestamped folder under `quality/` containing
`native.json` and `report.md`. Send both files to the reviewer. The files never
contain the API key, reasoning traces, temporary workspaces, authentication
paths, or session IDs. Configure the credential with `glm-acp --setup` or in
`ZAI_API_KEY`; never put the key itself in the command line.

Only one live benchmark can run from a checkout at a time. Each attempt prints
its case, attempt number, outcome, and elapsed time. Both artifacts are updated
atomically after every completed attempt, so cancellation preserves usable
partial results and a stale lock is recovered automatically on the next run.

The catalog covers Python, TypeScript, Go, and Rust tasks including multi-file
changes, async cleanup, nested instructions, path security, and CLI behavior.
An external agent can run the same corpus with `--runner external
--external-command <command...>`. Generate a comparison table with:

```bash
.venv/bin/python3 benchmarks/report.py quality/native.json quality/competitor.json \
  --output quality/report.md
```

Reports contain outcome, end-to-end and first-delta latency, token totals,
candidate version/model, a system-prompt fingerprint, and non-identifying
runtime details. They exclude credentials, reasoning traces, authentication
paths, and session IDs. The manually triggered **Quality
benchmark** GitHub workflow provides an opt-in native run and job-summary
dashboard; it never runs live API usage on ordinary CI or pull requests.

## Troubleshooting

### Agent crashes on startup in other repos (exit 1, no error)

The most common cause: **the `glm_acp` package is not installed in the
virtualenv.** Python only finds it when run from this repo's directory
(the cwd is on `sys.path`), so opening any other project in Zed makes
`python -m glm_acp` exit with `ModuleNotFoundError`. Fix:

```bash
cd /path/to/glm-acp
uv pip install -e .
```

You can confirm it's installed by checking for the editable finder:

```bash
ls .venv/lib/*/site-packages/ | grep glm_acp
# expect: glm_acp-1.2.0.dist-info  (and editable-install metadata)
```

### Agent reports missing API credentials

Run `native-glm-acp --setup` (or `glm-acp --setup` for a Python install), or
set `ZAI_API_KEY` in the agent server's `env` block. Get a key at https://z.ai/.

### Vision models return content filter errors

The Coding Plan endpoint applies a strict content filter on image inputs.
Switch to **Standard API** in the API Plan dropdown and ensure you have
balance on your Z.ai account.

### Rate limit errors (429)

The agent automatically retries with provider-directed or jittered backoff (3 attempts). If
errors persist, the model will receive the error and can inform the user.

## License

Apache-2.0. Copyright 2025 Aleksejs Kozlitins.

## Author

Created and maintained by **Aleksejs Kozlitins**.
