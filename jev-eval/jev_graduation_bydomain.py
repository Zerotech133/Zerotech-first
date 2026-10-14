#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev_graduation_bydomain — 毕业判据市场域拆分重述（W5b 任务B，2026-10-14，只读）。

背景：FOCUS n=336 达标（周报口径 n≥300）但前向均值 -1.04pp 未过 +1pp；
弱否决审计发现拖累集中在 CRYPTO 域（quality 全 low、仅 6 只标的循环入池，
名义 82 条的有效独立样本远小于名义值）。毕业判据原文要求"同市场域"，
本脚本把 n / 前向均值 / 达标状态按市场域拆开，并对 CRYPTO 做有效样本修正。

口径（与 jev_weakveto_audit.py 完全一致）：
  - 数据源：review_data/triples.jsonl（live，candidate_id 去重）+
    replay/replay-20260922-030434.jsonl（replay，(ts_code,pool_date) 去重），
    仅取有 T+5 收益（ret_nd / ret_5d）的已判定样本。
  - FOCUS 组 = decision == "FOCUS"（产线口径 quality=low 触发）。
  - 前向均值 = T+5 收益算术平均；"标的聚类均值" = 先按 ts_code 求均值再等权
    平均（消除单标的反复入池的重复计数，即有效样本口径）。
  - 毕业判据 v1（周报口径）：FOCUS n≥300 且前向均值 >+1pp。

本脚本零写入产线数据、不碰 DB、不调 API。输出：
  stdout 报告 + review_data/graduation_bydomain_20261014.json（留痕引用）。

用法：
  "C:/Program Files/Python311/python.exe" jev_graduation_bydomain.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
TRIPLES = HERE / "review_data" / "triples.jsonl"
REPLAY = HERE / "replay" / "replay-20260922-030434.jsonl"
OUT_JSON = HERE / "review_data" / "graduation_bydomain_20261014.json"

GRAD_N_MIN = 300      # 毕业判据 v1（周报口径）
GRAD_PP = 0.01        # 毕业判据 v1：前向均值 >+1pp
# 判据 v2 建议参数（按有效标的数计的最小样本；依据见报告）
V2_N_MIN = 30         # 每市场域有效标的数 ≥30（对齐阶段2判据 n≥30 的量级）
CRYPTO_V2_N_MIN = 6   # CRYPTO 域降级门槛（当前 fresh 池规模下可达的量级）

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def load_all() -> list[dict]:
    """两源合并，去重口径与 weakveto 审计一致。"""
    recs, seen = [], set()
    for line in TRIPLES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("status") != "reviewed" or r.get("ret_nd") is None:
            continue
        if r["candidate_id"] in seen:
            continue
        seen.add(r["candidate_id"])
        recs.append({"src": "live", "decision": r["decision"],
                     "market": r.get("market", "?"), "ts_code": r["ts_code"],
                     "pool_date": r.get("pool_date", ""),
                     "ret5": float(r["ret_nd"]), "quality": r.get("quality", "?")})
    seen = set()
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
        recs.append({"src": "replay", "decision": r["decision"],
                     "market": r.get("market", "?"), "ts_code": r["ts_code"],
                     "pool_date": r.get("pool_date", ""),
                     "ret5": float(r["ret_5d"]), "quality": r.get("quality", "?")})
    return recs


def domain_stats(fo: list[dict], pool: list[dict], m: str) -> dict:
    """单市场域统计：名义 n / 有效标的数 / 两种口径均值 / FOCUS-PASS 差。"""
    rs = [r for r in fo if r["market"] == m]
    codes = sorted({r["ts_code"] for r in rs})
    by_code: dict[str, list[float]] = defaultdict(list)
    for r in rs:
        by_code[r["ts_code"]].append(r["ret5"])
    code_means = [mean(v) for v in by_code.values()]
    ps = [r["ret5"] for r in pool if r["market"] == m and r["decision"] == "PASS"]
    return {
        "n_nominal": len(rs),
        "n_unique_codes": len(codes),
        "rec_per_code": round(len(rs) / len(codes), 1) if codes else None,
        "ret5_nominal": round(mean([r["ret5"] for r in rs]), 6) if rs else None,
        "ret5_cluster": round(mean(code_means), 6) if code_means else None,
        "code_mean_range": [round(min(code_means), 4), round(max(code_means), 4)]
        if code_means else None,
        "pass_n": len(ps),
        "pass_ret5": round(mean(ps), 6) if ps else None,
        "focus_minus_pass": round(mean([r["ret5"] for r in rs]) - mean(ps), 6)
        if rs and ps else None,
        "top_codes": Counter({c: len(by_code[c]) for c in codes}).most_common(6),
    }


def main() -> None:
    recs = load_all()
    fo = [r for r in recs if r["decision"] == "FOCUS"]
    markets = sorted({r["market"] for r in fo})
    print(f"样本：{len(recs)} 条；FOCUS n={len(fo)}（名义）；市场域 {markets}")

    rows = {}
    print("\n===== FOCUS 分市场域拆分（T+5 前向，毕业判据 v1：n≥300 且 >+1pp） =====")
    hdr = f"{'域':8s} {'名义n':>5s} {'标的数':>5s} {'条/标的':>7s} {'名义均值':>9s} {'聚类均值':>9s} {'PASS差':>8s} {'v1判定':>10s}"
    print(hdr)
    for m in markets:
        st = domain_stats(fo, recs, m)
        rows[m] = st
        v1 = "n未达标" if st["n_nominal"] < GRAD_N_MIN else (
            "✓过线" if (st["ret5_nominal"] or 0) > GRAD_PP else "✗未过+1pp")
        print(f"{m:8s} {st['n_nominal']:>5d} {st['n_unique_codes']:>5d} "
              f"{st['rec_per_code']:>7.1f} {(st['ret5_nominal'] or 0)*100:>+8.2f}pp "
              f"{(st['ret5_cluster'] or 0)*100:>+8.2f}pp "
              f"{(st['focus_minus_pass'] or 0)*100:>+7.2f}pp {v1:>10s}")

    # ── CRYPTO 有效独立样本修正 ──
    print("\n===== CRYPTO 域有效独立样本修正 =====")
    cr = rows.get("CRYPTO", {})
    print(f"名义 {cr['n_nominal']} 条 → 唯一标的仅 {cr['n_unique_codes']} 只"
          f"（每只平均入池 {cr['rec_per_code']} 次）" if cr else "无 CRYPTO 样本")
    if cr:
        print(f"标的循环入池明细（top6）：{cr['top_codes']}")
        print(f"名义均值 {cr['ret5_nominal']*100:+.2f}pp vs 标的聚类均值 "
              f"{cr['ret5_cluster']*100:+.2f}pp —— 差异来自单标的重复计数")
        print(f"标的内均值极差 {[x*100 for x in cr['code_mean_range']]}% "
              f"→ 6 只标的互相之间方向都不同，名义 n 虚高")

    # ── v2 判据对照 ──
    print("\n===== 毕业判据 v2 对照（每域独立：有效标的数 + 前向均值>+1pp） =====")
    for m in markets:
        st = rows[m]
        n_eff = st["n_unique_codes"]
        thr = CRYPTO_V2_N_MIN if m == "CRYPTO" else V2_N_MIN
        ok_n = "✓" if n_eff >= thr else "✗"
        ok_pp = "✓" if (st["ret5_cluster"] or 0) > GRAD_PP else "✗"
        print(f"{m:8s} 有效标的 {n_eff:>3d}/{thr} {ok_n}  聚类均值 "
              f"{(st['ret5_cluster'] or 0)*100:>+6.2f}pp（>{GRAD_PP*100:.0f}pp {ok_pp}）")

    out = {
        "generated_at": "2026-10-14",
        "sample": {"total": len(recs), "focus_n_nominal": len(fo),
                   "window": "2026-08-13 ~ 2026-10-08（T+5 回填截止）"},
        "criterion_v1": {"n_min": GRAD_N_MIN, "pp": GRAD_PP,
                         "source": "docs/changes/20261014_shadow_report_changes.md:26"},
        "criterion_v2_proposal": {
            "per_domain": True, "n_by_unique_codes": True,
            "n_min": V2_N_MIN, "crypto_n_min": CRYPTO_V2_N_MIN, "pp": GRAD_PP},
        "by_market": rows,
        "all_focus_nominal": {
            "n": len(fo),
            "ret5_nominal": round(mean([r["ret5"] for r in fo]), 6)},
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n结果已写 {OUT_JSON}")


if __name__ == "__main__":
    main()
