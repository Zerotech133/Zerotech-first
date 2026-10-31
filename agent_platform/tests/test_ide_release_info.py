# -*- coding: utf-8 -*-
"""IDE release artifact 版本/哈希契约。"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ide_release_info import build_info  # noqa: E402


def _fixture(tmp_path):
    art = tmp_path / "20260101_000000_abcdef"
    art.mkdir()
    data = b"console.log('x')\n"
    (art / "app.js").write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    (art / "manifest.json").write_text(json.dumps({
        "schema": "legion-ide-release/v1",
        "version": art.name,
        "commit": "deadbeef",
        "files": {"app.js": {"sha256": sha, "bytes": len(data)}},
    }), encoding="utf-8")
    (tmp_path / "current.json").write_text(json.dumps({
        "version": art.name, "dir": str(art),
    }), encoding="utf-8")
    return tmp_path, art, sha


def test_manifest_hashes_match(tmp_path):
    root, art, sha = _fixture(tmp_path)
    info = build_info(root)
    assert info["ok"] and info["all_match"] is True
    assert info["version"] == art.name
    assert info["files"]["app.js"]["sha256"] == sha


def test_corrupt_artifact_detected(tmp_path):
    root, art, sha = _fixture(tmp_path)
    (art / "app.js").write_text("tampered", encoding="utf-8")
    info = build_info(root)
    assert info["ok"] and info["all_match"] is False
    assert info["files"]["app.js"]["match"] is False


def test_missing_current(tmp_path):
    info = build_info(tmp_path)
    assert info["ok"] is False
    assert info["error"] == "release_current_missing"
