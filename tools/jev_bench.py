# -*- coding: utf-8 -*-
"""jev_bench.py — JEV 两段式推理 A/B 基准（K-ORG-20261026-JEV2STAGE）。

要回答的问题
------------
规则粗筛（jev_prefilter）能不能在**不牺牲关键方向判断**的前提下，把 9B 判读成本降 40%+？

协议
----
1. 从 news.db 待判队列随机抽 n 条（默认 100，可 --n）。
2. 每条先过 jev_prefilter.decide() 得到 skip/keep。
3. --live 模式下对**每一条**（含 skip）都调 news_ai.jev_judge_one() 拿「真值」bias
   （这是基准成本，也是漏判评估的唯一手段；离线模式不调 LLM，只做静态统计）。
4. 指标
   · skip_ratio = skip / n
   · 成本节省 = 1 - (keep 的判读耗时 + 粗筛耗时) / (全部判读耗时)
   · **漏判率** = skip 的条目里真值 bias != neutral 的比例（关键红线 ≤3%）
   · 多判率 = keep 的条目里真值 neutral 的比例（只浪费算力，不算错误）
5. 结论：节省 ≥40% 且漏判 ≤3% → 试点达标，可进夜间灰度。

用法
----
  python -B tools/jev_bench.py --n 100            # 离线（不调 LLM，秒级）
  python -B tools/jev_bench.py --n 100 --live     # 调 9B（约 5-10 分钟，GPU 空闲时跑）
  python -B tools/jev_bench.py --live --out-dir quant_backtest/reports
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve()
# 数据根默认=脚本所在仓库；工作树里 news.db 被 gitignore，验证时可用 JEV_ROLES_ROOT 指向 master。
ROOT = Path(os.environ.get("JEV_ROLES_ROOT") or HERE.parent.parent)
NEWS_DB = ROOT / "news_collector" / "news.db"
REPORT_DIR = ROOT / "quant_backtest" / "reports"

# 代码优先取脚本同级（工作树里才有未合并的新模块），数据根侧兜底。
sys.path.insert(0, str(HERE.parent.parent / "news_collector"))  # HERE=文件 → 上两级=仓库根
sys.path.insert(0, str(ROOT / "news_collector"))

# 离线估算用：审计实测单条判读耗时（中位 2.57s / 均值 9.58s）
EST_MED_S = 2.57
EST_MEAN_S = 9.58


def sample_items(n: int, seed: int = 20261026) -> list[dict]:
    """从待判队列抽 n 条（随机，只读）。"""
    if not NEWS_DB.is_file():
        return []
    conn = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True)
    try:
        rows = conn.execute(
            "SELECT id, title, source FROM news "
            "WHERE (jev IS NULL OR jev='') ORDER BY ts DESC LIMIT ?",
            (n * 8,)).fetchall()
    finally:
        conn.close()
    rnd = random.Random(seed)
    picked = rnd.sample(rows, min(n, len(rows)))
    return [{"id": r[0], "title": r[1] or "", "source": r[2] or ""} for r in picked]


def _truth_bias(items: list[dict], live: bool) -> tuple[dict, float]:
    """返回 {id: (bias, ms)} 与总耗时秒数。离线模式返回空字典。"""
    if not live:
        return {}, 0.0
    import news_ai  # 延迟导入：离线模式不加载（更不会碰 Ollama）
    out = {}
    t0 = time.time()
    for it in items:
        ms0 = time.time()
        try:
            j = news_ai.jev_judge_one(it["title"], [])
            bias = (j or {}).get("bias")
            conf = (j or {}).get("conf")
        except Exception:                   # noqa: BLE001  判读失败按 None 记（不参与漏判分母）
            bias, conf = None, None
        out[it["id"]] = {"bias": bias, "conf": conf, "ms": int((time.time() - ms0) * 1000)}
    return out, time.time() - t0


def run(n: int, live: bool, seed: int = 20261026) -> dict:
    import jev_prefilter

    items = sample_items(n, seed=seed)
    if not items:
        return {"ok": False, "error": "news.db 无待判条目或不可读"}
    decisions = []
    for it in items:
        d = jev_prefilter.decide(it["title"], it["source"])
        decisions.append((it, d))

    truth, live_seconds = _truth_bias(items, live)

    skip = [it for it, d in decisions if d["decision"] == "skip"]
    keep = [it for it, d in decisions if d["decision"] == "keep"]
    skip_ratio = len(skip) / len(items)

    # 成本：live 用真实 ms；离线用审计常量估算
    if live:
        cost_all = sum(v["ms"] for v in truth.values()) / 1000.0
        cost_keep = sum(truth[it["id"]]["ms"] for it in keep if it["id"] in truth) / 1000.0
    else:
        cost_all = len(items) * EST_MEAN_S
        cost_keep = len(keep) * EST_MEAN_S
    saving = (1 - cost_keep / cost_all) if cost_all else None

    # 漏判/多判（仅 live 有真值）
    miss = None
    over = None
    if live:
        judged = [(it, truth[it["id"]]) for it in skip if truth.get(it["id"], {}).get("bias")]
        misses = [it for it, tr in judged if tr["bias"] != "neutral"]
        miss = (len(misses) / len(judged)) if judged else None
        kept_judged = [truth[it["id"]] for it in keep if truth.get(it["id"], {}).get("bias")]
        over_n = sum(1 for tr in kept_judged if tr["bias"] == "neutral")
        over = (over_n / len(kept_judged)) if kept_judged else None

    n_with_truth = sum(1 for v in truth.values() if v.get("bias"))
    return {
        "ok": True,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "live" if live else "offline",
        "n": len(items), "skip": len(skip), "keep": len(keep),
        "skip_ratio": round(skip_ratio, 4),
        "cost_all_s": round(cost_all, 1), "cost_keep_s": round(cost_keep, 1),
        "saving_pct": round(saving * 100, 1) if saving is not None else None,
        "live_seconds": round(live_seconds, 1),
        "n_with_truth": n_with_truth,
        "miss_rate": round(miss * 100, 2) if miss is not None else None,
        "over_rate": round(over * 100, 2) if over is not None else None,
        "thresholds": {"saving_pct_min": 40, "miss_rate_max": 3},
        "verdict": _verdict(saving, miss, live),
        "samples": {
            "skip": [{"id": it["id"], "title": it["title"][:60], "matched": d["matched"],
                      "truth": (truth.get(it["id"]) or {}).get("bias")}
                     for it, d in decisions if d["decision"] == "skip"][:15],
            "keep_sample": [{"id": it["id"], "title": it["title"][:60], "reason": d["reason"]}
                            for it, d in decisions if d["decision"] == "keep"][:5],
        },
    }


def _verdict(saving, miss, live: bool) -> str:
    if not live:
        return "offline（仅静态统计；漏判率需 --live）"
    if saving is None or miss is None:
        return "insufficient"
    ok = saving >= 0.40 and miss <= 0.03
    return "PASS（节省 %.1f%% / 漏判 %.2f%%）" % (saving * 100, miss * 100) if ok else (
        "FAIL（节省 %.1f%% / 漏判 %.2f%%）" % (saving * 100, miss * 100))


def render_md(r: dict) -> str:
    if not r.get("ok"):
        return "# JEV 两段式基准\n\n不可用：%s\n" % r.get("error")
    lines = [
        "# JEV 两段式推理 A/B 基准 · %s" % r["generated_at"],
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 模式 | %s |" % r["mode"],
        "| 样本 | %d（skip %d / keep %d）|" % (r["n"], r["skip"], r["keep"]),
        "| 粗筛跳过率 | %.1f%% |" % (r["skip_ratio"] * 100),
        "| 全量成本 | %.0fs |" % r["cost_all_s"],
        "| 两段式成本 | %.0fs |" % r["cost_keep_s"],
        "| **成本节省** | **%s** |" % (("%.1f%%" % r["saving_pct"]) if r["saving_pct"] is not None else "—"),
        "| 漏判率（skip 中真非中性） | %s |" % (("%.2f%%" % r["miss_rate"]) if r["miss_rate"] is not None else "—（需 --live）"),
        "| 多判率（keep 中真中性） | %s |" % (("%.2f%%" % r["over_rate"]) if r["over_rate"] is not None else "—（需 --live）"),
        "| 结论 | **%s** |" % r["verdict"],
        "",
        "阈值：节省 ≥40% 且漏判 ≤3%。",
        "",
        "## skip 样本（前 15）",
    ]
    for s in r["samples"]["skip"]:
        lines.append("- `%s` [%s] %s（真值：%s）" % (s["id"], s["matched"] or "-", s["title"], s["truth"]))
    if not r["samples"]["skip"]:
        lines.append("_无 skip 条目_")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="JEV 两段式推理 A/B 基准")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--live", action="store_true", help="调 9B 拿真值（需 GPU，约 5-10 分钟）")
    ap.add_argument("--seed", type=int, default=20261026)
    ap.add_argument("--out-dir", default=str(REPORT_DIR))
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写报告")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    r = run(args.n, args.live, args.seed)
    if not r.get("ok"):
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 2
    if args.dry_run:
        print(render_md(r))
        return 0
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / ("jev_2stage_%s.json" % date.today().isoformat())
    mp = out / ("jev_2stage_%s.md" % date.today().isoformat())
    jp.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(render_md(r), encoding="utf-8")
    print("报告：%s" % jp)
    print(render_md(r).split("| 结论")[0].splitlines()[-1] if False else "结论：%s" % r["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
