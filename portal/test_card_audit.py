# -*- coding: utf-8 -*-
"""H-B · card_audit 审计脚本单测（P3-lite 波次 2026-10-20，离线只读）。

纪律：零网络、零真实发送、零写盘（audit()/render_report() 纯内存）；
覆盖：审计可跑且实测 kind 数齐全、新注册 km_card 模板 header 齐全、
text 发送 kind（dead_letter_alert）不注册并给出口径、无发送语境 kind
（deadline_watch）不注册并说明、注册表快照同步（FC-C 判例）、
渲染报告含关键结论。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import card_audit  # noqa: E402
import test_card_templates  # noqa: E402  （FC-C 快照：ALL_KINDS/_COLORS/_TITLES）
from card_templates import KIND_CARD_TEMPLATES, apply_card_template  # noqa: E402

_RESULT = card_audit.audit()


# 1. 审计可跑：扫描覆盖齐、零读取失败、实测 kind 数达标


def test_audit_runs_with_measured_kinds():
    s = _RESULT["summary"]
    assert s["n_scanned"] >= 100  # 硬编码清单 132 个生产文件
    assert s["n_scan_errors"] == 0
    assert s["n_kinds"] >= 15  # 实测 20 种
    assert s["n_registered"] >= 6  # 5 既有 + km_card
    assert _RESULT["scan_errors"] == []
    assert len(_RESULT["scanned_files"]) == s["n_scanned"]


# 2. 新注册 kind：km_card 模板 header 齐全（蓝「🗂 KM 知识卡」）


def test_km_card_registered_header_complete():
    assert "km_card" in KIND_CARD_TEMPLATES
    out, mt = apply_card_template("km_card", {"text": "KM 变更摘要"}, "interactive")
    assert mt == "interactive"
    assert out["config"].get("wide_screen_mode") is True
    assert out["header"]["template"] == "blue"
    assert out["header"]["title"]["content"] == "🗂 KM 知识卡"
    assert out["header"]["title"]["tag"] == "plain_text"
    assert out["elements"][0]["text"] == {"text": "KM 变更摘要"}
    # str JSON content 同样包装（发送侧实际以 JSON 字符串过闸）
    out2, _ = apply_card_template(
        "km_card", json.dumps({"text": "km"}, ensure_ascii=False), "interactive"
    )
    assert json.loads(out2)["header"]["title"]["content"] == "🗂 KM 知识卡"


# 3. text 发送 kind 不注册：dead_letter_alert 实测 text → skip 并给出口径


def test_dead_letter_alert_not_registered_with_text_verdict():
    assert "dead_letter_alert" not in KIND_CARD_TEMPLATES
    entry = _RESULT["kinds"]["dead_letter_alert"]
    assert entry["registered"] is False
    assert entry["verdict"] == "skip_text_send"
    assert "text" in entry["msg_types"]
    assert "push_admin_server.py:870" in entry["occurrences"][0]["send_point"]
    assert "text" in entry["suggestion"]  # 汇报说明注册无效果口径


# 4. 无发送语境 kind 不注册：deadline_watch → skip 并挂账说明


def test_deadline_watch_not_registered_no_send_ctx():
    assert "deadline_watch" not in KIND_CARD_TEMPLATES
    entry = _RESULT["kinds"]["deadline_watch"]
    assert entry["registered"] is False
    assert entry["verdict"] == "skip_no_send_ctx"
    assert entry["msg_types"] == []
    assert "api_notify.py:313" in entry["occurrences"][0]["send_point"]


# 5. 注册表快照同步（FC-C 判例）：ALL_KINDS/_COLORS/_TITLES 与注册表一致


def test_registry_snapshot_synced():
    assert set(KIND_CARD_TEMPLATES.keys()) == test_card_templates.ALL_KINDS
    for kind, fn in KIND_CARD_TEMPLATES.items():
        assert callable(fn)
        card = fn({"text": "x"})
        assert card["header"]["template"] == test_card_templates._COLORS[kind]
        assert card["header"]["title"]["content"] == test_card_templates._TITLES[kind]
        assert card["header"]["title"]["tag"] == "plain_text"


# 6. 审计条目结构完整：每 kind 均有 发送点/msg_type/注册状态/建议


def test_audit_entries_complete_fields():
    assert set(_RESULT["registered_kinds"]) == set(KIND_CARD_TEMPLATES)
    for kind, entry in _RESULT["kinds"].items():
        assert entry["occurrences"], kind
        assert entry["verdict"] in (
            "registered",
            "register_candidate",
            "skip_text_send",
            "skip_no_send_ctx",
            "observe",
        )
        assert isinstance(entry["suggestion"], str) and entry["suggestion"]
        assert entry["registered"] == (kind in KIND_CARD_TEMPLATES)
        for occ in entry["occurrences"]:
            assert occ["file"] in card_audit.SCAN_FILES
            assert ":" in occ["send_point"]  # 含 文件:行号 发送点定位


# 7. 渲染报告含关键结论（对照表可读、kind 全覆盖）


def test_render_report_contains_key_findings():
    report = card_audit.render_report(_RESULT)
    for kind in (
        "km_card",
        "dead_letter_alert",
        "deadline_watch",
        "inbound:feed",
        "osint:cookie_expired",
    ):
        assert kind in report
    assert "已注册模板" in report
    assert "🗂 KM 知识卡" in report or "补注册" in report
    assert report.count("\n") > 20  # 对照表非空壳
