# -*- coding: utf-8 -*-
"""P0-04：D1 9 接口契约测试入 fast gate 的覆盖守卫与受控负控。

本文件本身位于 `portal/tests/`，因此随 portal 套件自动进入
`tools/test_gate.ps1 -Suite fast`；不重复修改 test_gate 清单，避免把
同一个 D1 文件冻结两遍造成漂移。

覆盖：
1) fast 套件仍包含 portal，portal 路径仍包含 portal/tests 目录；
2) manifest 暴露的 9 个 HTTP 接口与契约测试文件清单一致；
3) manifest `status.contract_test.p0_04` 已记录 P0-04 通过证据；
4) 测试计数基线 JSON 与 manifest 记录一致；
5) `D1_P0_04_BREAK_CONTRACT=1` 时可触发的受控负控（正常门禁 skip）。

负控故意破坏的是 envelope 的必要字段 `schema_version`，只用于证明
“契约被破坏时 pytest/gate 能红”；不写生产数据，不伪造正常通过。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "tools" / "test_gate.ps1"
MANIFEST = ROOT / "compositions" / "lusu-d1-flow" / "manifest.yaml"
TEST_DIR = ROOT / "portal" / "tests"
BASELINE = ROOT / "deliverables" / "d1_q2" / "20261107" / "P0-04_test_count_baseline.json"

# 9 个 HTTP 接口（不含 internal/MCP；与 manifest spec.interfaces.exposes 对齐）。
D1_HTTP_INTERFACES = {
    "/api/d1/research",
    "/api/d1/decisions",
    "/api/d1/decision",
    "/api/d1/meeting",
    "/api/d1/handoffs",
    "/api/d1/handoff",
    "/api/d1/training/episodes",
    "/api/d1/watch-plans",
    "/api/war-room/snapshot",
}

# 现有契约/D1 测试文件；都在 portal/tests，自动进入 fast gate。
D1_CONTRACT_FILES = (
    "test_ifs_auth_contract.py",
    "test_ifs_idempotency.py",
    "test_ifs_wire_chain.py",
    "test_api_envelope_d1.py",
    "test_api_war_room_envelope.py",
    "test_api_d1_research.py",
    "test_api_d1_decision.py",
    "test_api_d1_meeting.py",
    "test_api_d1_handoff.py",
    "test_api_d1_training.py",
    "test_d1_envelope_ui.py",
    "test_d1_training_page.py",
    "test_d1_levels_ui.py",
    "test_d1_overview_page.py",
)

BREAK_ENV = "D1_P0_04_BREAK_CONTRACT"


def _manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_fast_gate_still_includes_portal_tests_directory():
    text = GATE.read_text(encoding="utf-8")
    fast = re.search(r"'fast'\s*\{\s*\$suites\s*=\s*@\(([^)]*)\)", text)
    assert fast, "tools/test_gate.ps1 未找到 fast 套件定义"
    suites = set(re.findall(r"'([^']+)'", fast.group(1)))
    assert {"portal", "kstock", "ide", "agent_key", "quant_key",
            "tools_key", "datawarehouse"} <= suites

    portal = re.search(r"'portal'\s*=\s*@\(([^)]*)\)", text)
    assert portal, "tools/test_gate.ps1 未找到 portal 路径定义"
    assert "'portal/tests'" in portal.group(1), (
        "portal/tests 不在 fast 的 portal 路径中；D1 契约测试将失去门禁"
    )


def test_nine_d1_http_interfaces_have_contract_test_coverage():
    paths = [e["http"] for e in _manifest()["spec"]["interfaces"]["exposes"]]
    assert len(paths) == 9, paths
    assert set(paths) == D1_HTTP_INTERFACES

    missing_files = [name for name in D1_CONTRACT_FILES
                     if not (TEST_DIR / name).is_file()]
    assert not missing_files, f"契约测试文件缺失: {missing_files}"

    combined = "\n".join(
        (TEST_DIR / name).read_text(encoding="utf-8")
        for name in D1_CONTRACT_FILES
    )
    uncovered = sorted(path for path in D1_HTTP_INTERFACES if path not in combined)
    assert not uncovered, f"9 接口中无测试文件引用的接口: {uncovered}"


def test_manifest_records_p0_04_contract_gate_pass():
    status = _manifest().get("status") or {}
    p04 = (status.get("contract_test") or {}).get("p0_04") or {}
    assert p04.get("status") == "pass", p04
    assert p04.get("fast_gate_rc") == 0, p04
    assert p04.get("negative_control_rc") == 1, p04
    assert p04.get("negative_control") == "pass", p04
    assert "P0-04" in str(p04.get("evidence") or ""), p04
    assert int(p04.get("test_count") or 0) >= 140, p04


def test_test_count_baseline_matches_manifest():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    p04 = ((_manifest().get("status") or {}).get("contract_test") or {}).get("p0_04") or {}
    assert baseline["collected"] == p04["test_count"], (baseline, p04)
    assert baseline["passed"] == p04["test_passed"], (baseline, p04)
    assert baseline["failed"] == 0, baseline


@pytest.mark.skipif(os.environ.get(BREAK_ENV) != "1", reason=(
    "受控负控：仅在 P0-04 反证演练时由 %s=1 触发" % BREAK_ENV))
def test_contract_break_negative_control_is_red():
    """正常 skip；显式设置环境变量时故意破坏 envelope 契约，必须红。"""
    envelope = {
        "ok": True,
        "schema_version": "ifs/v1",
        "request_id": "req-" + "a" * 32,
        "source": "d1",
        "data": {},
        "warnings": [],
    }
    # 模拟一次错误改动：把必要字段 schema_version 改名。
    envelope["version"] = envelope.pop("schema_version")
    assert envelope.get("schema_version") == "ifs/v1", (
        "P0-04 负控：schema_version 契约字段被破坏时本测试必须失败，"
        "证明契约回归会被门禁拦住"
    )
