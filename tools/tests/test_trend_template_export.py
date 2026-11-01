# -*- coding: utf-8 -*-
"""tools/tests/test_trend_template_export.py — 最后一个中风险脚本写入加固（wave31 收尾）。

覆盖（任务验收 ≥3）：
  1. export_payload：写盘合法 + 二次写入生成写前备份 + 原子写（无 tmp 残留）；
  2. 校验失败不覆盖：不可序列化 payload → SafeJsonError，目标文件字节不变；
  3. main() 端到端（合成 trend_template 报告）：返回 0、门户 schema 字段齐全、二次运行产生备份；
  4. 不泄露内部路径（sources 仅公开链接；无 research/reports 原路径）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_trend_template_export.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import safe_json as sj  # noqa: E402


def _load():
    p = ROOT / "tools" / "research" / "trend_template_export.py"
    spec = importlib.util.spec_from_file_location("trend_export_under_test", p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def _tmp_backup(monkeypatch, tmp_path):
    """把 safe_json 备份目录指向 tmp，避免污染 research/reports/backups。"""
    orig = mod.backup_then_atomic_write
    monkeypatch.setattr(
        mod, "backup_then_atomic_write",
        lambda path, payload, **kw: orig(path, payload, backup_dir=tmp_path / "backups", **kw))


def _fake_report(date: str = "20261030") -> dict:
    return {
        "meta": {"date": date, "adjust": "qfq", "year_bars": 250,
                 "c3_method": "endpoint_proxy", "rs_method": "r252_approx"},
        "universe": {"all_codes": 5571, "base_codes": 5000},
        "condition_pass_rate": {"C1_pct": 30.0, "C2_pct": 25.0},
        "pass_counts": {"pass_all": 12},
        "near_miss_7": 34,
        "candidates": [{"code": "000001.SZ", "name": "平安银行", "close": 11.6,
                        "RS_approx": 88.0, "S50": 10.9, "S150": 10.5, "S200": 10.2,
                        "low_dist_pct": 32.0, "high_dist_pct": 80.0}],
        "near_miss_list": [{"code": "000002.SZ", "name": "万科A", "RS_approx": 66.0}],
    }


# ------------------------------------------------------------------ 1. 备份 + 原子写
def test_export_payload_backup_and_atomic(tmp_path, monkeypatch):
    _tmp_backup(monkeypatch, tmp_path)
    out = tmp_path / "latest.json"
    first = mod.export_payload({"v": 1}, out)
    assert first["ok"] is True and out.exists()
    old_bytes = out.read_bytes()
    second = mod.export_payload({"v": 2}, out)
    backups = list((tmp_path / "backups").glob("latest.*.bak.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == old_bytes
    assert json.loads(out.read_text(encoding="utf-8"))["v"] == 2
    assert second["sha_before"] and second["sha_after"]
    assert not list(out.parent.glob(".latest.*.tmp"))


# ------------------------------------------------------------------ 2. 校验失败不覆盖
def test_serialization_failure_keeps_target(tmp_path, monkeypatch):
    _tmp_backup(monkeypatch, tmp_path)
    out = tmp_path / "latest.json"
    mod.export_payload({"ok": True}, out)
    before = out.read_bytes()
    try:
        mod.export_payload({"bad": object()}, out)   # 不可 JSON 序列化
        assert False, "应抛 SafeJsonError"
    except mod.SafeJsonError:
        pass
    assert out.read_bytes() == before
    assert not list(out.parent.glob(".latest.*.tmp"))


# ------------------------------------------------------------------ 3. main 端到端 + schema
def test_main_end_to_end_schema(tmp_path, monkeypatch):
    _tmp_backup(monkeypatch, tmp_path)
    reports = tmp_path / "research" / "reports"
    reports.mkdir(parents=True)
    (reports / "trend_template_20261030.json").write_text(
        json.dumps(_fake_report(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "portal" / "static" / "trend_template_latest.json"
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "REPORTS", reports)
    monkeypatch.setattr(mod, "OUT", out)
    assert mod.main() == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    for key in ("api_version", "kind", "generated_at", "trade_date", "dataset",
                "conditions", "results", "candidates", "near_miss_sample",
                "method", "boundary_notes", "sources", "disclaimer"):
        assert key in payload, f"门户 schema 缺字段 {key}"
    assert payload["trade_date"] == "20261030"
    assert payload["candidates"][0]["code"] == "000001.SZ"
    # 二次运行 → 写前备份（历史 latest 保留）
    assert mod.main() == 0
    assert list((tmp_path / "backups").glob("trend_template_latest.*.bak.json"))


# ------------------------------------------------------------------ 4. 不泄露内部路径
def test_no_internal_paths_leaked(tmp_path, monkeypatch):
    _tmp_backup(monkeypatch, tmp_path)
    reports = tmp_path / "research" / "reports"
    reports.mkdir(parents=True)
    (reports / "trend_template_20261030.json").write_text(
        json.dumps(_fake_report(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "latest.json"
    monkeypatch.setattr(mod, "REPO", tmp_path)
    monkeypatch.setattr(mod, "REPORTS", reports)
    monkeypatch.setattr(mod, "OUT", out)
    mod.main()
    text = out.read_text(encoding="utf-8")
    assert str(tmp_path).replace("\\", "/") not in text
    assert "C:\\项目" not in text and "C:/项目" not in text
