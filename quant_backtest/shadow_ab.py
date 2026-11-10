# -*- coding: utf-8 -*-
"""止盈成交价的影子对照：回测假设的「按目标价成交」比实盘乐观多少。

老田 2026-11-10：「确认限价单实盘成交价与回测无系统性偏差」。

为什么不能等一周实盘：一周只有 5 个交易日、V2 一年多也就几十笔止盈，
样本量根本不足以说「无偏差」。但**偏差的来源**现在就能量出来——
回测里止盈成交价写的是 `max(目标价, 开盘价)`，隐含假设是
「盘中只要 high 碰到目标价，就一定能在目标价成交」。实盘会在这三处失手：

  1. 冲高回落：high 摸到目标，但收盘又跌回目标下方 → 尾盘才成交，价格≈收盘
  2. 一价到底：开盘就跳空过目标 → 只能按开盘价成交（这条回测已模拟，不是偏差）
  3. 排队/滑点：挂在目标价上，成交价被吃掉几个 bp

所以本脚本拿回测自己产出的逐笔明细（want / d_open / d_high / d_close），
在**同一批成交**上算四种口径的成交价，差值就是「假设 vs 可实现」的偏差区间。
这是可证伪的：如果 close 口径与 limit 口径的中位偏差接近 0，说明止盈
绝大多数是「当日冲上去就没回来」，限价单假设成立；如果偏差大，V2 的
+8.38%/+29.62% 就要打折。

两种模式：
  --diag   逐笔成交价诊断（历史三年，立刻出结论）
  --daily  写当日影子台账（六腿：v3 现役 / d_legacy 旧方案 /
           F1 v3+止损−12% 固化主腿候选 / W1 C6 组合观察腿 /
           A3 slot_score / D1 score+cap25，供连续累积）

用法：
  python shadow_ab.py --diag
  python shadow_ab.py --daily
产出：reports/bt10m_shadow.json / data/shadow/YYYYMMDD.json
"""
import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bt10m_sweep as SW  # noqa: E402
import bt10m_verify as V  # noqa: E402
import backtest_engine_v2 as eng  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports", "bt10m_shadow.json")
SHADOW_DIR = os.path.join(BASE, "data", "shadow")

WINDOWS = list(V.WINDOWS) + [("PRE", "20231010", "20240930", "第三窗口")]

# 事件缓存根（shadow_ab.BASE=quant_backtest 目录；引擎里的 cache_fp 以仓库根
# 为 BASE 再拼 quant_backtest，两边落地是同一目录，别再叠一层 quant_backtest）
SIG_DIR = os.path.join(BASE, "data", "sig_events")
RESCAN_LOG = os.path.join(BASE, "reports", "market_scan_rescan_%s.log")


def ensure_sig_cache(d0, d1):
    """日期滚动自愈：v3 事件缓存缺失则自动重扫 market_scan（11-10 判例）。

    d1 滚到新交易日后缓存名 (d0,d1) 变化，六腿全部报「缓存不存在」；
    当天靠手工重扫救回，裁定改为流水线内自愈。重扫 ~3 分钟、32 核，
    输出落 reports/market_scan_rescan_<d1>.log（长跑输出必须落文件）。
    返回 (ok, cache_fp)；**内部任何异常都吞掉返回 False**——调用方
    （pool_daily 第 7 步）是 soft，六腿各自 run_job 会再报出同样的缓存
    错误进台账，自愈本身绝不能打断 18:30 链。
    """
    cache_fp = os.path.join(SIG_DIR, "sig_events_market_v3_%s_%s.json" % (d0, d1))
    try:
        if os.path.exists(cache_fp):
            return True, cache_fp
        log_fp = RESCAN_LOG % d1
        print("[rescan] 缓存缺失：%s" % cache_fp, flush=True)
        print("[rescan] 自动重扫 market_scan --d0 %s --d1 %s（输出 %s）"
              % (d0, d1, log_fp), flush=True)
        t0 = time.time()
        with open(log_fp, "w", encoding="utf-8") as lf:
            rc = subprocess.call(
                [sys.executable, os.path.join(BASE, "market_scan.py"),
                 "--d0", d0, "--d1", d1],
                stdout=lf, stderr=subprocess.STDOUT, cwd=BASE)
        ok = os.path.exists(cache_fp)
        print("[rescan] rc=%s 用时 %.1fs 缓存%s"
              % (rc, time.time() - t0, "已生成" if ok else "仍缺失"), flush=True)
        return ok, cache_fp
    except Exception as e:  # 自愈失败不影响主链（soft 语义）
        print("[rescan] 自愈失败（不阻断，六腿将各自报缓存错误）：%r" % e,
              flush=True)
        return False, cache_fp


def _pct(xs, q):
    """分位（0~1），空列表返回 None。"""
    if not xs:
        return None
    s = sorted(xs)
    i = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[i]


def _stat(xs):
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean": round(statistics.mean(xs), 1),
        "median": round(statistics.median(xs), 1),
        "p10": round(_pct(xs, 0.10), 1),
        "p90": round(_pct(xs, 0.90), 1),
        "min": round(min(xs), 1),
        "max": round(max(xs), 1),
    }


def diag():
    """逐笔：limit(乐观) vs close / gap_only / slip 的成交价偏差。"""
    res = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "windows": {}}
    for tag, d0, d1, desc in WINDOWS:
        # 底只放 preset：BASE_PARAMS 显式带着 three_fail_exit=True / holding_max_days=0，
        # 会把 V2 的「关失效线 + 60 日」整体盖掉（bt10m_v3 冒烟时踩过，差 13pp）。
        p = {"preset": "v2"}
        p["d0"], p["d1"] = d0, d1
        p["cost"] = 0.0061           # 122bp：真实资金口径
        t0 = time.time()
        payload, err = eng.run_job(p, "shadow_%s" % tag, progress=lambda *a, **k: None)
        if err or not payload:
            print("%s 失败：%s" % (tag, err), flush=True)
            continue
        tps = [t for t in payload["trades"]
               if t.get("how") == "take_profit" and t.get("want")]
        row = {"desc": desc, "n_tp": len(tps), "secs": round(time.time() - t0, 1)}

        dev_close, dev_slip3, gap_ok, fell_back, no_gap_fell = [], 0, 0, 0, 0
        for t in tps:
            want = float(t["want"])
            op = t.get("d_open")
            cl = t.get("d_close")
            f_lim = max(want, op) if op else want
            if not f_lim:
                continue
            # 口径 A：收盘价成交（冲高回落则在尾盘了结）
            f_cl = min(want, cl) if cl else want
            dev_close.append((f_cl / f_lim - 1) * 10000)
            # 口径 B：开盘就 ≥ 目标才算成交（盘中触价不算）
            if op and op >= want:
                gap_ok += 1
            if cl is not None and cl < want:
                fell_back += 1
                if not (op and op >= want):
                    no_gap_fell += 1
        row["dev_close_bp"] = _stat(dev_close)
        row["gap_only_rate_pct"] = round(100.0 * gap_ok / len(tps), 1) if tps else None
        row["fell_back_rate_pct"] = round(100.0 * fell_back / len(tps), 1) if tps else None
        row["no_gap_and_fell_pct"] = (
            round(100.0 * no_gap_fell / len(tps), 1) if tps else None
        )
        # 口径 C：固定滑点（不需要逐笔明细，直接由目标价推）
        for bp in (3, 6, 10):
            row["slip%d_bp_avg" % bp] = round(-bp, 1)
        res["windows"][tag] = row
        print("%-4s 止盈 %3d 笔 | close 口径偏差 中位 %+6.1fbp 均值 %+6.1fbp "
              "p90 %+6.1fbp | 冲高回落 %5.1f%% | 开盘即达标 %5.1f%% | "
              "既无跳空又回落 %5.1f%%"
              % (tag, len(tps), row["dev_close_bp"].get("median", 0),
                 row["dev_close_bp"].get("mean", 0), row["dev_close_bp"].get("p90", 0),
                 row["fell_back_rate_pct"] or 0, row["gap_only_rate_pct"] or 0,
                 row["no_gap_and_fell_pct"] or 0), flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n产物：%s" % OUT)
    return res


def daily():
    """当日影子台账：同一批信号，六条腿各自的持仓与动作快照。

    六腿 = 现役 v3 / 旧 d_legacy / 固化主腿候选 F1（v3+止损−12%）/
    观察腿 W1（C6 组合）/ 候选池 A3（评分定仓）/ 候选池 D1（评分定仓
    +单票帽 25%）。候选腿只记账不交易，连续累积动作差；一周后按 note
    与实际次日开盘价核对成交。固化口径定义见 bt10m_frozen.py。
    """
    os.makedirs(SHADOW_DIR, exist_ok=True)
    today = time.strftime("%Y%m%d")
    d1 = today
    d0 = str(int(today[:4]) - 1) + today[4:]  # 滚动一年

    # 日期滚动自愈（11-10 判例）：缓存缺失自动重扫，失败不抛，六腿各自报错进台账
    ensure_sig_cache(d0, d1)

    # 2026-11-10 固化 DEFAULT_PRESET=v3 后主腿是 v3；d_legacy 继续留作对照；
    # 2026-11-10 口径固化 v1（bt10m_frozen.py）：新增 F1（v3+止损−12%，固化
    # 主腿候选）与 W1（C6 组合，观察腿）。六腿并行无成本记账，
    # F1 通过影子验证期（核对成交价）后转正为唯一主腿。
    import bt10m_frozen
    LEGS = [
        ("v3", {"preset": "v3"}),
        ("d_legacy", {"preset": "d_legacy"}),
        (bt10m_frozen.F1[0], dict(bt10m_frozen.F1[1])),
        (bt10m_frozen.W1[0], dict(bt10m_frozen.W1[1])),
        ("A3_slot_score", {"preset": "v3", "slot_mode": "score"}),
        ("D1_score_cap25", {"preset": "v3", "slot_mode": "score",
                            "slot_score_cap": 0.25}),
    ]

    out = {"date": today, "d0": d0, "d1": d1, "legs": {}}
    for leg, p in LEGS:
        p = dict(p)
        p["d0"], p["d1"] = d0, d1
        p["cost"] = 0.0061
        # 自检（「参数看着传了」家族防御）：显式键必须原样进入 apply_preset
        ap = eng.apply_preset(p)
        for k, v in p.items():
            if k != "d0" and k != "d1" and ap.get(k) != v:
                raise SystemExit("影子自检失败：%s 参数 %s 传 %r 实际 %r"
                                 % (leg, k, v, ap.get(k)))
        payload, err = eng.run_job(p, "shadow_daily_%s_%s" % (leg, today),
                                   progress=lambda *a, **k: None)
        if err or not payload:
            out["legs"][leg] = {"error": str(err)}
            continue
        # 最后一日的持仓 = 明天开盘要管理的仓（holdings 是顶层 dict，不是 days[day] 的子键）
        hold = payload.get("holdings") or {}
        last_day = sorted(hold)[-1] if hold else None
        pos = hold.get(last_day) or []
        out["legs"][leg] = {
            "last_day": last_day,
            "holdings": pos,
            "summary": {
                "ret_pct": round((payload["equity_curve"][-1]["equity"] - 1) * 100, 2),
                "n_trades": len([t for t in payload["trades"] if t["side"] == "SELL"]),
                "avg_inv": payload["summary"].get("avg_invested_pct"),
            },
            # assumptions 取 apply_preset 展开后的真实值（此前取参数 dict，
            # 预设腿全记成 null——11-10 修）。
            "assumptions": {
                "slot_mode": ap.get("slot_mode", "fixed"),
                "slot_score_cap": ap.get("slot_score_cap", 0.0),
                "tp_pct": ap.get("tp_pct", 0.0),
                "holding_max_days": ap.get("holding_max_days", 0),
                "stop_pct": ap.get("stop_pct", 0.0),
                "trail": (ap.get("trail_activate", 0.0),
                          ap.get("trail_drawdown", 0.0)),
            },
        }
    fp = os.path.join(SHADOW_DIR, "%s.json" % today)
    json.dump(out, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("影子台账：%s" % fp)
    for leg, v in out["legs"].items():
        if "error" in v:
            print("  %-9s ERROR %s" % (leg, v["error"]))
        else:
            print("  %-9s 末日 %s 持仓 %d 只 累计 %+.2f%%"
                  % (leg, v["last_day"], len(v["holdings"]),
                     v["summary"]["ret_pct"]))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--diag", action="store_true", help="逐笔成交价诊断（历史）")
    ap.add_argument("--daily", action="store_true", help="写当日影子台账")
    a = ap.parse_args()
    if a.daily:
        daily()
    else:
        diag()
