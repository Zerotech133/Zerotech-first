# -*- coding: utf-8 -*-
"""④⑤ 信号下线开关单测（20261014 linkage-dev）。

覆盖四条路径：
  1. ④ 关闭零输出（judge_series 不产 tag4）
  2. ⑤ 判定在但不展示（report/侧车过滤，判定结果保留）
  3. collector 仍收 ⑤（展示隐藏不影响留档）
  4. 开关缺省 = 旧行为（无配置文件 → 全开）
"""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import linkage_signals as LS   # noqa: E402
import linkage_collector as LC  # noqa: E402

N = 150


def _mk(stock_end, idx_block_end, step=0.1):
    """构造判定序列：stock 单调升破前高；idx/block 由 stock_end 涨到
    idx_block_end（升→三线同破前高=⑤；降→仅个股破前高=④）。"""
    stock = [10 + (stock_end - 10) * i / (N - 1) for i in range(N)]
    up = idx_block_end > 50
    base = 100 if up else 50
    ib = [base + (idx_block_end - base) * i / (N - 1) for i in range(N)]
    return {"ok": True, "stock": list(stock),
            "index": list(ib), "block": list(ib)}


def _r4():
    """④ 场景：个股独破前高，板块/大盘未破。"""
    return _mk(20.0, 45.0)


def _r5():
    """⑤ 场景：三线同破前高。"""
    return _mk(20.0, 120.0)


def _tags(res, per="1d"):
    return [str(s["tag"]) for s in res[per]["signals"]]


def _res(r):
    return {"1d": LS.judge_series(r)}


# ── 路径1：④ 关闭零输出 ──────────────────────────────────────────
def test_sig4_disabled_zero_output():
    on = LS.judge_series(_r4(), cfg={})
    assert "4" in _tags({"1d": on})
    off = LS.judge_series(_r4(), cfg={"sig4_disabled": True})
    assert "4" not in _tags({"1d": off})
    # 报告文本同样零输出
    txt = LS.report({"1d": off}, "600000.SH")
    assert "信号4" not in txt and "孤军深入" not in txt


# ── 路径2：⑤ 判定在但不展示 ─────────────────────────────────────
def test_sig5_judged_but_hidden():
    res = _res(_r5())
    assert "5" in _tags(res)                    # 判定保留
    txt = LS.report(res, "600000.SH", cfg={"sig5_display_only": False})
    assert "信号5" not in txt                   # 展示隐藏
    assert "已隐藏" in txt                      # 操作者可见提示
    txt_old = LS.report(res, "600000.SH", cfg={})
    assert "信号5" in txt_old                   # 旧行为照常展示


# ── 路径3：collector 仍收 ⑤ ─────────────────────────────────────
def test_collector_still_records_sig5(tmp_path, monkeypatch):
    shadow = tmp_path / "shadow.jsonl"
    monkeypatch.setattr(LC, "SHADOW", str(shadow))
    monkeypatch.setattr(LC, "LOCK", str(shadow) + ".lock")
    monkeypatch.setattr(LC, "_seen", None)
    monkeypatch.setattr(LC, "_block_cache", {"600000.SH": None})
    res = {"1d": {"signals": [{"tag": "5", "name": "三共振", "note": "x"}],
                  "ctx": {}, "last_pct": {},
                  "stock_last_dt": "2026-10-13 15:00:00"}}
    recs = LC.collect("600000.SH", res, source="test")
    assert len(recs) == 1 and recs[0]["signal"] == "5"
    assert shadow.exists()
    # 幂等：同键不重复落
    monkeypatch.setattr(LC, "_seen", None)
    assert LC.collect("600000.SH", res, source="test") == []


# ── 路径4：开关缺省 = 旧行为 ─────────────────────────────────────
def test_default_old_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(LS, "_cfg_cache", None)
    missing = str(tmp_path / "no_such_config.json")
    assert LS.load_config(missing) == {}
    assert LS.sig4_off({}) is False
    assert LS.sig5_hidden({}) is False
    # 缺省判定路径（cfg=None 但指向不存在配置）行为同旧
    monkeypatch.setattr(LS, "CONFIG_PATH", missing)
    assert "4" in _tags({"1d": LS.judge_series(_r4())})
    assert "5" in _tags({"1d": LS.judge_series(_r5())})
    assert "信号5" in LS.report(_res(_r5()), "600000.SH")


def test_load_config_real_file():
    """真实 linkage_config.json 按拍板内容生效。"""
    cfg = LS.load_config()
    assert cfg.get("sig4_disabled") is True
    assert cfg.get("sig5_display_only") is False
    assert LS.sig4_off(cfg) is True
    assert LS.sig5_hidden(cfg) is True
    # ④ 不判定、⑤ 判定保留
    assert "4" not in _tags({"1d": LS.judge_series(_r4(), cfg=cfg)})
    assert "5" in _tags({"1d": LS.judge_series(_r5(), cfg=cfg)})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
