# -*- coding: utf-8 -*-
"""settle() 的「数据未发布」与「真缺价」必须分开（2026-11-11 立）。

事故：11-11 15:10 调度环境首次跑 --settle，11-06/main 第 3 日到期日正是当天，
当日日线还没发布（15:50 才拉到）。旧代码走进「无有效价格」分支：
  · 静默写一条 settled[key] = {"error": "无有效价格"}，一个字都不打印；
  · 汇总行照样报「没有到期待结项」。
看上去一切正常，实际那一笔整晚没结，15:50 人工重跑才补上
（settlements.jsonl 里 ts=15:50:22 是铁证，而调度任务是 15:10:02 启动的）。

所以本组用例守的是三条不变量：
  T1 到期日=今天且取不到价 → 不许写 error 记录，必须打印「待结算」
  T2 到期日已过且取不到价 → 必须写 error（否则会无限重试掩盖数据洞）
  T3 汇总行不许把「待结算」说成「没有到期待结项」
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cohort_track as ct  # noqa: E402


def _mk_cohort(d, asof, pool, codes, entry, mats):
    fp = os.path.join(d, "cohort_%s_%s.json" % (asof, pool))
    json.dump({"asof": asof, "pool": pool, "codes": list(codes),
               "n": len(codes), "entry_date": entry,
               "maturities": mats, "settled": {}},
              open(fp, "w", encoding="utf-8"), ensure_ascii=False)
    return fp


def _wire(monkeypatch, tmpdir, today):
    """把 settle 的 IO 与时钟全部换掉，保证不联网、不碰生产台账。"""
    monkeypatch.setattr(ct, "COHORT_DIR", str(tmpdir))
    monkeypatch.setattr(ct, "SETTLE_JSONL", os.path.join(str(tmpdir), "settlements.jsonl"))
    monkeypatch.setattr(ct, "_today", lambda: today)
    monkeypatch.setattr(ct, "_market_closed", lambda: True)   # 已收盘
    monkeypatch.setattr(ct, "load_daily", lambda d: {})       # 一只价都取不到
    monkeypatch.setattr(ct, "load_adjf", lambda d: {})


# ── 纯函数 ────────────────────────────────────────────────────
def test_v1_取到价判ok():
    assert ct.settle_verdict("20261112", "20261112", 7) == "ok"


def test_v2_到期日就是今天且无价判待结算():
    assert ct.settle_verdict("20261112", "20261112", 0) == "pending"


def test_v3_到期日已过且无价判错误():
    assert ct.settle_verdict("20261111", "20261112", 0) == "error"


def test_v4_到期日在未来也按待结算处理():
    """正常流程里 m > today 已被上游 continue 掉，这里只锁边界语义。"""
    assert ct.settle_verdict("20261113", "20261112", 0) == "pending"


# ── settle 行为 ───────────────────────────────────────────────
def test_t1_当日数据未发布不得落错误记录(tmp_path, monkeypatch, capsys):
    _wire(monkeypatch, tmp_path, "20261112")
    fp = _mk_cohort(str(tmp_path), "20261105", "auto",
                    ["600000.SH"], "20261106", {"5": "20261112"})
    ct.settle()
    c = json.load(open(fp, encoding="utf-8"))
    assert c["settled"] == {}, "数据没发布就写 error 记录，会让台账长出一条假故障"
    out = capsys.readouterr().out
    assert "待结算" in out
    assert "没有到期待结项" not in out, "待结算不能说成没有到期待结项（本次事故原话）"


def test_t2_历史日期缺价必须落错误记录(tmp_path, monkeypatch, capsys):
    _wire(monkeypatch, tmp_path, "20261112")
    fp = _mk_cohort(str(tmp_path), "20261105", "auto",
                    ["600000.SH"], "20261106", {"5": "20261111"})
    ct.settle()
    c = json.load(open(fp, encoding="utf-8"))
    assert c["settled"]["5"]["error"] == "无有效价格"
    out = capsys.readouterr().out
    assert "[失败]" in out


def test_t3_待结算不写流水文件(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path, "20261112")
    _mk_cohort(str(tmp_path), "20261105", "auto",
               ["600000.SH"], "20261106", {"5": "20261112"})
    ct.settle()
    assert not os.path.exists(os.path.join(str(tmp_path), "settlements.jsonl"))


def test_t4_已成功结算的项不被重算(tmp_path, monkeypatch):
    _wire(monkeypatch, tmp_path, "20261112")
    fp = os.path.join(str(tmp_path), "cohort_20261105_auto.json")
    json.dump({"asof": "20261105", "pool": "auto", "codes": ["600000.SH"], "n": 1,
               "entry_date": "20261106", "maturities": {"5": "20261112"},
               "settled": {"5": {"m": "20261112", "ret_mean": 0.01}}},
              open(fp, "w", encoding="utf-8"), ensure_ascii=False)
    ct.settle()
    c = json.load(open(fp, encoding="utf-8"))
    assert c["settled"]["5"] == {"m": "20261112", "ret_mean": 0.01}
