# -*- coding: utf-8 -*-
"""K-ORG-20261031-ORGGATEWIRE 守卫：test_gate.ps1 的 org 模型一致性 gate（warn-only）。

静态锁死：
  · org gate 只在 fast/all 跑，调 `python tools/org_model_consistency.py --json`；
  · **warn-only**：块内不得出现 `$newFailures++`（不得阻断），必须有 WARN-ONLY/不阻断；
  · JSON 解析（ConvertFrom-Json）与 fail/warn/info 汇总行；
  · 缺脚本/解析失败只告警不阻断；
  · B1/B2 关键集不回归（agent 52 / quant 23 / tools 13 = 88）。
动态只读：org 工具 --json 契约与当前真实 fail>0（配置债）；「fail>0 仍 GATE PASS」
由实跑 gate 证据覆盖（见 C:\\项目\\work\\_dsh_orggate_20261031\\）。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
GATE = (REPO / "tools" / "test_gate.ps1").read_text(encoding="utf-8")
ORG_TOOL = REPO / "tools" / "org_model_consistency.py"
MARKER = "K-ORG-20261031-ORGGATEWIRE：org 模型一致性 gate（warn-only）"


def _org_block() -> str:
    start = GATE.find(MARKER)
    assert start >= 0, "test_gate.ps1 缺 org gate 块（marker: %s）" % MARKER
    end = GATE.find("} finally {", start)
    assert end > start, "org gate 块未闭合到 finally"
    return GATE[start:end]


def test_org_gate_wired_fast_all_warn_only():
    block = _org_block()
    assert "org_model_consistency.py" in block
    assert "--json" in block
    assert "ConvertFrom-Json" in block
    assert "WARN-ONLY" in block and "不阻断" in block
    assert "$newFailures++" not in block, "org gate 必须 warn-only，不得阻断门禁"
    assert re.search(r"if \(\$Suite -in @\('fast', 'all'\)\)", block), "org gate 应只在 fast/all 跑"


def test_org_gate_missing_tool_only_warns():
    block = _org_block()
    assert "缺 tools/org_model_consistency.py" in block
    assert "只告警，不阻断" in block
    tail = block[block.find("缺 tools/org_model_consistency.py"):]
    assert "$newFailures++" not in tail, "缺脚本时也必须 warn-only（不阻断）"


def test_org_gate_summary_row():
    block = _org_block()
    assert "Suite = 'org_gate'" in block
    assert "Failed = $(if ($oparse) { $ofail } else { 0 })" in block
    assert "Known = $(if ($oparse) { $owarn } else { 0 })" in block
    assert "New = 0" in block, "warn-only 汇总行 New 必须恒为 0"


def test_fast_suites_and_b1_b2_not_regressed():
    m = re.search(r"'fast'\s*\{\s*\$suites\s*=\s*@\(([^)]*)\)", GATE)
    assert m, "test_gate.ps1 缺 fast 定义"
    assert re.findall(r"'([a-z_]+)'", m.group(1)) == [
        "portal", "kstock", "ide", "agent_key", "quant_key", "tools_key", "datawarehouse"]
    for name, count in (("agent_key", 52), ("quant_key", 23), ("tools_key", 14)):
        m = re.search(r"'%s'\s*=\s*@\((.*?)\n    \)" % name, GATE, re.S)
        assert m, "缺 %s 清单" % name
        files = re.findall(r"'([^']+\.py)'", m.group(1))
        assert len(files) == count, "%s 应 %d 文件（B1+B2 不回归），实为 %d" % (
            name, count, len(files))


def test_org_tool_json_contract_readonly():
    assert ORG_TOOL.is_file(), "缺 tools/org_model_consistency.py"
    r = subprocess.run([sys.executable, "-X", "utf8", str(ORG_TOOL), "--json"],
                       cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode in (0, 1), (r.stderr or "")[-500:]
    data = json.loads(r.stdout)
    assert data.get("schema") == "org_model_consistency/v1"
    summary = data["summary"]
    for key in ("fail", "warn", "info"):
        assert isinstance(summary[key], int) and summary[key] >= 0
    assert data["ok"] == (summary["fail"] == 0)
    assert data["exit_code"] == (0 if summary["fail"] == 0 else 1)
