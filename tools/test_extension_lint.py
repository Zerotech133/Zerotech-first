# -*- coding: utf-8 -*-
"""O3 扩展 manifest 校验器单测。"""
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("extension_lint", HERE / "extension_lint.py")
el = importlib.util.module_from_spec(spec)
spec.loader.exec_module(el)


def _good(**kw):
    m = {
        "name": "demo",
        "version": "1.0.0",
        "type": "tool",
        "owner": "ARCH1",
        "description": "demo tool for test",
        "risk": "read-only",
        "transport": "cli",
        "entry": "tools/seat_evidence_packet.py",
        "allow_tools": ["read_only"],
        "docs": ["docs/testing.md"],
        "tests": ["tools/test_d2_gates.py"],
        "lifecycle": "active",
    }
    m.update(kw)
    return m


def test_good_manifest():
    errs, warns = el.lint_one("extensions/demo/manifest.json", _good())
    assert errs == []
    assert warns == []


def test_missing_field():
    errs, _ = el.lint_one("extensions/demo/manifest.json", _good(owner=""))
    assert any("owner" in e for e in errs)


def test_bad_name_version():
    errs, _ = el.lint_one("extensions/demo/manifest.json", _good(name="Demo_1", version="1.0"))
    assert any("name" in e for e in errs)
    assert any("version" in e for e in errs)


def test_http_requires_token_env():
    errs, _ = el.lint_one("extensions/demo/manifest.json",
                          _good(transport="http", url="https://api.example.com/mcp/", entry=""))
    assert any("token_env" in e for e in errs)


def test_http_token_in_url():
    errs, _ = el.lint_one("extensions/demo/manifest.json",
                          _good(transport="http", url="https://x/mcp/?token=a",
                                token_env="MY_TOKEN", entry=""))
    assert any("查询参数" in e for e in errs)


def test_write_requires_l2():
    errs, _ = el.lint_one("extensions/demo/manifest.json", _good(risk="write"))
    assert any("l2_approved" in e for e in errs)


def test_missing_docs_path():
    errs, _ = el.lint_one("extensions/demo/manifest.json", _good(docs=["docs/nope.md"]))
    assert any("docs 路径不存在" in e for e in errs)


def test_duplicate_names(tmp_path):
    # 用 lint_dir 的 name 去重逻辑：构造两个目录
    import json
    for d in ("a", "b"):
        p = tmp_path / d
        p.mkdir()
        (p / "manifest.json").write_text(json.dumps(_good(name="dup")), encoding="utf-8")
    results = el.lint_dir(str(tmp_path))
    assert any(any("重复" in e for e in r["errors"]) for r in results.values())
