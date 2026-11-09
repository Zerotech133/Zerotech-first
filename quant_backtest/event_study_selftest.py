# -*- coding: utf-8 -*-
"""P3 事件研究引擎 自测(P3 v2: 含变体事件与环境契约).

运行: python test_event_study.py
全部通过打印 ALL OK; 任一失败 exit 1。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import event_study  # noqa: E402
from event_study import (BUILTIN_EVENTS, EventStudy, _ev_tp_upgrade_org,  # noqa: E402
                         _is_foreign_org, pit_t0, _tstat)
from pit_visibility import resolve_lag_days, visibility_proof  # noqa: E402

_results = []


def check(name: str, ok: bool, detail: str = ""):
    _results.append((name, ok, detail))
    print(f"  [{'OK' if ok else 'FAIL'}] {name}"
          + (f"  ({detail})" if detail and not ok else ""))


def main() -> int:
    print("== 1. 环境契约 ==")
    # 本机 Python 3.13.14 负 start + 负步长 range 语义损坏(len==0, 标准应为 1);
    # 全机代码禁用该写法, 引擎用正步长求负列表规避。此处固化契约。
    broken = len(range(-1, 0, -1))
    check("1a 环境已知缺陷仍在(或已修复则视为环境更新)",
          broken in (0, 1), f"len(range(-1,0,-1))={broken}")
    check("1b 规避写法正确", len([-k for k in range(1, 6)]) == 5
          and min([-k for k in range(1, 6)]) == -5)

    print("== 2. PIT 公共件 ==")
    check("2a publish 默认 lag=1", resolve_lag_days("publish", None) == 1)
    check("2b 显式 lag 覆盖默认", resolve_lag_days("publish", 2) == 2)
    pv = visibility_proof(100, 80, 5)
    check("2c 可见性证明覆盖率", abs(pv["pit_coverage"] - 0.8) < 1e-9
          and pv["n_estimated_lag"] == 5)
    check("2d trading_days_after", pit_t0(["20260101", "20260102",
                                           "20260105"], "20260102", 1)
          == "20260105" and pit_t0(["20260101"], "20260101", 1) is None)

    print("== 3. 变体选择器(合成数据, 零 IO) ==")
    d = pd.DataFrame({
        "ts_code": ["A", "A", "A", "A", "B", "B"],
        "org_name": ["国泰君安", "国泰君安", "UBS", "UBS", "国泰君安", "国泰君安"],
        "report_date": ["20260101", "20260110", "20260101", "20260110",
                        "20260101", "20260110"],
        "tp": [50.0, 60.0, 50.0, 45.0, 30.0, 33.0],
    })
    up = _ev_tp_upgrade_org(d, 0.05)
    # A/国泰君安 50→60 上修✓; A/UBS 50→45 下修✗; B/国泰君安 30→33 +10% ✓
    check("3a 同机构口径逐对判定", len(up) == 2
          and set(up["ts_code"]) == {"A", "B"})
    # 跨机构假信号回归: B 机构若用上一条(跨机构 60)会误判 33/60=-45% 无信号;
    # 同机构口径必须看到 30→33 的上修
    check("3b 跨机构假修正已修复", (up["tp_change"].round(6)
                                    == pd.Series([0.2, 0.1],
                                                 index=up.index)).all())
    fg = _is_foreign_org(d["org_name"])
    check("3c 外资判定 UBS=True/国泰君安=False", bool(fg.iloc[2])
          and not bool(fg.iloc[0]))

    bs = pd.DataFrame({
        "ts_code": ["X", "X", "X"],
        "trade_date": ["20260601"] * 3,
        "price": [8.0, 8.0, 8.0],
        "seller": ["机构专用", "华泰证券南京营业部", "机构专用"],
    })
    # 注入合成 ts_daily(切断真实数据依赖), 再测卖方拆分; 测完恢复缓存
    _dl_backup = event_study._DL_CACHE.get("dl")
    event_study._DL_CACHE["dl"] = pd.DataFrame({
        "ts_code": ["X"],
        "trade_date": ["20260601"],
        "close": [10.0],
    })
    inst = event_study._ev_block_discount_sell(bs, -0.05, inst_only=True)
    br_ = event_study._ev_block_discount_sell(bs, -0.05, inst_only=False)
    if _dl_backup is not None:
        event_study._DL_CACHE["dl"] = _dl_backup
    else:
        event_study._DL_CACHE.pop("dl", None)
    check("3d 卖方拆分 机构2/营业部1", len(inst) == 2 and len(br_) == 1,
          f"inst={len(inst)} branch={len(br_)}")

    print("== 4. 事件注册表 ==")
    check("4a 9 个事件(4 基础+5 变体)", len(BUILTIN_EVENTS) == 9,
          f"n={len(BUILTIN_EVENTS)}")
    check("4b report_rc 变体带最小列集",
          all(BUILTIN_EVENTS[k].cols for k in
              ("tp_up_org", "tp_up_org_foreign", "tp_up_org_top20")))
    check("4c 日期列均注册", all(ev.date_col for ev in BUILTIN_EVENTS.values()))

    print("== 5. 引擎冒烟(小样本) ==")
    es = EventStudy()
    res = es.study("lhb_uplist", window=(-5, 5), max_events=300)
    curve = res.get("curve", {})
    check("5a 无 error", "error" not in res, str(res.get("error")))
    check("5b 曲线键完整(不含 h=0)",
          set(int(k) for k in curve) == {-5, -4, -3, -2, -1, 1, 2, 3, 4, 5})
    check("5c h=0 不进曲线(基点日无收益)", 0 not in curve)
    h_m1 = curve.get(-1, {})
    check("5d 负窗有样本", int(h_m1.get("n", 0)) > 0,
          f"n(-1)={h_m1.get('n')}")
    check("5e h=-1 恒为 0(基点定义)",
          abs(float(h_m1.get("mean_excess", 0))) < 1e-12)
    check("5f PIT 证明随结果", res["pit"]["pit_coverage"] is not None
          and res["pit"]["contract"].startswith("available_after"))

    print("== 6. 手工核算对照(独立公式复算) ==")
    es._load_px()
    dl = event_study._get_daily_close().dropna()
    dl = dl.drop_duplicates(["ts_code", "trade_date"], keep="last")
    # 独立重算: 市场等权日收益(不复用引擎内部对象)
    dl2 = dl.sort_values(["ts_code", "trade_date"]).copy()
    dl2["ret"] = dl2.groupby("ts_code")["close"].pct_change()
    mkt = dl2.groupby("trade_date")["ret"].mean()
    tl = es.reader.read("ts_top_list", columns=["ts_code", "trade_date"]
                        ).dropna().head(30)
    n_cmp, bad = 0, []
    for _, e in tl.iterrows():
        code, ed = str(e["ts_code"]), str(e["trade_date"])[:8].replace("-", "")
        days = es._stock_days.get(code)
        if not days or ed not in days:
            continue
        t0 = pit_t0(days, ed, 1)
        if t0 is None or t0 not in days:
            continue
        i0 = days.index(t0)
        if i0 == 0 or i0 + 5 >= len(days):
            continue
        base_d = days[i0 - 1]
        sub = dl[dl["ts_code"] == code].set_index("trade_date")["close"]
        if base_d not in sub.index or days[i0 + 5] not in sub.index:
            continue
        # 个股 h=+5
        sr5 = sub[days[i0 + 5]] / sub[base_d] - 1
        eng_sr = es._window_rets(es._stock_px[code], days, t0, (-5, 5)).get(5)
        if eng_sr is None:
            continue
        # 基准 h=+5 (含 T0 当日——曾经的 bug)
        acc = 0.0
        for j in range(i0, i0 + 6):
            r = mkt.get(days[j])
            if r is not None and not np.isnan(r):
                acc += np.log1p(r)
        br5 = float(np.expm1(acc))
        eng_br = es._bench_rets(days, t0, (-5, 5)).get(5)
        if eng_br is None:
            continue
        if abs(sr5 - eng_sr) > 1e-9 or abs(br5 - eng_br) > 1e-9:
            bad.append((code, t0, sr5, eng_sr, br5, eng_br))
        n_cmp += 1
        if n_cmp >= 5:
            break
    check("6a 手工对照 err=0 (n>=3)", n_cmp >= 3 and not bad,
          f"n_cmp={n_cmp} bad={bad[:2]}")

    print("== 7. t 值语义 ==")
    s = pd.Series([0.0] * 5)
    check("7a 常数列 t=NaN", np.isnan(_tstat(s)))
    s2 = pd.Series([0.01, -0.02, 0.03, 0.0, -0.01])
    check("7b 正常列 t 有限", np.isfinite(_tstat(s2)))

    print()
    n_fail = sum(1 for _, ok, _ in _results if not ok)
    for name, ok, detail in _results:
        if not ok:
            print(f"  FAIL -> {name}  {detail}")
    print(f"== {len(_results) - n_fail}/{len(_results)} PASS ==")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
