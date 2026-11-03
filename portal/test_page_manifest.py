# -*- coding: utf-8 -*-
"""page_manifest 回归（UI-UNIFY-MANIFEST-20261103）。

覆盖：
1. 当前树 `--check` rc=0（manifest 123 页覆盖生产页集、schema/入口职责/依赖齐全）；
2. 四类漂移各自 FAIL：页面缺 manifest / manifest 多余 / 路由漂移 / active 缺 tokens·nav.js·m.js；
3. 路由基线（portal_route_inventory）对账漂移 FAIL，`--skip-route-baseline` 可跳过；
4. 生成物/备份目录不入清单；`--generate` 幂等且保留人工字段；
5. 真实 manifest 在测试中不被修改。

运行：python -m pytest portal/test_page_manifest.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "page_manifest_t", str(ROOT / "tools" / "page_manifest.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

REAL_MANIFEST = ROOT / P.DEFAULT_MANIFEST_REL


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- 测试夹具：合成一棵最小 portal 树（tmp）----------
def _page(title: str, tokens: bool = True, nav: bool = True, mjs: bool = False,
          os_theme: bool = True) -> str:
    head = ["<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">",
            "<meta name=\"viewport\" content=\"width=device-width\">",
            "<title>%s</title>" % title]
    if tokens:
        head.append('<link rel="stylesheet" href="/static/tokens.css">')
    if os_theme:
        head.append('<link rel="stylesheet" href="/static/os_theme.css">')
    head.append("</head><body><h1>%s</h1>" % title)
    if mjs:
        head.append('<script src="/m/assets/m.js"></script>')
    if nav:
        head.append('<script src="/static/nav.js" defer></script>')
    head.append("</body></html>")
    return "".join(head)


NGINX_CONF = """
server {
    location = / { try_files /home_v3.html =404; }
    location = /os-entry {
        auth_request /_auth_guard;
        error_page 401 = @login_required;
        try_files /os_entry_v9_prod.html =404;
    }
    location = /os-board {
        auth_request /_auth_guard;
        try_files /os.html =404;
    }
    location = /hq {
        auth_request /_auth_guard;
        try_files /hq.html =404;
    }
    location = /login { try_files /login.html =404; }
    location = /hall { try_files /index.html =404; }
}
"""


def _mk(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _build_tree(root: Path) -> None:
    """最小生产页集 + 入口页 + 生成物/备份目录（应被排除）。"""
    _mk(root, "portal/nginx.conf", NGINX_CONF)
    _mk(root, "portal/home_v3.html", _page("首页"))
    _mk(root, "portal/alpha.html", _page("Alpha 页"))
    _mk(root, "portal/beta.html", _page("Beta 页"))
    _mk(root, "portal/index.html", _page("旧 index"))
    _mk(root, "portal/home.html", _page("旧首页", os_theme=False))
    _mk(root, "portal/login.html", _page("登录"))
    _mk(root, "portal/zero_proto.html", _page("浅色驾驶舱"))
    _mk(root, "portal/os_entry_v9_prod.html", _page("深色入口"))
    _mk(root, "portal/os.html", _page("实况深页"))
    _mk(root, "portal/hq.html", _page("指挥部"))
    _mk(root, "portal/m/index.html", _page("移动端", mjs=True))
    _mk(root, "portal/m/picks.html", _page("移动选股", mjs=True))
    _mk(root, "portal/quant/index.html", _page("量化包", tokens=False, nav=False, os_theme=False))
    _mk(root, "portal/quant/app.html", _page("量化包 app（构建产物）"))
    # 生成物/备份/资产模板：均不得进入清单
    _mk(root, "portal/work/rollback_1/old.html", _page("回滚副本"))
    _mk(root, "portal/picks_public/gen.html", _page("发布生成页"))
    _mk(root, "portal/static/dw_v2.html", _page("静态模板"))
    _mk(root, "portal/_templates/page_shell_v1.html", _page("壳模板"))
    _mk(root, "portal/data/os_goals/x/workspace/w.html", _page("任务工作区"))
    _mk(root, "portal/agent_board.html", _page("运行时生成页"))


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    _build_tree(tmp_path)
    return tmp_path


@pytest.fixture()
def tree_with_manifest(tree: Path) -> Path:
    assert P.main(["--root", str(tree), "--generate"]) == 0
    assert (tree / P.DEFAULT_MANIFEST_REL).is_file()
    return tree


def _manifest(tree: Path) -> dict:
    return json.loads((tree / P.DEFAULT_MANIFEST_REL).read_text(encoding="utf-8"))


def _save_manifest(tree: Path, manifest: dict) -> None:
    (tree / P.DEFAULT_MANIFEST_REL).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _codes(result: dict) -> set:
    return {v.get("code") for v in result.get("violations") or []}


def _check(tree: Path, *extra: str) -> dict:
    """直接跑校验函数，返回结果 dict（测试断言用）。"""
    return P.check_manifest(root=tree, skip_route_baseline="--skip-route-baseline" in extra)


# ---------- 1. 当前树 ----------
def test_current_tree_check_passes(capsys):
    assert P.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "OK" in out and "pages=" in out
    assert "violations" not in out


def test_current_manifest_covers_production_pages():
    manifest = P.load_manifest(P.resolve_root())
    sources = {p["source"] for p in manifest["pages"]}
    disk = {p.relative_to(P.resolve_root()).as_posix() for p in P.production_files()}
    assert sources == disk
    assert len(manifest["pages"]) == 123
    counts = manifest["counts"]["by_status"]
    assert counts["active"] + counts["legacy"] + counts["review"] + counts["archived"] == 123
    assert counts["review"] >= 1 and counts["legacy"] >= 1 and counts["active"] >= 1


def test_current_manifest_schema_and_unique(capsys):
    manifest = P.load_manifest(P.resolve_root())
    assert manifest["schema"] == P.SCHEMA
    assert isinstance(manifest["entry_map"], list) and manifest["entry_map"]
    for page in manifest["pages"]:
        assert all(k in page for k in P.REQUIRED_FIELDS)
        assert page["family"] in P.FAMILIES
        assert page["theme"] in P.THEMES
        assert page["shell"] in P.SHELLS
        assert page["nav"] in P.NAVS
        assert page["auth"] in P.AUTHS
        assert page["status"] in P.STATUSES
        assert page["path"].startswith("/")
    assert len({p["id"] for p in manifest["pages"]}) == len(manifest["pages"])
    assert len({p["path"] for p in manifest["pages"]}) == len(manifest["pages"])
    # 入口职责（UI 宪法 §1）
    entries = {e["entry"] for e in manifest["entry_map"]}
    assert {"/", "/portal", "/zero_proto.html", "/os-entry", "/hq", "/home.html"} <= entries
    # 生成物/备份不入清单
    for page in manifest["pages"]:
        assert "/static/" not in page["source"]
        assert "/_templates/" not in page["source"]
        assert "picks_public" not in page["source"]
        assert "work/" not in page["source"]


def test_real_manifest_not_modified_by_check():
    before = _sha256(REAL_MANIFEST)
    assert P.main(["--check"]) == 0
    assert _sha256(REAL_MANIFEST) == before
    assert P.main(["--check", "--json"]) == 0
    assert _sha256(REAL_MANIFEST) == before


# ---------- 2. tmp 树基线 ----------
def test_tmp_tree_generate_and_check_ok(tree_with_manifest):
    assert P.main(["--root", str(tree_with_manifest), "--check"]) == 0
    manifest = _manifest(tree_with_manifest)
    sources = {p["source"] for p in manifest["pages"]}
    assert "portal/work/rollback_1/old.html" not in sources
    assert "portal/picks_public/gen.html" not in sources
    assert "portal/static/dw_v2.html" not in sources
    assert "portal/_templates/page_shell_v1.html" not in sources
    assert "portal/agent_board.html" not in sources
    assert "portal/alpha.html" in sources


def test_missing_manifest_entry_fails(tree_with_manifest):
    _mk(tree_with_manifest, "portal/gamma.html", _page("Gamma 页"))
    result = _check(tree_with_manifest)
    assert not result["ok"]
    assert "missing_page" in _codes(result)
    assert any("gamma.html" in str(v.get("ref")) for v in result["violations"])


def test_extra_manifest_entry_fails(tree_with_manifest):
    # 页面被移走（改名成备份后缀，扫描侧不再看到）但 manifest 仍登记 → 多余
    alpha = tree_with_manifest / "portal" / "alpha.html"
    alpha.rename(alpha.with_name("alpha.html.bak"))
    result = _check(tree_with_manifest)
    assert not result["ok"]
    assert "extra_page" in _codes(result)


def test_route_drift_fails(tree_with_manifest):
    manifest = _manifest(tree_with_manifest)
    for page in manifest["pages"]:
        if page["source"] == "portal/alpha.html":
            page["path"] = "/alpha-renamed.html"
    _save_manifest(tree_with_manifest, manifest)
    result = _check(tree_with_manifest)
    assert not result["ok"]
    assert "route_drift" in _codes(result)


def test_active_missing_tokens_fails(tree_with_manifest):
    _mk(tree_with_manifest, "portal/alpha.html",
        _page("Alpha 页", tokens=False, nav=True))
    result = _check(tree_with_manifest)
    assert not result["ok"]
    assert "active_deps" in _codes(result)
    assert any("tokens" in v["message"] for v in result["violations"])


def test_active_missing_nav_js_fails(tree_with_manifest):
    _mk(tree_with_manifest, "portal/alpha.html",
        _page("Alpha 页", tokens=True, nav=False))
    result = _check(tree_with_manifest)
    assert not result["ok"]
    assert "active_deps" in _codes(result)
    assert any("nav.js" in v["message"] for v in result["violations"])


def test_active_h5_missing_mjs_fails(tree_with_manifest):
    _mk(tree_with_manifest, "portal/m/picks.html", _page("移动选股", mjs=False))
    result = _check(tree_with_manifest)
    assert not result["ok"]
    assert "active_deps" in _codes(result)
    assert any("m.js" in v["message"] for v in result["violations"])


def test_review_page_in_nav_fails(tree_with_manifest, monkeypatch):
    # 合成 nav 源：把 review 页的路径塞进导航 → 导航隔离违规
    manifest = _manifest(tree_with_manifest)
    for page in manifest["pages"]:
        if page["source"] == "portal/beta.html":
            page["status"] = "review"
            page["note"] = "评审稿（测试）"
    _save_manifest(tree_with_manifest, manifest)
    _mk(tree_with_manifest, "portal/api_common.py",
        "DOMAIN_NAV_GROUPS = {\"x\": ((\"组\", ((\"Beta\", \"/beta.html\", \"b\"),)),)}")
    result = _check(tree_with_manifest)
    assert not result["ok"]
    assert "nav_isolation" in _codes(result)


def test_entry_map_mismatch_fails(tree_with_manifest):
    manifest = _manifest(tree_with_manifest)
    for entry in manifest["entry_map"]:
        if entry["entry"] == "/":
            entry["source"] = "portal/alpha.html"
    _save_manifest(tree_with_manifest, manifest)
    result = _check(tree_with_manifest)
    assert not result["ok"]
    assert "entry_map_mismatch" in _codes(result)


# ---------- 3. 路由基线对账 ----------
def _write_baseline(tree: Path, total: int, extra_sig=None) -> None:
    sigs = [["GET", "exact", "/"], ["GET", "exact", "/api/health"]]
    if extra_sig:
        sigs.append(extra_sig)
    _mk(tree, P.ROUTE_BASELINE_REL,
        json.dumps({"generated_at": "2026-11-03T00:00:00", "total": total,
                    "signatures": sigs}, ensure_ascii=False, indent=1) + "\n")


def test_route_baseline_drift_fails(tree_with_manifest):
    _write_baseline(tree_with_manifest, 2)
    assert P.main(["--root", str(tree_with_manifest), "--generate"]) == 0
    assert _check(tree_with_manifest)["ok"]
    # 路由基线变化（新增路由）→ 漂移
    _write_baseline(tree_with_manifest, 3, ["GET", "exact", "/api/new-route"])
    result = _check(tree_with_manifest)
    assert not result["ok"]
    assert "route_baseline_drift" in _codes(result)
    # 显式跳过 → 通过（本地排查用）
    assert P.main(["--root", str(tree_with_manifest), "--check",
                   "--skip-route-baseline"]) == 0


def test_manifest_missing_returns_rc2(tree):
    assert P.main(["--root", str(tree), "--check"]) == 2


def test_check_json_output(tree_with_manifest, capsys):
    capsys.readouterr()
    assert P.main(["--root", str(tree_with_manifest), "--check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["pages_total"] >= 1
    assert isinstance(payload["counts"], dict)


# ---------- 4. 生成/列表 ----------
def test_generate_idempotent_and_preserves_curation(tree_with_manifest):
    manifest = _manifest(tree_with_manifest)
    target = None
    for page in manifest["pages"]:
        if page["source"] == "portal/alpha.html":
            page["status"] = "review"
            page["note"] = "人工判定：评审稿（测试保留）"
            target = page["source"]
    _save_manifest(tree_with_manifest, manifest)
    assert P.main(["--root", str(tree_with_manifest), "--generate"]) == 0
    again = _manifest(tree_with_manifest)
    kept = next(p for p in again["pages"] if p["source"] == target)
    assert kept["status"] == "review"
    assert kept["note"] == "人工判定：评审稿（测试保留）"
    # --refresh 重算人工字段（alpha 回到内置判定 active）
    assert P.main(["--root", str(tree_with_manifest), "--generate", "--refresh"]) == 0
    refreshed = _manifest(tree_with_manifest)
    page = next(p for p in refreshed["pages"] if p["source"] == target)
    assert page["status"] == "active"


def test_list_and_json(monkeypatch, capsys, tree_with_manifest):
    capsys.readouterr()
    assert P.main(["--root", str(tree_with_manifest), "--list"]) == 0
    out = capsys.readouterr().out
    assert "pages=" in out and "/alpha.html" in out
    assert P.main(["--root", str(tree_with_manifest), "--list", "--status", "active"]) == 0
    active_only = capsys.readouterr().out
    assert "review" not in active_only.split("title")[-1]
    assert P.main(["--root", str(tree_with_manifest), "--json"]) == 0
    dumped = json.loads(capsys.readouterr().out)
    assert dumped["schema"] == P.SCHEMA
    assert isinstance(dumped["pages"], list)


def test_scan_excludes_generated_and_backup(tree):
    files = {p.relative_to(tree).as_posix() for p in P.production_files(tree)}
    assert "portal/alpha.html" in files
    assert "portal/m/picks.html" in files
    assert "portal/quant/index.html" in files
    for excluded in ("portal/work/rollback_1/old.html", "portal/picks_public/gen.html",
                     "portal/static/dw_v2.html", "portal/_templates/page_shell_v1.html",
                     "portal/data/os_goals/x/workspace/w.html", "portal/agent_board.html",
                     "portal/quant/app.html"):
        assert excluded not in files
