# -*- coding: utf-8 -*-
"""panel_export（研报面板 → 门户静态 JSON + selection.html 区块）契约测试 —— 不调 LLM、不写门户目录。

覆盖：
  1. 门户 JSON schema/声明字段（generated_at/免责声明/summary/candidates/reports/review/page_issues）；
  2. 由真实 v3 面板 JSON 导出：候选/被拦截/角色×模型 verdict/页码越界/复核摘要一致性；
  3. CLI export / --check / check 往返；篡改缺声明、坏文件、缺文件时 check 返回非 0；
  4. selection.html 挂载点（区块 id/页签/fetch 路径/降级文案/免责声明），且不动既有区块；
  5. 异常容错：缺输入文件/非对象输入/字段缺失时 build 与校验的降级行为。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXPORT_PATH = REPO / "tools" / "research" / "panel_export.py"
V3_JSON = REPO / "research" / "reports" / "report_panel_v3_20261101.json"
PAGE = REPO / "portal" / "selection.html"


def _load_export():
    spec = importlib.util.spec_from_file_location("panel_export_under_test", EXPORT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pe = _load_export()


# ---------------------------------------------------------------------------
# 1. schema / 声明字段（真实 v3 数据）
# ---------------------------------------------------------------------------
def test_build_payload_schema_and_disclaimer_from_real_v3():
    v3 = pe.load_json(V3_JSON)
    payload = pe.build_portal_payload(v3, source_path=V3_JSON)
    assert pe.validate_portal_payload(payload) == []
    for key in pe.REQUIRED_TOP:
        assert key in payload
    # 声明字段硬要求
    assert "仅供研究" in payload["disclaimer"] and "不构成投资建议" in payload["disclaimer"]
    assert "不构成任何投资建议" in payload["disclaimer_full"]
    assert payload["generated_at"] == v3["generated_at"]
    assert payload["exported_at"]
    assert payload["panel_version"] == v3["schema_version"]
    # 与 v3 数据一致：3 篇评审、1 候选、2 被拦截、1 处精确页码越界
    assert payload["summary"]["reports"] == len(v3["panel"]) == 3
    assert payload["summary"]["candidates"] == len(v3["candidates"]) == 1
    assert payload["summary"]["rejected"] == 2
    assert payload["summary"]["page_invalid_facts"] == 1
    assert payload["summary"]["conclusion"]["verdict"] == "达到"
    assert [c["report_id"] for c in payload["candidates"]] == [c["report_id"] for c in v3["candidates"]]
    # 逐篇：角色×模型 verdict 齐全（3 角色 × 2 模型）
    for row in payload["reports"]:
        assert row["report_id"] and row["title"]
        assert len(row["verdicts"]) == 6
        assert {v["model"] for v in row["verdicts"]} == {"qwen3.5:9b", "qwen3.6:35b-a3b"}
        assert row["role_agreement_rate"] is not None
        assert row["candidate"] == (row["report_id"] in [c["report_id"] for c in payload["candidates"]])
    # 精确页码越界明细
    assert len(payload["page_issues"]) == 1
    issue = payload["page_issues"][0]
    assert issue["report_id"] and issue["page"] is not None and issue["allowed_pages"]
    # 人工复核摘要
    review = payload["review"]
    assert review["file"].endswith("report_panel_v3_review.md")
    assert len(review["items"]) == 3
    assert review["open_checkboxes"] >= 3


# ---------------------------------------------------------------------------
# 2. CLI export / --check / check
# ---------------------------------------------------------------------------
def test_cli_export_check_roundtrip_and_tamper(tmp_path):
    out = tmp_path / "report_panel.json"
    proc = subprocess.run([sys.executable, "-X", "utf8", str(EXPORT_PATH), "export",
                           "--input", str(V3_JSON), "--out", str(out), "--check"],
                          capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["candidates"] and data["reports"]

    proc = subprocess.run([sys.executable, "-X", "utf8", str(EXPORT_PATH), "check", "--out", str(out)],
                          capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    assert "check OK" in proc.stdout

    # 篡改：删掉免责声明 → check 必须失败
    data.pop("disclaimer")
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([sys.executable, "-X", "utf8", str(EXPORT_PATH), "check", "--out", str(out)],
                          capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
    assert proc.returncode == 1
    assert "disclaimer" in (proc.stdout + proc.stderr)


def test_check_handles_missing_and_corrupt_files(tmp_path):
    missing = tmp_path / "nope.json"
    assert pe.check_portal_json(missing)  # 非空错误列表
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert pe.check_portal_json(bad)
    proc = subprocess.run([sys.executable, "-X", "utf8", str(EXPORT_PATH), "check", "--out", str(bad)],
                          capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
    assert proc.returncode == 1


# ---------------------------------------------------------------------------
# 3. selection.html 挂载点
# ---------------------------------------------------------------------------
def test_selection_page_mount_points_and_fallback():
    html = PAGE.read_text(encoding="utf-8")
    assert 'id="report-panel"' in html
    assert "研报候选（多智能体）" in html
    assert "/static/report_panel.json" in html
    for tab in ('data-rp-tab="cand"', 'data-rp-tab="rej"', 'data-rp-tab="review"'):
        assert tab in html
    # 失败降级文案 + 免责声明必须出现在页面
    assert "研报面板暂不可用" in html
    assert "仅供研究，不构成投资建议" in html
    # 不得破坏既有区块（选股/策略/影子盘/观察）
    for anchor in ('id="pick-body"', 'id="strat-body"', 'id="s-nav"', 'id="watch-body"',
                   'id="pausedBanner"', '影子盘'):
        assert anchor in html
    # 页面里只有一处 fetch('/static/report_panel.json...') 调用（注释提及不算）
    assert html.count("fetch('/static/report_panel.json") == 1


# ---------------------------------------------------------------------------
# 4. 异常容错
# ---------------------------------------------------------------------------
def test_build_payload_tolerates_partial_data():
    payload = pe.build_portal_payload({"schema_version": 3, "generated_at": "2026-11-01T00:00:00+08:00",
                                       "panel": [], "candidates": []}, source_path="x.json")
    assert payload["summary"]["reports"] == 0
    assert payload["reports"] == [] and payload["candidates"] == []
    assert payload["disclaimer"] == pe.DISCLAIMER
    # 缺 reports 时 schema 校验应报错（不允许空门户 JSON 上线）
    assert any("reports" in e for e in pe.validate_portal_payload(payload))
    with pytest.raises(pe.PanelExportError):
        pe.build_portal_payload(None)
    with pytest.raises(pe.PanelExportError):
        pe.load_json(Path("C:/__not_exist__/panel.json"))
    with pytest.raises(pe.PanelExportError):
        pe.export_panel(input_path="C:/__not_exist__/v3.json", out_path="C:/__not_exist__/o.json")


def test_validation_catches_missing_disclaimer_and_bad_rows():
    payload = pe.build_portal_payload(pe.load_json(V3_JSON), source_path=V3_JSON)
    tampered = json.loads(json.dumps(payload, ensure_ascii=False))
    tampered["disclaimer"] = "仅供参考"
    errors = pe.validate_portal_payload(tampered)
    assert any("仅供研究" in e or "不构成投资建议" in e for e in errors)
    tampered2 = json.loads(json.dumps(payload, ensure_ascii=False))
    tampered2["reports"][0].pop("verdicts")
    assert any("verdict" in e for e in pe.validate_portal_payload(tampered2))
    tampered3 = json.loads(json.dumps(payload, ensure_ascii=False))
    tampered3["summary"]["candidates"] = 99
    assert any("长度不一致" in e for e in pe.validate_portal_payload(tampered3))


def test_cli_help():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(EXPORT_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0
    assert "export" in proc.stdout and "check" in proc.stdout
