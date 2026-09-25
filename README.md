# agent-fleet

A fleet of autonomous coding agents, Devin-style. Give it a task or a GitHub issue; it
clones the repo, reads the code, plans, splits the work across parallel agents in separate
git worktrees, edits, runs tests, gets every change reviewed, merges the results, runs the
full suite, and (on repos you own) opens a PR.

- **Claude Opus 5.5** (`claude-opus-5-5`) via the `anthropic` SDK with tool use, adaptive
  thinking, streaming and prompt caching (a cached tools + system prefix shared by every
  agent of a role), on the Claude API, **Amazon Bedrock** or **Google Vertex AI**
  (`--provider`)
- **Roles**: planner, parallel workers, reviewer, integrator, and with `--test-first` a
  **tester** that commits a failing regression test before each fix
- **Human in the loop**: `--await-approval` holds the PR until you approve it with
  `fleet approve` or the dashboard's Approve button
- **Sandboxed**: every command runs in a network-less Docker container, or without Docker
  in an OS jail (`sandbox-exec` on macOS, bubblewrap / `unshare` on Linux) that blocks the
  network and confines writes to the agent's git worktree; the isolation level in force is
  shown in `fleet status` and the dashboard
- **Codebase index**: `repo_map` (file tree + top-level symbols, Python via `ast`, other
  languages via regex) and `search_symbols`, so agents orient in one or two calls
- **Cost tracking**: per-agent tokens and estimated USD (compaction calls included) from the
  model's published prices or your own (`--prices` for Bedrock, Vertex or negotiated
  rates), live in status and dashboard, with a hard `--max-cost` stop
- **Resumable**: every agent checkpoints its conversation after each turn; tasks survive an
  orchestrator crash or restart and continue where they stopped
- **Queue + orchestrator**: SQLite task queue, concurrency limit, retries, status CLI and a
  live web dashboard (stdlib `http.server`)
- **Every step logged** as JSONL trajectories
- **Offline scripted backend** that replays deterministic tool calls, so the whole pipeline
  runs and is tested without an API key
- **Benchmark**: `fleet bench` runs seven seeded-bug toy repos (Python, JavaScript and
  TypeScript with `node --test`) and reports pass rate, turns, tokens and cost (scripted in
  CI, Claude when credentials are present)
- **GitHub**: `fleet issue owner/repo#N --pr`, and `fleet watch owner/repo --label fleet`
  to pick up labeled issues (dry-run by default); PRs carry a trajectory summary and the
  test output

## Architecture

```mermaid
flowchart LR
    CLI["fleet CLI<br/>run / issue / submit"] -->|enqueue| Q[(SQLite queue)]
    GH["GitHub issue<br/>(gh api)"] --> CLI
    WATCH["fleet watch owner/repo<br/>--label fleet"] -->|"labeled issues on an owned repo,<br/>dry-run unless told to apply"| CLI
    BENCH["fleet bench<br/>7 seeded-bug repos<br/>Python, JS, TS"] -->|enqueue, then verify<br/>in a clean checkout| Q
    Q -->|claim, concurrency limit, retries| O[Orchestrator]
    O --> P

    subgraph P["Pipeline (one task)"]
        direction LR
        PL["Planner<br/>read-only"] -->|1-4 subtasks| T1["Tester 1<br/>--test-first:<br/>red regression test"]
        T1 --> W1["Worker 1<br/>worktree fleet/T-w1"]
        PL --> W2["Worker 2<br/>worktree fleet/T-w2"]
        W1 <-->|diff + tests / feedback| R1[Reviewer]
        W2 <-->|diff + tests / feedback| R2[Reviewer]
        R1 -->|approved| I["Integrator<br/>merge, resolve conflicts,<br/>full test run"]
        R2 -->|approved| I
    end

    P -->|every agent turn| T[("JSONL trajectories<br/>+ checkpoints")]
    T -->|restart: resume from last checkpoint| O
    P -->|usage x price| M["Cost meter<br/>hard --max-cost stop"]
    T --> D["Dashboard<br/>cost, isolation, agents"]
    Q --> D
    I -->|branch fleet/T| B{--pr and repo<br/>owned by you?}
    B -->|yes| AP{--await-approval?}
    AP -->|no| PR["Push + gh pr create<br/>trajectory summary + test output"]
    AP -->|"yes: fleet approve<br/>or dashboard button"| PR
    AP -->|fleet reject| L
    B -->|no| L[Stays local]
```

Each agent is the same loop with a different role (system prompt, tool allowlist, `finish`
schema):

```mermaid
sequenceDiagram
    participant A as Agent loop
    participant M as Model (Claude / scripted)
    participant T as Toolbox (worktree jail)
    participant S as Sandbox (docker / sandbox-exec / bwrap)
    A->>A: resume from checkpoint if a previous run was cut off
    loop until finish or budget spent
        A->>A: stop if the task's token or --max-cost budget is spent
        A->>M: system + append-only history + tools
        M-->>A: thinking + text + tool_use blocks
        A->>A: price the usage, validate each input against its schema
        A->>T: repo_map / search_symbols / read_file / apply_patch ...
        T->>S: run_command / run_tests
        S-->>T: output, exit code (denylist, timeout, no network)
        T-->>A: results, all in one user message
        A->>A: log to JSONL, checkpoint, compact when the context grows
    end
```

| Module | What it does |
|---|---|
| `sandbox.py` | Denylist check, then Docker (`--network none`, only the worktree mounted, memory/CPU/pid limits) or a subprocess in the strongest OS jail available (`sandbox-exec` / `bwrap` / `unshare`), secrets scrubbed from env, process-group timeout, capped output |
| `tools.py` | `repo_map`, `search_symbols`, `read_file`, `list_dir`, `grep`, `write_file`, `apply_patch` (exact unique search/replace), `run_command`, `run_tests`, `git_diff`; paths that escape the worktree (incl. symlinks) or touch `.git` are refused |
| `index.py` | Repo map and symbol search: Python via `ast`, JS/TS, Go, Rust, Ruby, Java, Kotlin, C, shell via regex |
| `agent.py` | Tool-use loop, turn + token budgets, task-wide token/USD meter, schema validation of tool inputs, checkpoints and resume, compaction |
| `pricing.py` | Per-model $/MTok table, Bedrock/Vertex ID mapping, `--prices` overrides and per-request cost (fresh input, cache writes, cache reads, output) |
| `models.py` | `ClaudeModel` (Opus 5.5 on the Claude API, Bedrock or Vertex) and `ScriptedModel` (offline replay) |
| `roles.py` | Planner / tester / worker / reviewer / integrator prompts, tool allowlists and `finish` schemas |
| `pipeline.py` | plan -> (regression test) -> parallel reviewed workers -> integrate, all in git worktrees |
| `queue.py`, `orchestrator.py` | SQLite queue with atomic claims and retries; concurrent task runner; approve / reject |
| `github.py` | Issue import, labeled-issue polling, ownership guard, push + PR with trajectory summary |
| `bench/` | Seven seeded-bug repos (5 Python, 1 JavaScript, 1 TypeScript) with scripted solutions and the scoring harness behind `fleet bench` |
| `dashboard.py` | Live task list, per-agent trajectory panels, diff + Approve / Reject for waiting tasks |

## Quickstart

```sh
pip install -e '.[dev]'

# offline demo: no API key, fixes the two seeded bugs in examples/sample-repo
examples/demo.sh

# real run with Claude on a local repo (never pushes)
export ANTHROPIC_API_KEY=...
fleet run ~/code/myproject "Make the date parser accept ISO week dates" --test-cmd "python -m pytest -q"

# work on a GitHub issue; --pr pushes and opens a PR, only if you own the repo
fleet issue you/yourrepo#12 --pr

# pick up issues labeled "fleet" on a repo you own: prints what it would do...
fleet watch you/yourrepo --label fleet --once
# ...and only runs them (and opens PRs) when asked
fleet watch you/yourrepo --label fleet --apply --pr --max-cost 5

# regression test first, and nothing is pushed until you approve
fleet issue you/yourrepo#12 --test-first --pr --await-approval
fleet approve <id>      # opens the PR (or click Approve in `fleet serve`); fleet reject <id>

# Claude on Bedrock or Vertex, priced at your rates
AWS_REGION=us-east-1 fleet --prices my-rates.json run ~/code/myproject "..." --provider bedrock

fleet bench             # 7 seeded-bug repos: pass rate, turns, tokens, cost
fleet status            # all tasks, with estimated cost
fleet status <id>       # one task: sandbox isolation, subtasks, verdicts, diffstat, tokens, cost, PR
fleet logs <id> -f      # stream every agent's trajectory
fleet serve             # dashboard on http://127.0.0.1:8765
```

Queue now, run later: `fleet submit <repo> "<task>"` then `fleet worker --concurrency 3`
(add `--forever` to keep polling). `fleet cancel <id>` / `fleet retry <id>` manage tasks.

Useful options: `--max-workers` (parallel workers per task), `--review-rounds`,
`--max-turns` (per agent), `--task-tokens` (shared by all agents of a task),
`--max-cost USD` (hard stop on the task's estimated API cost),
`--effort` (`high` by default; Opus 5.5 would otherwise default to `medium`),
`--test-first` (tester writes a failing regression test before each fix),
`--await-approval` (with `--pr`: wait for `fleet approve`),
`--provider anthropic|bedrock|vertex`, `fleet --prices FILE` (or `FLEET_PRICES`),
`--sandbox auto|docker|subprocess`, `--image` (Docker image with your project's test deps),
`--max-attempts` (retries on crashes). State lives in `~/.fleet` (override with `--home` or
`FLEET_HOME`).

## Safety model

- **Local by default.** Nothing is pushed unless you pass `--pr`; with `--await-approval`
  nothing is pushed until a human approves the finished change (`fleet approve` or the
  dashboard, whose POST endpoints accept only same-origin JSON requests, so another web page
  cannot approve for you). A second approval cannot open a second PR.
- **Owned repos only.** Before any push the authenticated `gh` login must equal the repo
  owner, the API must report that owner with admin permission, and the clone's `origin` must
  be exactly `https://github.com/<owner>/<repo>`. Anything else raises `NotOwnedError`;
  `fleet issue <someone-else's repo> --pr` is refused before a single token is spent.
  Branches are pushed without `--force`.
- **Isolated workspaces.** Agents only ever touch their own worktree on a `fleet/*` branch;
  your checkout is never modified. Merged worker branches are deleted afterwards; rejected
  ones are kept for inspection.
- **Sandboxed commands.** With Docker (auto-detected) every command runs in a throwaway
  container with no network, only the worktree mounted, non-root uid, memory/CPU/pid
  limits. The default image (`agent-fleet-sandbox:py3.12`, slim + pytest) is built locally
  on first use. Without Docker, commands run as a subprocess with `cwd` in the worktree,
  secret-looking env vars (`*KEY*`, `*TOKEN*`, ...) removed, a hard timeout that kills the
  whole process group, and the strongest jail that starts on the host (each is probed once):

  | Jail | Network | Writes | Where |
  |---|---|---|---|
  | `docker` | none | only the mounted worktree | Docker available |
  | `sandbox-exec` | denied (unix sockets allowed) | worktree + private `TMPDIR` | macOS |
  | `bwrap` | own empty net namespace | worktree + private `TMPDIR`, read-only root | Linux with bubblewrap and user namespaces |
  | `unshare` | own empty net namespace | **not confined** | Linux, no bubblewrap |
  | `none` | **not confined** | **not confined** | anything else |

  The level in force is logged in every agent's start event and shown by `fleet status <id>`
  and the dashboard (in red when something is not confined). The denylist (pushes, remote
  edits, `gh`, `sudo`, deletes outside the workspace, pipe-to-shell, disk/system commands)
  applies in every mode; it is a speed bump, not a security boundary.
- **Budgets.** Turn and token limits per agent, a token budget per task, and `--max-cost`:
  once the task's estimated spend reaches it, no agent takes another turn.
- **Least privilege per role.** Planner and reviewer cannot edit files; nobody can reach
  `.git` through the file tools. The tester's commit is kept only if it touches test files
  alone, and a worker that edits that regression test gets it restored and is sent back.

## Claude integration

`ClaudeModel` calls `client.messages.stream(...)` with `model="claude-opus-5-5"`,
`thinking={"type": "adaptive"}`, `output_config={"effort": "high"}`, two cache
breakpoints - an explicit one on the system block (tools render before system, so every
agent of the same role, every revision round and every post-compaction context reads the
same tools + system entry) and top-level `cache_control` for the growing conversation -
and `eager_input_streaming` on every
tool (inputs are validated against the schema before running, and calls cut off by
`max_tokens` are never executed). `tool_choice` is left at `auto` - forced tool choice is
rejected by Opus 5.5.

Opus 5.5 ties thinking blocks to the conversation, so the history is **append-only**:
response content, thinking blocks included, is replayed verbatim. When a request's input
passes `compact_at` (150k tokens by default) the agent does *simple compaction*: it sends
its own next request (same model, system, tools, effort and history) with one appended
instruction to write working notes, so the history is a cache read rather than fresh
input; the agent then continues in a fresh context that starts from those notes, without
replaying any earlier thinking. The summary call is priced and charged like any turn. `refusal` stop reasons end the agent cleanly;
400/401/403/404 API errors fail the task without burning retries, while 429/5xx are retried.

`--provider bedrock` uses the SDK's `AnthropicBedrockMantle` client (`AWS_REGION` and the
usual AWS credentials; model IDs get the `anthropic.` prefix, inference profiles such as
`us.anthropic.claude-opus-5-5` pass through), `--provider vertex` uses `AnthropicVertex`
(`ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION`, application default credentials). The
request is the same on all three.

## Scripted backend

A script maps agent names (or role names) to turns; each turn is a list of tool calls or
text blocks:

```json
{
  "planner":  [[{"name": "finish", "input": {"summary": "...", "subtasks": [{"title": "Fix add", "description": "..."}]}}]],
  "worker-1": [[{"text": "root cause is in add()"},
                {"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "a + b"}}],
               [{"name": "run_tests", "input": {}}]],
  "reviewer": [[{"name": "finish", "input": {"verdict": "approve", "feedback": "lgtm"}}]]
}
```

Lookup is exact name first (`worker-1`, `worker-1.r2` for a revision, `integrator-1`), then
the role. An exhausted script calls `finish` with schema-valid defaults (a reviewer then
requests changes, never approves). See `examples/scripts/`.

## Trajectories

`~/.fleet/runs/<task>/<agent>.jsonl`, one JSON object per line:

```json
{"ts": 1790325106.9, "agent": "worker-1", "event": "start", "model": "claude-opus-5-5", "sandbox": "subprocess", "isolation": "sandbox-exec: no network, writes confined to the worktree", "tools": ["repo_map", "..."], "task": "..."}
{"ts": 1790325107.2, "agent": "worker-1", "event": "model", "turn": 2, "stop_reason": "tool_use", "usage": {"input_tokens": 1843, "output_tokens": 61, "cache_read_input_tokens": 1500, "cache_creation_input_tokens": 300}, "cost": 0.00316, "content": [...]}
{"ts": 1790325107.4, "agent": "worker-1", "event": "tool", "name": "apply_patch", "input": {...}, "is_error": false, "output": "patched shop/pricing.py"}
{"ts": 1790325111.0, "agent": "worker-1", "event": "resume", "turn": 2, "tokens": 2210, "cost": 0.0121}
{"ts": 1790325112.0, "agent": "worker-1", "event": "end", "status": "finished", "turns": 4, "tokens": 3179, "cost": 0.0163, "output": {...}}
```

Next to each trajectory, `<agent>.ckpt.json` holds the agent's full conversation (thinking
signatures included) as of its last completed turn; it is deleted when the agent ends.

## Cost and budgets

Each model call is priced from its usage with the rates in `pricing.py`, taken from the
Claude API model table (Anthropic first-party rates, USD per million tokens):

| Model | Input | Output | Cache read |
|---|---:|---:|---:|
| `claude-opus-5-5` (default) | 4.00 | 20.00 | 0.20 |
| `claude-opus-5`, `claude-opus-4-8` / `4-7` / `4-6` | 5.00 | 25.00 | 0.50 |
| `claude-fable-5-1` | 10.00 | 50.00 | 0.25 |
| `claude-sonnet-5` | 2.00 | 10.00 | 0.20 |
| `claude-haiku-4-5` | 1.00 | 5.00 | 0.10 |

Cache writes are billed at 1.25x input (the 5-minute TTL the Claude backend requests).
Compaction summary calls are priced like any other turn. Bedrock and Vertex model IDs
(`anthropic.` / `us.anthropic.` prefixes, `-v1:0` and `@date` suffixes) map to their
first-party row. Partner and negotiated rates differ, so pass your own with
`fleet --prices rates.json` (or `FLEET_PRICES=rates.json`); cache read defaults to 0.1x and
cache write to 1.25x input when omitted:

```json
{"us.anthropic.claude-opus-5-5": {"input": 4.4, "output": 22, "cache_read": 0.44, "cache_write": 5.5}}
```

Entries match the exact model ID first, then the first-party ID; a malformed file is
rejected at startup.
Costs are shown per agent and per task in `fleet status`, `fleet logs`, the dashboard and the
PR footer. `--max-cost USD` is a hard stop checked before every model call, so a task
overshoots by at most one turn per running agent; the task then fails with
`task budget exhausted: <tokens>, $<spent> of $<limit>`. With the scripted backend the
"tokens" are a character-count estimate, so its costs are simulated.

## Resuming after a restart

Agents checkpoint after every turn. If the orchestrator dies (crash, `kill -9`, reboot),
the next `fleet worker` requeues the interrupted task without charging an attempt and the
pipeline picks up where it stopped: same base commit, surviving worktrees and branches
reused (uncommitted edits included), agents that had already ended are replayed from their
trajectory instead of re-run, and interrupted agents continue their conversation
append-only from the last checkpoint. Integration restarts from base, since merges are
cheap and deterministic. A turn that was in flight when the process died is generated
again. `fleet retry <id>` is different: it moves the old run to `runs/<id>/attempt-<ts>/`
and starts over.

## Benchmark

`fleet bench` seeds seven toy repos with known bugs, runs the fleet on each, and verifies
the result itself: the branch must pass the suite in a clean checkout outside the sandbox,
and the seeded tests must be untouched (new regression tests are allowed).

| Case | Bug |
|---|---|
| `paginate` | off-by-one slice end drops the last item of every page |
| `median` | even-length median returns the upper middle value |
| `inventory` | mutable default argument leaks state between calls |
| `duration` | wrong unit constant (a minute is 6 seconds) |
| `bank` | two independent bugs in two modules (overdraft allowed, percent used as a fraction) |
| `wordcount-js` | JavaScript (ES modules, `node --test`): words split on a single space |
| `lastn-ts` | TypeScript run by node's type stripping (node >= 22.18): `slice(-0)` returns everything |

It uses Claude when `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is set and the scripted
solutions otherwise (`--backend` overrides); `--min-pass 1` makes it a CI gate, `--json`
writes the results, `--max-cost` caps each case, `--test-first` adds the tester stage
(the `median` and `wordcount-js` scripts include one).

## Verification

`pytest -q` runs 135 tests (unit tests plus end-to-end pipeline, test-first, approval,
resume and bench runs with the scripted backend, and the Claude backend driven through the
agent loop with a fake SDK client; the Docker sandbox test is opt-in via
`FLEET_DOCKER_TESTS=1`). CI runs them on Python 3.10-3.13 with node 22 and bubblewrap
installed (and fails if the Linux jail is not `bwrap`), plus the Docker sandbox test, the
offline demo, `fleet bench --backend scripted --min-pass 1` and the same with
`--test-first`.

`fleet bench --backend scripted` (macOS, `sandbox-exec` jail, node 22.22):

```
bench: 7 case(s), scripted backend, state in /var/folders/.../fleet-bench-fpl3nzfg
CASE         RESULT TURNS   TOKENS      COST  NOTE
bank         PASS      13    4,411   $0.0267
duration     PASS       7    1,737   $0.0109
inventory    PASS       7    1,920   $0.0132
lastn-ts     PASS       7    2,047   $0.0129
median       PASS       7    1,815   $0.0119
paginate     PASS       7    1,841   $0.0116
wordcount-js PASS       7    2,210   $0.0140
pass rate 7/7 (100%), turns 55, tokens 15,981, cost $0.1013 [scripted backend; tokens and cost are simulated]
```

`fleet bench --backend scripted --test-first`: the tester adds 4 turns where its script has
one (`median`, `wordcount-js`); elsewhere the exhausted script writes nothing and the
worker starts from base as before:

```
CASE         RESULT TURNS   TOKENS      COST  NOTE
bank         PASS      15    4,661   $0.0288
duration     PASS       8    1,833   $0.0117
inventory    PASS       8    2,036   $0.0142
lastn-ts     PASS       8    2,185   $0.0140
median       PASS      11    3,771   $0.0228
paginate     PASS       8    1,953   $0.0125
wordcount-js PASS      11    4,758   $0.0271
pass rate 7/7 (100%), turns 69, tokens 21,197, cost $0.1312 [scripted backend; tokens and cost are simulated]
```

Custom rates reach every call, scripted ones included (`{"claude-opus-5-5": {"input": 4.4,
"output": 22}}`, i.e. list price + 10%):

```
$ fleet bench --backend scripted --cases median,wordcount-js | tail -1
pass rate 2/2 (100%), turns 14, tokens 4,024, cost $0.0259 [scripted backend; tokens and cost are simulated]
$ fleet --prices rates.json bench --backend scripted --cases median,wordcount-js | tail -1
pass rate 2/2 (100%), turns 14, tokens 4,024, cost $0.0285 [scripted backend; tokens and cost are simulated]
```

Symbol index cache: `search_symbols` over this checkout's `site-packages` (2,883 files)
took 1.40s cold and 0.04s warm.

Resume after `kill -9` of the orchestrator while `worker-1` was mid-task (scripted backend
with a slow command added to its script), then a plain `fleet worker`:

```
== after kill -9
ID        STATUS     STAGE                     TRY     AGE      COST  TASK
4d457633  running    working (2 subtasks)        1      4s   $0.0363  Fix the failing tests
== restart
task 4d457633: done (finished), attempts 1/2
sandbox: sandbox-exec: no network, writes confined to the worktree
branch: fleet/4d457633  tests: PASS
  - [approve] Fix apply_discount (1 round(s))
  - [approve] Fix slugify (1 round(s))
cost: $0.0511 est. (planner $0.0155, reviewer-1 $0.0052, reviewer-2 $0.0069, worker-1 $0.0159, worker-2 $0.0076)
== worker-1 log
02:30:16 worker-1       ~ turn 2 (tool_use) apply_discount returns the discount, not the discounted price. ...
02:30:16 worker-1       > apply_patch {"path": "shop/pricing.py", ...}
02:30:16 worker-1       ~ turn 3 (tool_use)
02:30:20 worker-1       + resumed from checkpoint after turn 2
02:30:20 worker-1       ~ turn 3 (tool_use)
02:30:28 worker-1       > run_command {"command": "sleep 8"}
...
02:30:28 worker-1       = finished after 5 turns, 2386 tokens, $0.0135
```

Labeled issue to PR on the sandbox repo
([agent-fleet-sandbox](https://github.com/sakshamchitkara-dotcom/agent-fleet-sandbox),
seeded bug + [issue #1](https://github.com/sakshamchitkara-dotcom/agent-fleet-sandbox/issues/1)
labeled `fleet`), resulting in
[PR #4](https://github.com/sakshamchitkara-dotcom/agent-fleet-sandbox/pull/4) with the
trajectory table and test output in its description:

```
$ fleet watch sakshamchitkara-dotcom/agent-fleet-sandbox --label fleet --once
watching sakshamchitkara-dotcom/agent-fleet-sandbox for open issues labeled 'fleet' (dry-run)
would work on #1: Discounts are inverted: apply_discount returns the discount instead of the discounted price  (pass --apply to run it)

$ fleet watch sakshamchitkara-dotcom/agent-fleet-sandbox --label fleet --once --apply --pr \
    --backend scripted --script examples/scripts/sandbox-issue.json \
    --test-cmd "python -m unittest -v" --sandbox subprocess --max-cost 1
watching sakshamchitkara-dotcom/agent-fleet-sandbox for open issues labeled 'fleet' (apply + PR)
#1: Discounts are inverted: apply_discount returns the discount instead of the discounted price -> task d4961dd9
task d4961dd9: done (finished), attempts 1/2
repo: sakshamchitkara-dotcom/agent-fleet-sandbox
sandbox: sandbox-exec: no network, writes confined to the worktree
branch: fleet/d4961dd9  tests: PASS
  - [approve] Fix inverted discount in apply_discount (1 round(s))
 shop/pricing.py | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
tokens: 7,658 (planner 2,955, reviewer-1 1,524, worker-1 3,179)
cost: $0.0396 est. of $1.00 budget (planner $0.0157, reviewer-1 $0.0076, worker-1 $0.0163)
PR: https://github.com/sakshamchitkara-dotcom/agent-fleet-sandbox/pull/4

$ fleet watch torvalds/linux --once
refused: torvalds/linux is not owned by the authenticated user 'sakshamchitkara-dotcom'; refusing to push or open a PR
```

Test first, then human approval from the dashboard, on the same issue
([PR #5](https://github.com/sakshamchitkara-dotcom/agent-fleet-sandbox/pull/5)):

```
$ fleet issue sakshamchitkara-dotcom/agent-fleet-sandbox#1 --pr --await-approval --test-first \
    --backend scripted --script examples/scripts/sandbox-issue.json \
    --test-cmd "python -m unittest -v" --sandbox subprocess --max-cost 1
issue: Discounts are inverted: apply_discount returns the discount instead of the discounted price (https://github.com/sakshamchitkara-dotcom/agent-fleet-sandbox/issues/1)
task 3207b7d1 queued; running...
task 3207b7d1: awaiting_approval (awaiting approval), attempts 1/2
repo: sakshamchitkara-dotcom/agent-fleet-sandbox
sandbox: sandbox-exec: no network, writes confined to the worktree
branch: fleet/3207b7d1  tests: PASS
  - [approve] Fix inverted discount in apply_discount (1 round(s)), regression test tests/test_regression_discount.py
 shop/pricing.py                   |  2 +-
 tests/test_regression_discount.py | 17 +++++++++++++++++
 2 files changed, 18 insertions(+), 1 deletion(-)
tokens: 11,283 (planner 2,954, reviewer-1 2,133, tester-1 2,799, worker-1 3,397)
cost: $0.0590 est. of $1.00 budget (planner $0.0157, reviewer-1 $0.0100, tester-1 $0.0162, worker-1 $0.0172)
awaiting approval: review with `git -C .../repos/sakshamchitkara-dotcom__agent-fleet-sandbox diff d426cc320d1c fleet/3207b7d1`, then `fleet approve 3207b7d1` to open the PR or `fleet reject 3207b7d1`

$ git log --format='%h %s' fleet/3207b7d1
acef913 Merge fleet/3207b7d1-w1: Fix inverted discount in apply_discount
1f83955 Fix inverted discount in apply_discount
0f2f3b1 Add regression test: Fix inverted discount in apply_discount
d426cc3 feat: shop package with pricing and text helpers
```

The tester's run of the new test on the unfixed code ended `[exit code 1]` with both
regression tests (`test_cart_total_with_discount`, `test_ten_percent_off`) failing. In
`fleet serve` the task page showed the diff, a reason field and **Approve and open PR** /
**Reject**; clicking Approve (in a real browser) turned the task `done` with the PR link.
A second approval is refused:

```
$ fleet approve 3207b7d1
task 3207b7d1 is not awaiting approval
```

The PR description names the regression test and lists `tester-1` in the trajectory table.
The earlier issue-to-PR run
([PR #3](https://github.com/sakshamchitkara-dotcom/agent-fleet-sandbox/pull/3)) was a
duplicate of #4 and is closed; see `CHANGELOG.md` for what changed since.

## Limitations

- The scripted backend proves the plumbing, not the model's judgment; its token and cost
  numbers are simulated. `fleet bench` against Claude has not been run in CI (no key there),
  and nothing in this repo has been run against Bedrock or Vertex: `--provider` is tested
  up to client construction and request shape only.
- The prompt-caching changes (system breakpoint, compaction as a fork of the cached
  conversation) are verified on the request shape with a fake SDK client, not by observing
  `cache_read_input_tokens` on real traffic.
- Without Docker, isolation depends on the host: `sandbox-exec` and `bwrap` block network
  and confine writes, `unshare` only blocks network, and `none` confines nothing (Ubuntu
  24.04 blocks the user namespaces bwrap needs unless
  `kernel.apparmor_restrict_unprivileged_userns=0`). `fleet status` tells you which you got.
- Cost is an estimate: first-party list prices unless you pass `--prices`; batch
  discounts, long-context premiums and the 1-hour cache TTL are not modelled.
- `--test-first` decides a test is "red" by running the test command with the new files
  appended; that narrows pytest, unittest, node --test, jest, vitest, mocha and rspec, but a
  `--test-cmd` that already names its files (or a runner that ignores file arguments) runs
  more, so a pre-existing failure can pass for red.
- The symbol index is regex-based outside Python (parsed files are cached by mtime).
- CI exercises Python, JavaScript and TypeScript test commands; others work through
  `--test-cmd` but are untested.
- The planner is asked for non-overlapping subtasks; when they do collide the integrator
  resolves the conflict or the branch is skipped (never force-merged).

## License

MIT
