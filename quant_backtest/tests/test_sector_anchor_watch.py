# -*- coding: utf-8 -*-
"""sector_anchor_watch 单测（WO-QT-20261020-065 2026-10-21）：全合成数据。

  1. 板块锚滞后基准日 >3 天 → guard_alert P0 告警（内容四要素齐全 + 状态落盘）
  2. 滞后 ≤3 天 → 静默（不推、不写状态）；边界 3/4 天双向验证
  3. 同因重跑 → 复用 guard_alert 单线单键 12h 去重
  4. 板块锚缺失（无K线）→ 同样 P0 告警
  5. send 缺省 False → dry：不推、不写状态
  6. CLI --dry-run 出判定文本

零真实推送（ga._push 打桩）、零真实状态/日志（tmp_path），不碰生产数据；
板块锚读取一律打桩（_read_sector_anchor），不触 linkage/parquet。

隔离判例（对齐 test_guard_alert.py）：
  · autouse log_fp → ga.LOG_PATH 指向 tmp_path（告警留痕生产面不污染）；
  · state_path 一律显式传 tmp_path（ga.STATE_PATH 不指向生产）。
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import guard_alert as ga          # noqa: E402
import sector_anchor_watch as saw  # noqa: E402


@pytest.fixture
def state_fp(tmp_path):
    return str(tmp_path / "sector_anchor_watch_state.json")


@pytest.fixture(autouse=True)
def log_fp(tmp_path, monkeypatch):
    """隔离 ga.LOG_PATH→tmp_path（同 test_guard_alert.log_fp 判例）。"""
    fp = str(tmp_path / "guard_alert.log")
    monkeypatch.setattr(ga, "LOG_PATH", fp)
    return fp


@pytest.fixture
def fake_push(monkeypatch):
    """打桩推送通道：记录调用，返回 True。"""
    calls = []

    def _p(text):
        calls.append(text)
        return True

    monkeypatch.setattr(ga, "_push", _p)
    return calls


def set_anchor(monkeypatch, dt):
    """打桩板块锚读取：返回口径与 linkage.block_bars 一致（ISO 日期或 None）。"""
    monkeypatch.setattr(saw, "_read_sector_anchor", lambda: dt)


BASELINE = "2026-10-21"   # 板块日线 20261012 停更背景下的巡检基准日
STALE_ANCHOR = "2026-10-12"   # 滞后 9 天（>3）→ 应 P0
NOW = None  # guard_alert 去重窗口用真实时钟即可（同一测试内时间单调）


def test_stale_over_threshold_alerts(fake_push, state_fp, monkeypatch):
    """滞后 9 天 >3 → P0 发送，内容含任务/原因/双锚/建议，状态落盘。"""
    set_anchor(monkeypatch, STALE_ANCHOR)
    sent, text, detail = saw.sector_anchor_watch(
        baseline=BASELINE, send=True, state_path=state_fp)
    assert sent and len(fake_push) == 1
    assert "任务: SectorAnchorWatch" in text
    assert "板块锚 881019=2026-10-12" in text
    assert "基准日=2026-10-21" in text
    assert "建议动作" in text
    assert detail["stale_days"] == 9 and detail["alerted"] is True
    st = json.load(open(state_fp, encoding="utf-8"))
    assert len(st) == 1 and "ts" in list(st.values())[0]


def test_fresh_within_threshold_silent(fake_push, state_fp, monkeypatch):
    """滞后 2 天 ≤3 → 静默：不推、不写状态、reason 为 None。"""
    set_anchor(monkeypatch, "2026-10-19")
    sent, text, detail = saw.sector_anchor_watch(
        baseline=BASELINE, send=True, state_path=state_fp)
    assert not sent and text == "" and len(fake_push) == 0
    import os
    assert not os.path.exists(state_fp)
    assert detail["stale_days"] == 2 and detail["alerted"] is False
    assert detail["reason"] is None


def test_boundary_three_vs_four(fake_push, state_fp, monkeypatch):
    """边界口径：差 3 天静默，差 4 天告警（严格大于 3）。"""
    import os
    set_anchor(monkeypatch, "2026-10-18")   # 10-21 - 10-18 = 3
    sent3, _, d3 = saw.sector_anchor_watch(
        baseline=BASELINE, send=True, state_path=state_fp)
    assert not sent3 and d3["stale_days"] == 3
    assert not os.path.exists(state_fp)
    set_anchor(monkeypatch, "2026-10-17")   # 4 天 → 告警
    sent4, _, d4 = saw.sector_anchor_watch(
        baseline=BASELINE, send=True, state_path=state_fp)
    assert sent4 and d4["stale_days"] == 4 and len(fake_push) == 1


def test_dedup_same_reason_12h(fake_push, state_fp, monkeypatch):
    """同因（同锚同基准日）窗口内重跑 → 单线单键去重只发 1 次。"""
    from datetime import datetime
    t0 = datetime(2026, 10, 21, 8, 0, 0)
    set_anchor(monkeypatch, STALE_ANCHOR)
    ok1, _, _ = saw.sector_anchor_watch(
        baseline=BASELINE, send=True, state_path=state_fp, now=t0)
    ok2, _, _ = saw.sector_anchor_watch(
        baseline=BASELINE, send=True, state_path=state_fp,
        now=t0.replace(hour=11))
    assert ok1 and not ok2 and len(fake_push) == 1


def test_anchor_missing_alerts(fake_push, state_fp, monkeypatch):
    """板块锚无K线（采集断供）→ 同样 P0 告警，原因含『无K线』。"""
    set_anchor(monkeypatch, None)
    sent, text, detail = saw.sector_anchor_watch(
        baseline=BASELINE, send=True, state_path=state_fp)
    assert sent and len(fake_push) == 1
    assert "无K线" in text and "881019" in text
    assert detail["anchor"] is None and detail["alerted"] is True


def test_default_send_false_dry(fake_push, state_fp, monkeypatch):
    """send 缺省 False → dry：不推、不写状态（凭据注入前安全）。"""
    import os
    set_anchor(monkeypatch, STALE_ANCHOR)
    sent, text, detail = saw.sector_anchor_watch(
        baseline=BASELINE, state_path=state_fp)
    assert not sent and len(fake_push) == 0
    assert not os.path.exists(state_fp)
    # dry：告警条件已触发（拼装卡），仅推送被抑制——sent=False 才是关键
    assert detail["alerted"] is True
    # 但判定文本仍拼装完整（dry 只拼不推）
    assert "任务: SectorAnchorWatch" in text and "2026-10-12" in text


def test_cli_dry_run(capsys, monkeypatch, tmp_path):
    """CLI --dry-run：出判定不推不写，退出码 0。"""
    set_anchor(monkeypatch, STALE_ANCHOR)
    state = str(tmp_path / "cli_state.json")
    rc = saw.main(["--baseline", BASELINE, "--dry-run", "--state", state,
                   "--now", "2026-10-21 08:00:00"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "anchor=2026-10-12 baseline=2026-10-21 stale_days=9 " \
           "alerted=True sent=False" in out
    # dry：不推不写状态不留日志（连日志文件都不产生）
    assert not os.path.exists(state)
    assert not os.path.exists(ga.LOG_PATH)


def test_baseline_defaults_to_today(monkeypatch):
    """baseline 缺省=当天：detail.baseline 与 date.today() 一致。"""
    set_anchor(monkeypatch, date.today().isoformat())
    _, _, detail = saw.sector_anchor_watch(
        state_path=str(Path(__file__).parent / "_x_state.json"))
    assert detail["baseline"] == date.today().isoformat()
    assert detail["stale_days"] == 0
