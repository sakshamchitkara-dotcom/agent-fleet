# Changelog

All notable changes to agent-fleet. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-09-25

### Added
- **OS jails for the non-Docker sandbox**: `sandbox-exec` on macOS (no network, writes
  confined to the worktree and a private `TMPDIR`), bubblewrap on Linux (read-only root, no
  network, worktree bind) or `unshare -rn` (no network). Each jail is probed once and the
  strongest one that starts is used.
- Sandbox **isolation level** in every agent's start event, `fleet status <id>` and the
  dashboard (in red when network or writes are not confined).
- **Codebase index tools** for every role: `repo_map` (file tree + top-level symbols with
  line numbers; Python via `ast`, JS/TS, Go, Rust, Ruby, Java, Kotlin, C and shell via
  regex) and `search_symbols`.
- **Cost tracking**: per-request USD estimate from the model's published prices (fresh
  input, cache writes at 1.25x, cache reads, output), per agent and per task, live in
  `fleet status`, `fleet logs`, the dashboard and the PR footer.
- `--max-cost USD`: hard task budget; no agent takes another turn once it is reached and
  the task fails with a budget error.
- **Resumability**: agents checkpoint their conversation after every turn; a restarted
  orchestrator requeues interrupted tasks without charging an attempt, reuses their base
  commit, worktrees and branches, replays finished agents and resumes interrupted ones.
- `fleet bench`: five seeded-bug toy repos (`paginate`, `median`, `inventory`, `duration`,
  `bank`) with scripted solutions; reports pass rate, turns, tokens and cost, verifies each
  branch in a clean checkout with the tests untouched. Claude when a key is present,
  scripted otherwise; `--min-pass`, `--json`, `--cases`.
- `fleet watch owner/repo --label fleet`: polls an owned repo for open labeled issues;
  dry-run unless `--apply`, PRs only with `--pr`, skips issues that already have a task.
- PR descriptions include a per-agent **trajectory summary** (outcome, turns, tool calls,
  tokens, cost, steps and reports) next to the test output.
- CI runs `fleet bench --backend scripted --min-pass 1` and exercises the bubblewrap jail.

### Changed
- `fleet retry` starts from scratch; the previous run is kept in
  `runs/<id>/attempt-<ts>/`.
- Worktrees are kept after a crash (so a retry can resume) and removed once a task
  succeeds or fails for good.
- The example scripts orient with `repo_map` / `search_symbols`.

## [0.1.0] - 2026-09-25

### Added
- Planner, parallel reviewed workers and integrator on per-agent git worktrees.
- Claude Opus 5.5 backend (adaptive thinking, streaming, prompt caching, eager input
  streaming) and a deterministic scripted backend.
- SQLite queue, concurrent orchestrator with retries, `fleet` CLI, live dashboard, JSONL
  trajectories, task-wide token budget.
- Docker sandbox (no network, per-task image) with a subprocess fallback.
- GitHub issue import and owner-only PR opening.

[0.2.0]: https://github.com/sakshamchitkara-dotcom/agent-fleet/compare/fe5b508...main
[0.1.0]: https://github.com/sakshamchitkara-dotcom/agent-fleet/tree/fe5b508
