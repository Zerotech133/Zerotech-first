# -*- coding: utf-8 -*-
"""tools/tests/test_eval_report_header.py — 评测报告头工具（wave15 支线 AS）。

覆盖：
  1. make_header 返回 set_id/set_version/n_queries/content_hash/annotation_policy 等字段，
     content_hash 为 64 hex 且与资产/manifest 一致（默认校验 hash）；
  2. header_markdown / header_json 内容包含 set_id + content_hash + 标注规范路径；
  3. 版本缺失抛 FileNotFoundError；资产被篡改时 verify=True 抛 ValueError，verify=False 可加载；
  4. 报告头自检：hash 重算 == 存储 hash（与 eval_registry 校验一致）；
  5. CLI header/list 可运行。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_eval_report_header.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_registry as er  # noqa: E402
import eval_report_header as eh  # noqa: E402

EVAL_DIR = ROOT / "research" / "reports" / "eval_sets"


def _require_assets():
    if not (EVAL_DIR / "manifest.json").exists() or not list(EVAL_DIR.glob("eval_set_*.json")):
        pytest.fail(f"评测集资产缺失: {EVAL_DIR}（test_eval_assets.py 会给出更完整提示）")


# ------------------------------------------------------------------ 1. make_header
def test_make_header_basic():
    _require_assets()
    h = eh.make_header(2)
    assert h["schema_version"] == "eval_report_header.v1"
    assert h["set_id"] == "eval_set_20261101_v2"
    assert h["set_version"] == 2
    assert h["n_queries"] >= 60 and h["n_queries"] == 89
    assert len(h["content_hash"]) == 64 and all(c in "0123456789abcdef"
                                               for c in h["content_hash"])
    assert h["hash_verified"] is True
    policy = h["annotation_policy"]
    assert policy and "policy_ref" in policy and "grades" in policy
    assert h["policy_ref"].endswith("检索评测标注规范_v1.md")
    assert set(policy["grades"]) == {"2", "1", "0"}


# ------------------------------------------------------------------ 2. 与资产/manifest 一致
def test_header_consistent_with_asset_and_manifest():
    _require_assets()
    h = eh.make_header(2)
    asset = er.load(version=2, out_dir=EVAL_DIR)
    assert h["content_hash"] == asset["content_hash"]
    assert h["n_queries"] == asset["n_queries"] == len(asset["queries"])
    assert h["set_id"] == asset["set_id"]
    manifest = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))
    entry = next(s for s in manifest["sets"] if int(s["version"]) == 2)
    assert h["content_hash"] == entry["content_hash"]
    # 自检：hash 重算与存储一致
    assert eh.er.compute_hash(asset) == asset["content_hash"]


# ------------------------------------------------------------------ 3. markdown/json 格式
def test_header_markdown_and_json():
    _require_assets()
    h = eh.make_header(2)
    md = eh.header_markdown(2)
    assert h["set_id"] in md and h["content_hash"] in md
    assert "检索评测标注规范_v1.md" in md
    assert "冻结" in md
    js = json.loads(eh.header_json(2))
    assert js == h


# ------------------------------------------------------------------ 4. 缺失/篡改
def test_header_missing_version_and_tamper(tmp_path):
    _require_assets()
    with pytest.raises(FileNotFoundError):
        eh.make_header(99)
    # 复制资产到 tmp 后篡改 → 默认 verify 抛 ValueError；verify=False 仍可读
    for name in ("manifest.json", "eval_set_20261101_v2.json"):
        shutil.copy(EVAL_DIR / name, tmp_path / name)
    p = tmp_path / "eval_set_20261101_v2.json"
    asset = json.loads(p.read_text(encoding="utf-8"))
    asset["queries"][0]["type"] = "被篡改"
    p.write_text(json.dumps(asset, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        eh.make_header(2, out_dir=tmp_path, verify=True)
    raw = eh.make_header(2, out_dir=tmp_path, verify=False)
    assert raw["hash_verified"] is False and raw["set_version"] == 2


# ------------------------------------------------------------------ 5. CLI
def test_cli_header_and_list(capsys):
    _require_assets()
    rc = eh.main(["header", "--version", "2", "--format", "markdown"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "eval_set_20261101_v2" in out and "content_hash" in out
    rc2 = eh.main(["header", "--version", "2", "--format", "json"])
    assert rc2 == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["set_version"] == 2
    rc3 = eh.main(["list"])
    assert rc3 == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["n_sets"] >= 1
