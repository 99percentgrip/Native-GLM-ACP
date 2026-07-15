# Tests

## Purpose

Own offline verification for ACP behavior, tools, persistence, packaging, authentication, and Registry assets.

## Ownership

- `test_agent.py` covers ACP lifecycle, capabilities, authentication, plans, and prompts.
- `test_cli.py` covers terminal setup and secret-safe status output.
- `test_config.py` covers models, endpoints, credential precedence, and secure persistence.
- `test_registry_package.py` covers public Registry identity and release URL invariants.
- `test_installers.py` covers the user-local command aliases, checksum enforcement, platform mapping, and release publication contract.
- `test_uninstall.py` covers frozen-copy guards, default and installer-selected profile cleanup, command/PATH removal, credential preservation and purge, and surgical Zed JSONC cleanup with backup.
- Remaining modules cover tools, sessions, streaming, compaction, and GLM HTTP behavior.
- `test_mcp.py` and `test_memory.py` cover remote MCP, scoped memory, verified skills, telemetry, pinning, curation, and reversible archival.
- `test_session_store.py` covers JSON persistence plus redacted FTS5 discovery, scrolling, legacy backfill, and deletion.
- `test_quality.py` covers tool-loop, failed-verification and unverified-edit recovery, benchmark locking/incremental reports, million-token estimation, and real stdio/SDK ACP process lifecycles.

## Local Contracts

- Tests must not require a real Z.ai API key or make live model requests.
- Secret tests must assert that credential values never appear in output.
- Registry tests must fail when source, manifest, and archive versions diverge.
- Platform-specific tests must skip when their operating-system semantics are unavailable.
- Streaming tests must cover incomplete HTTP 200 responses, retry boundaries,
  delta coalescing, continuation caps, and exact Deep High/Max request fields.
- Tool tests must cover output bounds and make command exit status observable.
- Persistence tests must verify metadata-sidecar listing and deletion behavior.
- Learning tests must cover verification gating, progressive skill discovery, secret rejection, workspace containment, and deletion limited to agent-owned skills.
- Learning lifecycle tests must cover profile privacy, exact forgetting, usage/revision telemetry, pinned protection, stale/archive thresholds, and restoration.
- Curator tests must cover manual content drift and evidence-only overlap detection without automatic merging.
- Session-search tests must prove system/reasoning/credential exclusion and legacy-session coverage.
- Process-level ACP tests must use test credentials, isolated HOME state, and the official SDK lifecycle helpers where available.

## Work Guidance

- Use temporary directories and monkeypatched environment variables for credentials and state.
- Keep network behavior deterministic with existing HTTP mocks.

## Verification

- Run `.venv/bin/python3 -m pytest tests/ -q`.

## Child DOX Index

No children.
