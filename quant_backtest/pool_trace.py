#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""缠论池 · 盘中快照溯源（"当时说了什么，后来怎么样"）
============================================================

为什么需要它：
  一天 16 份 pool_judge_*.json 躺在 reports/ 里，每份都记了「此刻 A/B/C/D/E
  是谁」，但没有任何一个工具把它们串起来。于是每天收盘后能说的只有最后
  那一刻的名单——上午 10:05 点过谁、那只票后来走到哪、被否的 305 只里有
  多少其实涨了，全是空白。缺的不是记录，是回看。

四条不许含糊的口径：
  1. 收益 = 首次点名那个快照里的 lpx → 当日最后一次快照里的 lpx。
     不含滑点，不含「这个价能不能真的买到」，也不含 A/B/E 语义差异。
     它只回答「事后看，那一刻的位置对不对」，不回答「能不能赚」。
  2. 各层语义不同，**不能加总平均**：A/B/E 是可介入、C 是持有跟踪、
     D 是减仓预警（它对了应该跌）。分开展示，各自比各自的。
  3. 被否的票（drop）同等对待。判据只有能把自己错在哪说清楚，才叫判据。
  4. **末刻首现的样本不计入统计**。它在「首现→最后」之间的价格必然没变，
     ret 恒为 0，混进来会把均值往 0 拽、把胜率往跌拽。单列「不可评估」。

用法：
  python pool_trace.py                        # 今天
  python pool_trace.py --date 20261009
  python pool_trace.py --date 20261109 --detail
  python pool_trace.py --date 20261109 --layer B --detail
  python pool_trace.py --date 20261109 --save          # 落 trace，供 T+N 用
  python pool_trace.py --forward 20261109 --n 1        # 次日回看（读日线）
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

REPORTS = os.path.join(HERE, "reports")
ASHARE = os.path.join(HERE, "data", "ashare")

LAYERS = ("A", "B", "C", "D", "E")
LNAME = {"A": "A 突破", "B": "B 三买", "C": "C 持有", "D": "D 衰竭", "E": "E 贴沿"}
LSIDE = {"A": "可介入", "B": "可介入", "C": "持有跟踪", "D": "减仓预警", "E": "可介入"}
# D 是反向判据：对了应该跌。统计时单独处理，不与买侧混算。
REVERSE = {"D"}
FLAT = 0.001          # ±0.1% 以内算平，不计胜率


# ───────────────────────── 排版 ─────────────────────────
def _dw(s):
    """显示宽度：CJK/全角按 2 算。%-6s 只数字符，中文列必然错位。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in str(s))


def _p(s, w, right=False):
    s = str(s)
    gap = max(w - _dw(s), 0)
    return (" " * gap + s) if right else (s + " " * gap)


# ───────────────────────── 读盘 ─────────────────────────
def load_snaps(date):
    """读当日全部 judge 快照，按 live_at 去重。

    同一 live_at 多份是重跑（同一盘面、不同代码版本），保留 run_at 最新
    的那份——它代表当前口径。若不分，一次调试就能让同 5 分钟被数 4 遍。
    """
    fs = sorted(glob.glob(os.path.join(REPORTS, "pool_judge_%s_*.json" % date)))
    by, raw = {}, len(fs)
    for p in fs:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:                       # noqa: BLE001
            continue
        key = d.get("live_at") or os.path.basename(p)
        prev = by.get(key)
        if prev is None or (d.get("run_at") or "") >= (prev.get("run_at") or ""):
            by[key] = d
    snaps = [by[k] for k in sorted(by)]
    return snaps, raw


def _lag_min(snap):
    """run_at - live_at 的分钟数。盘中计划任务跑，差值通常 <2 分钟；
    手工补跑/调试重算会差几十分钟——那是「事后重算」，不是当时那一枪。"""
    try:
        a = datetime.strptime(snap["live_at"], "%Y-%m-%d %H:%M:%S")
        b = datetime.strptime(snap["run_at"], "%Y-%m-%d %H:%M:%S")
        return int((b - a).total_seconds() // 60)
    except Exception:                           # noqa: BLE001
        return None


def px_map(snap):
    """某一刻的全量价：五层 + 被否，后者 setdefault 不覆盖前者。"""
    m = {}
    for L in LAYERS:
        for it in snap.get(L) or []:
            if it.get("lpx") is not None:
                m[it["code"]] = it
    for it in snap.get("drop") or []:
        if it.get("lpx") is not None:
            m.setdefault(it["code"], it)
    return m


def last_quotes(snaps):
    """每股当日最后一个已知价。逆序扫，第一枪命中即停。"""
    out = {}
    for s in reversed(snaps):
        for c, it in px_map(s).items():
            if c not in out:
                out[c] = it
    return out


# ───────────────────────── 归集 ─────────────────────────
def collect(snaps):
    """首现表：layer -> code -> 首现时的记录（含快照序号，用于配对基准）。"""
    first = {L: {} for L in LAYERS}
    for i, s in enumerate(snaps):
        t = (s.get("live_at") or "")[11:16]
        for L in LAYERS:
            for it in s.get(L) or []:
                c = it.get("code")
                if not c or c in first[L]:
                    continue
                rec = dict(it)
                rec["first_t"] = t
                rec["first_px"] = it.get("lpx")
                rec["_i"] = i
                first[L][c] = rec
    return first


def mkt_ret(snaps, i0, i1):
    """同期结构池基准收益：首现那一刻的池内中位涨幅 → 最后一刻的中位涨幅。

    用中位不用均值——均值被涨停票拉偏。缺该字段（旧快照）返回 None，
    报告里如实写「无基准」，不拿涨跌家数比糊弄过去。
    """
    a = (snaps[i0].get("market") or {}).get("med")
    b = (snaps[i1].get("market") or {}).get("med")
    if a is None or b is None:
        return None
    return (1 + b / 100.0) / (1 + a / 100.0) - 1


def _stat(rows, reverse=False, band=FLAT):
    """三分类，不平白算输。"""
    if not rows:
        return None
    v = sorted(r[0] for r in rows)
    n = len(v)
    med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
    up = sum(1 for x in v if x > band)
    dn = sum(1 for x in v if x < -band)
    good = dn if reverse else up
    return {"n": n, "mean": sum(v) / n, "med": med, "up": up,
            "flat": n - up - dn, "dn": dn,
            "win": good / n if n else 0.0, "best": v[-1], "worst": v[0]}


# ───────────────────────── 打印 ─────────────────────────
def report(date, snaps, raw, detail=False, only=None):
    if not snaps:
        print("[trace] %s 无 judge 快照。先跑 pool_state.py --judge --save" % date)
        return 1

    last = snaps[-1]
    lt = (last.get("live_at") or "")[11:19]
    lastq = last_quotes(snaps)
    first = collect(snaps)
    tail_t = (snaps[-1].get("live_at") or "")[11:16]
    tail2_t = (snaps[-2].get("live_at") or "")[11:16] if len(snaps) > 1 else ""

    print("═" * 104)
    print("缠论池 · 盘中溯源   %s" % date)
    print("═" * 104)
    dup = raw - len(snaps)
    print("快照 %d 份（原始 %d%s）· 盘面 %s → %s · 结构基准 %s"
          % (len(snaps), raw, "，同盘面重复 %d 已合并" % dup if dup else "",
             (snaps[0].get("live_at") or "")[11:19], lt,
             last.get("struct_asof")))
    closed = lt >= "15:00"
    if not closed:
        print("！最后一刻 %s 不是收盘，下文「最后价」实为此刻价。"
              "收盘复盘须 15:00 后再跑一次 --judge --save。" % lt[:5])

    # ── 1. 时点 ──
    print("\n── 1. 时点（盘面怎么变）" + "─" * 78)
    print(_p("时刻", 7) + _p("已交易", 8, 1) + _p("A", 4, 1) + _p("B", 4, 1)
          + _p("C", 4, 1) + _p("D", 4, 1) + _p("E", 4, 1) + _p("被否", 6, 1)
          + "  " + _p("涨/跌/停", 14) + "备注")
    prev_fp = None
    for s in snaps:
        c = s.get("counts") or {}
        # 早期快照的 counts 结构不同，缺键时回退到列表长度——否则一天开头
        # 那几份会全印成 0，看着像「A/B/C 全空」，实际是字段没取到。
        def _n(L):
            v = c.get(L)
            return v if isinstance(v, int) else len(s.get(L) or [])
        fp = (s.get("params") or {}).get("fingerprint")
        note = []
        if fp and prev_fp and fp != prev_fp:
            note.append("口径变更 %s→%s" % (prev_fp, fp))
        if fp and not prev_fp:
            note.append("口径 %s" % fp)
        if fp:
            prev_fp = fp
        lag = _lag_min(s)
        if lag is not None and lag >= 20:
            note.append("事后重算 %d 分钟" % lag)
        mk = (s.get("market") or {}).get("med")
        if mk is not None:
            note.append("池中位 %+.2f%%" % mk)
        up, dn, lu = s.get("up"), s.get("down"), s.get("limit_up")
        print(_p((s.get("live_at") or "")[11:16], 7)
              + _p(s.get("mins"), 8, 1) + _p(_n("A"), 4, 1)
              + _p(_n("B"), 4, 1) + _p(_n("C"), 4, 1)
              + _p(_n("D"), 4, 1) + _p(_n("E"), 4, 1)
              + _p(_n("drop"), 6, 1) + "  "
              + _p("%s/%s/%s" % (up, dn, lu) if up is not None else "-", 14)
              + " ".join(note))

    # ── 1b. 市场背景 ──
    # 分母用 up+down 而不是 judged：早期快照没有 judged 字段，用它当分母会
    # 让「净宽」恒等于 0，看着像「市场没动」，其实只是分母没取到。
    bg = [((s.get("live_at") or "")[11:16], s.get("up"), s.get("down"))
          for s in snaps if s.get("up") and s.get("down")]
    if len(bg) >= 2:
        t0, u0, d0 = bg[0]
        t1, u1, d1 = bg[-1]
        w0 = (u0 - d0) / (u0 + d0) * 100
        w1 = (u1 - d1) / (u1 + d1) * 100
        print("\n── 1b. 市场背景（涨跌家数，相对昨收；净宽 =（涨−跌）/（涨+跌））" + "─" * 38)
        print("  %s   %d 涨 / %d 跌  → 净宽 %+.0f%%" % (t0, u0, d0, w0))
        print("  %s   %d 涨 / %d 跌  → 净宽 %+.0f%%   变动 %+.0f 个百分点"
              % (t1, u1, d1, w1, w1 - w0))
        if w1 < w0 - 20:
            print("  这段时间市场在明显走弱。下文各层的负收益里有多少是 β、多少是判据，"
                  "没有同期基准就拆不开——超额列若是空的，正是这个原因。")

    # ── 2. 各层当日表现 ──
    print("\n── 2. 各层当日表现（首现价 → 当日最后价，未计滑点）" + "─" * 52)
    print(_p("层", 10) + _p("语义", 10) + _p("样本", 6, 1) + _p("均值", 9, 1)
          + _p("中位", 9, 1) + _p("超额", 9, 1) + _p("涨/平/跌", 12, 1)
          + _p("胜率", 7, 1) + " 首现区间")
    rows_by_layer = {}
    for L in LAYERS:
        if only and L != only:
            continue
        rows, stall = [], 0
        for c, rec in first[L].items():
            if rec.get("first_t") == tail_t:
                stall += 1                      # 末刻才首现，ret 恒 0，不可评估
                continue
            q = lastq.get(c)
            if not q or not rec.get("first_px") or not q.get("lpx"):
                continue
            rows.append((q["lpx"] / rec["first_px"] - 1, c, rec, q,
                         mkt_ret(snaps, rec.get("_i", 0), len(snaps) - 1)))
        rows_by_layer[L] = rows
        st = _stat(rows, reverse=(L in REVERSE))
        if st is None:
            print(_p(LNAME[L], 10) + _p(LSIDE[L], 10) + _p(0, 6, 1)
                  + _p("-", 9, 1) + _p("-", 9, 1) + _p("-", 9, 1) + _p("-", 12, 1)
                  + _p("-", 7, 1) + " 仅末刻 %d 只，无法评估" % stall)
            continue
        ex = [r[0] - r[4] for r in rows if r[4] is not None]
        exs = ("%+.2f%%" % (sum(ex) / len(ex) * 100)) if ex else "-"
        ts = sorted(r[2].get("first_t") or "" for r in rows)
        print(_p(LNAME[L], 10) + _p(LSIDE[L], 10) + _p(st["n"], 6, 1)
              + _p("%+.2f%%" % (st["mean"] * 100), 9, 1)
              + _p("%+.2f%%" % (st["med"] * 100), 9, 1)
              + _p(exs, 9, 1)
              + _p("%d/%d/%d" % (st["up"], st["flat"], st["dn"]), 12, 1)
              + _p("%.0f%%" % (st["win"] * 100), 7, 1)
              + " %s~%s%s%s" % (ts[0], ts[-1],
                                "  +末刻%d" % stall if stall else "",
                                "  ← 反向" if L in REVERSE else ""))

    print("\n  注：胜率按「方向正确」算（A/B/E/C 涨为对，D 跌为对），平盘算平不算输。")
    print("      各层语义不同，不可加总。末刻首现的样本已剔除（没有可观测区间）。")
    n_mkt = sum(1 for s in snaps if (s.get("market") or {}).get("med") is not None)
    if n_mkt == len(snaps) and n_mkt:
        print("      超额 = 个股同期收益 − 结构池中位同期收益（逐票配对，剔掉大盘那段 β）。")
    elif n_mkt:
        print("      超额列空着：只有 %d/%d 份快照存了 market，缺起点就算不出配对基准。"
              % (n_mkt, len(snaps)))
    else:
        print("      ！本次快照均未存 market 字段，无法算超额。"
              "「均值 -1%」可能只是大盘在跌，这一列空着就别当结论用。")

    # ── 3. 被否的票 ──
    if not only:
        drops, nodrop_px = {}, set()
        for s in snaps:
            for it in s.get("drop") or []:
                c = it.get("code")
                if not c or c in drops:
                    continue
                if it.get("lpx") is None:
                    nodrop_px.add(c)            # 同一只票在十几份快照里都无价，只算一次
                    continue
                drops[c] = dict(it, first_t=(s.get("live_at") or "")[11:16])
        rows = []
        for c, rec in drops.items():
            if rec.get("first_t") == tail_t:
                continue
            q = lastq.get(c)
            if not q or not q.get("lpx"):
                continue
            rows.append((q["lpx"] / rec["lpx"] - 1, c, rec, q))
        print("\n── 3. 被否的票（首否价 → 当日最后价）" + "─" * 70)
        st = _stat(rows)
        if st:
            print("可追踪 %d 只   均值 %+.2f%%   中位 %+.2f%%   涨/平/跌 %d/%d/%d"
                  % (st["n"], st["mean"] * 100, st["med"] * 100,
                     st["up"], st["flat"], st["dn"]))
            print("  「被否还大涨」不必然是判据错：否决理由是「止损过远」时，它涨"
                  "恰恰在验证动量；只有「结构不合格」类才需要复盘。")
        else:
            print("无可追踪样本%s。" % (
                "（%d 条 drop 只有理由没有价格——旧版落盘字段，明日快照起有价）"
                % len(nodrop_px) if nodrop_px else ""))

    # ── 4. 明细 ──
    if detail:
        # 最后一份快照里被否的票：首现时在层内、现在已被判据剔除。
        # 这是「当时对、按现行口径不对」的那一类，必须标出来——否则
        # 拿一份旧口径的名单做复盘，会把已经失效的样本当成有效样本。
        now_drop = {it.get("code"): it.get("why")
                    for it in (snaps[-1].get("drop") or [])}
        print("\n── 4. 明细" + "─" * 92)
        for L in LAYERS:
            if only and L != only:
                continue
            rows = rows_by_layer.get(L) or []
            if not rows:
                continue
            print("\n%s（%d）" % (LNAME[L], len(rows)))
            print("  " + _p("代码", 12) + _p("名称", 10) + _p("首现", 7)
                  + _p("首现价", 9, 1) + _p("最后价", 9, 1) + _p("变化", 9, 1)
                  + _p("超额", 9, 1) + " 守位/距守位")
            for ret, c, rec, q, mk in sorted(rows, key=lambda x: -x[0]):
                # 守位与「距守位」必须同源。三买待守末中枢上沿（guard/zg），
                # 三买守信号中枢上沿（sig_zg）——混用会让两个数互相打脸。
                # 早期快照没存 guard 字段，用 stage 兜底。
                g = rec.get("guard")
                if not g:
                    g = (rec.get("zg") if rec.get("stage") == "三买待"
                         else (rec.get("sig_zg") or rec.get("zg")))
                dg = (rec["first_px"] / g - 1) if (g and rec.get("first_px")) else None
                extra = ("守位%.2f" % g) if L in ("B", "C") and g else ""
                if dg is not None:
                    extra += "  %+.0f%%" % (dg * 100)
                if c in now_drop:
                    extra += "  [末刻已否：%s]" % (now_drop[c] or "")
                ex = ("%+.2f%%" % ((ret - mk) * 100)) if mk is not None else "-"
                print("  " + _p(c, 12) + _p((rec.get("name") or "")[:6], 10)
                      + _p(rec.get("first_t"), 7)
                      + _p("%.2f" % (rec.get("first_px") or 0), 9, 1)
                      + _p("%.2f" % (q.get("lpx") or 0), 9, 1)
                      + _p("%+.2f%%" % (ret * 100), 9, 1)
                      + _p(ex, 9, 1) + " " + extra)

    return 0


# ───────────────────────── 落盘 / T+N ─────────────────────────
def dump(date, snaps):
    first, lastq = collect(snaps), last_quotes(snaps)
    tail_t = (snaps[-1].get("live_at") or "")[11:16]
    entries = []
    for L in LAYERS:
        for c, rec in first[L].items():
            q = lastq.get(c) or {}
            t0 = None
            if q.get("lpx") and rec.get("first_px") and rec.get("first_t") != tail_t:
                t0 = q["lpx"] / rec["first_px"] - 1
            entries.append({
                "date": date, "layer": L, "code": c, "name": rec.get("name"),
                "stage": rec.get("stage"), "first_t": rec.get("first_t"),
                "first_px": rec.get("first_px"), "zg": rec.get("zg"),
                "zd": rec.get("zd"), "dguard": rec.get("dguard"),
                "last_px": q.get("lpx"), "t0": t0,
                "eval": t0 is not None,
            })
    p = os.path.join(REPORTS, "pool_trace_%s.json" % date)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"date": date, "live_at": snaps[-1].get("live_at"),
                   "snaps": len(snaps),
                   "struct_asof": snaps[-1].get("struct_asof"),
                   "entries": entries}, f, ensure_ascii=False, indent=1)
    print("\n[saved] %s  (%d 条，可评估 %d)"
          % (p, len(entries), sum(1 for e in entries if e["eval"])))
    return p


def _bars(code):
    import pandas as pd
    num, _, mkt = code.partition(".")
    p = os.path.join(ASHARE, "%s_%s.parquet" % (num, mkt))
    if not os.path.exists(p):
        return None
    return pd.read_parquet(p).sort_values("trade_date")


def forward(date, n):
    """读已落盘的 trace，用日线补 T+N。日线数据没到就如实说没到。"""
    p = os.path.join(REPORTS, "pool_trace_%s.json" % date)
    if not os.path.exists(p):
        print("[trace] 缺 %s，先跑 --date %s --save" % (os.path.basename(p), date))
        return 1
    with open(p, encoding="utf-8") as f:
        tr = json.load(f)

    print("═" * 96)
    print("缠论池 · T+%d 回看   %s 首现 → 之后第 %d 个交易日收盘" % (n, date, n))
    print("═" * 96)
    by, miss = {}, 0
    for e in tr["entries"]:
        if not e.get("eval"):
            continue
        df = _bars(e["code"])
        if df is None:
            miss += 1
            continue
        fut = df[df["trade_date"] > date]
        if len(fut) < n:
            miss += 1
            continue
        by.setdefault(e["layer"], []).append(
            (float(fut.iloc[n - 1]["close"]) / e["first_px"] - 1, e))
    if miss:
        print("（%d 条日线未到，不计入）" % miss)
    print(_p("层", 10) + _p("样本", 6, 1) + _p("均值", 9, 1) + _p("中位", 9, 1)
          + _p("涨/平/跌", 12, 1) + _p("胜率", 7, 1))
    for L in LAYERS:
        st = _stat(by.get(L) or [], reverse=(L in REVERSE))
        if not st:
            print(_p(LNAME[L], 10) + _p(0, 6, 1) + _p("-", 9, 1) + _p("-", 9, 1)
                  + _p("-", 12, 1) + _p("-", 7, 1))
            continue
        print(_p(LNAME[L], 10) + _p(st["n"], 6, 1)
              + _p("%+.2f%%" % (st["mean"] * 100), 9, 1)
              + _p("%+.2f%%" % (st["med"] * 100), 9, 1)
              + _p("%d/%d/%d" % (st["up"], st["flat"], st["dn"]), 12, 1)
              + _p("%.0f%%" % (st["win"] * 100), 7, 1))
    return 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                           # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--layer", default=None, choices=list(LAYERS))
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--forward", default=None, metavar="DATE", help="对某日 trace 做 T+N")
    ap.add_argument("--n", type=int, default=1)
    a = ap.parse_args()

    if a.forward:
        return forward(a.forward, a.n)

    snaps, raw = load_snaps(a.date)
    rc = report(a.date, snaps, raw, detail=a.detail, only=a.layer)
    if a.save and snaps:
        dump(a.date, snaps)
    return rc


if __name__ == "__main__":
    sys.exit(main() or 0)
