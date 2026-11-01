# -*- coding: utf-8 -*-
"""tools/tests/test_eval_registry.py — 评测集资产化（wave14 支线 AP）单元测试。

覆盖（任务验收 ≥4）：
  1. freeze：导出资产 + manifest，hash 为 sha256(64 hex)，n/语法分布与源一致，verify 通过；
  2. hash 稳定性与内容寻址：重复 freeze 内容 hash 不变（时间戳不参与），篡改查询 → verify 失败；
  3. 规范字段检查失败：n<60 / 缺 grade-2 / 缺题型 / 配额不足 → 返回明确错误；
  4. load/list/verify 结构：按版本加载、缺失版本报错、缺文件校验失败、目录全量校验。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_eval_registry.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import eval_registry as er  # noqa: E402


def _minimal_asset(n: int = 2, types: tuple[str, ...] = ("主题",)) -> dict:
    """构造最小资产（用于规范校验失败用例）。"""
    queries = [{"id": f"T{i}", "q": f"q{i}", "type": types[i % len(types)],
                "source": "test",
                "graded": {"2": [f"kw{i}"], "1": []},
                "expected_units": [{"unit": i, "grade": 2}]}
               for i in range(n)]
    asset = {"schema_version": er.SCHEMA_VERSION, "set_id": "test_set", "version": 99,
             "frozen_at": "2026-11-01T00:00:00+08:00", "source": "test",
             "n_queries": n, "type_distribution": {}, "annotation_policy": er.ANNOTATION_POLICY,
             "queries": queries}
    asset["content_hash"] = er.compute_hash(asset)
    return asset


# ------------------------------------------------------------------ 1. freeze
def test_freeze_creates_asset_and_manifest(tmp_path):
    rep = er.freeze(out_dir=tmp_path)
    assert rep["validation"]["ok"] is True
    asset_path = Path(rep["asset_path"])
    manifest_path = Path(rep["manifest_path"])
    assert asset_path.exists() and manifest_path.exists()
    asset = json.loads(asset_path.read_text(encoding="utf-8"))
    assert asset["schema_version"] == "eval_set.v1"
    assert asset["version"] == 2 and asset["n_queries"] == 89
    assert len(asset["queries"]) == 89
    assert len(asset["content_hash"]) == 64 and all(c in "0123456789abcdef"
                                                   for c in asset["content_hash"])
    # 每条查询都有分级标注与展开的 expected_units
    for q in asset["queries"]:
        assert q["graded"]["2"], f"{q['id']} 缺 grade-2"
        assert q["expected_units"], f"{q['id']} expected_units 为空"
        assert set(q["graded"]) <= {"1", "2"}
    # manifest 记录版本/hash/查询数/语法分布
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["sets"][0]
    assert entry["version"] == 2 and entry["content_hash"] == asset["content_hash"]
    assert entry["n_queries"] == 89 and entry["validation_ok"] is True
    assert sum(entry["type_distribution"].values()) == 89
    # 规范校验结果
    v = er.verify(asset_path)
    assert v["ok"] is True and v["hash_match"] is True


# ------------------------------------------------------------------ 2. hash 稳定性 / 篡改检测
def test_hash_deterministic_and_tamper_detection(tmp_path):
    a1 = er.build_asset()
    a2 = er.build_asset()
    assert a1["content_hash"] == a2["content_hash"]   # 时间戳不影响 hash
    assert len(a1["content_hash"]) == 64
    # 冻结到磁盘后篡改任一查询 → verify 必须失败
    rep = er.freeze(out_dir=tmp_path)
    p = Path(rep["asset_path"])
    asset = json.loads(p.read_text(encoding="utf-8"))
    asset["queries"][0]["graded"]["2"] = ["被篡改的关键词"]
    p.write_text(json.dumps(asset, ensure_ascii=False), encoding="utf-8")
    v = er.verify(p)
    assert v["ok"] is False and v["hash_match"] is False
    assert any("content_hash" in e for e in v["errors"])


# ------------------------------------------------------------------ 3. 规范字段检查失败
def test_spec_validation_failures():
    # n < 60
    errs = er.validate_spec(_minimal_asset(n=2))
    assert any("规范下限 60" in e for e in errs)
    # 缺少 grade-2 标注
    a = _minimal_asset(n=70)
    a["queries"][0]["graded"]["2"] = []
    errs = er.validate_spec(a)
    assert any("grade-2" in e for e in errs)
    # 缺少题型（只有主题）
    errs = er.validate_spec(_minimal_asset(n=70, types=("主题",)))
    assert any("缺少题型: 同义词" in e for e in errs)
    # 配额不足：同义词类数量 < 8（构造 60 条但同义词仅 2 条）
    a2 = _minimal_asset(n=61, types=("主题", "同义词"))
    types = ["主题"] * 59 + ["同义词"] * 2
    for q, t in zip(a2["queries"], types):
        q["type"] = t
    errs = er.validate_spec(a2)
    assert any("同义词" in e and "低于规范下限" in e for e in errs)
    # id 重复
    a3 = _minimal_asset(n=70)
    a3["queries"][1]["id"] = a3["queries"][0]["id"]
    assert any("id 存在重复" in e for e in er.validate_spec(a3))


# ------------------------------------------------------------------ 4. load / list / verify 结构
def test_load_list_verify_structure(tmp_path):
    er.freeze(out_dir=tmp_path)
    listed = er.list_sets(tmp_path)
    assert listed["n_sets"] == 1 and listed["sets"][0]["version"] == 2
    asset = er.load(version=2, out_dir=tmp_path)
    assert asset["n_queries"] == 89
    assert asset["set_id"] == "eval_set_20261101_v2"
    # 缺失版本 → 报错
    with pytest.raises(FileNotFoundError):
        er.load(version=99, out_dir=tmp_path)
    # 目录全量校验
    allv = er.verify_all(tmp_path)
    assert allv["ok"] is True and allv["n_sets"] == 1
    # 缺文件 → 校验失败
    bad = er.verify(tmp_path / "not_exists.json")
    assert bad["ok"] is False and "不存在" in bad["errors"][0]
    # 结构缺字段 → 校验失败
    broken = _minimal_asset(n=70)
    broken.pop("schema_version")
    rep = er.validate_asset(broken, verify_hash=False)
    assert rep["ok"] is False and any("缺少字段: schema_version" in e for e in rep["errors"])
