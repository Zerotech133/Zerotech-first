# -*- coding: utf-8 -*-
"""K35 三线联动 · 五信号标注引擎 v1（影子观察，只判定+展示不推送）。

信号体系（编号对齐联动复盘模板）：
  ① 抗跌      跌市（大盘或板块窗口收益<0）且 超额(个股-板块) ≥ +5pp
  ② 滞涨(弱于板块)  板块窗口上涨 且 超额 ≤ -5pp（信号2 产线同阈值，
                慢变量 5pp=九维 1pp 十倍宽放）
  ③ 共振底    三线收盘均跌破各自近端前低（lo_win 根，不含最新一根）
  ④ 孤军深入  个股收盘突破近端前高（hi_win 根）而板块/大盘未突破
  ⑤ 三共振    三线收盘同破各自近端前高

口径声明：
  · 窗口收益=联动页归一化序列末值（公共首根=0%，1d 默认 250 根），
    超额=末值个股-板块（与 turn_lookup/联动页同源）；
  · 关键位用 close 近端极值近似（series 只有收盘线；盘中 high/low
    假突破不计，日线收盘口径更严）；
  · 每周期独立判定（30m/5m 的 lo/hi 窗口按 K 线根数计，非自然日）。

用法:
  python linkage_signals.py 301216.SZ            # 三周期判定报告
  python linkage_signals.py 301216.SZ --periods 1d --lo-win 60 --hi-win 120
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import linkage as LK  # noqa: E402

GAP = 5.0            # 超额显著性阈值 pp（信号② 产线同款）
LO_WIN, HI_WIN = 60, 120   # 前低/前高近端窗口（根，不含最新一根）
NAMES = {
    "1": "抗跌", "2": "滞涨(弱于板块)", "3": "共振底",
    "4": "孤军深入", "5": "三共振",
}


def _last(arr):
    """有效末值（None 过滤）。"""
    v = [x for x in arr if x is not None]
    return v[-1] if v else None


def _extreme(arr, win, fn):
    """近端极值（排除最新一根的有效值；不足窗口用现有值，len<2 返回 None）。"""
    v = [x for x in arr if x is not None][:-1]
    if len(v) < 2:
        return None
    return fn(v[-int(win):])


def judge_series(r, gap=GAP, lo_win=LO_WIN, hi_win=HI_WIN):
    """单周期判定。r = linkage.series(...) 返回。纯读 r，无 IO。"""
    keys = [k for k in ("stock", "index", "block") if r.get(k)]
    if "stock" not in keys:
        return {"signals": [], "ctx": {"error": "无个股线"}}
    rv = {k: _last(r[k]) for k in keys}
    ctx = {"r": rv}
    sigs = []

    if "block" in keys:
        ex = round(rv["stock"] - rv["block"], 2)
        ctx["excess_vs_block"] = ex
        # ② 滞涨：板块涨而个股掉队（模板信号2：板块上涨+显著落后）
        if rv["block"] > 0 and ex <= -gap:
            sigs.append({"tag": "2", "note": "板块 %+.1f%% 而超额 %+.1fpp"
                         % (rv["block"], ex)})
        # ① 抗跌：跌市而个股少跌（大盘或板块任一为负）
        if (rv.get("index", 0) < 0 or rv["block"] < 0) and ex >= gap:
            sigs.append({"tag": "1", "note": "超额 %+.1fpp（跌市少跌）"
                         % ex})

    # 关键位（close 口径，前低/前高窗口排除最新一根）
    lo = {k: _extreme(r[k], lo_win, min) for k in keys}
    hi = {k: _extreme(r[k], hi_win, max) for k in keys}
    ctx["prev_low"], ctx["prev_high"] = lo, hi
    brk_lo = {k: (lo[k] is not None and rv[k] < lo[k]) for k in keys}
    brk_hi = {k: (hi[k] is not None and rv[k] > hi[k]) for k in keys}
    ctx["break_low"], ctx["break_high"] = brk_lo, brk_hi
    need = ["stock", "index", "block"]
    if all(k in keys for k in need):
        if all(brk_lo[k] for k in need):
            sigs.append({"tag": "3", "note": "三线同破前低（%s）"
                         % "/".join("%+.1f" % rv[k] for k in need)})
        if all(brk_hi[k] for k in need):
            sigs.append({"tag": "5", "note": "三线同破前高（%s）"
                         % "/".join("%+.1f" % rv[k] for k in need)})
        elif brk_hi["stock"]:
            laggard = [k for k in ("index", "block") if not brk_hi[k]]
            sigs.append({"tag": "4", "note": "个股独破前高，%s 未破"
                         % "、".join(laggard)})
    for s in sigs:
        s["name"] = NAMES[s["tag"]]
    return {"signals": sigs, "ctx": ctx}


def judge(code, periods=LK.PERIODS, last=None, block=None, block_name=None,
          index=None, gap=GAP, lo_win=LO_WIN, hi_win=HI_WIN):
    """三周期全判。返回 {period: judge_series 结果或 {"error":…}}。"""
    out = {}
    for per in periods:
        r = LK.series(code, per, last, block=block, block_name=block_name,
                      index_code=index or LK.DEFAULT_INDEX)
        if not r.get("ok"):
            out[per] = {"error": r.get("error", "fail")}
            continue
        out[per] = judge_series(r, gap=gap, lo_win=lo_win, hi_win=hi_win)
        out[per]["last_pct"] = {k: (r[k][-1] if r.get(k) else None)
                                for k in ("stock", "index", "block")}
    return out


def report(res, code):
    """判定结果 → 人读报告文本。"""
    L = ["== 五信号标注 · %s ==" % code,
         "阈值：超额±%.0fpp｜前低 %d 根 / 前高 %d 根（close 口径，排除最新一根）"
         % (GAP, LO_WIN, HI_WIN)]
    for per, d in res.items():
        if "error" in d:
            L.append("[%s] 取数失败：%s" % (per, d["error"]))
            continue
        lp = d.get("last_pct", {})
        head = "[%s] 末值 个股 %s%% · 板块 %s%% · 大盘 %s%%" % (
            per, lp.get("stock"), lp.get("block"), lp.get("index"))
        L.append(head)
        if d["signals"]:
            for s in d["signals"]:
                L.append("  ⚑ 信号%s %s —— %s" % (s["tag"], s["name"],
                                                   s["note"]))
        else:
            L.append("  无信号")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="三线联动五信号标注（影子）")
    ap.add_argument("code")
    ap.add_argument("--periods", default=",".join(LK.PERIODS))
    ap.add_argument("--last", type=int, default=0)
    ap.add_argument("--block", default=None)
    ap.add_argument("--block-name", default=None)
    ap.add_argument("--index", default=None)
    ap.add_argument("--gap", type=float, default=GAP)
    ap.add_argument("--lo-win", type=int, default=LO_WIN)
    ap.add_argument("--hi-win", type=int, default=HI_WIN)
    a = ap.parse_args()
    res = judge(a.code, a.periods.split(","), a.last or None,
                block=a.block, block_name=a.block_name, index=a.index,
                gap=a.gap, lo_win=a.lo_win, hi_win=a.hi_win)
    print(report(res, a.code))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
