# Changelog

All notable changes to agent-fleet. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [0.4.0] - 2026-09-25

### Added
- **Repository conventions**: a root `AGENTS.md` and/or `CLAUDE.md` (read from the task's base
  commit, symlinks and duplicates once, capped at 20k characters) is appended to every
  agent's system prompt.
- **Dashboard access token**: `fleet serve` requires a token (random per start, `--token` or
  `FLEET_DASHBOARD_TOKEN`; `--no-auth` to disable). The printed link sets an `HttpOnly`,
  `SameSite=Strict` cookie; API clients can send `Authorization: Bearer`.

### Fixed
- `--test-first` counted pre-existing failures as a red regression test when `--test-cmd`
  named test files itself (`node --test tests/a.test.ts`, `pytest tests/`): those paths are
  now swapped for the new test files. Commands that can't be narrowed (`npm test`, `make`,
  `go test ./...`, pipelines) only get a tester when the suite passes on base.

## [0.3.0] - 2026-09-25

### Added
- `--test-first`: a **tester** agent commits a regression test on each worker's branch
  before the fix. It is kept only if it touches test files alone and fails on the base
  code; workers are told to make it pass without changing it, and a worker that edits it
  gets it restored and is sent back for another round without spending a review.
- **Human approval before PRs**: `--pr --await-approval` stops successful tasks in
  `awaiting_approval`; `fleet approve <id>` opens the PR, `fleet reject <id> --reason`
  declines it. The dashboard shows the diff with Approve / Reject buttons (same-origin JSON
  POSTs only). Approval is an atomic status transition, so it opens at most one PR.
- `--provider bedrock|vertex`: Claude through the SDK's `AnthropicBedrockMantle` and
  `AnthropicVertex` clients.
- `fleet --prices FILE` / `FLEET_PRICES`: custom $/MTok rates (Bedrock, Vertex, negotiated)
  that take precedence over list prices.
- Benchmark cases in **JavaScript** (`wordcount-js`, `node --test`) and **TypeScript**
  (`lastn-ts`, node's type stripping); `fleet bench --test-first`, `--provider`.
- CI sets up node 22 and also runs the bench with `--test-first`.

### Changed
- The Claude backend puts an explicit cache breakpoint on the system block, so the tools +
  system prefix is shared by every agent of a role, revision rounds and post-compaction
  contexts; the top-level breakpoint still caches the conversation.
- Compaction sends the agent's own next request plus a notes instruction, so the history
  is a cache read instead of a fresh-input transcript; falls back to the transcript summary
  if the model only calls tools.
- `repo_map` / `search_symbols` cache parsed files by path, mtime and size.
- `fleet bench` accepts new test files on a branch and still rejects edits to seeded tests.
- `fleet logs` and the dashboard show what each compaction cost.

### Fixed
- The compaction summary call was not priced; it is now charged to the agent and the task
  (and counts toward `--max-cost`).
- Bedrock / Vertex model IDs (`anthropic.` and region prefixes, `-v1:0`, `@date`) fell
  through to the default model's price.
- A price file did not apply to requests without a model ID (the scripted backend).

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

[0.3.0]: https://github.com/sakshamchitkara-dotcom/agent-fleet/compare/0d0d12e...main
[0.2.0]: https://github.com/sakshamchitkara-dotcom/agent-fleet/compare/fe5b508...0d0d12e
[0.1.0]: https://github.com/sakshamchitkara-dotcom/agent-fleet/tree/fe5b508
