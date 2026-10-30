# -*- coding: utf-8 -*-
"""TRACELINK 回归：run_shadow 必须在成功落账后调用 _record_trace_link（原为死代码）。"""
from __future__ import annotations

from pathlib import Path

RUN_SHADOW = Path(__file__).resolve().parents[1] / "run_shadow.py"


def test_record_trace_link_is_not_dead_code():
    src = RUN_SHADOW.read_text(encoding="utf-8")
    lines = src.splitlines()
    call_idx = next(i for i, ln in enumerate(lines)
                    if ln.strip() == "_record_trace_link(row)")
    # 必须与循环体同级（8 空格），不能缩进进 except（12 空格）——那是死代码
    assert lines[call_idx] == "        _record_trace_link(row)", repr(lines[call_idx])
    # 最近的前一条语句必须是落账失败的 return 3（即调用位于 try/except 之后）
    prev = [ln for ln in lines[:call_idx]
            if ln.strip() and not ln.strip().startswith("#")]
    assert prev and prev[-1].strip() == "return 3", prev[-1] if prev else ""
