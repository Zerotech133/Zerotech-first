# -*- coding: utf-8 -*-
"""xq_anomaly_calibration.py — 雪球异动信号阈值敏感性复检（3×3 网格）

背景
----
xq_daily_report.py 的异动信号 = BEARISH_MIN(3) + BEARISH_SHARE(0.6) + 6 日基线
（anomaly_signals 函数）。参考判例：盯盘 RiskGuard 曾因阈值过严误杀严重
（critical 拦截样本后验 +0.65% vs 放行 -0.25%），阈值校准是本数据线既定课题。

方法
----
1. import 复用 xq_daily_report 的 load_rows / summarize / anomaly_signals /
   split_tickers（同目录 import，不复制逻辑、不改原文件）。
   阈值经运行时 monkeypatch（xqdr.BEARISH_MIN / xqdr.BEARISH_SHARE）遍历网格，
   原文件字节不动。
2. 样本日自动选取：classified(parse_ok=1) 按 created_at 有数据的日期中，
   取「bearish+tickers 活跃日 top」+ 种子日（2020-05-13，seed 播种验证日）±3 天
   窗口 + 最近活跃日，合并去重，上限 20。
3. 3×3 网格：MIN∈{2,3,5} × SHARE∈{0.5,0.6,0.7}；每格输出触发标的数、
   触发日平均样本量、触发分布（零触发/单标的/多标的日数）。
4. 建议段：现行 (3, 0.6) 的灵敏度评价（过严/合理/过松）+ 维持或调整建议 + 依据。

纪律
----
只读库（mode=ro）；stdout 人读报告形态；空窗口日不崩（显式标注跳过）。
⚠ 当前库为雪球存量爬取数据（2017-2023 稀疏打标），实时采集（2026-10-18 起）
积累后建议复检——结论仅对当前数据池成立。

用法
----
    python tools/research/xq_anomaly_calibration.py            # 全量敏感性复检
    python tools/research/xq_anomaly_calibration.py --cap 12   # 限制样本日数
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import xq_daily_report as xqdr  # noqa: E402  （复用生产逻辑，见模块 docstring）
import logging

SEED_DAY = "2020-05-13"  # seed 播种验证日（2026-10-18 判例）
SEED_WINDOW = 3  # 种子日 ±3 天
GRID_MIN = (2, 3, 5)  # BEARISH_MIN 网格
GRID_SHARE = (0.5, 0.6, 0.7)  # BEARISH_SHARE 网格
CURRENT = (3, 0.6)  # 现行阈值


# ---------------------------------------------------------------- 数据池探查


def list_active_days(con):
    """classified(parse_ok=1) 按 created_at 日聚合。

    返回 {day: {"posts": 已打标帖数, "bear_t": bearish且带tickers帖数}}。
    """
    q = (
        "SELECT substr(s.created_at,1,10) d, COUNT(*),"
        "       SUM(CASE WHEN c.sentiment='bearish' AND c.tickers IS NOT NULL"
        "                 AND c.tickers != '[]' THEN 1 ELSE 0 END)"
        "  FROM xq_status s JOIN xq_status_classified c ON s.status_id = c.status_id"
        " WHERE c.parse_ok=1 AND s.created_at IS NOT NULL"
        " GROUP BY d ORDER BY d"
    )
    return {d: {"posts": p, "bear_t": b} for d, p, b in con.execute(q)}


def pick_sample_days(active, cap=20):
    """样本日自动选取（纯函数，可单测）。

    三路来源合并去重，超上限按「bear_t 降序、日期升序」截断：
      A. bearish+tickers 活跃日（bear_t >= 1）——有信号潜力的日子
      B. 种子日 ±SEED_WINDOW 天内的活跃日——seed 播种判例窗口
      C. 最近 3 个活跃日——数据池尾部形态
    """
    cand = set()
    a_days = sorted(
        (d for d, v in active.items() if v["bear_t"] >= 1),
        key=lambda d: (-active[d]["bear_t"], d),
    )
    cand.update(a_days[:12])  # A：top 活跃日（bear_t 降序）
    for i in range(-SEED_WINDOW, SEED_WINDOW + 1):  # B：种子日窗口
        cand.add(_shift_day(SEED_DAY, i))
    cand.update(sorted(active)[-3:])  # C：最近活跃日
    cand = [d for d in cand if d in active]
    cand.sort(key=lambda d: (-active[d]["bear_t"], d))
    return sorted(cand[:cap])


def _shift_day(day, n):
    from datetime import datetime, timedelta

    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 网格执行


def run_grid(con, days):
    """逐日跑生产口径汇总，再按网格阈值逐格跑 anomaly_signals。

    返回 (day_data, results, detail)：
      day_data: {day: (posts, summ)}          空窗口日 summ 为 None（fail-visible 标注）
      results:  {(m,s): {day: [ (code,n,bear,base) ]}}
      detail:   {(m,s): [ (day,code,n,bear,base) ]}
    """
    # 词频打桩：summarize 的 title_words 依赖 jieba（本环境未装），且词频段
    # 与 ticker_senti/异动口径无关——置空不影响敏感性结果。
    xqdr.title_words = lambda title: set()

    day_data, skipped = {}, []
    for day in days:
        try:
            tac, _ing = xqdr.load_rows(con, day)
            day_data[day] = (len(tac), xqdr.summarize(tac) if tac else None)
            if not tac:
                skipped.append(day)
        except Exception as e:  # fail-visible，不中断
            print("    [跳过] %s 汇总失败：%s" % (day, e))
            day_data[day] = (0, None)
            skipped.append(day)

    results, detail = {}, defaultdict(list)
    for m in GRID_MIN:
        for s in GRID_SHARE:
            xqdr.BEARISH_MIN, xqdr.BEARISH_SHARE = m, s  # 运行时阈值注入
            per_day = {}
            for day, (_n, summ) in day_data.items():
                sigs = xqdr.anomaly_signals(summ, con, day) if summ else []
                per_day[day] = sigs
                for code, n, bear, base in sigs:
                    detail[(m, s)].append((day, code, n, bear, base))
            results[(m, s)] = per_day
    return day_data, results, detail, skipped


def grid_stats(results, day_data):
    """(m,s) -> 统计 dict（纯函数，可单测）。"""
    out = {}
    for key, per_day in results.items():
        total = sum(len(v) for v in per_day.values())
        hit_days = [d for d, v in per_day.items() if v]
        multi_days = [d for d in hit_days if len(per_day[d]) >= 2]
        ns = [day_data[d][0] for d in hit_days if d in day_data]
        out[key] = {
            "total": total,
            "days_hit": len(hit_days),
            "days_zero": len(per_day) - len(hit_days),
            "days_multi": len(multi_days),
            "avg_n_at_hit": (sum(ns) / len(ns)) if ns else None,
        }
    return out


# ---------------------------------------------------------------- 评价与建议


def make_recommendation(stats):
    """基于网格统计生成（评价, 建议文本）——规则见脚本 docstring 与输出依据行。"""
    cur, lo, hi = stats[CURRENT], stats[(2, 0.5)], stats[(5, 0.7)]
    t, tl = cur["total"], lo["total"]
    if t == 0 and tl == 0:
        verdict = "过严（通道空转）"
        advice = (
            "维持代码暂不动：当前数据池为 2017-2023 存量稀疏打标"
            "（单日已打标峰值约 25 帖，同标的单日 bearish>=2 都罕见），"
            "最低格 (2, 0.5) 亦零触发，说明瓶颈在数据密度而非阈值刻度。"
            "实时采集（2026-10-18 起）积累 2-4 周后必须复检；"
            "届时若仍零触发再降阈值。"
        )
    elif t == 0:
        verdict = "过严"
        advice = (
            "建议下调至 (2, 0.5) 或 (2, 0.6)：现行格在样本日零触发，"
            "而最低格有 %d 次触发——(3, 0.6) 在当前数据密度下高于触发带。"
            "同时参照 RiskGuard 误杀判例，宁可略松勿过严。" % tl
        )
    elif tl == 0 and cur["days_hit"] > 0:
        verdict = "合理"
        advice = (
            "建议维持 (3, 0.6)：现行格 %d 次触发而最低格零触发，"
            "阈值带具区分度——现行刻度恰在「有信号/无信号」分界上，"
            "无过严（漏报）或过松（噪音）证据。" % t
        )
    elif tl >= t:
        verdict = "过松/无区分度"
        advice = (
            "最低格 (2, 0.5) 触发数（%d）不低于现行（%d），"
            "网格未覆盖到阈值带——需向更严方向扩格（如 MIN>=5、SHARE>=0.8）复检。"
            % (tl, t)
        )
    else:
        verdict = "偏严"
        advice = (
            "现行 %d 次触发，最低格 %d 次：现行已滤掉大部分低密度触发。"
            "若异动信号定位为「少量高置信预警」可维持；"
            "若希望提高召回，建议 (2, 0.6) 折中并人工复核触发样本。" % (t, tl)
        )
    basis = (
        "依据：现行(3,0.6) 触发 %d 次/%d 日非零/%d 日多标的；"
        "(2,0.5) 触发 %d 次；(2,0.6) 触发 %d 次；(5,0.7) 触发 %d 次。"
        % (
            t,
            cur["days_hit"],
            cur["days_multi"],
            tl,
            stats[(2, 0.6)]["total"],
            hi["total"],
        )
    )
    return verdict, advice + "\n    " + basis


# ---------------------------------------------------------------- 输出


def main():
    ap = argparse.ArgumentParser(description="雪球异动信号阈值敏感性复检（3×3 网格）")
    ap.add_argument("--cap", type=int, default=20, help="样本日上限（默认 20）")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/research/xq_anomaly_calibration.py", exc)

    print("=" * 72)
    print("雪球异动信号阈值敏感性复检（3×3 网格）· 只读 mode=ro")
    print("复用 xq_daily_report.load_rows/summarize/anomaly_signals，运行时注入阈值")
    print("=" * 72)

    if not os.path.exists(xqdr.DB_RO.split("?")[0].replace("file:", "")):
        print("[空态] 雪球库不存在：%s（fail-visible）" % xqdr.DB_RO)
        return 1
    con = sqlite3.connect(xqdr.DB_RO, uri=True)
    try:
        # [1] 数据池概况
        active = list_active_days(con)
        total_cls = sum(v["posts"] for v in active.values())
        total_bear = sum(v["bear_t"] for v in active.values())
        rng = (min(active), max(active)) if active else ("-", "-")
        print("\n[1] 数据池概况（classified parse_ok=1，created_at 口径）")
        print(
            "    活跃日 %d 天 ｜ 范围 %s ~ %s ｜ 已打标帖 %d ｜ 其中 bearish+tickers %d"
            % (len(active), rng[0], rng[1], total_cls, total_bear)
        )
        days = pick_sample_days(active, cap=args.cap)
        if not days:
            print(
                "    [空态] 无可用样本日（classified 无数据），管道终止（fail-visible）。"
            )
            return 1
        print(
            "    样本日 %d 个（活跃日 top + 种子日 %s ±%d 天 + 最近活跃日）："
            % (len(days), SEED_DAY, SEED_WINDOW)
        )
        seed_win = {
            _shift_day(SEED_DAY, i) for i in range(-SEED_WINDOW, SEED_WINDOW + 1)
        }
        print("    %-12s %6s %8s" % ("日期", "打标帖", "bear+tk"))
        for d in days:
            mark = "  ← 种子日窗口" if d in seed_win else ""
            print(
                "    %-12s %6d %8d%s"
                % (d, active[d]["posts"], active[d]["bear_t"], mark)
            )

        # [2] 3×3 敏感性网格
        day_data, results, detail, skipped = run_grid(con, days)
        if skipped:
            print(
                "\n    [注明] 空窗口/汇总失败日（按零触发计）：%s" % "、".join(skipped)
            )
        stats = grid_stats(results, day_data)
        print(
            "\n[2] 3×3 敏感性网格（行=MIN 列=SHARE；格: 触发标的·日次 | 非零日/多标的日/零日 | 触发日均样本量）"
        )
        header = "    %-4s" % "MIN\\SHARE" + "".join(
            "%-22s" % ("%.1f" % s) for s in GRID_SHARE
        )
        print(header)
        for m in GRID_MIN:
            row = "    %-8s" % m
            for s in GRID_SHARE:
                st = stats[(m, s)]
                avg = "-" if st["avg_n_at_hit"] is None else "%.1f" % st["avg_n_at_hit"]
                cur_mark = " ←现行" if (m, s) == CURRENT else ""
                row += "%-22s" % (
                    "%d | %d/%d/%d | %s%s"
                    % (
                        st["total"],
                        st["days_hit"],
                        st["days_multi"],
                        st["days_zero"],
                        avg,
                        cur_mark,
                    )
                )
            print(row)

        # [3] 触发明细（每格前 8 条）
        print(
            "\n[3] 触发明细（各格最多 8 条：日 | 代码 | 当日样本 | bearish | 前6日基线）"
        )
        any_detail = False
        for m in GRID_MIN:
            for s in GRID_SHARE:
                rows = detail[(m, s)]
                if not rows:
                    continue
                any_detail = True
                print("    --- (%d, %.1f) 共 %d 条 ---" % (m, s, len(rows)))
                for day, code, n, bear, base in rows[:8]:
                    print(
                        "        %s  %s  n=%d  bear=%d  base=%d"
                        % (day, code, n, bear, base)
                    )
                if len(rows) > 8:
                    print("        ... 其余 %d 条略" % (len(rows) - 8))
        if not any_detail:
            print("    （全部 9 格均零触发——数据密度瓶颈，见建议段）")

        # [4] 灵敏度评价与建议
        verdict, advice = make_recommendation(stats)
        print("\n[4] 现行阈值 (3, 0.6) 灵敏度评价：%s" % verdict)
        print("    " + advice)
        print("\n⚠ 结论仅对当前存量数据池成立；实时采集积累 2-4 周后复检。")
        print("完成。")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
