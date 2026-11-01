# -*- coding: utf-8 -*-
"""tools/tests/test_eval_assets.py — 评测资产 hash 守卫（wave15 支线 AS，轻量质量门）。

不修改 `tools/test_gate.ps1`（避免动质量门核心），以 pytest 形式提供守卫：
  1. `research/reports/eval_sets/` 全部资产经 `eval_registry.verify` 校验通过
     （content_hash 重算一致 + 结构 + 规范字段 n≥60/每查询≥1 grade-2/题型配额）；
  2. manifest 与资产一致（file/hash/n_queries/语法分布/validation_ok）；
  3. 至少存在 version 2 资产且 n ≥ 60；
  4. 篡改/缺失资产时给出清晰失败（用 tmp 副本模拟，不污染真实资产）；
  5. `expected_units` 已展开（审计可用）。

纳入质量门建议（本次**不修改** test_gate）：在 `tools/test_gate.ps1` 的
`$suites` 配置中 `'tools_key' = @( ... )` 追加一行
`'tools/tests/test_eval_assets.py',`（tools_key 属 fast 套件），即可在合并门生效。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_eval_assets.py
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

EVAL_DIR = ROOT / "research" / "reports" / "eval_sets"


def _require_assets():
    """守卫：资产目录/文件缺失时明确 fail（防止被静默删除）。"""
    if not EVAL_DIR.exists():
        pytest.fail(f"评测集资产目录缺失: {EVAL_DIR}（评测资产被删除/未生成）")
    sets = sorted(EVAL_DIR.glob("eval_set_*.json"))
    if not sets:
        pytest.fail(f"{EVAL_DIR} 下无 eval_set_*.json 资产（评测资产缺失）")
    if not (EVAL_DIR / "manifest.json").exists():
        pytest.fail(f"manifest.json 缺失: {EVAL_DIR / 'manifest.json'}")
    return sets


# ------------------------------------------------------------------ 1. 真实资产校验
def test_eval_sets_verify_all_ok():
    _require_assets()
    rep = er.verify_all(EVAL_DIR)
    assert rep["ok"] is True, f"评测资产校验失败: {json.dumps(rep, ensure_ascii=False)}"
    assert rep["n_sets"] >= 1
    for s in rep["sets"]:
        assert s["ok"] is True, f"{s.get('file')} 校验失败: {s.get('errors')}"
        assert s["hash_match"] is True
        assert len(s["hash"] or "") == 64


# ------------------------------------------------------------------ 2. version 2 / n>=60
def test_version2_present_and_n_ge_60():
    _require_assets()
    manifest = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))
    versions = [int(s["version"]) for s in manifest.get("sets") or []]
    assert 2 in versions, f"manifest 缺少 version 2 资产（现有 {versions}）"
    asset = er.load(version=2, out_dir=EVAL_DIR)
    assert asset["n_queries"] >= 60, f"v2 评测集 n={asset['n_queries']} < 规范下限 60"
    assert asset["n_queries"] == len(asset["queries"])
    assert asset["schema_version"] == "eval_set.v1"
    # 规范字段复检（避免 verify 逻辑被误改时静默放行）
    assert er.validate_spec(asset) == []


# ------------------------------------------------------------------ 3. manifest 一致性
def test_manifest_consistent_with_assets():
    _require_assets()
    manifest = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))
    sets = manifest.get("sets") or []
    assert sets, "manifest.sets 为空"
    for entry in sets:
        f = EVAL_DIR / entry["file"]
        assert f.exists(), f"manifest 登记的资产文件缺失: {f}"
        asset = json.loads(f.read_text(encoding="utf-8"))
        assert entry["content_hash"] == asset["content_hash"], (
            f"manifest hash 与资产不一致: {entry['file']}")
        assert entry["n_queries"] == asset["n_queries"]
        assert entry["set_id"] == asset["set_id"]
        assert entry.get("validation_ok") is True
        # 语法分布一致
        assert entry["type_distribution"] == asset["type_distribution"]
        # 每条查询均有 grade-2 与展开的 expected_units
        for q in asset["queries"]:
            assert q["graded"]["2"], f"{entry['file']} {q['id']} 缺 grade-2"
            assert q["expected_units"], f"{entry['file']} {q['id']} expected_units 为空"


# ------------------------------------------------------------------ 4. 篡改 / 缺失清晰失败
def test_tampered_asset_fails_clearly(tmp_path):
    _require_assets()
    # 复制整个资产目录到 tmp 后篡改一个查询（不污染真实资产）
    for name in ("manifest.json", "eval_set_20261101_v2.json"):
        shutil.copy(EVAL_DIR / name, tmp_path / name)
    p = tmp_path / "eval_set_20261101_v2.json"
    asset = json.loads(p.read_text(encoding="utf-8"))
    asset["queries"][0]["graded"]["2"] = ["被篡改的关键词"]
    p.write_text(json.dumps(asset, ensure_ascii=False), encoding="utf-8")
    v = er.verify(p)
    assert v["ok"] is False
    assert v["hash_match"] is False
    assert any("content_hash" in e for e in v["errors"]), f"应明确提示 hash 不匹配: {v}"
    assert er.verify_all(tmp_path)["ok"] is False


def test_missing_or_broken_asset_fails_clearly(tmp_path):
    # 空目录 → 目录级校验失败且信息明确
    rep = er.verify_all(tmp_path)
    assert rep["ok"] is False
    assert any("无 eval_set" in e for e in rep["errors"])
    # 单文件缺失
    v = er.verify(tmp_path / "no_such_set.json")
    assert v["ok"] is False and "不存在" in v["errors"][0]
    # manifest 缺失 → load 报错
    with pytest.raises(FileNotFoundError):
        er.load(version=2, out_dir=tmp_path)
    # 结构缺字段 → 规范校验失败并指名缺字段
    broken = {"schema_version": "eval_set.v1", "queries": [], "n_queries": 0}
    rep2 = er.validate_asset(broken, verify_hash=False)
    assert rep2["ok"] is False
    assert any("缺少字段" in e for e in rep2["errors"])
