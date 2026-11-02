# -*- coding: utf-8 -*-
"""P1 same-origin 模式测试（task-26）：tools/hermes_acad1_build.py 的 /quant/ 页面包构建规则。

全部用临时目录 + 假 JS/CSS/HTML，不真构建、不碰外部仓/portal/quant。
覆盖：相对前缀 patch/assert 正例、裸前缀残留拒、WS host 残留拒、禁词拒、根 logo 残留拒、
绝对 origin 模式回归、--same-origin 自动判定/参数校验、build_env（VERSION env 口径）、
verify-only CLI 集成（same-origin 跳过 Hermes 静态 handler）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "hermes_acad1_build.py"

spec = importlib.util.spec_from_file_location("hermes_acad1_build_under_test", TOOL)
hb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hb)


# ---------- fixture ----------
def _make_bundle(root: Path, base: str, *, css: str = "body{color:#fff}",
                 ws: bool = True) -> Path:
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    js = 'fetch("/api/v1/health");img.src="/logo.png";img2.src="/logo-icon.png";'
    if ws:
        js += 'const w=`ws://${location.host}/api/v1/ws`;'
    (assets / "app.js").write_text(js, encoding="utf-8")
    (assets / "app.css").write_text(css, encoding="utf-8")
    (root / "index.html").write_text(
        '<script type="module" src="%sassets/app.js"></script>'
        '<link rel="stylesheet" href="%sassets/app.css">' % (base, base),
        encoding="utf-8")
    return root


def _it(root: Path) -> None:
    """验证 index/资产存在（patch_build 内部 assert 需要）。"""
    assert (root / "index.html").exists()


SAME_BASE = "/quant/"
SAME_API = "/quant-api/api/v1"


# ---------- same-origin patch/assert ----------
def test_same_origin_patch_rules(tmp_path):
    root = _make_bundle(tmp_path / "quant", SAME_BASE, css="body{background:url(/logo.png)}")
    _it(root)
    report = hb.patch_build(root, SAME_API, SAME_BASE, same_origin=True)
    js = (root / "assets" / "app.js").read_text(encoding="utf-8")
    css = (root / "assets" / "app.css").read_text(encoding="utf-8")
    # 裸相对 /api/v1 → /quant-api/api/v1（不重复加前缀）
    assert 'fetch("/quant-api/api/v1/health")' in js
    # WS host 保留 location.host，仅补前缀
    assert "${location.host}/quant-api/api/v1/ws" in js
    assert "${location.host}/api/v1" not in js
    # logo 根路径 → base 前缀（JS + CSS 均补）
    assert 'img.src="/quant/logo.png"' in js
    assert "/logo-icon.png" not in js.replace("/quant/logo-icon.png", "")
    assert "url(/quant/logo.png)" in css
    # 不引入绝对 origin / 8105 / 5175（WS 保留 ws://${location.host} 同源占位，非绝对 origin）
    for bad in ("127.0.0.1:8105", "127.0.0.1:5175", "127.0.0.1", "http://", "localhost"):
        assert bad not in js, bad
    assert report["same_origin"] is True
    assert report["counts"]["ws_replaced"] == 1
    assert report["counts"]["api_replaced"] >= 1
    assert report["assertions"]["ok"] is True, report["assertions"]["details"]
    counts = report["assertions"]["counts"]
    assert counts["bare_api_remaining"] == 0
    assert counts["forbidden_token_hits"] == 0
    assert counts["api_prefix_hits"] >= 2


def test_assert_rejects_bare_api_remaining(tmp_path):
    root = _make_bundle(tmp_path / "quant", SAME_BASE)
    hb.patch_build(root, SAME_API, SAME_BASE, same_origin=True)
    p = root / "assets" / "app.js"
    p.write_text(p.read_text(encoding="utf-8") + 'fetch("/api/v1/leak");', encoding="utf-8")
    checks = hb.assert_patched(root, SAME_API, SAME_BASE, same_origin=True)
    assert checks["ok"] is False
    assert "relative_api_remaining" in checks["details"]
    assert checks["counts"]["bare_api_remaining"] >= 1


def test_assert_rejects_ws_host_residual(tmp_path):
    root = _make_bundle(tmp_path / "quant", SAME_BASE, ws=False)
    hb.patch_build(root, SAME_API, SAME_BASE, same_origin=True)
    p = root / "assets" / "app.js"
    p.write_text(p.read_text(encoding="utf-8")
                 + 'const w2=`ws://${location.host}/api/v1/ws`;', encoding="utf-8")
    checks = hb.assert_patched(root, SAME_API, SAME_BASE, same_origin=True)
    assert checks["ok"] is False
    assert "ws_relative_remaining" in checks["details"]
    assert checks["counts"]["ws_relative_remaining"] >= 1


def test_assert_rejects_forbidden_tokens(tmp_path):
    root = _make_bundle(tmp_path / "quant", SAME_BASE)
    hb.patch_build(root, SAME_API, SAME_BASE, same_origin=True)
    p = root / "assets" / "app.js"
    p.write_text(p.read_text(encoding="utf-8")
                 + 'x="127.0.0.1:8105";y="127.0.0.1:5175";'
                   'z="autoSession";q="zhice_auto_password";', encoding="utf-8")
    checks = hb.assert_patched(root, SAME_API, SAME_BASE, same_origin=True)
    assert checks["ok"] is False
    assert "forbidden_token" in checks["details"]
    hits = checks["counts"]["forbidden_hits"]
    assert hits.get("127.0.0.1:8105") == 1
    assert hits.get("127.0.0.1:5175") == 1
    assert hits.get("autoSession") == 1
    assert hits.get("zhice_auto_password") == 1


def test_assert_rejects_root_logo_remaining(tmp_path):
    root = _make_bundle(tmp_path / "quant", SAME_BASE)
    hb.patch_build(root, SAME_API, SAME_BASE, same_origin=True)
    p = root / "assets" / "app.js"
    p.write_text(p.read_text(encoding="utf-8") + 'img3.src="/logo.png";', encoding="utf-8")
    checks = hb.assert_patched(root, SAME_API, SAME_BASE, same_origin=True)
    assert checks["ok"] is False
    assert "relative_logo_remaining" in checks["details"]


# ---------- 绝对 origin 模式回归（P2-3 行为不变） ----------
def test_absolute_mode_unchanged(tmp_path):
    root = _make_bundle(tmp_path / "ext", "/extensions/zhice/")
    _it(root)
    report = hb.patch_build(root, "http://127.0.0.1:8105/api/v1", "/extensions/zhice/")
    js = (root / "assets" / "app.js").read_text(encoding="utf-8")
    # 原行为：相对 API → 绝对 origin；WS host → 绝对 ws host；logo → 扩展 base
    assert 'fetch("http://127.0.0.1:8105/api/v1/health")' in js
    assert "ws://127.0.0.1:8105/api/v1/ws" in js
    assert 'img.src="/extensions/zhice/logo.png"' in js
    assert report["same_origin"] is False
    assert report["assertions"]["ok"] is True, report["assertions"]["details"]
    assert "same_origin" not in (report["assertions"].get("details") or {})


# ---------- 模式判定 / 参数校验 / VERSION env ----------
def test_resolve_same_origin_and_invalid_params():
    assert hb.resolve_same_origin("/quant-api/api/v1") is True      # 相对路径自动启用
    assert hb.resolve_same_origin("http://127.0.0.1:8105/api/v1") is False
    assert hb.resolve_same_origin("http://127.0.0.1:8105/api/v1", True) is True
    with pytest.raises(SystemExit) as exc:
        hb._api_parts("http://127.0.0.1:8105/api/v1", True)  # 显式 same-origin 但绝对 api
    assert exc.value.code == 2


def test_build_env_records_version_fields():
    env = hb.build_env(SAME_API, SAME_BASE, "CN,US,HK,CRYPTO", "hash")
    assert env["VITE_BASE"] == SAME_BASE
    assert env["VITE_API_BASE"] == SAME_API
    assert env["VITE_ROUTER"] == "hash"


# ---------- verify-only CLI 集成（不真构建） ----------
def test_verify_only_same_origin_cli(tmp_path, capsys):
    dest = _make_bundle(tmp_path / "quant", SAME_BASE)
    _it(dest)
    hb.patch_build(dest, SAME_API, SAME_BASE, same_origin=True)
    tree = hb.tree_files(dest)
    manifest = {"version": "t-test", "output": {"files": tree["files"]},
                "build": {"env": {"VITE_BASE": SAME_BASE, "VITE_API_BASE": SAME_API}},
                "same_origin": {"enabled": True}}
    (dest / "VERSION.json").write_text(json.dumps(manifest), encoding="utf-8")
    deliverables = tmp_path / "deliv"
    rc = hb.main(["--verify-only", "--same-origin", "--base-path", SAME_BASE,
                  "--api-base", SAME_API, "--dest", str(dest),
                  "--deliverables", str(deliverables), "--no-verify", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    result = payload["result"]
    assert result["file_hashes_match"] is True
    assert result["assert_patched"]["ok"] is True
    assert result["same_origin"] is True
    assert result["static_path"]["skipped"] is True   # same-origin 不套 Hermes handler
    assert result["static_path"]["ok"] is None
    assert (deliverables / "VERIFY.json").exists()


# ---------- 审计修复回归（task-30）：--no-verify 语义 ----------
def test_no_verify_skips_static_path_check(tmp_path, monkeypatch, capsys):
    """修复 C：--no-verify 必须跳过 static_path_check（帮助文档承诺跳过静态路径验证）。"""
    src = tmp_path / "frontend"
    src.mkdir()
    (src / "package.json").write_text('{"name":"fake"}', encoding="utf-8")
    dest = tmp_path / "out"
    deliv = tmp_path / "deliv"
    monkeypatch.setattr(hb, "TMP_ROOT", tmp_path / "tmp_root")
    monkeypatch.setattr(hb, "LOCK_FILE", tmp_path / "build.lock")
    monkeypatch.setattr(hb, "source_git_info", lambda p: {
        "git_ok": False, "repo": str(p), "head": "", "dirty": False, "dirty_count": 0})
    monkeypatch.setattr(hb, "source_digest",
                        lambda p: {"digest": "d", "file_count": 0, "bytes": 0})

    def fake_run_build(source, out_dir, env, log_path, skip_typecheck):
        (out_dir / "assets").mkdir(parents=True)
        (out_dir / "assets" / "a.js").write_text("console.log(1)", encoding="utf-8")
        (out_dir / "assets" / "a.css").write_text("body{}", encoding="utf-8")
        (out_dir / "index.html").write_text(
            '<script src="/extensions/zhice/assets/a.js"></script>'
            '<link rel="stylesheet" href="/extensions/zhice/assets/a.css">',
            encoding="utf-8")
        return {"out_dir": str(out_dir), "typecheck": "skipped", "typecheck_rc": None,
                "vite_rc": 0, "assets": ["a.js", "a.css"],
                "tools": {"node": "node", "node_version": "v24.20.0",
                          "vite": "8.2.1", "typescript": "6.0.3"}}

    def fake_patch(out_dir, api_base, base_path, same_origin=False):
        return {"api_base": api_base, "base_path": base_path, "same_origin": False,
                "api_prefix": "http://127.0.0.1:8105", "rules": [], "files": {},
                "counts": {"ws_replaced": 0, "api_replaced": 0, "logo_replaced": 0},
                "assertions": {"ok": True, "counts": {}}}

    monkeypatch.setattr(hb, "run_build", fake_run_build)
    monkeypatch.setattr(hb, "patch_build", fake_patch)
    monkeypatch.setattr(hb, "write_readme", lambda d, m: d / "README.md")
    calls = {"static": 0}

    def boom(*a, **k):
        calls["static"] += 1
        raise AssertionError("--no-verify 不应调用 static_path_check")

    monkeypatch.setattr(hb, "static_path_check", boom)
    rc = hb.main(["--source", str(src), "--dest", str(dest),
                  "--deliverables", str(deliv), "--no-verify", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0, (rc, payload.get("exit_code"))
    assert calls["static"] == 0, "--no-verify 仍调用了 static_path_check"
    verify = payload["manifest"]["verify"]
    assert verify["static_path"]["skipped"] is True
    assert verify["static_path"]["ok"] is None
    assert "--no-verify" in verify["static_path"]["reason"]
    assert (dest / "VERSION.json").exists()
