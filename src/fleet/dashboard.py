"""Tiny live dashboard (stdlib http.server): task list + per-task agent trajectories."""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .queue import Queue
from .trajectory import isolation, read, spend

TASK_ID = re.compile(r"^[0-9a-f]{8}$")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agent-fleet</title>
<link rel="icon" href="data:,">
<style>
  :root { --bg:#fbfbfa; --fg:#1d1d1b; --muted:#6b6b66; --line:#e4e4df; --card:#fff;
          --ok:#1a7f37; --bad:#c2261d; --run:#9a6700; --accent:#3050c8; }
  @media (prefers-color-scheme: dark) { :root { --bg:#161615; --fg:#e8e8e3; --muted:#9a9a93;
          --line:#2c2c29; --card:#1e1e1c; --ok:#4ac26b; --bad:#ff7b72; --run:#d4a72c; --accent:#8fa8ff; } }
  * { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--fg);
      font:14px/1.45 ui-sans-serif,system-ui,sans-serif }
  main { max-width:1100px; margin:0 auto; padding:20px 16px 60px }
  h1 { font-size:18px; margin:0 0 16px } h1 a { color:inherit; text-decoration:none }
  table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line) }
  th,td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); vertical-align:top }
  th { font-size:12px; color:var(--muted); font-weight:600 }
  a { color:var(--accent) } .muted { color:var(--muted) }
  .s-done{color:var(--ok)} .s-failed{color:var(--bad)} .s-running{color:var(--run)}
  .agents { display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:12px; margin-top:14px }
  .agent { background:var(--card); border:1px solid var(--line); border-radius:6px; overflow:hidden }
  .agent h2 { font-size:13px; margin:0; padding:8px 10px; border-bottom:1px solid var(--line);
              display:flex; justify-content:space-between }
  .ev { font:12px/1.4 ui-monospace,Menlo,monospace; padding:3px 10px; border-bottom:1px dashed var(--line);
        white-space:pre-wrap; word-break:break-word }
  .ev.err { color:var(--bad) } .ev.model { color:var(--muted) }
  .log { max-height:460px; overflow:auto }
  pre { background:var(--card); border:1px solid var(--line); padding:10px; overflow:auto; font-size:12px }
  .table-wrap { overflow-x:auto }
  .approval { border:1px solid var(--run); border-radius:6px; padding:10px 12px; margin:12px 0; background:var(--card) }
  .approval button { font:inherit; padding:5px 12px; margin-right:8px; border-radius:5px; border:1px solid var(--line);
                     background:var(--bg); color:var(--fg); cursor:pointer }
  .approval button.go { background:var(--ok); border-color:var(--ok); color:#fff }
  .approval input { font:inherit; padding:4px 8px; width:min(360px,100%); margin:6px 0; border:1px solid var(--line);
                    border-radius:5px; background:var(--bg); color:var(--fg) }
  .s-awaiting_approval{color:var(--run)}
</style></head><body><main>
<h1><a href="/">agent-fleet</a> <span id="sub" class="muted"></span></h1>
<div id="app" class="muted">loading...</div>
</main>
<script>
const el = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) k === "class" ? e.className = v : e.setAttribute(k, v);
  for (const k of kids) e.append(k); return e; };
const usd = c => c < 1 ? `$${(c || 0).toFixed(4)}` : `$${c.toFixed(2)}`;
const ago = t => { const s = Date.now()/1000 - t; return s < 60 ? `${s|0}s` : s < 3600 ? `${s/60|0}m` : `${s/3600|0}h`; };
const m = location.pathname.match(/^\\/task\\/([0-9a-f]{8})$/);
let paused = false;  // no re-render while a human is deciding on an approval

async function decide(id, action) {
  const reason = (document.getElementById("reason") || {}).value || "";
  const msg = document.getElementById("decision");
  msg.textContent = action === "approve" ? "opening PR..." : "rejecting...";
  for (const b of document.querySelectorAll(".approval button")) b.disabled = true;
  const r = await fetch(`/api/task/${id}/${action}`, {method: "POST",
    headers: {"Content-Type": "application/json"}, body: JSON.stringify({reason})});
  const d = await r.json();
  msg.textContent = r.ok ? (d.pr_url ? `PR opened: ${d.pr_url}` : "rejected") : `error: ${d.error}`;
  paused = false;
}

function describe(e) {
  if (e.event === "tool") return `${e.is_error ? "!" : ">"} ${e.name} ${JSON.stringify(e.input).slice(0, 160)}`;
  if (e.event === "model") { const t = (e.content || []).filter(b => b.type === "text").map(b => b.text).join(" ");
    return `~ turn ${e.turn} (${e.stop_reason}) ${t.slice(0, 300)}`; }
  if (e.event === "start") return `+ start (${(e.isolation || e.sandbox || "?").split(":")[0]} sandbox): ${e.task.split("\\n").find(l => l.trim() && !/^(Overall )?task:$/i.test(l.trim())) || ""}`.slice(0, 200);
  if (e.event === "end") return `= ${e.status} after ${e.turns} turns, ${e.tokens} tokens, ${usd(e.cost)}`;
  if (e.event === "resume") return `+ resumed from checkpoint after turn ${e.turn}`;
  if (e.event === "compact") return `# compacted ${e.messages_before} messages into notes (${usd(e.cost)})`;
  return `# ${e.event}`;
}

async function list() {
  const tasks = await (await fetch("/api/tasks")).json();
  const rows = tasks.map(t => el("tr", {},
    el("td", {}, el("a", {href: `/task/${t.id}`}, t.id)),
    el("td", {class: `s-${t.status}`}, t.status), el("td", {}, t.stage),
    el("td", {}, t.repo), el("td", {}, (t.text.split("\\n")[0] || "").slice(0, 80)),
    el("td", {}, t.pr_url ? el("a", {href: t.pr_url}, "PR") : ""), el("td", {}, usd(t.cost)),
    el("td", {class: "muted"}, ago(t.created))));
  const table = el("table", {}, el("tr", {}, ...["id","status","stage","repo","task","pr","cost","age"].map(h => el("th", {}, h))), ...rows);
  document.getElementById("app").replaceChildren(tasks.length ? el("div", {class: "table-wrap"}, table) : "No tasks yet. Try: fleet run <repo> \\"<task>\\"");
  document.getElementById("app").classList.remove("muted");
}

async function detail(id) {
  const d = await (await fetch(`/api/task/${id}`)).json();
  const t = d.task, r = t.result || {};
  document.getElementById("sub").textContent = `/ ${id}`;
  const head = el("div", {},
    el("p", {}, el("b", {class: `s-${t.status}`}, t.status), ` - ${t.stage} - attempt ${t.attempts}/${t.max_attempts} - `,
      t.repo, t.pr_url ? " - " : "", t.pr_url ? el("a", {href: t.pr_url}, t.pr_url) : ""),
    el("pre", {}, t.text));
  const spent = Object.values(d.spend).reduce((a, s) => ({tokens: a.tokens + s.tokens, cost: a.cost + s.cost}), {tokens: 0, cost: 0});
  head.append(el("p", {}, `cost ${usd(spent.cost)} est.` + (d.max_cost ? ` of ${usd(d.max_cost)} budget` : "") +
    ` - ${spent.tokens.toLocaleString()} tokens`));
  if (d.isolation) head.append(el("p", {class: /NOT confined/.test(d.isolation) ? "s-failed" : "muted"},
    `sandbox: ${d.isolation}`));
  if (r.branch !== undefined) head.append(el("p", {}, `branch ${r.branch} - tests ${r.tests_passed ? "PASS" : "FAIL"}`),
    el("pre", {}, r.diffstat || "(no changes)"));
  if (t.error) head.append(el("pre", {class: "s-failed"}, t.error));
  if (t.status === "awaiting_approval") {
    paused = true;
    const approveBtn = el("button", {class: "go"}, "Approve and open PR"), rejectBtn = el("button", {}, "Reject");
    approveBtn.onclick = () => decide(id, "approve"); rejectBtn.onclick = () => decide(id, "reject");
    head.append(el("div", {class: "approval"},
      el("p", {}, el("b", {}, "Waiting for your approval. "), "Nothing has been pushed; review the change below."),
      el("pre", {}, r.diff || "(no diff)"),
      el("label", {for: "reason", class: "muted"}, "Reason (optional, recorded on reject)"), el("br"),
      el("input", {id: "reason", type: "text"}), el("br"), approveBtn, rejectBtn,
      el("span", {id: "decision", class: "muted"})));
  }
  const agents = el("div", {class: "agents"});
  for (const [name, evs] of Object.entries(d.agents)) {
    const last = evs[evs.length - 1] || {};
    const log = el("div", {class: "log"}, ...evs.map(e => el("div",
      {class: `ev ${e.event}${e.is_error ? " err" : ""}`}, describe(e))));
    agents.append(el("div", {class: "agent"}, el("h2", {}, name,
      el("span", {class: "muted"}, `${usd((d.spend[name] || {}).cost)} - ${last.event === "end" ? last.status : "running"}`)), log));
  }
  document.getElementById("app").replaceChildren(head, agents);
  document.getElementById("app").classList.remove("muted");
  for (const l of document.querySelectorAll(".log")) l.scrollTop = l.scrollHeight;
}

const tick = () => paused ? null : (m ? detail(m[1]) : list()).catch(e => console.error(e));
tick(); setInterval(tick, 2000);
</script></body></html>
"""


def make_handler(home: Path):
    from .orchestrator import NotAwaitingApproval, approve, reject
    queue = Queue(home / "fleet.db")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def json(self, obj, code: int = 200) -> None:
            self.send(code, json.dumps(obj, default=str).encode(), "application/json")

        def do_GET(self):
            path = self.path.split("?")[0]
            parts = path.strip("/").split("/")
            if path == "/" or (len(parts) == 2 and parts[0] == "task" and TASK_ID.match(parts[1])):
                return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
            if path == "/api/tasks":
                return self.json([{**{k: t[k] for k in ("id", "status", "stage", "repo", "text", "pr_url",
                                                        "created", "attempts")},
                                   "cost": sum(a["cost"] for a in spend(home / "runs" / t["id"]).values())}
                                  for t in queue.list(100)])
            if len(parts) == 3 and parts[:2] == ["api", "task"] and TASK_ID.match(parts[2]):
                task = queue.get(parts[2])
                if task is None:
                    return self.json({"error": "not found"}, 404)
                runs = home / "runs" / parts[2]
                logs = [(f.stem, read(f)) for f in runs.glob("*.jsonl")]
                # start order; name breaks ties (glob order differs across filesystems)
                logs.sort(key=lambda kv: (kv[1][0]["ts"] if kv[1] else 0, kv[0]))
                agents = {name: evs[-300:] for name, evs in logs}
                return self.json({"task": task, "agents": agents, "isolation": isolation(runs),
                                  "spend": spend(runs), "max_cost": task["options"].get("max_cost")})
            self.json({"error": "not found"}, 404)

        def same_origin(self) -> bool:
            """State-changing requests only from this dashboard's own page: a JSON content type
            (cross-site forms can't send one without a CORS preflight, which is never granted),
            a Host that is this server (DNS rebinding) and a matching Origin when one is sent."""
            port = self.server.server_address[1]
            hosts = {f"{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]", self.server.server_address[0])}
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            return (self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json"
                    and host in hosts and (origin is None or origin == f"http://{host}"))

        def do_POST(self):
            parts = self.path.split("?")[0].strip("/").split("/")
            if not (len(parts) == 4 and parts[:2] == ["api", "task"] and TASK_ID.match(parts[2])
                    and parts[3] in ("approve", "reject")):
                return self.json({"error": "not found"}, 404)
            if not self.same_origin():
                return self.json({"error": "forbidden"}, 403)
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 10_000)
                body = json.loads(self.rfile.read(length) or b"{}")
                if parts[3] == "approve":
                    return self.json({"pr_url": approve(home, queue, parts[2])})
                reject(queue, parts[2], str(body.get("reason", ""))[:500])
                return self.json({"status": "failed"})
            except NotAwaitingApproval as e:
                return self.json({"error": str(e)}, 409)
            except Exception as e:  # e.g. NotOwnedError or gh failure; the task records it too
                return self.json({"error": f"{type(e).__name__}: {e}"}, 500)

    return Handler


def serve(home: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(Path(home)))
    print(f"dashboard on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
