# -*- coding: utf-8 -*-
"""strong_card stale 闸门单测（2026-10-19 事故补）。

事故：10-19 15:46 strong_card --send 把 10-13 旧名单 119 只「板块领涨」
全量静默重推（scan 被守卫拦下时 strong_latest.json 停留旧数据，card
无 stale 校验照发）。本文件覆盖：

  1. stale_check 判据：旧名单拒 / 当日放行 / date 空（裸数组名单）拦
  2. --send 旧名单：拒发（exit 3）、不触碰推送、guard_alert 留痕
  3. --send 当日名单：放行走正常发送（熔断登记照旧）、无告警
  4. --force 逃生口：旧名单强推放行、告警留痕注记 forced
  5. closed on failure：guard_alert 异常时仍拒发（告警故障不开闸）

零真实推送（send_card / guard_alert 双打桩）、零真实状态（tmp_path），
不碰生产 strong_state.json / outbox / logs。
"""

import datetime as _dt
import importlib
import json
import sys

import pytest

HERE = sys.path[0]
strong_card = importlib.import_module("strong_card")
sc = strong_card


TODAY = _dt.date.today().isoformat()  # 与 main() 的真当日同源
OLD = (_dt.date.today() - _dt.timedelta(days=6)).isoformat()


def _items(date, n=2):
    """构造 strong_latest.json 内容（n 只板块领涨）。"""
    return {
        "date": date,
        "ts": date + " 15:45",
        "items": [
            {
                "code": "30000%d" % i,
                "name": "票%d" % i,
                "block": "光模块",
                "ex250": 12.0,
                "tag": "板块领涨",
                "reasons": ["x"],
            }
            for i in range(n)
        ],
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离环境：latest/state/outbox 全指 tmp，推送/告警双打桩。"""
    e = {"sends": [], "alerts": []}

    latest_fp = tmp_path / "strong_latest.json"
    e["latest_fp"] = str(latest_fp)

    def _write_latest(date):
        latest_fp.write_text(
            json.dumps(_items(date), ensure_ascii=False), encoding="utf-8"
        )

    e["write_latest"] = _write_latest

    def _send_card(card):
        e["sends"].append(card)
        return "SEND_OK"

    monkeypatch.setattr(sc, "send_card", _send_card)

    def _fake_alert(reason, date, today, forced=False):
        e["alerts"].append(
            {"reason": reason, "date": date, "today": today, "forced": forced}
        )
        return True

    monkeypatch.setattr(sc, "stale_alert", _fake_alert)

    monkeypatch.setattr(sc, "STATE_FP", str(tmp_path / "strong_state.json"))
    monkeypatch.setattr(sc, "OUTBOX", str(tmp_path / "outbox"))

    def _main(*extra):
        argv = ["strong_card.py", "--latest", str(latest_fp)] + list(extra)
        monkeypatch.setattr(sys, "argv", argv)
        return sc.main()

    e["main"] = _main
    return e


# —— 1) 判据单测 ——


def test_stale_check_verdicts():
    """旧日期拒 / 当日放行 / 空与结构异常一律拦（closed on failure）。"""
    ok, why = sc.stale_check(OLD, today=TODAY)
    assert not ok and OLD in why and TODAY in why
    ok2, _ = sc.stale_check(TODAY, today=TODAY)
    assert ok2
    ok3, why3 = sc.stale_check("", today=TODAY)
    assert not ok3 and "异常" in why3  # 裸数组名单 date=""
    ok4, why4 = sc.stale_check("20261019", today=TODAY)
    assert not ok4 and "异常" in why4  # 紧凑格式非法
    ok5, _ = sc.stale_check(None, today=TODAY)
    assert not ok5


# —— 2) --send 旧名单：拒发 + 告警留痕 ——


def test_send_stale_list_blocked(env):
    """10-13 旧名单 --send：exit 3、零推送、state 不写、告警留痕。"""
    env["write_latest"](OLD)
    rc = env["main"]("--send")
    assert rc == sc.EXIT_STALE_BLOCK
    assert env["sends"] == []  # 未触碰推送
    assert len(env["alerts"]) == 1  # 告警留痕 1 次
    a = env["alerts"][0]
    assert OLD in a["reason"] and a["date"] == OLD
    assert not a["forced"]
    import os

    assert not os.path.exists(sc.STATE_FP)  # 熔断状态未被污染


def test_send_missing_date_blocked(env):
    """date 为空（裸数组名单）→ closed on failure 拦发。"""
    with open(env["latest_fp"], "w", encoding="utf-8") as f:
        f.write(json.dumps(_items(OLD)["items"], ensure_ascii=False))
    rc = env["main"]("--send")
    assert rc == sc.EXIT_STALE_BLOCK
    assert env["sends"] == []
    assert len(env["alerts"]) == 1


# —— 3) --send 当日名单：放行 + 正常熔断登记 ——


def test_send_today_list_passes(env):
    """当日名单放行：走发送、SEND_OK 后熔断登记、无 stale 告警。"""
    env["write_latest"](TODAY)
    rc = env["main"]("--send")
    assert rc == 0
    assert len(env["sends"]) == 1  # 卡片已交给发送器
    assert env["alerts"] == []  # 无 stale 告警
    st = json.load(open(sc.STATE_FP, encoding="utf-8"))
    assert len(st["pushed"]) == 2  # 2 只已登记
    assert all(v.startswith(TODAY[:4]) for v in st["pushed"].values())


def test_send_today_list_push_fail_no_state(env):
    """推送失败（非 SEND_OK）：熔断不登记（下轮可重试），原语义不变。"""
    env["write_latest"](TODAY)
    calls = []

    def _fail(card):
        calls.append(card)
        return "SEND_FAIL openapi:down no lark-cli"

    orig = sc.send_card
    sc.send_card = _fail  # 负路径：覆盖 fixture 桩
    try:
        rc = env["main"]("--send")
    finally:
        sc.send_card = orig
    assert rc == 0
    assert len(calls) == 1
    import os

    assert not os.path.exists(sc.STATE_FP)  # 失败不写状态


# —— 4) --force 逃生口：留痕后放行 ——


def test_force_stale_list_passes_with_alert(env):
    """--force 旧名单强推：放行发送，但告警留痕且注记 forced。"""
    env["write_latest"](OLD)
    rc = env["main"]("--send", "--force")
    assert rc == 0
    assert len(env["sends"]) == 1  # 强推已放行
    assert len(env["alerts"]) == 1
    assert env["alerts"][0]["forced"] is True  # 留痕注记
    st = json.load(open(sc.STATE_FP, encoding="utf-8"))
    assert len(st["pushed"]) == 2  # 熔断照常登记


def test_force_today_list_no_alert(env):
    """--force 对当日名单无副作用（闸门本就放行）。"""
    env["write_latest"](TODAY)
    rc = env["main"]("--send", "--force")
    assert rc == 0 and env["alerts"] == []


# —— 5) closed on failure：告警链故障不开闸 ——


def test_alert_failure_still_blocks(env, monkeypatch):
    """stale_alert 内部告警链异常（guard_alert 抛错）→ 仍拒发，exit 3。"""
    env["write_latest"](OLD)

    def _boom(*a, **k):
        raise RuntimeError("guard_alert channel down")

    import guard_alert as _ga

    monkeypatch.setattr(_ga, "guard_alert", _boom)  # 只断内层告警链
    rc = env["main"]("--send")
    assert rc == sc.EXIT_STALE_BLOCK
    assert env["sends"] == []
