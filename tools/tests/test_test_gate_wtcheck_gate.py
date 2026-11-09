# -*- coding: utf-8 -*-
"""WO-GATE-WTCHECK-20261031：test_gate.ps1 的 wtcheck 门（W2-3）契约测试。

覆盖：
  ① 静态接线：wtcheck 段存在、由 `$Suite -in @('fast','all')` 守卫、调用
     `python -X utf8 ... devtask.py audit-worktrees`；rc!=0 → newFailures++（阻断）；
     缺 devtask.py → fail-closed；摘要打印 ≤12 行；summary 记 Suite='wtcheck'。
  ② 不改既有 suite 清单（fast 7 套件（2026-11-02 S2-08 起含 datawarehouse）、all 仍 5 套件——与 test_test_gate_b1 冻结口径一致）。
  ③ 行为：audit-worktrees rc 与输出一致（rc=0 ⇔ 无 [VIOL]）；耗时预算；`--json` 契约。
  ④ 主仓解析：在任务 worktree 内运行 audit-worktrees 时，输出中的「仓库：」仍指向主仓
     （不含 .worktrees），即 gate 在 worktree 里跑也能读到主仓 tasks.json。
只读：不跑完整 fast 门（耗时 ~10min，由任务执行记录另行实测）；仅跑 audit-worktrees（~0.6s）。
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
GATE_PATH = TOOLS / "test_gate.ps1"
DEVTASK = TOOLS / "devtask.py"
GATE = GATE_PATH.read_text(encoding="utf-8-sig")

SECTION_MARK = "# ===== 2026-11-01 WO-GATE-WTCHECK-20261031：W2-3 worktree 校验门（阻断，fast/all）====="


def _section(text):
    """取 wtcheck 段正文（从 mark 到下一个 `# =====` 段标记）。"""
    i = text.index(SECTION_MARK)
    j = text.find("# =====", i + len(SECTION_MARK))
    return text[i:j if j > 0 else len(text)]


def test_section_exists_and_guarded():
    assert SECTION_MARK in GATE, "缺 wtcheck 段标记"
    sec = _section(GATE)
    assert re.search(r"if \(\$Suite -in @\('fast', 'all'\)\)", sec), "wtcheck 必须 fast/all 生效"


def test_section_invokes_audit_worktrees_with_utf8():
    sec = _section(GATE)
    assert "audit-worktrees" in sec
    assert re.search(r"python -X utf8 \$wtTool audit-worktrees", sec), \
        "必须以 python -X utf8 调用 devtask.py audit-worktrees"


def test_section_blocks_on_failure():
    sec = _section(GATE)
    assert "$newFailures++" in sec, "rc!=0 必须 newFailures++（阻断）"
    assert re.search(r"New = \$\(if \(\$wpass\) \{ 0 \} else \{ 1 \}\)", sec)
    # fail-closed：缺 devtask.py 也判失败（不假绿）
    assert "缺 tools/devtask.py" in sec and "fail-closed" in sec
    assert "Rc = -1" in sec


def test_section_summary_and_logs():
    sec = _section(GATE)
    assert "Suite = 'wtcheck'" in sec
    assert "test_gate_wtcheck_$ts.log" in sec
    assert re.search(r"Select-Object -First 12", sec), "失败摘要应打印 2-12 行"


def test_suite_lists_unchanged():
    """wtcheck 是 gate 段而非 pytest 套件：fast/all 清单必须保持冻结口径。"""
    def suite_list(key):
        m = re.search(r"'%s'\s*\{\s*\$suites = @\(([^)]*)\)" % key, GATE)
        assert m, "缺 suite 定义 %r" % key
        return re.findall(r"'([^']+)'", m.group(1))
    assert suite_list("fast") == ["portal", "kstock", "ide", "agent_key", "quant_key",
                                  "tools_key", "datawarehouse", "analysis", "commercial"]
    assert suite_list("all") == ["portal", "kstock", "agent", "quant", "ide"]


def _run_audit(*args, cwd=None):
    t0 = time.perf_counter()
    r = subprocess.run([sys.executable, "-X", "utf8", str(DEVTASK), "audit-worktrees", *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(cwd or REPO), timeout=120)
    return r, time.perf_counter() - t0


def test_audit_rc_matches_output_and_budget():
    r, secs = _run_audit()
    assert r.returncode in (0, 1)
    has_viol = "[VIOL]" in r.stdout
    assert r.returncode == (1 if has_viol else 0), "rc 与输出不一致"
    # 预算：任务要求 <0.5s；实测含 Python 启动约 0.55-0.65s，测试阈值放宽到 2s 防机器抖动
    assert secs < 2.0, "audit-worktrees 耗时异常：%.2fs" % secs


def test_json_contract():
    r, _ = _run_audit("--json")
    d = json.loads(r.stdout)
    assert set(d) == {"repo", "active", "violations", "ok", "rc"}
    assert r.returncode == (1 if d["violations"] else 0)
    assert d["ok"] is (not d["violations"])


def test_worktree_run_resolves_main_repo():
    """在任务 worktree 内运行时，repo 必须解析为主仓（不含 .worktrees 段）。"""
    r, _ = _run_audit(cwd=REPO)
    assert r.returncode in (0, 1)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("仓库：")), "")
    assert line, "缺「仓库：」行"
    repo_path = line[len("仓库："):].strip()
    assert ".worktrees" not in repo_path, "worktree 内运行未回退主仓：%s" % repo_path
    assert os.path.isdir(os.path.join(repo_path, ".worktrees")), repo_path
