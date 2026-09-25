from pathlib import Path

import pytest

from fleet.index import index, repo_map, search_symbols
from fleet.tools import ToolError, Toolbox

PY = '''"""mod"""
MAX_ITEMS = 10


def apply_discount(price, percent=0):
    return price


class Cart:
    def total(self, *, tax: float = 0.0):
        return 0

    async def checkout(self):
        pass
'''
TS = '''export function formatPrice(p: number) { return p }
export const toCents = (x: number) => x * 100
class Store {}
export interface Item { id: string }
'''
GO = '''package shop

type Order struct{}

func (o *Order) Total() int { return 0 }
func NewOrder() *Order { return nil }
'''


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "pricing.py").write_text(PY)
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "price.ts").write_text(TS)
    (tmp_path / "shop" / "order.go").write_text(GO)
    (tmp_path / "shop" / "broken.py").write_text("def ok():\n    pass\ndef broken(:\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("function hidden() {}")
    (tmp_path / "README.md").write_text("hi")
    return tmp_path


def test_python_symbols_via_ast(tree):
    syms = {(s.kind, s.name): s for s in index(tree) if s.path == "shop/pricing.py"}
    assert syms[("def", "apply_discount")].signature == "(price, percent=0)"
    assert syms[("def", "apply_discount")].line == 5
    assert ("class", "Cart") in syms and ("method", "Cart.checkout") in syms
    assert syms[("method", "Cart.total")].signature == "(self, *, tax: float=0.0)"
    assert ("const", "MAX_ITEMS") in syms


def test_regex_languages_and_fallback(tree):
    names = {(s.path, s.name) for s in index(tree)}
    assert {("web/price.ts", n) for n in ("formatPrice", "toCents", "Store", "Item")} <= names
    assert {("shop/order.go", n) for n in ("Order", "Total", "NewOrder")} <= names
    assert {("shop/broken.py", "ok"), ("shop/broken.py", "broken")} <= names  # syntax error: regex
    assert not any("node_modules" in p for p, _ in names)


def test_repo_map_tree(tree):
    out = repo_map(tree)
    assert "shop/\n" in out and "  pricing.py" in out and "README.md" in out
    assert "def apply_discount(price, percent=0)" in out
    assert out.index("shop/") < out.index("pricing.py") < out.index("apply_discount")
    assert "hidden" not in out
    assert "web/" not in repo_map(tree, tree / "shop")
    assert "truncated" in repo_map(tree, limit=80)


def test_search_symbols(tree):
    out = search_symbols(tree, "total")
    assert out.splitlines()[0] == "shop/order.go:5 func Total"
    assert "shop/pricing.py:10 method Cart.total(self, *, tax: float=0.0)" in out
    assert search_symbols(tree, "Cart", kind="class") == "shop/pricing.py:9 class Cart"
    assert "no symbols" in search_symbols(tree, "nope_xyz")
    assert search_symbols(tree, "Cart.(") == "no symbols match 'Cart.('"  # bad regex: substring, no crash


def test_toolbox_exposes_index_tools(tree):
    tb = Toolbox(tree)
    assert "apply_discount" in tb.execute("repo_map", {})
    assert "apply_discount" in tb.execute("search_symbols", {"query": "discount"})
    with pytest.raises(ToolError):
        tb.execute("repo_map", {"path": "../"})
    with pytest.raises(ToolError):
        tb.execute("search_symbols", {"query": " "})
