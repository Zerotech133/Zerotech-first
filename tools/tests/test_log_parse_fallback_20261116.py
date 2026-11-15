# -*- coding: utf-8 -*-
r"""parse_last_log：最新结论行不可解析时不得回退到旧行（2026-11-16）。

判例
----
2026-11-15 起，四个日报任务的日志末尾变成 ``... daily_kb rc=``（rc 后为空）。

根因在启动器侧：本机 PowerShell 里 `$LASTEXITCODE` 只在命令位于**管道**中时被回填，
裸调用或 `$out = & $exe ...` 赋值形态下它保持上一次的值（哨兵实验实测：置 42 后
跑 `exit 7`，读回仍是 42）。于是 `$rc` 为 ``$null``，拼字符串得 ``rc=``，`exit $rc`
退化为无参 `exit` → **退出码 0**。任何 Python 失败都会被伪装成成功。

解析侧的错误是**静默回退**：`parse_last_log` 从末尾往前找第一个能匹配
``rc=(-?\d+)`` 的行，最新行匹配不上就退到 11-14 那行 ``rc=0``（age 约 44h），
巡检据此报「日志过期 44.24h」。**「日志格式坏了」被显示成「日志太久没写」**，
两件事的处置完全不同。

本文件把「不得回退」钉死：
  最新结论行 rc 缺失 → rc=None + 明确 error，绝不冒用旧行的 rc。
运行：python -m pytest -q tools/tests/test_log_parse_fallback_20261116.py
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

_NOW = datetime.now().astimezone()


def _load():
    p = ROOT / "tools" / "research" / "daily_tasks_health.py"
    spec = importlib.util.spec_from_file_location("daily_tasks_health_log_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stamp(delta_hours: float) -> str:
    return (_NOW - timedelta(hours=delta_hours)).strftime("%Y-%m-%d %H:%M:%S")


def _write(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "zero_daily_x.log"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return p


# ---------------------------------------------------------------------------
# ① 核心：最新行 rc 为空时不许冒用旧行的 rc
# ---------------------------------------------------------------------------
def test_empty_rc_on_latest_line_does_not_fall_back(tmp_path):
    """最新行 `rc=`（空），旧行 `rc=0` → 必须报 rc 缺失，不能返回 rc=0。"""
    mod = _load()
    p = _write(tmp_path, [
        f"{_stamp(48)} daily_kb rc=0",
        "  some output line",
        f"{_stamp(44)} daily_kb rc=",
    ])

    out = mod.parse_last_log(p, _NOW)

    assert out["exists"] is True
    assert out["rc"] is None, f"冒用了旧行的 rc：{out.get('rc')}"
    assert out["rc_unparsable"] is True
    assert "退出码" in (out.get("error") or "") or "rc" in (out.get("error") or "")
    # age 必须按**最新那行**算（44h），而不是冒用旧行（48h）
    assert out["age_hours"] is not None and 43 < out["age_hours"] < 45


def test_normal_rc_still_parsed(tmp_path):
    """正常 rc=5 → 照旧返回，不带 rc_unparsable。"""
    mod = _load()
    p = _write(tmp_path, [f"{_stamp(2)} daily_kb rc=5"])

    out = mod.parse_last_log(p, _NOW)

    assert out["rc"] == 5
    assert not out.get("rc_unparsable")
    assert out["age_hours"] is not None and 1 < out["age_hours"] < 3


def test_skipped_line_is_surfaced_not_hidden(tmp_path):
    """`SKIPPED rc=`（显式跳过）→ rc 缺失，且行原文保留，便于看出是「拒出过期报告」。"""
    mod = _load()
    p = _write(tmp_path, [
        f"{_stamp(60)} daily_screening rc=0",
        f"{_stamp(30)} daily_screening SKIPPED rc= (data not fresh; refuse to emit stale report)",
    ])

    out = mod.parse_last_log(p, _NOW)

    assert out["rc"] is None
    assert "SKIPPED" in (out["line"] or "")
    assert out["age_hours"] is not None and out["age_hours"] < 40


def test_exception_line_still_wins(tmp_path):
    """EXCEPTION 行仍在最新处 → 照旧判 rc=1（没有被新逻辑吞掉）。"""
    mod = _load()
    p = _write(tmp_path, [f"{_stamp(1)} daily_kb EXCEPTION: boom"])

    out = mod.parse_last_log(p, _NOW)

    assert out["rc"] == 1
    assert out.get("exception") == "boom"


def test_dual_rc_line_still_parsed(tmp_path):
    """学习线的 rc_pipeline/rc_gate 双 rc 行不受影响。"""
    mod = _load()
    p = _write(tmp_path, [f"{_stamp(3)} learning_intake_task.ps1 rc_pipeline=0 rc_gate=1"])

    out = mod.parse_last_log(p, _NOW)

    assert out["rcs"] == {"rc_pipeline": 0, "rc_gate": 1}
    assert out["rc"] == 1


# ---------------------------------------------------------------------------
# ② 消费面：不可解析必须产生明确 issue，而不是「日志过期」
# ---------------------------------------------------------------------------
def test_issue_says_unparsable_not_stale(tmp_path):
    """巡检文案：应说「退出码缺失」，不应只说「日志过期 N h」。"""
    mod = _load()
    p = _write(tmp_path, [f"{_stamp(44)} daily_kb rc="])
    log = mod.parse_last_log(p, _NOW)

    task = mod.check_task(
        {"name": "ZeroBound_DailyKB", "label": "每日知识库", "stale_hours": 30},
        _NOW,
        info={"found": True, "query_state": "found", "last_result": 0},
        log=log,
        outputs=[{"path": "p", "exists": True, "fresh": True, "age_hours": 0.5, "spec": {}}])

    joined = " | ".join(task["issues"])
    assert "退出码" in joined, f"没指出退出码缺失：{joined}"
    assert task["status"] == "alert"
