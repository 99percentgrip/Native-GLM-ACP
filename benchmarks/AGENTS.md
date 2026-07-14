# Benchmarks

## Purpose

Own opt-in, reproducible coding-agent quality evaluation without placing live API usage in CI.

## Ownership

- `eval.py` runs native or external agents against isolated task fixtures and emits JSON results.
- `cases.json` defines prompts, fixture files, verification commands, and time limits.
- `report.py` combines compatible JSON results into a Markdown leaderboard.
- `run_live.py` performs credential-safe preflight and creates handoff-ready native JSON and Markdown artifacts.

## Local Contracts

- Live native runs require the user's existing Z.ai credential configuration.
- Benchmark output must never contain API keys, authentication paths, reasoning traces, or session IDs.
- Cases must verify observable repository outcomes rather than judge prose subjectively.
- External runners receive the task prompt over stdin and the isolated workspace as their cwd.
- Reports record a schema version, candidate label, pass rate, end-to-end and first-delta latency, token totals, native prompt fingerprint, and non-identifying runtime details.
- The live runner permits one process per checkout, recovers stale locks, prints per-attempt progress, and preserves atomic partial JSON and Markdown reports after cancellation.
- Missing non-Python runtimes skip only their affected cases and remain visible in report totals.

## Work Guidance

- Keep default cases small enough for routine release comparisons.
- Add cases for confirmed quality regressions and important tool workflows.

## Verification

- Run `.venv/bin/python3 benchmarks/eval.py --list`.
- Run `.venv/bin/python3 benchmarks/eval.py --validate` after changing cases.
- Run `.venv/bin/python3 benchmarks/run_live.py --check` before spending live API tokens.
- Run `.venv/bin/python3 benchmarks/report.py <report.json> [<report.json> ...]` after comparable runs.

## Child DOX Index

No children.
