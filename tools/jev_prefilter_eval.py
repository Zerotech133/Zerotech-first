# -*- coding: utf-8 -*-
"""prefilter 灰度评估（2026-10-30 PREFILTEREVAL，只读 news.db）。

目的：prefilter 一直默认关闭；开启前需要量化"误杀率"（把有方向信号的新闻
判成 neutral skip）。没人手工标注，于是用**已判数据做代理标注**：
  - 预筛决策 = jev_prefilter.decide(title, source)
  - 参照信号 = 现有 JEV 判定中 bias∈(bull,bear) 且 conf>=阈值（默认 0.75）
  - 误杀 = 预筛 skip 但参照是方向信号
输出 JSON + Markdown 报告；不写库、不改规则。

用法：
    python -B tools/jev_prefilter_eval.py --days 30 --conf 0.75
    python -B tools/jev_prefilter_eval.py --days 30 --json out.json --md out.md
"""
from __future__ import annotations

import argparse
import os
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NEWS_DB = Path(os.environ.get("JEV_NEWS_DB", r"C:\aios\news_collector\news.db"))
NC = REPO / "news_collector"
if str(NC) not in sys.path:
    sys.path.insert(0, str(NC))

import jev_prefilter as pf  # noqa: E402


def load_rows(days: int = 30, limit: int = 20000) -> list[dict]:
    conn = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True, timeout=20)
    try:
        rows = conn.execute(
            "SELECT id, title, source, jev FROM news"
            " WHERE jev IS NOT NULL AND jev<>''"
            " AND created_at >= date('now', ?) ORDER BY created_at DESC LIMIT ?",
            ("-%d day" % int(days), int(limit))).fetchall()
    finally:
        conn.close()
    out = []
    for nid, title, source, jev in rows:
        try:
            j = json.loads(jev or "{}")
        except ValueError:
            continue
        if not isinstance(j, dict):
            continue
        out.append({"id": nid, "title": title or "", "source": source or "",
                    "bias": j.get("bias"), "conf": j.get("conf"),
                    "event_type": j.get("event_type")})
    return out


def evaluate(rows: list[dict], conf_threshold: float = 0.75,
             mode: str = "safe") -> dict:
    """纯函数：计算预筛误杀/命中率（便于单测）。"""
    stats = Counter()
    patterns = Counter()
    pat_n = Counter()
    pat_fk = Counter()
    examples = {"false_kill": [], "skip_ok": []}
    for r in rows:
        d = pf.decide(r["title"], r["source"], mode=mode)
        ref_signal = (r["bias"] in ("bull", "bear")
                      and isinstance(r["conf"], (int, float))
                      and r["conf"] >= conf_threshold)
        if d["decision"] == "skip":
            stats["skip"] += 1
            pat = d.get("matched") or d.get("reason") or "?"
            patterns[pat] += 1
            pat_n[pat] += 1
            if ref_signal:
                pat_fk[pat] += 1
            if ref_signal:
                stats["false_kill"] += 1
                if len(examples["false_kill"]) < 10:
                    examples["false_kill"].append(
                        {"id": r["id"], "title": r["title"][:70],
                         "bias": r["bias"], "conf": r["conf"],
                         "matched": d.get("matched")})
            else:
                stats["skip_neutral_like"] += 1
                if len(examples["skip_ok"]) < 5:
                    examples["skip_ok"].append(
                        {"id": r["id"], "title": r["title"][:70],
                         "bias": r["bias"], "conf": r["conf"]})
        else:
            stats["keep"] += 1
            if ref_signal:
                stats["keep_signal"] += 1
    total = len(rows)
    skip_n = stats["skip"]
    fk = stats["false_kill"]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n": total,
        "mode": mode,
        "conf_threshold": conf_threshold,
        "skip": skip_n,
        "keep": stats["keep"],
        "skip_rate": round(skip_n / total, 4) if total else None,
        "false_kill": fk,
        "false_kill_rate_in_skip": round(fk / skip_n, 4) if skip_n else None,
        "false_kill_rate_in_all": round(fk / total, 4) if total else None,
        "skip_neutral_like": stats["skip_neutral_like"],
        "keep_signal": stats["keep_signal"],
        "signal_capture_rate": (round(stats["keep_signal"] / (stats["keep_signal"] + fk), 4)
                                if (stats["keep_signal"] + fk) else None),
        "top_skip_patterns": [
            {"pattern": k, "n": v, "false_kill": pat_fk.get(k, 0),
             "precision": round(1.0 - pat_fk.get(k, 0) / v, 4) if v else None}
            for k, v in patterns.most_common(20)],
        "examples_false_kill": examples["false_kill"],
        "examples_skip_ok": examples["skip_ok"],
    }


def render_md(rep: dict) -> str:
    lines = [
        "# prefilter 灰度评估（数据版，%s）" % rep["generated_at"],
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 样本（近 N 天已判） | %d |" % rep["n"],
        "| 预筛 skip | %d（%.1f%%） |" % (rep["skip"], (rep["skip_rate"] or 0) * 100),
        "| 误杀（skip 但模型高置信方向） | %d（占 skip %.1f%% / 占全量 %.1f%%） |"
        % (rep["false_kill"], (rep["false_kill_rate_in_skip"] or 0) * 100,
           (rep["false_kill_rate_in_all"] or 0) * 100),
        "| skip 且模型中性/低置信（正确跳过） | %d |" % rep["skip_neutral_like"],
        "| 方向信号捕获率（keep 信号 / 全部信号） | %.1f%% |"
        % ((rep["signal_capture_rate"] or 0) * 100),
        "",
        "## 高频 skip 命中模式",
        "",
        "| 模式 | skip 次数 | 误杀 | 精度(中性率) |",
        "|---|---|---|---|",
    ]
    for p in rep["top_skip_patterns"]:
        lines.append("| `%s` | %d | %d | %.1f%% |" % (
            str(p["pattern"])[:60].replace("|", "\\|"), p["n"],
            p.get("false_kill") or 0, (p.get("precision") or 0) * 100))
    if rep["examples_false_kill"]:
        lines += ["", "## 误杀样例（前 10）", ""]
        for e in rep["examples_false_kill"]:
            lines.append("- %s `%s`（%s, conf=%s, 命中 `%s`）"
                         % (e["id"], e["title"], e["bias"], e["conf"], e["matched"]))
    lines += ["", "> 结论口径：误杀率占 skip ≤2% 且方向信号捕获率 ≥95% 才建议灰度开启。"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=20000)
    ap.add_argument("--conf", type=float, default=0.75)
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    ap.add_argument("--mode", default="safe", choices=("safe", "aggressive"))
    a = ap.parse_args(argv)
    rows = load_rows(a.days, a.limit)
    rep = evaluate(rows, a.conf, mode=a.mode)
    text = json.dumps(rep, ensure_ascii=False, indent=2)
    print(text)
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
    if a.md:
        Path(a.md).write_text(render_md(rep), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
