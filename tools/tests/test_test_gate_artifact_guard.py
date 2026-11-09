# -*- coding: utf-8 -*-
"""K-ORG-20261031-ARTGUARDWIRE：test_gate artifact guard 接入的契约测试（静态 + Parser）。

覆盖：
- PowerShell Parser 零语法错误 + UTF-8 BOM（PS5.1 中文解析约定）；
- guard 调用：`release_artifact_guard.py --check --json --release-root ...`，只在 fast/all；
- rc 映射：0=PASS / 1=FAIL(newFailures++ 唯一一次) / 2 与缺脚本/缺 release 根=只告警；
- 汇总行契约：`Suite='artifact'` 行含 Rc/Failed/Known/New/Seconds/Log；
- 前置位置：guard 步骤在跑任何测试套件（foreach $suites）之前；
- 只读：gate 不调用 --lock/--unlock/repair/publish；
- 不回归：B1/B2 清单标记、A5 strict、B5 bundle bootstrap 仍在。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "tools" / "test_gate.ps1"
GUARD = ROOT / "tools" / "release_artifact_guard.py"


def _text() -> str:
    return GATE.read_text(encoding="utf-8-sig")


def _region(text: str, start: str, end: str, occurrence: str = "") -> str:
    """取 start 到 end 之间的片段（occurrence 指定 start 定位用的子串）。"""
    i = text.index(occurrence or start)
    j = text.index(end, i)
    return text[i:j]


def _artifact_block() -> str:
    text = _text()
    return _region(text, "ARTGUARDWIRE", "foreach ($s in $suites)", "ARTGUARDWIRE")


# ── PowerShell 语法 / 编码 ─────────────────────────────────────────────────

def test_ps1_has_utf8_bom():
    """PS5.1 必须 UTF-8 BOM，否则中文注释/字符串按 ANSI 解析出错。"""
    raw = GATE.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "test_gate.ps1 缺 UTF-8 BOM"


@pytest.mark.skipif(os.name != "nt", reason="PowerShell Parser 仅 Windows")
def test_ps1_parses_without_errors():
    path = str(GATE).replace("'", "''")
    cmd = (
        "$e=$null; $t=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$t,[ref]$e) | Out-Null; "
        "Write-Output ('ERRS=' + @($e).Count)" % path
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60)
    assert "ERRS=0" in (r.stdout or ""), r.stdout + r.stderr


# ── guard 调用契约 ─────────────────────────────────────────────────────────

def test_guard_invoked_readonly_check_only():
    block = _artifact_block()
    assert "release_artifact_guard.py" in block
    assert "--check" in block and "--json" in block and "--release-root" in block
    # 只读：不得出现写模式
    for forbidden in ("--lock", "--unlock", "--repair", "--publish"):
        assert forbidden not in block, "artifact guard 步骤不应调用 %s" % forbidden
    # guard 工具本身也不该在这些行出现
    assert "release_artifact_guard.py --lock" not in _text()


def test_guard_step_only_fast_and_all():
    block = _artifact_block()
    assert "$Suite -in @('fast', 'all')" in block


def test_guard_step_is_before_test_suites():
    text = _text()
    assert text.index("ARTGUARDWIRE") < text.index("foreach ($s in $suites)")


# ── rc 映射 / 失败计数 / 汇总行 ────────────────────────────────────────────

def test_rc0_pass_without_failure_increment():
    block = _artifact_block()
    seg = _region(block, "PASS：version=", "} elseif ($grc -eq 1)", "PASS：version=")
    assert "$newFailures++" not in seg
    assert "Suite = 'artifact'; Rc = 0" in seg


def test_rc1_fail_increments_exactly_once():
    block = _artifact_block()
    seg = _region(block, "} elseif ($grc -eq 1) {", "} else {", "} elseif ($grc -eq 1) {")
    assert "FAIL：artifact 完整性失败" in seg
    assert seg.count("$newFailures++") == 1
    assert "Suite = 'artifact'; Rc = 1" in seg
    # 整块只有 rc==1 会增加失败数（缺脚本/缺根/环境错误不得假红或假绿）
    assert block.count("$newFailures++") == 1


def test_missing_tool_and_missing_root_warn_not_block():
    block = _artifact_block()
    tool_warn = _region(block, "缺 tools/release_artifact_guard.py", "} elseif (-not (Test-Path -LiteralPath $releaseRoot)) {",
                        "缺 tools/release_artifact_guard.py")
    assert "只告警" in tool_warn and "Rc = -1" in tool_warn and "$newFailures++" not in tool_warn
    root_warn = _region(block, "无 release 根", "} else {", "无 release 根")
    assert "只告警" in root_warn and "Rc = -2" in root_warn and "$newFailures++" not in root_warn


def test_env_error_warn_line_no_block():
    block = _artifact_block()
    seg = _region(block, "guard 环境错误", block.rstrip()[-40:], "guard 环境错误")
    assert "不阻断" in seg and "$newFailures++" not in seg


def test_summary_rows_contract():
    block = _artifact_block()
    lines = block.splitlines()
    rows = []
    for i, ln in enumerate(lines):
        if "Suite = 'artifact'" in ln:
            # pscustomobject 跨两行：Suite/Rc/Failed/Known 一行，New/Seconds/Log 下一行
            rows.append(ln.strip() + " " + (lines[i + 1].strip() if i + 1 < len(lines) else ""))
    assert len(rows) == 5, rows        # PASS / FAIL / 缺脚本 / 无 release 根 / 环境错误 共 5 个分支
    joined = "\n".join(rows)
    for key in ("Rc", "Failed", "Known", "New", "Seconds", "Log"):
        assert key in joined
    # 每个分支都应写汇总行（含环境错误分支）
    for marker in ("Rc = 0", "Rc = 1", "Rc = -1", "Rc = -2", "Rc = $grc"):
        assert marker in joined, marker


# ── 不回归：B1/B2、A5 strict、B5 bootstrap ─────────────────────────────────

def test_b1_b2_lists_not_regressed():
    text = _text()
    assert "K-ORG-20261031-GATEB2" in text          # B1+B2 扩容标记
    for probe in ("tools/tests/test_merge_gated.py",
                  "agent_platform/tests/test_gate_coverage_w5.py",
                  "tools/tests/test_test_gate_b1.py"):
        assert probe in text, probe                  # 清单标记在
    m = re.search(r"\$suites\s*=\s*@\(([^)]*'portal'[^)]*)\)", text)
    assert m, "未找到 fast 套件清单"
    assert re.findall(r"'([^']+)'", m.group(1)) == [
        'portal', 'kstock', 'ide', 'agent_key', 'quant_key', 'tools_key',
        'datawarehouse', 'analysis', 'commercial',
    ]


def test_a5_strict_gate_not_regressed():
    text = _text()
    assert "check_portal_split.py" in text and "--strict" in text
    assert "[budget] PORTAL-SPLIT strict" in text


def test_b5_bundle_bootstrap_not_regressed():
    text = _text()
    assert "build_agent_ide_bundle.py" in text
    assert "portal/agent_ide_bundle.json" in text
    assert "[bootstrap]" in text


# ── guard 工具契约（被接入者存在且只读） ───────────────────────────────────

def test_guard_tool_contract():
    import json
    assert GUARD.is_file()
    r = subprocess.run([sys.executable, "-X", "utf8", str(GUARD), "--version"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["schema"] == "release_artifact_guard/v1"
    assert payload["modes"] == ["--check", "--lock", "--unlock"]
