# -*- coding: utf-8 -*-
"""JEV-SRCGUARD-20261105 单测：公告源守卫（全 mock，不推送、不碰生产日志）。

覆盖：
  1) 正常轮次（公告源=tushare）→ OK / rc=0
  2) 40203（有令牌无接口权限）→ WARN，cause=NO_PERMISSION:40203，并给处置动作
  3) 40101（令牌失效）→ CRITICAL / rc=2
  4) 日志无「公告源=」记录 → WARN NO_SIGNAL
  5) 最近一轮过旧 → CRITICAL STALLED（停摆优先于错误码）
  6) none_streak 只数尾部连续 none
  7) 同因未到 --remind-days 不重复推送；状态变化立即推
  8) 未知错误码 → OTHER:<code>

运行：
  "C:/Program Files/Python311/python.exe" -m pytest jev-eval -q
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

JEV_DIR = Path(__file__).resolve().parents[1]
if str(JEV_DIR) not in sys.path:
    sys.path.insert(0, str(JEV_DIR))

import jev_source_guard as sg  # noqa: E402

NOW = datetime(2026, 11, 5, 6, 0, 0)


def line(ts: datetime, msg: str) -> str:
    return json.dumps(
        {"ts": ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3], "level": "info",
         "mod": "review", "msg": msg, "pid": 1},
        ensure_ascii=False,
    )


def round_line(ts: datetime, mode: str = "none") -> str:
    return line(ts, f"本轮 0 条（已落盘） regime=0.00 公告源={mode}")


def fail_line(ts: datetime, code: int, msg: str = "") -> str:
    return line(ts, f"公告源探测失败（降级 none 模式）: anns_d 失败 code={code}: {msg or 'x'}")


def write_log(tmp_path: Path, lines: list[str]) -> Path:
    p = tmp_path / "review.log"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def run(tmp_path: Path, lines: list[str], **kw):
    log = write_log(tmp_path, lines)
    state = tmp_path / "state.json"
    kw.setdefault("now", NOW)
    out = sg.evaluate(log_path=log, data_dir=tmp_path, state_file=state, **kw)
    return out["report"], state


# ── 1) 正常 ─────────────────────────────────────────────────
def test_ok_when_tushare(tmp_path):
    ts = NOW - timedelta(minutes=5)
    rep, _ = run(tmp_path, [round_line(ts, "none"), round_line(ts + timedelta(minutes=1), "tushare")])
    assert rep["level"] == "OK" and rep["code"] == 0
    assert rep["ann_mode"] == "tushare"
    assert rep["none_streak"] == 0
    assert rep["ann_cause"] is None


# ── 2) 40203 无权限 ─────────────────────────────────────────
def test_no_permission_is_warn(tmp_path):
    ts = NOW - timedelta(minutes=10)
    rep, _ = run(tmp_path, [fail_line(ts, 40203, "抱歉，您没有接口(anns_d)访问权限"),
                            round_line(ts + timedelta(seconds=1))])
    assert rep["level"] == "WARN" and rep["code"] == 1
    assert rep["ann_mode"] == "none"
    assert rep["ann_cause"] == "NO_PERMISSION:40203"
    assert "积分" in rep["ann_cause_txt"]
    assert "提积分" in rep["action"] or "换源" in rep["action"]


# ── 3) 40101 令牌失效 → CRITICAL ────────────────────────────
def test_token_invalid_is_critical(tmp_path):
    ts = NOW - timedelta(minutes=10)
    rep, _ = run(tmp_path, [fail_line(ts, 40101, "您的token不对"), round_line(ts)])
    assert rep["level"] == "CRITICAL" and rep["code"] == 2
    assert rep["ann_cause"] == "TOKEN_INVALID:40101"
    assert "set_env_secret" in rep["action"]


# ── 4) 日志里没有公告源记录 ─────────────────────────────────
def test_no_signal(tmp_path):
    rep, _ = run(tmp_path, [line(NOW - timedelta(minutes=3), "本轮 0 条（已落盘） regime=0.00")])
    assert rep["code"] == 1
    assert rep["ann_cause"] == "NO_SIGNAL"
    assert rep["ann_mode"] is None


def test_missing_log_file(tmp_path):
    out = sg.evaluate(log_path=tmp_path / "nope.log", data_dir=tmp_path,
                      state_file=tmp_path / "s.json", now=NOW)
    assert out["report"]["ann_cause"] == "NO_SIGNAL"


# ── 5) 停摆优先于错误码 ─────────────────────────────────────
def test_stalled_overrides_old_error(tmp_path):
    old = NOW - timedelta(hours=30)
    rep, _ = run(tmp_path, [fail_line(old, 40203), round_line(old + timedelta(seconds=1))],
                 max_age_hours=6)
    assert rep["code"] == 2
    assert rep["ann_cause"] == "STALLED"
    assert rep["age_hours"] > 6


# ── 6) none_streak 只数尾部 ─────────────────────────────────
def test_none_streak_counts_tail_only(tmp_path):
    t0 = NOW - timedelta(minutes=40)
    lines = [round_line(t0, "tushare")]
    lines += [round_line(t0 + timedelta(minutes=i + 1), "none") for i in range(5)]
    rep, _ = run(tmp_path, lines)
    assert rep["none_streak"] == 5
    assert rep["rounds_seen"] == 6
    # 尾部全是 none 但没错误码 → NO_TOKEN 而非 NO_SIGNAL
    assert rep["ann_cause"] == "NO_TOKEN"


# ── 7) 推送去重 ─────────────────────────────────────────────
def test_should_push_dedup_and_change(tmp_path):
    state = tmp_path / "s.json"
    first = sg.should_push({}, "NO_PERMISSION:40203", NOW, 3)
    assert first is True
    state.write_text(json.dumps({"cause": "NO_PERMISSION:40203",
                                 "pushed_at": (NOW - timedelta(days=1)).isoformat()}),
                     encoding="utf-8")
    st = sg.load_state(state)
    assert sg.should_push(st, "NO_PERMISSION:40203", NOW, 3) is False
    # 原因变了 → 立刻推
    assert sg.should_push(st, "TOKEN_INVALID:40101", NOW, 3) is True
    # 超过提醒间隔 → 再推一次
    assert sg.should_push(st, "NO_PERMISSION:40203", NOW + timedelta(days=3), 3) is True
    # remind_days=0 表示每次都推
    assert sg.should_push(st, "NO_PERMISSION:40203", NOW, 0) is True


def test_state_written_ok_resets_cause(tmp_path):
    ts = NOW - timedelta(minutes=2)
    rep, state = run(tmp_path, [round_line(ts, "tushare")])
    assert rep["level"] == "OK"
    st = json.loads(state.read_text(encoding="utf-8"))
    assert st["cause"] is None
    assert st.get("last_ok")


# ── 8) 未知错误码 ───────────────────────────────────────────
def test_unknown_code_is_other(tmp_path):
    ts = NOW - timedelta(minutes=5)
    rep, _ = run(tmp_path, [fail_line(ts, 40002, "请求过于频繁"), round_line(ts)])
    assert rep["code"] == 1
    assert rep["ann_cause"] == "OTHER:40002"
    assert rep["last_fail_code"] == "40002"


# ── CLI 参数 ────────────────────────────────────────────────
def test_cli_rejects_bad_arg():
    assert sg.main(["--max-age-hours", "0", "--no-push"]) == 3
    assert sg.main(["--bogus"]) == 3
