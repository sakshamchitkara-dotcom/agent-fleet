"""Codebase index for agents: a repo map (file tree + top-level symbols) and
symbol search, so an agent can orient itself in one or two tool calls instead
of a dozen list_dir/read_file turns.

Python is parsed with `ast`; other languages use per-language regexes that
catch top-level declarations (good enough for orientation, not a compiler).
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
             "dist", "build", "target", ".tox", ".idea", ".vscode"}
MAX_FILE_BYTES = 400_000


@dataclass(frozen=True)
class Symbol:
    path: str
    line: int
    kind: str
    name: str
    signature: str = ""

    def render(self) -> str:
        return f"{self.path}:{self.line} {self.kind} {self.name}{self.signature}"


_R = lambda *pats: [(kind, re.compile(p, re.M)) for kind, p in pats]  # noqa: E731
_JS = _R(("function", r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)"),
         ("class", r"^(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"),
         ("const", r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
         ("type", r"^(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)"))
REGEXES = {
    ".js": _JS, ".jsx": _JS, ".ts": _JS, ".tsx": _JS, ".mjs": _JS, ".cjs": _JS,
    ".go": _R(("func", r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),
              ("type", r"^type\s+([A-Za-z_]\w*)")),
    ".rs": _R(("fn", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"),
              ("type", r"^(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type)\s+([A-Za-z_]\w*)")),
    ".rb": _R(("def", r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)"),
              ("class", r"^\s*(?:class|module)\s+([A-Z]\w*)")),
    ".java": _R(("class", r"^\s*(?:public\s+|final\s+|abstract\s+)*(?:class|interface|enum|record)\s+([A-Za-z_]\w*)")),
    ".kt": _R(("fun", r"^\s*(?:\w+\s+)*fun\s+(?:<[^>]*>\s*)?([A-Za-z_]\w*)"),
              ("class", r"^\s*(?:\w+\s+)*(?:class|interface|object)\s+([A-Za-z_]\w*)")),
    ".c": _R(("func", r"^[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w*)\s*\([^;]*$")),
    ".h": _R(("decl", r"^[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w*)\s*\([^)]*\)\s*;")),
    ".sh": _R(("func", r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{")),
}
_C_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "else"}


def _py_args(node: ast.AST) -> str:
    try:
        return f"({ast.unparse(node.args)})"
    except Exception:  # pragma: no cover - ast.unparse is total on parsed input
        return "(...)"


def python_symbols(rel: str, text: str) -> list[Symbol]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return regex_symbols(rel, text, _R(("def", r"^(?:async\s+)?def\s+([A-Za-z_]\w*)"),
                                           ("class", r"^class\s+([A-Za-z_]\w*)")))
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(Symbol(rel, node.lineno, "def", node.name, _py_args(node)))
        elif isinstance(node, ast.ClassDef):
            out.append(Symbol(rel, node.lineno, "class", node.name))
            for sub in node.body:  # methods: one level down is where most of the code lives
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(Symbol(rel, sub.lineno, "method", f"{node.name}.{sub.name}", _py_args(sub)))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper():  # module constants only
                    out.append(Symbol(rel, node.lineno, "const", t.id))
    return out


def regex_symbols(rel: str, text: str, patterns) -> list[Symbol]:
    out = []
    for kind, rx in patterns:
        for m in rx.finditer(text):
            if m.group(1) in _C_KEYWORDS:
                continue
            out.append(Symbol(rel, text.count("\n", 0, m.start()) + 1, kind, m.group(1)))
    return sorted(out, key=lambda s: s.line)


def file_symbols(root: Path, path: Path) -> list[Symbol]:
    rel = str(path.relative_to(root))
    ext = path.suffix.lower()
    if ext not in REGEXES and ext != ".py":
        return []
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    return python_symbols(rel, text) if ext == ".py" else regex_symbols(rel, text, REGEXES[ext])


def walk(root: Path, base: Path | None = None):
    for d, dirs, files in os.walk(base or root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        for f in sorted(files):
            yield Path(d) / f


# ponytail: re-parses the tree on every call; fine for repos up to a few
# thousand files, cache per (path, mtime) if agents work on big monorepos.
def index(root: Path, base: Path | None = None) -> list[Symbol]:
    return [s for f in walk(root, base) for s in file_symbols(root, f)]


def repo_map(root: Path, base: Path | None = None, limit: int = 20_000) -> str:
    """Indented file tree; each source file lists its top-level symbols."""
    root = Path(root)
    by_file: dict[str, list[Symbol]] = {}
    for s in index(root, base):
        by_file.setdefault(s.path, []).append(s)
    lines, shown_dirs = [], set()
    for f in walk(root, base):
        rel = f.relative_to(root)
        for depth, parent in enumerate(reversed(rel.parents[:-1])):
            if parent not in shown_dirs:
                shown_dirs.add(parent)
                lines.append(f"{'  ' * depth}{parent.name}/")
        indent = "  " * (len(rel.parts) - 1)
        lines.append(f"{indent}{rel.name}")
        for s in by_file.get(str(rel), []):
            lines.append(f"{indent}  {s.line:>4} {s.kind} {s.name}{s.signature}")
    out = "\n".join(lines)
    if len(out) > limit:
        out = out[:limit].rsplit("\n", 1)[0] + "\n... (map truncated; pass a sub-path to see more)"
    return out or "(no files)"


def search_symbols(root: Path, query: str, kind: str | None = None, limit: int = 100) -> str:
    """Symbols whose name matches `query` (case-insensitive regex, or substring if it isn't one)."""
    try:
        rx = re.compile(query, re.I)
    except re.error:
        rx = re.compile(re.escape(query), re.I)
    hits = [s for s in index(Path(root)) if rx.search(s.name) and (not kind or s.kind == kind)]
    exact = [s for s in hits if s.name.split(".")[-1].lower() == query.lower()]
    hits = exact + [s for s in hits if s not in exact]
    body = "\n".join(s.render() for s in hits[:limit])
    if len(hits) > limit:
        body += f"\n... ({len(hits) - limit} more; narrow the query)"
    return body or f"no symbols match {query!r}"
