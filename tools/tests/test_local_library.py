# -*- coding: utf-8 -*-
"""本地个人库读取/总结：隐私边界与稳定输出。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import local_library as ll  # noqa: E402


def test_reads_markdown_and_json(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("# 我的研究\n数据资源台账待补\n", encoding="utf-8")
    (tmp_path / "b.json").write_text('{"x": 1}', encoding="utf-8")
    d = ll.build_brief("tf", tmp_path)
    assert d["schema"] == ll.SCHEMA
    assert d["stats"]["files"] == 2
    assert d["documents"][0]["title"]
    assert "我的研究" in d["summary"]
    assert d["privacy"]["raw_uploaded"] is False


def test_hidden_and_non_text_skipped(tmp_path):
    (tmp_path / ".secret.md").write_text("hidden", encoding="utf-8")
    (tmp_path / "ok.md").write_text("# ok", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x89PNG")
    d = ll.build_brief("tf", tmp_path)
    assert d["stats"]["files"] == 1
    assert d["documents"][0]["path"] == "ok.md"


def test_missing_root_and_bad_user(tmp_path):
    assert ll.main(["--user", "tf", "--root", str(tmp_path / "nope")]) == 2
    assert ll.main(["--user", "../etc", "--root", str(tmp_path)]) == 2


def test_out_file(tmp_path):
    (tmp_path / "x.md").write_text("# X", encoding="utf-8")
    out = tmp_path / "brief.json"
    assert ll.main(["--user", "tf", "--root", str(tmp_path), "--out", str(out)]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["user_id"] == "tf" and d["stats"]["files"] == 1


def test_inside_rejects_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("x", encoding="utf-8")
    assert ll._inside(root, outside) is False
    assert ll._inside(root, root / "inside.md") is True


def test_manifest_allowlist_and_exclude(tmp_path):
    (tmp_path / "notes" / "private").mkdir(parents=True)
    (tmp_path / "notes" / "a.md").write_text("# A", encoding="utf-8")
    (tmp_path / "notes" / "private" / "s.md").write_text("# secret", encoding="utf-8")
    (tmp_path / "root.md").write_text("# root", encoding="utf-8")
    (tmp_path / "library.json").write_text(json.dumps({
        "schema": ll.MANIFEST_SCHEMA, "user_id": "tf",
        "sources": ["notes"], "exclude": ["notes/private"]}), encoding="utf-8")
    d = ll.build_brief("tf", tmp_path)
    assert d["stats"]["files"] == 1
    assert d["documents"][0]["path"] == "notes/a.md"
    assert d["manifest"]["present"] is True


def test_manifest_user_mismatch_and_outside(tmp_path):
    (tmp_path / "library.json").write_text(json.dumps({"user_id": "other"}), encoding="utf-8")
    try:
        ll.build_brief("tf", tmp_path); assert False
    except ValueError as e:
        assert "manifest_user_mismatch" in str(e)
    (tmp_path / "library.json").write_text(json.dumps({"user_id": "tf", "sources": ["../"]}), encoding="utf-8")
    try:
        ll.build_brief("tf", tmp_path); assert False
    except ValueError as e:
        assert "manifest_source" in str(e)


def test_cloud_payload_has_no_raw_or_paths(tmp_path):
    (tmp_path / "a.md").write_text("# 私人研究\n原文 secret", encoding="utf-8")
    d = ll.build_brief("tf", tmp_path)
    cp = ll.cloud_payload(d)
    assert cp["schema"] == ll.CLOUD_SCHEMA
    assert cp["privacy"]["raw_uploaded"] is False
    blob = json.dumps(cp, ensure_ascii=False)
    assert "私人研究" in blob  # summary/title allowed
    assert "原文 secret" not in blob
    assert "root" not in cp and "path" not in blob and "excerpt" not in cp["evidence"][0]


def test_cloud_export_requires_authorize(tmp_path):
    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    out = tmp_path / "cloud.json"
    assert ll.main(["--user", "tf", "--root", str(tmp_path), "--export-cloud", str(out)]) == 2
    assert not out.exists()
    assert ll.main(["--user", "tf", "--root", str(tmp_path), "--export-cloud", str(out), "--authorize"]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["privacy"]["confirmed"] is True and d["privacy"]["raw_uploaded"] is False
