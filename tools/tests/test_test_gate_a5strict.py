# -*- coding: utf-8 -*-
"""K-ORG-20261031-A5STRICT 守卫：test_gate.ps1 A5 预算门阶段2 **strict**（2026-10-31）。

静态锁死：
  · fast/all 的 budget 步骤必须以 `python -X utf8 $budget --strict` 调用；
  · strict 违规/执行异常/缺脚本必须计入 `$newFailures`（fail-closed，不假绿）；
  · 汇总仍保留 budget 行（Rc/New/Seconds/Log）与 suiteKey/耗时列；
  · B1/B2 关键集不回归（agent 52 / quant 23 / tools 16 = 91，含黄忠聚合入口），fast 7 套件（2026-11-02 S2-08 起含 datawarehouse）。
动态只读：
  · `check_portal_split.py --strict` 当前必须 PASS（rc=0，0 WARN）——strict 升级的前置条件；
  · PowerShell Parser 校验 test_gate.ps1 可解析、BOM 为 EF BB BF（PS5.1 中文不乱码）。
"""
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
GATE_PATH = REPO / "tools" / "test_gate.ps1"
GATE = GATE_PATH.read_text(encoding="utf-8")
BUDGET_MARKER = "# ===== 2026-10-31 W5 P1-6 / A5"
NAV_MARKER = "# ===== 2026-10-31 P132"


def _budget_block() -> str:
    start = GATE.find(BUDGET_MARKER)
    assert start >= 0, "test_gate.ps1 缺 A5 budget 块（marker: %s）" % BUDGET_MARKER
    end = GATE.find(NAV_MARKER, start)
    if end < 0:
        end = GATE.find("} finally {", start)
    assert end > start, "budget 块未正确闭合"
    return GATE[start:end]


def test_budget_block_uses_strict():
    block = _budget_block()
    assert "check_portal_split.py" in block
    assert "--strict" in block
    assert "python -X utf8 $budget --strict" in block, "阶段2 必须以 --strict 调用预算脚本"
    assert "PASS（strict）" in block
    assert "WARN 0 条" in block or "建议项 0 WARN" in block


def test_budget_strict_fail_visible():
    block = _budget_block()
    # 硬违规/执行异常 + 缺脚本两条路径都必须计入 newFailures（fail-closed）
    assert block.count("$newFailures++") >= 2, "违规与缺脚本都必须判失败，不得假绿"
    assert "fail-closed，不假绿" in block
    assert "缺 tools/check_portal_split.py" in block
    # 汇总行保留 New 字段（失败时 1）
    assert "Suite = 'budget'" in block
    assert "New = $(if ($bpass) { 0 } else { 1 })" in block


def test_budget_keeps_suitekey_and_timing():
    assert "suiteKey" in GATE
    assert "Seconds = $bsecs" in _budget_block()


def test_budget_strict_currently_passes():
    """strict 升级前置：当前仓库 0 违规（api_common 533≤1200、三冻结文件回基线）。"""
    tool = REPO / "tools" / "check_portal_split.py"
    assert tool.is_file()
    r = subprocess.run([sys.executable, "-X", "utf8", str(tool), "--strict"],
                       cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, "strict 当前应 PASS，实为 rc=%d：%s" % (
        r.returncode, (r.stdout or r.stderr)[-500:])
    assert "PORTAL-SPLIT PASS" in r.stdout
    warn_lines = [ln for ln in r.stdout.splitlines() if "PORTAL-SPLIT WARN" in ln]
    assert not warn_lines, "strict 下不应再有 WARN：%s" % warn_lines[:3]


def test_b1_b2_not_regressed():
    import re
    for name, count in (("agent_key", 52), ("quant_key", 23), ("tools_key", 16)):
        m = re.search(r"'%s'\s*=\s*@\((.*?)\n    \)" % name, GATE, re.S)
        assert m, "缺 %s 文件清单" % name
        assert len(re.findall(r"'([^']+\.py)'", m.group(1))) == count, "%s 清单应 %d 文件" % (
            name, count)
    m = re.search(r"'fast'\s*\{\s*\$suites\s*=\s*@\(([^)]*)\)", GATE)
    assert m
    assert re.findall(r"'([a-z_]+)'", m.group(1)) == [
        "portal", "kstock", "ide", "agent_key", "quant_key", "tools_key", "datawarehouse"]


def test_gate_ps1_bom_and_parser(tmp_path):
    raw = GATE_PATH.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf", "test_gate.ps1 必须带 UTF-8 BOM（PS5.1 中文兼容）"
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return  # 非 Windows/无 PowerShell：静态 BOM 已覆盖
    checker = tmp_path / "parse_check.ps1"
    checker.write_text(
        "$target = '%s'\n"
        "$errs = $null\n"
        "[void][System.Management.Automation.Language.Parser]::ParseFile($target, [ref]$null, [ref]$errs)\n"
        "if ($errs.Count -gt 0) { $errs | ForEach-Object { Write-Host $_.Message }; exit 1 }\n"
        "Write-Host 'PARSE-OK'\n" % GATE_PATH,
        encoding="utf-8-sig")
    r = subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(checker)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "PARSE-OK" in r.stdout
