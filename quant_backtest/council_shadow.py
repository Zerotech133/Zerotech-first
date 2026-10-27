# -*- coding: utf-8 -*-
"""K55-C4 影子对照统计 + 转正评估：分歧度-前向收益相关性

口径（K55 规划）：影子期并行不打扰产线；累计 ≥30 票对照
「分歧度-前向收益」相关性显著才转正——转正后仍只获建仓 skip 建议权，
资金动作人工确认铁律不变。n<30 只记录不评判（K33 影子纪律）。

统计：
  · 前向收益 rN = 建仓后第 N 个交易日收盘 / 建仓日收盘 - 1（N=5/20）
  · 分歧度 dissent（代码算 0~100）四分桶 + 结论（支持/反对/中性）对照
  · Pearson r + t 统计（|t|≥2 近似 5% 显著）
  · 记忆使用统计：mem_used 引用计数 + lessons 池规模（月度淘汰位）

产出：reports/council_shadow_stats.json（门户 /council 页读缓存）

触发：
  python council_shadow.py            # 全量重算 + 写缓存 + 摘要
  python council_shadow.py --json     # 全量重算 + 输出原始 JSON
  （stock_council.py main 尾部自动调用 refresh()）
"""

import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

REPORTS = os.path.join(HERE, "reports")
PROPOSALS = os.path.join(REPORTS, "council_proposals.jsonl")
REVIEWS = os.path.join(REPORTS, "council_reviews.jsonl")
STATS = os.path.join(REPORTS, "council_shadow_stats.json")
MKT_TABLE = {
    "CN": "ts_daily",
    "US": "us_daily",
    "HK": "hk_daily",
    "CRYPTO": "crypto_daily",
}
N_MIN = 30  # 转正门槛样本量（K55 规划原文）
NS = (5, 20)  # 前向收益窗口
BUCKETS = ("0-25", "25-50", "50-75", "75-100")

_DATE_KEYS = ("trade_date", "d", "date", "datetime")
_CLOSE_KEYS = ("close", "Close", "close_price")


def _load_jsonl(fp):
    out = []
    try:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return out


def _fwd(code, market, d):
    """建仓日起前向收益 {5: %, 20: %}（收盘/建仓日收盘-1，窗口不足 None）。
    假日/停牌自然体现为窗口不足（如国庆建仓 → 开市后才累积）。"""
    table = MKT_TABLE.get(market or "", "ts_daily")
    try:
        from agent_tools import call as T

        r = T("datamart_read", table=table, codes=[code], start=d, limit=40)
        d2 = r.get("data") if isinstance(r.get("data"), dict) else r
        rows = d2.get("rows") or []
    except Exception:
        return None
    d8 = str(d).replace("-", "")[:8]
    closes = []
    for row in rows:
        dt = next((str(row[k]) for k in _DATE_KEYS if row.get(k)), "")
        if dt[:10].replace("-", "")[:8] < d8:
            continue  # 保险：跨表日期格式混排防御
        px = next((row[k] for k in _CLOSE_KEYS if row.get(k) is not None), None)
        if px is None:
            continue
        closes.append(float(px))
    if not closes:
        return None
    base = closes[0]
    return {
        n: (round((closes[n] / base - 1) * 100, 2) if len(closes) > n else None)
        for n in NS
    }


def _bucket(v):
    i = 0 if v < 25 else 1 if v < 50 else 2 if v < 75 else 3
    return BUCKETS[i]


def _agg(rows):
    """{n, win5, r5均值, r20均值}（r5>0 记胜）。"""
    r5s = [r["r5"] for r in rows if r.get("r5") is not None]
    r20s = [r["r20"] for r in rows if r.get("r20") is not None]
    return {
        "n": len(rows),
        "win5": round(100.0 * sum(1 for x in r5s if x > 0) / len(r5s), 1)
        if r5s
        else None,
        "r5": round(sum(r5s) / len(r5s), 2) if r5s else None,
        "r20": round(sum(r20s) / len(r20s), 2) if r20s else None,
    }


def compute_stats(fwd_map=None):
    """fwd_map 注入模式供沙盒测试（key=code|market|d → {5:%,20:%}，
    注入时不查 datamart）。"""
    inject = fwd_map is not None
    cache = dict(fwd_map) if inject else {}
    props = _load_jsonl(PROPOSALS)
    uniq = {}  # (sid,code,d) → 最新一轮（--force 重辩去重）
    for p in props:
        if p.get("code") and p.get("d"):
            uniq[(p.get("sid"), p.get("code"), p.get("d"))] = p
    samples = []
    for (sid, code, d), p in uniq.items():
        key = "%s|%s|%s" % (code, p.get("market"), d)
        f = cache.get(key)
        if f is None and not inject:
            f = _fwd(code, p.get("market"), d)
            cache[key] = f
        samples.append(
            {
                "sid": sid,
                "code": code,
                "d": d,
                "name": p.get("name"),
                "market": p.get("market"),
                "dissent": p.get("dissent") or 0,
                "verdict": (p.get("memo") or {}).get("verdict"),
                "r5": (f or {}).get(5),
                "r20": (f or {}).get(20),
            }
        )
    have = [s for s in samples if s["r5"] is not None or s["r20"] is not None]
    by_dissent = [
        {"bucket": b, **_agg([s for s in have if _bucket(s["dissent"]) == b])}
        for b in BUCKETS
    ]
    by_verdict = [
        {"verdict": v, **_agg([s for s in have if s.get("verdict") == v])}
        for v in ("支持", "反对", "中性")
    ]
    pairs = [(s["dissent"], s["r20"]) for s in samples if s.get("r20") is not None]
    corr = None
    if len(pairs) >= 3:
        n = len(pairs)
        mx = sum(x for x, _ in pairs) / n
        my = sum(y for _, y in pairs) / n
        vx = sum((x - mx) ** 2 for x, _ in pairs)
        vy = sum((y - my) ** 2 for _, y in pairs)
        if vx > 0 and vy > 0:
            r = sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(vx * vy)
            t = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
            corr = {"n": n, "r": round(r, 3), "t": round(t, 2)}
    if len(pairs) < N_MIN:
        gate = {"status": "样本积累中", "n": len(pairs), "need": N_MIN, "corr": corr}
    elif corr and abs(corr["t"]) >= 2:
        gate = {"status": "显著·建议转正评估（人工拍板）", "corr": corr}
    else:
        gate = {"status": "不显著·维持影子", "corr": corr}
    mem_cnt = {}
    for p in props:
        for m in p.get("mem_used") or []:
            if isinstance(m, dict) and m.get("text"):
                mem_cnt[m["text"]] = mem_cnt.get(m["text"], 0) + 1
    n_lessons = sum(len(r.get("lessons") or []) for r in _load_jsonl(REVIEWS))
    return {
        "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_prop_total": len(props),
        "n_uniq": len(uniq),
        "n_have_fwd": len(have),
        "by_dissent": by_dissent,
        "by_verdict": by_verdict,
        "corr": corr,
        "gate": gate,
        "mem": {
            "pool": n_lessons,
            "cited_top": sorted(mem_cnt.items(), key=lambda x: -x[1])[:5],
        },
        "samples": sorted(samples, key=lambda s: s["d"], reverse=True),
    }


def refresh():
    st = compute_stats()
    tmp = STATS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATS)
    g, c = st["gate"], st["corr"]
    print(
        "[SHADOW] prop=%d uniq=%d fwd20=%d corr=%s gate=%s mem_pool=%d"
        % (
            st["n_prop_total"],
            st["n_uniq"],
            g.get("n") or 0,
            ("r=%.3f t=%.2f" % (c["r"], c["t"])) if c else "n/a",
            g["status"],
            st["mem"]["pool"],
        )
    )
    return st


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="K55-C4 投委会影子对照统计")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    a = ap.parse_args()
    st = refresh()
    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
