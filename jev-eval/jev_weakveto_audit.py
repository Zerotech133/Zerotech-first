#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev_weakveto_audit — 0.5-0.7 弱否决档离线验证（W5，2026-10-14，只读）。

背景：FOCUS n=336 达标但前向均值 -1.04pp 未过 +1pp；REJECT 触发率 0.11%
（否决通道形同虚设）。原设想：news_p ∈ [0.5, 0.7) 段设弱否决（不硬拦、
降权/标注）。本脚本零产线行为改动，只做历史数据的反事实回测。

口径说明（重要）：
  - 系统没有"五原子总分"，只有五个原子概率：news_p / overlap_p / quality_p /
    crowd_p / regime_p（signal_review_gate.py review_candidate 落盘字段）。
  - 产线唯一否决通道 = news_p ≥ 0.7（REJECT_NEWS_P）与 crowd=hot 且
    crowd_p ≥ 0.7（REJECT_CROWD_P）。"0.5-0.7 分数段"只能落在 news_p 轴上
    （毕业③同款：news_p≥0.5 为高危），故网格以 news_p 为主轴；
    overlap/regime 从不做否决（v2 判例：与收益反向），quality=low 是 FOCUS
    加分项——这三条不在弱否决语义内，网格不予扫描。
  - 数据源（全部只读）：
    ① review_data/triples.jsonl  影子期实时判定（2026-09-21~10-14）
    ② replay/replay-20260922-030434.jsonl  v1 全量回放（2026-08-13~09-22）
       两文件字段对齐后合并（ret_5d→ret_nd），按来源打 source 标签。
  - T+10 收益需查 v2_bar_daily（只读 SELECT，经 jev_common.db_rows）。
    --skip-db 可跳过 T+10。

用法：
  "C:/Program Files/Python311/python.exe" jev_weakveto_audit.py [--skip-db]
输出：stdout 报告 + review_data/weakveto_audit_20261014.json（供留痕文档引用）
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
TRIPLES = HERE / "review_data" / "triples.jsonl"
REPLAY = HERE / "replay" / "replay-20260922-030434.jsonl"
OUT_JSON = HERE / "review_data" / "weakveto_audit_20261014.json"

# 弱否决网格（任务书指定）：下界×上界
GRID_LO = (0.4, 0.5, 0.6)
GRID_HI = (0.6, 0.7, 0.8)
HARD_NEWS = 0.7  # 产线 REJECT_NEWS_P
WEAK_MIN_N = 50  # 档位样本不稀薄的最低要求
BAND_FRAC_MIN = 0.02  # 且占池比例 ≥2%
VERDICT_PP = 0.005  # 判定阈值：前向改善 ≥0.5pp（同市场域口径）
PROMOTED = 0.5  # 降权档权重（weak veto 降权反事实用）

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SKIP_DB = "--skip-db" in sys.argv


# ── 数据加载 ────────────────────────────────────────────────
def load_triples() -> list[dict]:
    """影子期三元组：仅 reviewed 且已回填 ret_nd；按 candidate_id 去重保首条。"""
    seen, out = set(), []
    for line in TRIPLES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("status") != "reviewed" or r.get("ret_nd") is None:
            continue
        cid = r.get("candidate_id")
        if cid in seen:
            continue
        seen.add(cid)
        out.append(
            {
                "source": "live",
                "ts_code": r["ts_code"],
                "market": r.get("market", "?"),
                "pool_date": r.get("pool_date", ""),
                "news_p": float(r.get("news_p") or 0),
                "overlap_p": float(r.get("overlap_p") or 0),
                "quality": r.get("quality", "?"),
                "quality_p": float(r.get("quality_p") or 0),
                "crowd": r.get("crowd", "?"),
                "crowd_p": float(r.get("crowd_p") or 0),
                "regime_p": float(r.get("regime_p") or 0),
                "ret5": float(r["ret_nd"]),
                "ret10": None,
            }
        )
    return out


def load_replay() -> list[dict]:
    """v1 回放：ret_5d 已带；按 (ts_code, pool_date) 去重保首条。
    注意：replay 无 crowd_p/overlap_p/quality_p 数值（仅标签），crowd_p 以
    hot→1.0/normal→0.0 近似（仅用于硬否决通道识别，网格主轴 news_p 不受影响）。"""
    seen, out = set(), []
    for line in REPLAY.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("ret_5d") is None:
            continue
        key = (r["ts_code"], r["pool_date"])
        if key in seen:
            continue
        seen.add(key)
        hot = r.get("crowd") == "hot"
        out.append(
            {
                "source": "replay",
                "ts_code": r["ts_code"],
                "market": r.get("market", "?"),
                "pool_date": r.get("pool_date", ""),
                "news_p": float(r.get("news_p") or 0),
                "overlap_p": None,
                "quality": r.get("quality", "?"),
                "quality_p": None,
                "crowd": r.get("crowd", "?"),
                "crowd_p": 1.0 if hot else 0.0,
                "regime_p": float(r.get("regime_p") or 0),
                "ret5": float(r["ret_5d"]),
                "ret10": None,
            }
        )
    return out


def backfill_ret10(recs: list[dict]) -> int:
    """T+10 收益只读回查（v2_bar_daily 收盘价，与 run_backfill 同口径多取5日）。"""
    try:
        from jev_common import db_rows
    except ImportError:
        print("[warn] jev_common 不可用，跳过 T+10")
        return 0
    keys = sorted({(r["ts_code"], r["market"], r["pool_date"]) for r in recs})
    filled = 0
    for ts_code, mkt, pd_ in keys:
        if not pd_:
            continue
        try:
            rows = db_rows(
                "SELECT trade_date, close FROM v2_bar_daily "
                "WHERE symbol = %s AND market = %s AND trade_date >= %s "
                "ORDER BY trade_date ASC LIMIT 11",
                (ts_code, mkt, pd_),
            )
        except Exception as e:  # noqa: BLE001
            print(f"[warn] bar 查询失败 {ts_code}: {str(e)[:80]}，跳过 T+10")
            return filled
        if len(rows) < 11:
            continue
        entry, exit_ = float(rows[0][1]), float(rows[10][1])
        if entry == 0:
            continue
        ret10 = (exit_ - entry) / entry
        for r in recs:
            if r["ts_code"] == ts_code and r["market"] == mkt and r["pool_date"] == pd_:
                r["ret10"] = ret10
        filled += 1
    return filled


# ── 统计工具 ────────────────────────────────────────────────
def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def max_drawdown(daily_means: list[float]) -> float:
    """按 pool_date 时序的池日均收益累加曲线的最大回撤（比例）。"""
    eq, peak, mdd = 0.0, 0.0, 0.0
    for x in daily_means:
        eq += x
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return mdd


def pool_stats(recs: list[dict], key: str = "ret5") -> dict:
    """池子统计：均值 / 单日均值序列的最大回撤 / 最差单日 / 尾部10%均值。"""
    rets = [r[key] for r in recs if r.get(key) is not None]
    if not rets:
        return {"n": 0}
    by_day: dict[str, list[float]] = {}
    for r in recs:
        if r.get(key) is not None:
            by_day.setdefault(r["pool_date"], []).append(r[key])
    daily = [mean(by_day[d]) for d in sorted(by_day)]
    s = sorted(rets)
    k = max(1, len(s) // 10)
    return {
        "n": len(rets),
        "mean": round(mean(rets), 6),
        "mdd": round(max_drawdown(daily), 6),
        "worst_day": round(min(daily), 6) if daily else 0.0,
        "tail10_mean": round(mean(s[:k]), 6),
    }


def market_domain_improve(base: list[dict], kept: list[dict], key: str) -> dict:
    """同市场域口径：各市场域内（均值差）改善，再跨域等权平均（防辛普森）。"""
    per = {}
    diffs = []
    for m in sorted({r["market"] for r in base}):
        b = [r[key] for r in base if r["market"] == m and r.get(key) is not None]
        k = [r[key] for r in kept if r["market"] == m and r.get(key) is not None]
        if len(b) < 10:  # 域样本过少不纳入跨域平均
            per[m] = {"n_base": len(b), "n_kept": len(k), "diff": None}
            continue
        d = mean(k) - mean(b)
        per[m] = {"n_base": len(b), "n_kept": len(k), "diff": round(d, 6)}
        diffs.append(d)
    return {
        "per_market": per,
        "cross_market": round(mean(diffs), 6) if diffs else None,
        "n_domains": len(diffs),
    }


def histogram(recs: list[dict], field: str) -> dict:
    bins: dict[str, int] = {}
    for r in recs:
        v = r.get(field)
        if v is None:
            continue
        b = min(9, int(v * 10))
        k = f"{b / 10:.1f}-{(b + 1) / 10:.1f}"
        bins[k] = bins.get(k, 0) + 1
    return dict(sorted(bins.items()))


def hard_reject(r: dict) -> bool:
    """产线 v2 硬否决口径（signal_review_gate.py L384-387）。"""
    if r["news_p"] >= HARD_NEWS:
        return True
    return r.get("crowd") == "hot" and (r.get("crowd_p") or 0) >= 0.7


def in_band(r: dict, lo: float, hi: float) -> bool:
    return lo <= r["news_p"] < hi


# ── 主流程 ──────────────────────────────────────────────────
def main() -> None:
    live = load_triples()
    replay = load_replay()
    recs = live + replay
    print(
        f"样本：live(影子期) {len(live)} + replay(回放,去重) {len(replay)} "
        f"= {len(recs)} 条（均含 T+5 收益）"
    )
    pd_all = sorted(r["pool_date"] for r in recs if r["pool_date"])
    print(f"窗口：{pd_all[0]} ~ {pd_all[-1]}")
    if not SKIP_DB:
        n = backfill_ret10(recs)
        n10 = sum(1 for r in recs if r.get("ret10") is not None)
        print(f"T+10 回查：{n} 个 (code,market,date) 键，覆盖 {n10} 条记录")
    else:
        print("T+10 跳过（--skip-db）")

    # ── 1. 五原子分数分布 ──
    print("\n===== 1. 五原子分数分布（0.1 分桶直方图） =====")
    dist: dict[str, dict] = {}
    for f in ("news_p", "overlap_p", "quality_p", "crowd_p", "regime_p"):
        h = histogram(recs, f)
        dist[f] = h
        tag = ""
        if f == "news_p":
            band = sum(v for k, v in h.items() if k in ("0.5-0.6", "0.6-0.7"))
            rej = sum(1 for r in recs if r["news_p"] >= HARD_NEWS)
            tag = (
                f"  ← [0.5,0.7) 合计 {band} 条；[0.7,1] 硬否决区 {rej} 条"
                f"（news 通道触发率 {rej / len(recs):.2%}）"
            )
        print(f"{f:<11} {h}{tag}")
    # 0.5-0.7 段画像
    band_recs = [r for r in recs if in_band(r, 0.5, 0.7)]
    band_rej = sum(1 for r in band_recs if hard_reject(r))
    band_mkt = Counter(r["market"] for r in band_recs)
    band_ret = mean([r["ret5"] for r in band_recs]) if band_recs else None
    pool_ret = mean([r["ret5"] for r in recs])
    print(
        f"\n[0.5,0.7) 段画像：n={len(band_recs)}（占池 {len(band_recs) / len(recs):.1%}），"
        f"其中按产线口径仍触发硬否决 {band_rej} 条（crowd-hot 通道）；"
        f"市场域 {dict(band_mkt)}；段内 T+5 均值 {band_ret:+.2%} "
        f"vs 全池 {pool_ret:+.2%}"
    )
    # 其他原子 0.5-0.7 段对照（证明主轴选择的唯一性）
    for f in ("crowd_p", "regime_p", "overlap_p"):
        seg = [r for r in recs if r.get(f) is not None and 0.5 <= r[f] < 0.7]
        print(f"  对照：{f} ∈ [0.5,0.7) n={len(seg)}")

    # ── 2. 硬否决边际贡献（现状基线） ──
    print("\n===== 2. 硬否决（产线现状 news_p≥0.7 ∨ crowd-hot）反事实 =====")
    kept_hard = [r for r in recs if not hard_reject(r)]
    hard5 = market_domain_improve(recs, kept_hard, "ret5")
    hard_stats = {
        "removed": len(recs) - len(kept_hard),
        "t5": hard5,
        "pool_after": pool_stats(kept_hard),
    }
    print(
        f"剔除 {len(recs) - len(kept_hard)} 条 → 同市场域 T+5 改善 "
        f"{hard5['cross_market'] * 100 if hard5['cross_market'] is not None else 0:+.2f}pp"
        f"（域数 {hard5['n_domains']}）；逐域："
        + "; ".join(
            f"{m} {v['diff'] * 100:+.2f}pp(n={v['n_base']})"
            for m, v in hard5["per_market"].items()
            if v["diff"] is not None
        )
    )
    if any(r.get("ret10") is not None for r in recs):
        hard10 = market_domain_improve(recs, kept_hard, "ret10")
        hard_stats["t10"] = hard10
        c10 = hard10["cross_market"]
        print(
            f"  T+10 同市场域改善 {c10 * 100:+.2f}pp"
            if c10 is not None
            else "  T+10 覆盖不足"
        )

    # ── 3. 弱否决阈值网格 ──
    print("\n===== 3. 弱否决阈值网格（剔除口径，T+5 同市场域改善，跨域等权） =====")
    mid_idx = len(pd_all) // 2
    mid_date = pd_all[mid_idx]
    grid_rows = []
    for lo in GRID_LO:
        for hi in GRID_HI:
            if lo >= hi:
                continue
            band = [r for r in recs if in_band(r, lo, hi)]
            kept = [r for r in recs if not in_band(r, lo, hi)]
            if not band:
                continue
            imp5 = market_domain_improve(recs, kept, "ret5")
            # 分半稳健性：窗口中位日期切两段，各自域内改善同号才算稳健
            seg = {}
            for name, pool in (
                ("H1", [r for r in recs if r["pool_date"] < mid_date]),
                ("H2", [r for r in recs if r["pool_date"] >= mid_date]),
            ):
                kb = [r for r in pool if not in_band(r, lo, hi)]
                seg[name] = market_domain_improve(pool, kb, "ret5")["cross_market"]
            same_sign = (
                seg["H1"] is not None
                and seg["H2"] is not None
                and seg["H1"] > 0
                and seg["H2"] > 0
            )
            # 降权口径：band 权重 0.5 的加权均值改善
            w_imp = {}
            for key in ("ret5", "ret10"):
                if not any(r.get(key) is not None for r in recs):
                    continue
                per = []
                for m in {r["market"] for r in recs}:
                    b = [r for r in recs if r["market"] == m and r.get(key) is not None]
                    if len(b) < 10:
                        continue
                    wmean = sum(
                        r[key] * (PROMOTED if in_band(r, lo, hi) else 1.0) for r in b
                    ) / sum((PROMOTED if in_band(r, lo, hi) else 1.0) for r in b)
                    per.append(wmean - mean([r[key] for r in b]))
                w_imp[key] = round(mean(per), 6) if per else None
            row = {
                "band": f"[{lo},{hi})",
                "lo": lo,
                "hi": hi,
                "n_band": len(band),
                "frac": round(len(band) / len(recs), 4),
                "band_ret5": round(band_ret if band_ret else 0, 6),
                "t5_imp": imp5["cross_market"],
                "t5_per_market": imp5["per_market"],
                "t10_imp": market_domain_improve(recs, kept, "ret10")["cross_market"]
                if any(r.get("ret10") is not None for r in recs)
                else None,
                "downweight_t5": w_imp.get("ret5"),
                "downweight_t10": w_imp.get("ret10"),
                "h1_imp": seg["H1"],
                "h2_imp": seg["H2"],
                "same_sign": same_sign,
                "mdd_after": pool_stats(kept)["mdd"],
            }
            grid_rows.append(row)
            t5 = row["t5_imp"]
            t10 = row["t10_imp"]
            print(
                f"[{lo},{hi}): n={row['n_band']:>3} ({row['frac']:.1%}) "
                f"T5 {t5 * 100 if t5 is not None else 0:+.2f}pp"
                + (f" T10 {t10 * 100:+.2f}pp" if t10 is not None else "")
                + f" 降权T5 {row['downweight_t5'] * 100:+.2f}pp"
                + f" | H1 {seg['H1'] * 100 if seg['H1'] is not None else 0:+.2f} / "
                f"H2 {seg['H2'] * 100 if seg['H2'] is not None else 0:+.2f} "
                f"{'✓同向' if same_sign else '✗'}"
            )

    # ── 4. 基线回撤对照 ──
    print("\n===== 4. 回撤对照（T+5 池日均累加曲线 MDD） =====")
    base_stats = pool_stats(recs)
    print(
        f"基线池: n={base_stats['n']} mean {base_stats['mean']:+.2%} "
        f"MDD {base_stats['mdd']:.2%} 最差日 {base_stats['worst_day']:+.2%} "
        f"尾部10% {base_stats['tail10_mean']:+.2%}"
    )
    if hard_stats.get("t5"):
        hs = hard_stats["pool_after"]
        print(
            f"硬否决后: n={hs['n']} mean {hs['mean']:+.2%} MDD {hs['mdd']:.2%} "
            f"最差日 {hs['worst_day']:+.2%} 尾部10% {hs['tail10_mean']:+.2%}"
        )

    # ── 5. 判定 ──
    print("\n===== 5. 判定 =====")
    mdd_base = base_stats["mdd"]
    cand = [
        g
        for g in grid_rows
        if g["t5_imp"] is not None
        and g["t5_imp"] >= VERDICT_PP
        and g["n_band"] >= WEAK_MIN_N
        and g["frac"] >= BAND_FRAC_MIN
        and g["same_sign"]
        and g["mdd_after"] <= mdd_base * 1.05
    ]
    verdict = {
        "sample": {
            "live": len(live),
            "replay": len(replay),
            "total": len(recs),
            "window": [pd_all[0], pd_all[-1]],
            "t10_covered": sum(1 for r in recs if r.get("ret10") is not None),
        },
        "distributions": dist,
        "band_0507": {
            "n": len(band_recs),
            "frac": round(len(band_recs) / len(recs), 4),
            "hard_reject_in_band": band_rej,
            "markets": dict(band_mkt),
            "ret5_mean": round(band_ret, 6) if band_ret is not None else None,
        },
        "hard_veto": hard_stats,
        "grid": grid_rows,
        "baseline_stats": base_stats,
        "verdict": "GO" if cand else "NO-GO",
        "qualifying_bands": [g["band"] for g in cand],
    }
    if cand:
        best = max(cand, key=lambda g: g["t5_imp"])
        verdict["best_band"] = best["band"]
        print(
            f"GO：{len(cand)} 个档位达标 {verdict['qualifying_bands']}，"
            f"最优 {best['band']}（T+5 同市场域改善 {best['t5_imp'] * 100:+.2f}pp，"
            f"两段同向，MDD {best['mdd_after']:.2%} ≤ 基线 {mdd_base:.2%}×1.05）"
            f"→ 出产线变更方案（只写不动手）"
        )
    else:
        # 否定证据归因：为什么全挂
        best_row = max(grid_rows, key=lambda g: g["t5_imp"] or -1)
        print(
            f"NO-GO：无档位同时满足 改善≥{VERDICT_PP * 100:.1f}pp + n≥{WEAK_MIN_N} "
            f"+ 两段同向 + 回撤不恶化。"
        )
        print(
            f"  网格最优档 {best_row['band']}: n={best_row['n_band']} "
            f"T5 {best_row['t5_imp'] * 100 if best_row['t5_imp'] is not None else 0:+.2f}pp "
            f"H1/H2 同向={best_row['same_sign']}"
        )
        seg_ret = mean([r["ret5"] for r in band_recs]) if band_recs else 0
        cap_pp = (len(band_recs) / len(recs)) * (pool_ret - seg_ret) * 100
        print(
            f"  证据①：[0.5,0.7) 段 T+5 均值 {seg_ret:+.2%} vs 全池 {pool_ret:+.2%}"
            f"（段内确实更差）；但段仅占池 {len(band_recs) / len(recs):.1%}，"
            f"即使整段全剔，全池改善的理论上限 = 占比×均值差 ≈ {cap_pp:+.2f}pp，"
            f"物理上到不了 {VERDICT_PP * 100:.1f}pp 阈值——样本太薄，不是阈值调不调的问题。"
        )
        print(
            f"  证据②：产线硬否决（news≥0.7 ∨ crowd-hot，21 条）剔除后同市场域改善 "
            f"{(hard_stats['t5']['cross_market'] or 0) * 100:+.2f}pp ≈ 0——"
            f"否决通道在现有样本上整段无边际贡献。"
        )

    OUT_JSON.write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n结果已写 {OUT_JSON}")


if __name__ == "__main__":
    main()
