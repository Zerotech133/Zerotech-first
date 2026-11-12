# -*- coding: utf-8 -*-
"""发现问题（detect_drift）：找那些「表都正常、但合起来不对」的事。

和 check_products / audit_preopen 的分工
----------------------------------------
那两者查**单点**：某张表到没到、某天价对不对。它们回答「这一项有没有坏」。
本模块查**关系**：两次运行之间、两个池子之间、评分与名单之间的关系。
单点全绿而关系已经坏掉，是最常见的失效形态 —— 因为监控总是按单项建的。

五类检测，都要求「可观测 + 给数字 + 说影响」：

  NAME_STALE          名单与上一份逐只相同 —— 形式上每天跑，内容没变
  BOUNDARY_FUZZY      「取前 15」的截断边界分差过小 —— 名单对参数极敏感
  UNIVERSE_SHRINK     参与打分的股数骤降 —— 选票宇宙静默缩水
  POOL_SYSTEMATIC     同一池跨批次同向偏离 —— 疑似系统性弱，不是运气
  POOL_INCOMPARABLE   两池样本量悬殊却并排比超额 —— 不可比的数字被放在一起

每条发现都必须带 so_what（所以呢）和 next（该做什么）。
只有数字没有 so_what 的告警，接收方无法判断要不要管，久之全部忽略。

用法：
    python tools/preopen/detect_drift.py                 # 对最近一次选票做检测
    python tools/preopen/detect_drift.py --day 20261111
    python tools/preopen/detect_drift.py --json
退出码：0 无发现 / 1 有 medium 以上发现
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys

ROOT = r"C:\项目\老田的AI助手"
DELIV = r"C:\项目\deliverables"
COHORT = os.path.join(ROOT, "portal", "daily_report", "cohorts", "status_latest.json")

# 阈值集中在此，便于老田一处调。全部写死比读配置好：这些是判据不是参数，
# 改了要重新论证，不该被顺手改。
TH = {
    "stale_overlap_ratio": 1.0,     # 名单重合度达到 100% 即视为同源
    "boundary_gap_min": 0.015,      # 第 N 与第 N+2 名分差低于此值，边界算模糊
    "universe_shrink_pct": 0.30,    # 参与打分股数降幅超过 30% 算缩水
    "pool_weak_min_periods": 3,     # 同一池至少 3 期同向才算系统性
    "pool_ratio_alert": 5.0,        # 两池样本量差 5 倍以上，超额不可直接比
}


def _load_picks() -> list[tuple[str, dict]]:
    fs = sorted(glob.glob(os.path.join(DELIV, "preopen_pick15_v2_adj_check_*.json")))
    out = []
    for f in fs:
        day = os.path.basename(f)[-13:-5]
        try:
            out.append((day, json.load(io.open(f, encoding="utf-8"))))
        except Exception:
            continue
    return out


def _cohorts() -> dict:
    if not os.path.exists(COHORT):
        return {}
    try:
        return json.load(io.open(COHORT, encoding="utf-8"))
    except Exception:
        return {}


def _scores(full: list) -> list[float]:
    """按评分**降序**返回。升序会让 sc[-n] 指向倒数第 n 名而不是第 n 名，
    边界检测会静默取错位置还照样出数 —— 这里必须显式 reverse。"""
    return sorted(
        ((x.get("score") or 0) for x in full
         if isinstance(x, dict) and x.get("score") is not None),
        reverse=True)


def detect(day: str | None = None) -> list[dict]:
    picks = _load_picks()
    if not picks:
        return [{
            "id": "NO_DATA", "severity": "low",
            "title": "没有可检测的选票产物",
            "evidence": "deliverables 下无 preopen_pick15_v2_adj_check_*.json",
            "so_what": "没有历史产物就无法做跨次比对，只说明首次运行。",
            "next": "积累到 2 份以上产物后本项自动生效。",
        }]

    cur_day, cur = picks[-1] if not day else next(
        ((d, p) for d, p in picks if d == day), picks[-1])
    prev = None
    for d, p in reversed(picks):
        if d < cur_day:
            prev = (d, p)
            break

    out: list[dict] = []
    cur_names = [x.get("ts_code") for x in (cur.get("top15_adj") or [])]
    full = cur.get("full_rated") or []
    sc = _scores(full)

    # ── 1. 名单同源 ────────────────────────────────────────────────────
    if prev:
        pv_names = [x.get("ts_code") for x in (prev[1].get("top15_adj") or [])]
        if pv_names and cur_names:
            inter = len(set(pv_names) & set(cur_names))
            ratio = inter / min(len(pv_names), len(cur_names))
            if ratio >= TH["stale_overlap_ratio"]:
                out.append({
                    "id": "NAME_STALE", "severity": "high",
                    "title": "精选名单与上一份逐只相同",
                    "evidence": "本次 %d 只 / 上次(%s) %d 只，重合 %d 只（%.0f%%）"
                                % (len(cur_names), prev[0], len(pv_names), inter, ratio * 100),
                    "so_what": "名单不变只有两种可能：数据没前进，或选票实质停摆。"
                               "无论哪种，这份名单都不是「今天的新信息」，不能当新增信号用。",
                    "next": "先确认数据日是否前进；若已前进而名单不变，查选票是否真读了新数据。",
                })

    # ── 2. 截断边界模糊 ────────────────────────────────────────────────
    if len(sc) > 17:
        n_top = len(cur_names) or 15
        if len(sc) >= n_top + 2:
            # sc 已降序：第 n 名 = sc[n-1]。这里必须用正索引，
            # 用负索引（sc[-n]）取到的是倒数第 n 名，会算出看着合理的错数。
            s_n, s_n2 = sc[n_top - 1], sc[n_top + 1]
            gap = s_n - s_n2
            if gap < TH["boundary_gap_min"]:
                order = sorted([x for x in full if isinstance(x, dict)],
                               key=lambda x: -(x.get("score") or 0))
                edge = [(x.get("name"), round(x.get("score") or 0, 4))
                        for x in order[n_top - 1:n_top + 2]]
                out.append({
                    "id": "BOUNDARY_FUZZY", "severity": "medium",
                    "title": "「取前 %d」的截断边界几乎无区分" % n_top,
                    "evidence": "第%d名 %.4f vs 第%d名 %.4f，分差 %.4f（阈值 %.3f）；"
                                "边界附近：%s"
                                % (n_top, s_n, n_top + 2, s_n2, gap,
                                   TH["boundary_gap_min"],
                                   "、".join("%s %.4f" % e for e in edge)),
                    "so_what": "第 %d 名与第 %d 名评分差 %.4f，谁是第 %d 只基本由"
                               "参数噪声决定。此时「精选 %d 只」的 %d 是人为截断，"
                               "不是数据在 %d 处自然分开 —— 换 %d 或 %d 只结果同样「合理」。"
                               % (n_top, n_top + 2, gap, n_top, n_top, n_top, n_top,
                                  n_top - 1, n_top + 2),
                    "next": "把名单当「评分前段」而非「前 N 名」表述；"
                            "或按分差阈值动态定名单长度，而不是固定 %d。" % n_top,
                })

    # ── 3. 选票宇宙缩水 ────────────────────────────────────────────────
    if prev:
        pv_rated = prev[1].get("rated_n") or len(prev[1].get("full_rated") or [])
        cu_rated = cur.get("rated_n") or len(full)
        if pv_rated and cu_rated:
            drop = (pv_rated - cu_rated) / pv_rated
            if drop > TH["universe_shrink_pct"]:
                out.append({
                    "id": "UNIVERSE_SHRINK", "severity": "high",
                    "title": "参与打分的股票数骤降",
                    "evidence": "上次(%s) %d 只 → 本次 %d 只，减少 %.0f%%"
                                % (prev[0], pv_rated, cu_rated, drop * 100),
                    "so_what": "评分宇宙是一次选票的分母。分母缩水后，"
                               "「前 15 名」是在更小的池子里选的，绝对水平与上次不可比。"
                               "常见诱因是某个数据源缺失导致部分票被静默跳过 —— 不报错，只是变少。",
                    "next": "查共识池推送包是否变小、以及被剔除的票卡在哪一步。",
                })

    # ── 4/5. 台账：池间可比性 & 系统性偏离 ─────────────────────────────
    out.extend(_cohort_findings())
    return out


def _cohort_findings() -> list[dict]:
    d = _cohorts()
    cs = d.get("cohorts") or []
    if not cs:
        return []

    out: list[dict] = []
    by_pool: dict[str, list[dict]] = {}
    for c in cs:
        by_pool.setdefault(c.get("pool") or "?", []).append(c)

    # 4. 同池跨批次同向
    for pool, rows in by_pool.items():
        vals = [r.get("excess") for r in rows if r.get("excess") is not None]
        if len(vals) < TH["pool_weak_min_periods"]:
            continue
        neg = sum(1 for v in vals if v < 0)
        pos = len(vals) - neg
        maj = max(neg, pos)
        if maj < TH["pool_weak_min_periods"]:
            continue
        unanimous = (maj == len(vals))
        # 不全都同向时，要求同向比例 ≥75% 才提。3 期里 2 期同向（67%）只是噪声。
        if not unanimous and maj / len(vals) < 0.75:
            continue
        direction = "为负" if neg > pos else "为正"
        out.append({
            "id": "POOL_SYSTEMATIC",
            "severity": "medium" if unanimous else "low",
            "title": "池「%s」%d/%d 个批次超额%s%s"
                     % (pool, maj, len(vals), direction, "" if unanimous else "（倾向，未全同向）"),
            "evidence": "各期超额：" + "、".join("%+.2f%%" % (v * 100) for v in vals)
                        + "；样本量 " + "、".join(str(r.get("n")) for r in rows),
            "so_what": ("%d 期全部同向，是稳定方向而非运气。" % len(vals) if unanimous
                        else "%d/%d 期同向，方向有倾向但未全同向，只能算疑似。"
                             % (maj, len(vals)))
                       + "单期正负是噪声，多期一致才有信息；同向%s时值得按策略问题查，"
                         "而不是当波动放过。" % direction,
            "next": ("按 precheck 规范立假设单（先估信息上限，上限不足即弃），不要直接调参。"
                     if neg > pos else
                     "正向前先看样本量：小样本全正也可能只是幸存者偏差，需扩样确认。"),
        })

    # 5. 池间不可比
    pools = [(p, r) for p, r in by_pool.items()]
    if len(pools) >= 2:
        def _mean_excess(rows):
            v = [r.get("excess") for r in rows if r.get("excess") is not None]
            return (sum(v) / len(v)) if v else None
        def _mean_n(rows):
            v = [r.get("n") for r in rows if r.get("n")]
            return (sum(v) / len(v)) if v else None

        big, small = None, None
        for p, rows in pools:
            n = _mean_n(rows)
            if n is None:
                continue
            if big is None or n > _mean_n(big[1]):
                big = (p, rows)
        for p, rows in pools:
            n = _mean_n(rows)
            if n is None or (big and p == big[0]):
                continue
            if small is None or n < _mean_n(small[1]):
                small = (p, rows)

        if big and small and _mean_n(small[1]):
            ratio = _mean_n(big[1]) / _mean_n(small[1])
            if ratio >= TH["pool_ratio_alert"]:
                eb, es = _mean_excess(big[1]), _mean_excess(small[1])
                if eb is not None and es is not None:
                    out.append({
                        "id": "POOL_INCOMPARABLE", "severity": "medium",
                        "title": "两个池样本量相差 %.0f 倍，超额却被并排展示" % ratio,
                        "evidence": "池「%s」均 %.0f 只、均超额 %+.2f%%；"
                                    "池「%s」均 %.0f 只、均超额 %+.2f%%"
                                    % (big[0], _mean_n(big[1]), eb * 100,
                                       small[0], _mean_n(small[1]), es * 100),
                        "so_what": "%.0f 只的池与 %.0f 只的池比平均超额，比的是方差不是能力。"
                                   "小样本池的单只权重极大，一票涨 10%% 就能把均值拉正，"
                                   "读者会误读成「小池更准」。"
                                   % (_mean_n(big[1]), _mean_n(small[1])),
                        "next": "两池不要并排给均值，改给「各自相对同规模随机组合的超额」；"
                                "或直接标注「样本不足，不可比」并只在同池内做时间序列比较。",
                    })
    return out


SEV_RANK = {"high": 3, "medium": 2, "low": 1}


def render(found: list[dict]) -> str:
    if not found:
        return "未发现跨次/跨池异常。"
    lines = []
    for f in sorted(found, key=lambda x: -SEV_RANK.get(x["severity"], 0)):
        lines.append("[%s] %s" % (f["severity"].upper(), f["title"]))
        lines.append("    证据：%s" % f["evidence"])
        lines.append("    影响：%s" % f["so_what"])
        lines.append("    建议：%s" % f["next"])
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="跨次/跨池异常检测")
    ap.add_argument("--day", default=None, help="检测哪天（默认最近一份选票产物）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    found = detect(a.day)
    if a.json:
        print(json.dumps(found, ensure_ascii=False, indent=1))
    else:
        print(render(found))
    return 1 if any(f["severity"] in ("high", "medium") for f in found) else 0


if __name__ == "__main__":
    sys.exit(main())
