# -*- coding: utf-8 -*-
"""signal_pool 单测（WO-QT-20261020-071）：零真实网络、零生产文件。

  1. 合法事件落账：一行 jsonl、read_events 读回一致、过 normalize_signal
  2. 契约校验：缺必填字段 / kind 负面清单 → SignalPointError 且不落账
  3. 幂等防重：同 signal_id 二次 append → appended=False，文件不增行
  4. 轮次确定性：同票同方向当日 r1→r2；他票他方向不串号
  5. 坏行容忍：尾行截断/坏行跳过不炸
  6. since 过滤：按 triggered_at 过滤
  7. 池印记：pool_id/pool_version=0/scan_run_date 齐全，过 normalize_pool_stamp

全部落 tmp_path（池文件 + todays_picks 样例），不碰生产 data/。
注意：signal_consumer/decision_snapshot 以 k_stock/paper 直连方式 import，
与 signal_pool 内部 import 同一模块对象（异常类单例，pytest.raises 才成立）。
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent / "k_stock" / "paper"))
import signal_pool as sp  # noqa: E402
import signal_consumer as sc  # noqa: E402
import decision_snapshot as ds  # noqa: E402

T0 = datetime(2026, 10, 21, 10, 0, 0)


@pytest.fixture
def tmp_env(tmp_path):
    """池文件与 todays_picks 样例全部落 tmp_path，返回 (jsonl, pool_json)。"""
    pool_json = tmp_path / "todays_picks.json"
    pool_json.write_text(json.dumps({
        "scan_run_date": "2026-10-19",
        "generated_at": "2026-10-20 15:40:09",
    }, ensure_ascii=False), encoding="utf-8")
    return str(tmp_path / "signal_pool.jsonl"), str(pool_json)


def make_event(tmp_env, direction="pull", code="002007.SZ", **kw):
    """构造事件：池文件与 todays_picks 路径一律注入 tmp（零生产触碰）。"""
    jsonl, pool_json = tmp_env
    base = dict(code=code, name="华兰生物", direction=direction,
                trigger_price=12.8, entry_px=13.07, now=T0,
                reg="down", source_dt="2026-09-16", tier="T2", n_hit=1,
                pool_jsonl=jsonl, pool_path=pool_json)
    base.update(kw)
    return sp.build_signal(**base)


def test_append_valid_event_and_readback(tmp_env):
    jsonl, pool_json = tmp_env
    ev = make_event(tmp_env)
    sig, appended = sp.append_event(ev, path=jsonl)
    assert appended is True
    lines = Path(jsonl).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    got = sp.read_events(path=jsonl)
    assert len(got) == 1
    assert got[0]["signal_id"] == sig["signal_id"]
    # 规范化产物必须无损过消费链契约校验（WO-005）
    assert sc.normalize_signal(got[0])["stock"] == "002007.SZ"
    assert pool_json  # pool_meta 源存在（印记来自该文件）


def test_schema_reject_missing_field_and_negative_kind(tmp_env):
    jsonl, _ = tmp_env
    ev = make_event(tmp_env)
    ev.pop("rule_version")
    with pytest.raises(sc.SignalPointError) as ei:
        sp.append_event(ev, path=jsonl)
    assert ei.value.reason_code == "missing_field"
    # kind 负面清单：全池导出冒充信号点 → 拒收
    ev2 = make_event(tmp_env)
    ev2["kind"] = "full_pool"
    with pytest.raises(sc.SignalPointError) as ei2:
        sp.append_event(ev2, path=jsonl)
    assert ei2.value.reason_code == "negative_list"
    assert sp.read_events(path=jsonl) == []  # 校验失败零落账


def test_idempotent_append_same_signal_id(tmp_env):
    jsonl, _ = tmp_env
    ev = make_event(tmp_env)
    _, a1 = sp.append_event(ev, path=jsonl)
    _, a2 = sp.append_event(dict(ev), path=jsonl)  # 同 signal_id 重放
    assert (a1, a2) == (True, False)
    assert len(sp.read_events(path=jsonl)) == 1


def test_signal_id_rounds_increment(tmp_env):
    jsonl, _ = tmp_env
    ids = []
    # 构造与落账交替进行：轮次取决于落账时池内已有事件数
    ids.append(sp.append_event(make_event(tmp_env), path=jsonl)[0]["signal_id"])
    ids.append(sp.append_event(
        make_event(tmp_env, now=T0.replace(hour=12)), path=jsonl)[0]["signal_id"])
    ids.append(sp.append_event(
        make_event(tmp_env, direction="break", now=T0.replace(hour=13)),
        path=jsonl)[0]["signal_id"])
    ids.append(sp.append_event(
        make_event(tmp_env, code="600519.SH", name="贵州茅台"), path=jsonl)[0]["signal_id"])
    assert ids == [
        "todays_picks.002007.SZ.pull.20261021.r1",
        "todays_picks.002007.SZ.pull.20261021.r2",
        "todays_picks.002007.SZ.break.20261021.r1",
        "todays_picks.600519.SH.pull.20261021.r1",
    ]


def test_corrupt_tail_line_tolerated(tmp_env):
    jsonl, _ = tmp_env
    good1 = json.dumps(make_event(tmp_env), ensure_ascii=False)
    good2 = json.dumps(make_event(tmp_env, code="600519.SH", name="贵州茅台"),
                       ensure_ascii=False)
    with open(jsonl, "w", encoding="utf-8") as f:
        f.write(good1 + "\n" + good2[:len(good2) // 2])  # 尾行截断（无换行）
    evs = sp.read_events(path=jsonl)
    assert [e["stock"] for e in evs] == ["002007.SZ"]  # 坏行跳过，好行保留


def test_read_events_since_filter(tmp_env):
    jsonl, _ = tmp_env
    sp.append_event(make_event(tmp_env), path=jsonl)                      # 10:00
    sp.append_event(make_event(tmp_env, now=T0.replace(hour=14)), path=jsonl)  # 14:00
    evs = sp.read_events(path=jsonl,
                         since=T0.replace(hour=12).isoformat(timespec="seconds"))
    assert len(evs) == 1 and evs[0]["signal_id"].endswith("r2")


def test_pool_stamp_carries_scan_run_date(tmp_env):
    jsonl, _ = tmp_env
    sig, appended = sp.append_event(make_event(tmp_env), path=jsonl)
    assert appended
    stamp = sc.normalize_signal(sig)["pool_stamp"]
    assert ds.normalize_pool_stamp(stamp) == stamp  # 过 -004 契约校验
    assert stamp["pool_id"] == "todays_picks"
    assert stamp["pool_version"] == 0
    assert stamp["scan_run_date"] == "2026-10-19"
    assert stamp["generated_at"] == "2026-10-20 15:40:09"


def test_evidence_replayable(tmp_env):
    jsonl, _ = tmp_env
    sig, _ = sp.append_event(make_event(tmp_env), path=jsonl)
    evd = sig["evidence"]
    assert evd["trigger_price"] == 12.8 and evd["entry_px"] == 13.07
    assert evd["direction"] == "pull" and evd["reg"] == "down"
    assert evd["source_dt"] == "2026-09-16" and evd["tier"] == "T2"


def test_valid_until_four_hours(tmp_env):
    jsonl, _ = tmp_env
    sig, _ = sp.append_event(make_event(tmp_env), path=jsonl)
    assert (datetime.fromisoformat(sig["valid_until"])
            - datetime.fromisoformat(sig["triggered_at"])).total_seconds() == 4 * 3600


def test_missing_pool_file_meta_degrades(tmp_env, tmp_path):
    jsonl, _ = tmp_env
    ev = make_event(tmp_env, pool_path=str(tmp_path / "no_such_pool.json"))
    sig, appended = sp.append_event(ev, path=jsonl)  # 读失败降级，不炸
    assert appended and sig["pool_stamp"]["scan_run_date"] is None


def test_default_path_isolated_from_cwd(tmp_env):
    """cwd 切到任意目录，落账/读回仍按注入路径工作（仓根直接 pytest 可收集）。"""
    jsonl, _ = tmp_env
    prev = os.getcwd()
    os.chdir(os.path.dirname(jsonl))
    try:
        ev = make_event(tmp_env)
        sig, appended = sp.append_event(ev, path=jsonl)
        assert appended and sig["signal_id"].startswith("todays_picks.")
        assert len(sp.read_events(path=jsonl)) == 1
    finally:
        os.chdir(prev)
