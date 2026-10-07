# -*- coding: utf-8 -*-
"""缠论结构时序图谱（第8步）：信号前结构序列指纹 + 分组战绩。

学习来源：数据强 Semantica 进阶篇"时序图谱"（Allen 区间关系）。
结合本项目：缠论结构天然是区间对象——笔 [s_dt,e_dt]、中枢
[s_dt,e_dt]×[zd,zg]，信号锚定在笔端点。Allen 关系在本项目的
简化口径（如实声明，非完整 13 关系）：
  · 笔×笔：时序相邻（前/后）+ 同向性（U/D 交替）→ bis6 序列
  · 笔×中枢 / 信号×中枢：包含(in)/上方(above)/下方(below) → pv
  · 中枢×中枢：个数递进（tbe 趋势背驰需 ≥2 同向不重叠）→ npv

指纹槽位（全部为信号时点前可见信息，无未来函数）：
  bis6  信号笔及其前 ≤5 根笔的方向序列（如 "UDUDUD"，末位=信号笔方向）
  amp   信号笔 vs 前一同向笔力度比 → exp(>1.05)/con(<0.95)/flat
        （同一类动量衰减口径：端点绝对价差）
  pv    信号价 vs 末个已完成中枢：above/below/in（3买需 above、3卖需 below）
  npv   窗口内已完成中枢数 0/1/2/3+

数据：chanlun_paper.json closed[]（ret=_dir_ret 方向口径，win=ret>0；
adj_flag 行剔除）。结构：每码一次 analyze(500 根全窗口，与扫描口径一致)，
按 (type, sig_dt) 匹配信号——全窗口口径=终修订结构（稳定、无修正噪声），
结构不重现记 matched=false 并计数。

用法：
  python chanlun_tgraph.py build          # 回填全部 closed → tgraph.jsonl
  python chanlun_tgraph.py stats [类型]    # 指纹分组战绩（可选 tbe 过滤 --tbe）
  python chanlun_tgraph.py slot  [类型]    # 槽位边际战绩（小样本更稳）
"""
import argparse
import json
import os
import statistics
import sys
import tempfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from chanlun_signal import analyze                     # noqa: E402
from chanlun_scan import _bars_from_parquet, _code_path, \
    MIN_BARS, _idx_bars                              # noqa: E402
from chanlun_flow import regime_trend_at             # noqa: E402

PAPER = os.path.join(HERE, "data", "chanlun_paper.json")
TGRAPH = os.path.join(HERE, "data", "semantica", "tgraph.jsonl")
LIMIT_BARS = 500           # 与 chanlun_scan 扫描口径一致
AMP_EXP, AMP_CON = 1.05, 0.95


# ── 指纹提取 ─────────────────────────────────────────────────
def _fp(res, sig):
    """从 analyze 结果提取信号前结构指纹。失败返回 None。"""
    bis = res.get("bis") or []
    k = next((i for i, b in enumerate(bis)
              if b.get("e_dt") == sig.get("dt")), None)
    if k is None:
        return None
    dirs = "".join("U" if b.get("dir") == 1 else "D"
                   for b in bis[max(0, k - 5):k + 1])
    # 信号笔 vs 前一同向笔力度比（同向笔相隔 2 根）
    amp = None
    if k >= 2 and bis[k - 2].get("dir") == bis[k].get("dir"):
        f0 = abs((bis[k].get("e_price") or 0) - (bis[k].get("s_price") or 0))
        f1 = abs((bis[k - 2].get("e_price") or 0)
                 - (bis[k - 2].get("s_price") or 0))
        if f0 and f1:
            r = f0 / f1
            amp = "exp" if r > AMP_EXP else "con" if r < AMP_CON else "flat"
    # 信号前已完成中枢（e_dt ≤ 信号 dt）
    pvs = [p for p in (res.get("pivots") or [])
           if (p.get("e_dt") or "") <= (sig.get("dt") or "")]
    npv = min(len(pvs), 3)
    pv = None
    if pvs:
        p = pvs[-1]
        px = sig.get("price")
        if px is not None:
            pv = ("above" if px > p["zg"] else
                  "below" if px < p["zd"] else "in")
    return {"bis6": dirs, "amp": amp, "pv": pv, "npv": npv,
            "fp": "%s|%s|%s|%d" % (dirs, amp or "-", pv or "-", npv)}


# ── build（每码一次 analyze，全窗口匹配） ────────────────────
def build():
    d = json.load(open(PAPER, encoding="utf-8"))
    closed = d.get("closed", [])
    by_code = defaultdict(list)
    for r in closed:
        if r.get("adj_flag"):
            continue  # 除权缺口>0.5% 剔除（项目铁律）
        by_code[r.get("code")].append(r)
    out, n_match, n_miss, n_skip = [], 0, 0, 0
    codes = sorted(by_code)
    for ci, code in enumerate(codes):
        if (ci + 1) % 300 == 0:
            print("  %d/%d 码 matched=%d miss=%d" % (
                ci + 1, len(codes), n_match, n_miss), flush=True)
        bars = _bars_from_parquet(_code_path(code), limit=LIMIT_BARS)
        if not bars or len(bars) < MIN_BARS:
            n_skip += len(by_code[code])
            for r in by_code[code]:
                out.append(_row(r, None))
                n_miss += 1
            continue
        try:
            res = analyze(bars)
        except Exception:
            res = {}
        sigs = res.get("signals") or []
        for r in by_code[code]:
            sig = next((s for s in sigs
                        if s.get("type") == r.get("type")
                        and s.get("dt") == r.get("sig_dt")), None)
            fp = _fp(res, sig) if sig else None
            if fp:
                n_match += 1
            else:
                n_miss += 1
            out.append(_row(r, fp))
    _atomic_jsonl(TGRAPH, out)
    print("BUILD 完成: %d 行 matched=%d(%0.1f%%) miss=%d skip_code=%d → %s"
          % (len(out), n_match, n_match * 100.0 / max(len(out), 1),
             n_miss, n_skip, TGRAPH))


def _row(r, fp):
    tags = r.get("tags") or {}
    row = {"code": r.get("code"), "name": r.get("name"),
           "type": r.get("type"), "sig_dt": r.get("sig_dt"),
           "detect_date": r.get("detect_date"), "ret": r.get("ret"),
           "tbe": tags.get("tbe"), "matched": fp is not None}
    if fp:
        row.update(fp)
    return row


def _atomic_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n"
                        for r in rows))
    os.replace(tmp, path)


def rows():
    if not os.path.exists(TGRAPH):
        return []
    out = []
    with open(TGRAPH, encoding="utf-8") as f:
        for ln in f:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
    return out


# ── 统计层 ───────────────────────────────────────────────────
def _sel(rs, sig_type=None, tbe=None):
    out = []
    for r in rs:
        if not r.get("matched") or r.get("ret") is None:
            continue
        if sig_type and r.get("type") != sig_type:
            continue
        if tbe is True and r.get("tbe") is not True:
            continue
        if tbe is False and r.get("tbe") is not False:
            continue
        out.append(r)
    return out


def _agg(group):
    rets = [r["ret"] for r in group]
    n = len(rets)
    return {"n": n,
            "win": round(sum(1 for v in rets if v > 0) * 100.0 / n, 1),
            "avg": round(statistics.mean(rets) * 100.0, 2)} if n else None


def fp_stats(sig_type=None, tbe=None, min_n=5):
    """指纹精确分组战绩（按 n 降序）。"""
    g = defaultdict(list)
    for r in _sel(rows(), sig_type, tbe):
        g[r.get("fp")].append(r)
    out = []
    for fp, grp in g.items():
        a = _agg(grp)
        if a and a["n"] >= min_n:
            a["fp"] = fp
            out.append(a)
    out.sort(key=lambda x: -x["n"])
    return out


def slot_stats(sig_type=None, tbe=None):
    """槽位边际战绩：每个槽位取值分组（小样本下比全指纹更稳）。"""
    rs = _sel(rows(), sig_type, tbe)
    out = {}
    for slot in ("amp", "pv", "npv", "bis6"):
        g = defaultdict(list)
        for r in rs:
            v = r.get(slot)
            g["-" if v is None else str(v)].append(r)
        out[slot] = {v: a for v, a in (
            (v, _agg(grp)) for v, grp in g.items()) if a}
    return out


# ── P10 升级预演：npv≤2 vs 3+ 的左尾/时间/regime 分层 ──────
def _left_tail(rets):
    """单组 ret 列表 → 胜率/均值/分位/左尾深度。"""
    if not rets:
        return None
    s = sorted(rets)
    n = len(s)

    def pct(p):
        return s[min(int(p * n), n - 1)]
    return {"n": n,
            "win": round(sum(1 for v in rets if v > 0) * 100.0 / n, 1),
            "avg": round(statistics.mean(rets) * 100.0, 2),
            "med": round(statistics.median(rets) * 100.0, 2),
            "p10": round(pct(0.10) * 100.0, 2),
            "p25": round(pct(0.25) * 100.0, 2),
            "worst": round(s[0] * 100.0, 2),
            "lt10": sum(1 for v in rets if v < -0.10),
            "lt20": sum(1 for v in rets if v < -0.20),
            "lt30": sum(1 for v in rets if v < -0.30)}


def eval_upgrade(sig_type="1卖", tbe=True):
    """P10 升级预演：npv≤2 vs 3+ 三维度分层（只产数据不改口径，n 不达标）。

    维度：①左尾深度（p10/p25/worst/<-10%·<-20%·<-30% 计数）
          ②时间分散（sig_dt 的 YYYY-MM 分布，查单期集中风险）
          ③regime 分层（reg_trend close>MA200：bull vs bear 子集战绩）
    """
    from collections import Counter
    rs = _sel(rows(), sig_type, tbe)
    g_le2 = [r for r in rs if r.get("npv") is not None and r["npv"] <= 2]
    g_3p = [r for r in rs if r.get("npv") is not None and r["npv"] >= 3]
    g_na = [r for r in rs if r.get("npv") is None]

    def _time(g):
        return dict(sorted(Counter((r.get("sig_dt") or "")[:7]
                                   for r in g).items()))

    idx = None
    try:
        idx = _idx_bars()
    except Exception:
        pass

    def _reg(g):
        out = {"bull": [], "bear": [], "na": []}
        if not idx:
            out["na"] = [r["ret"] for r in g]
            return out
        for r in g:
            try:
                reg, _ = regime_trend_at("CN", r.get("sig_dt") or "", idx)
            except Exception:
                reg = None
            (out["bull"] if reg is True else
             out["bear"] if reg is False else
             out["na"]).append(r["ret"])
        return out

    def _pack(g):
        rg = _reg(g)
        return {"tail": _left_tail([r["ret"] for r in g]),
                "time": _time(g),
                "regime": {k: _left_tail(v) for k, v in rg.items()}}
    return {"sig_type": sig_type, "tbe": tbe, "total": len(rs),
            "le2": _pack(g_le2), "3p": _pack(g_3p), "na_n": len(g_na)}


# ── CLI ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("build")
    s = sub.add_parser("stats")
    s.add_argument("sig_type", nargs="?", default=None)
    s.add_argument("--tbe", choices=["true", "false"], default=None)
    s.add_argument("--min-n", type=int, default=5)
    sl = sub.add_parser("slot")
    sl.add_argument("sig_type", nargs="?", default=None)
    sl.add_argument("--tbe", choices=["true", "false"], default=None)
    e = sub.add_parser("eval")
    e.add_argument("sig_type", nargs="?", default="1卖")
    e.add_argument("--tbe", choices=["true", "false"], default="true")
    args = ap.parse_args()
    tbe = {"true": True, "false": False}.get(args.tbe) \
        if hasattr(args, "tbe") else None
    if args.cmd == "build" or args.cmd is None:
        build()
    elif args.cmd == "stats":
        label = "%s%s" % (args.sig_type or "全型",
                          " 趋✓" if tbe is True else
                          " 非趋✓" if tbe is False else "")
        print("=== 指纹分组战绩 [%s] （fp=bis6|amp|pv|npv） ===" % label)
        for a in fp_stats(args.sig_type, tbe, args.min_n):
            print("  n=%-4d win=%5.1f%% avg=%+6.2f%%  %s"
                  % (a["n"], a["win"], a["avg"], a["fp"]))
    elif args.cmd == "slot":
        label = "%s%s" % (args.sig_type or "全型",
                          " 趋✓" if tbe is True else
                          " 非趋✓" if tbe is False else "")
        print("=== 槽位边际战绩 [%s] ===" % label)
        for slot, vals in slot_stats(args.sig_type, tbe).items():
            print(" ·%s" % slot)
            for v, a in sorted(vals.items(), key=lambda x: -x[1]["n"]):
                print("    %-8s n=%-4d win=%5.1f%% avg=%+6.2f%%"
                      % (v, a["n"], a["win"], a["avg"]))
    elif args.cmd == "eval":
        r = eval_upgrade(args.sig_type, tbe)
        print("=== P10 升级预演 [%s%s] total=%d（na=%d） ==="
              % (r["sig_type"], " 趋✓" if r["tbe"] else "", r["total"],
                 r["na_n"]))

        def _ptail(t, tag):
            if not t:
                print("  %s: 待样本" % tag)
                return
            print("  %s: n=%d win=%.1f%% avg=%+.2f%% med=%+.2f%% "
                  "p25=%+.2f%% p10=%+.2f%% worst=%+.2f%% "
                  "(<-10%%:%d <-20%%:%d <-30%%:%d)"
                  % (tag, t["n"], t["win"], t["avg"], t["med"],
                     t["p25"], t["p10"], t["worst"],
                     t["lt10"], t["lt20"], t["lt30"]))

        for grp in ("le2", "3p"):
            g = r[grp]
            tag = "≤2枢" if grp == "le2" else "3+枢"
            print("─ %s ─" % tag)
            _ptail(g["tail"], "全集")
            tm = g["time"]
            if tm:
                months = " ".join("%s×%d" % (k, v)
                                   for k, v in tm.items())
                print("    时间: %s" % months)
            for rk in ("bull", "bear", "na"):
                rt = g["regime"].get(rk)
                if rt:
                    _ptail(rt, "  reg·%s" % rk)
        print("注：n<30 不达标，仅预演不改口径。")


if __name__ == "__main__":
    main()
