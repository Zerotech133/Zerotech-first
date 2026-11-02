# -*- coding: utf-8 -*-
"""旧工具 DEPRECATED 标记与弃用清单测试（task-135）。

覆盖：
  1) 9 个历史工具文件头含 DEPRECATED（2026-11-02）且指向替代链（card_from_units → card_precheck_laomu → learning_release）；
  2) 替代链文件真实存在；弃用清单覆盖全部 9 个工具 + 表格列（旧工具/状态/替代/理由）；
  3) 不改工具行为：git diff 仅新增注释行（无删除/无代码行改动）；py_compile 通过；
  4) 语法门禁 PASS（check_syntax --fail-on-warning，仅改动文件）。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KB = REPO / "tools" / "kb_corpus"
DOC = REPO / "docs" / "standards" / "2026-11-02_学习工具弃用清单_v1.md"
FILES = ["card_proposal.py", "card_proposal_v2.py", "card_proposal_v3.py", "card_proposal_v4.py",
         "card_precheck.py", "card_precheck_v3.py", "card_revision.py",
         "card_revision_apply.py", "card_revision_round2.py"]
CHAIN = ["card_from_units.py", "card_precheck_laomu.py", "learning_release.py"]


# ---------------------------------------------------------------------------
# 1) 文件头 DEPRECATED + 替代链
# ---------------------------------------------------------------------------
def test_deprecated_headers():
    for name in FILES:
        p = KB / name
        assert p.is_file(), name
        lines = p.read_text(encoding="utf-8").splitlines()
        head = "\n".join(lines[:8])
        assert lines[0].strip() == "# -*- coding: utf-8 -*-", f"{name} 首行编码声明被破坏"
        assert "DEPRECATED" in head, f"{name} 缺 DEPRECATED"
        assert "2026-11-02" in head, f"{name} 缺生效日期"
        assert "v2 已统一" in head, f"{name} 缺 v2 口径"
        for target in CHAIN:
            assert target in head, f"{name} 未指向替代 {target}"
        assert "行为未改" in head, f"{name} 未说明行为不变"
        assert "docs/standards/2026-11-02_学习工具弃用清单_v1.md" in head


# ---------------------------------------------------------------------------
# 2) 替代链存在 + 清单覆盖
# ---------------------------------------------------------------------------
def test_replacements_and_list_doc():
    for target in CHAIN:
        assert (KB / target).is_file(), f"替代工具不存在：{target}"
    assert DOC.is_file(), DOC
    doc = DOC.read_text(encoding="utf-8")
    for name in FILES:
        assert name in doc, f"清单缺 {name}"
    for col in ("旧工具", "状态", "替代", "理由"):
        assert col in doc, col
    assert "DEPRECATED" in doc and "2026-11-02" in doc
    assert "learning_release.py" in doc and "发布门" in doc
    assert "禁止绕过" in doc or "禁止直接" in doc


# ---------------------------------------------------------------------------
# 3) 行为不变：仅新增注释行 + 可编译
# ---------------------------------------------------------------------------
def test_only_comment_changes():
    procs = subprocess.run(["git", "diff", "--unified=0", "--"] + [f"tools/kb_corpus/{n}" for n in FILES],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=str(REPO), timeout=120)
    if procs.returncode != 0:
        # 非 git 环境降级：仅校验可编译
        for name in FILES:
            compile((KB / name).read_text(encoding="utf-8"), name, "exec")
        return
    removed = []
    added_bad = []
    for line in procs.stdout.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            removed.append(line)
        elif line.startswith("+") and line != "+" and not line.startswith("+#"):
            added_bad.append(line)
    assert not removed, f"存在非注释删除行：{removed[:5]}"
    assert not added_bad, f"存在非注释新增行：{added_bad[:5]}"
    for name in FILES:
        compile((KB / name).read_text(encoding="utf-8"), name, "exec")


# ---------------------------------------------------------------------------
# 4) 语法门禁 PASS
# ---------------------------------------------------------------------------
def test_syntax_gate_pass():
    files_arg = ",".join(f"tools/kb_corpus/{n}" for n in FILES)
    proc = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools" / "check_syntax.py"),
                           "--files", files_arg, "--fail-on-warning"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(REPO), timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in (proc.stdout or "") or "错误 0" in (proc.stdout or ""), proc.stdout
