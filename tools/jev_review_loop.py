# -*- coding: utf-8 -*-
"""低置信 review 闭环（2026-10-30 JEVREVIEWLOOP，只读 news.db / 本地 JSONL）。

P1 中台治理：把"低置信判定"变成可标注、可回灌、可量化的一致率数据资产。
三步：
  1) --sample    从近 N 天已判新闻抽低置信样本 → candidates.jsonl（含模型 bias/prob/horizon）
  2) --ingest    读人工标注文件（JSONL: news_id,human_bias[,horizon,labeler,note]）
                 → 幂等写入 review_labels.jsonl（同 news_id 重复自动跳过）
  3) --report    计算人工 vs 模型一致率（总体/方向类/按模型 bias 分组）

用法：
    python -B tools/jev_review_loop.py --sample --days 3 --n 60 --out C:\\Temp\\cand.jsonl
    python -B tools/jev_review_loop.py --ingest C:\\Temp\\labels.jsonl
    python -B tools/jev_review_loop.py --report
退出码：0=成功；1=参数/数据错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

NEWS_DB = Path(r"C:\aios\news_collector\news.db")
OUT_DIR = Path(r"C:\aios\agent_platform\data\jev_audit")
CANDIDATES_PATH = OUT_DIR / "review_candidates.jsonl"
LABELS_PATH = OUT_DIR / "review_labels.jsonl"
BIASES = ("bull", "bear", "neutral")
HORIZONS = ("intraday", "days", "weeks", "long", "unknown")


def _stable_order(news_id: str) -> str:
    return hashlib.sha256(str(news_id).encode("utf-8")).hexdigest()


def sample_low_conf(days: int = 3, n: int = 60, max_conf: float = 0.7) -> list[dict]:
    """抽样：近 days 天、conf<=max_conf 的已判新闻；按 id 哈希稳定排序。"""
    conn = sqlite3.connect("file:%s?mode=ro" % NEWS_DB.as_posix(), uri=True, timeout=20)
    try:
        rows = conn.execute(
            "SELECT id, ts, source, title, jev FROM news"
            " WHERE jev IS NOT NULL AND jev<>''"
            " AND created_at >= date('now', ?)"
            " ORDER BY rowid DESC LIMIT ?", ("-%d day" % int(days), n * 8)).fetchall()
    finally:
        conn.close()
    out = []
    for nid, ts, source, title, raw in rows:
        try:
            j = json.loads(raw or "{}")
        except ValueError:
            continue
        conf = j.get("conf")
        if not isinstance(conf, (int, float)) or conf > max_conf:
            continue
        out.append({
            "news_id": nid, "ts": ts, "source": source,
            "title": str(title or "")[:120],
            "model_bias": j.get("bias"), "model_conf": conf,
            "model_horizon": j.get("horizon") or "unknown",
            "model_prob": j.get("prob") or {},
        })
    out.sort(key=lambda r: _stable_order(r["news_id"]))
    return out[:n]


def validate_label(row: dict) -> dict:
    """标注行校验 → 规范化 dict；非法抛 ValueError。"""
    if not isinstance(row, dict):
        raise ValueError("标注行必须是 JSON 对象")
    nid = str(row.get("news_id") or "").strip()
    if not nid:
        raise ValueError("缺 news_id")
    human = str(row.get("human_bias") or "").strip().lower()
    if human not in BIASES:
        raise ValueError("human_bias 必须是 bull/bear/neutral，得到 %r" % (human,))
    hz = str(row.get("horizon") or "").strip().lower()
    return {
        "news_id": nid,
        "human_bias": human,
        "horizon": hz if hz in HORIZONS else "",
        "labeler": str(row.get("labeler") or "human")[:40],
        "note": str(row.get("note") or "")[:200],
        "labeled_at": str(row.get("labeled_at") or time.strftime("%Y-%m-%dT%H:%M:%S")),
        "model_bias": str(row.get("model_bias") or ""),
    }


def load_labels(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def ingest(path: Path, out_path: Path = LABELS_PATH) -> dict:
    """幂等入库：同 news_id 已存在则跳过。返回 {recorded, skipped, invalid}。"""
    existing = {r.get("news_id") for r in load_labels(out_path)}
    recorded = skipped = invalid = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as fh:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = validate_label(json.loads(line))
            except (ValueError, json.JSONDecodeError):
                invalid += 1
                continue
            if row["news_id"] in existing:
                skipped += 1
                continue
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            existing.add(row["news_id"])
            recorded += 1
    return {"recorded": recorded, "skipped": skipped, "invalid": invalid}


def agreement(labels: list[dict]) -> dict:
    """人工 vs 模型一致率（总体、方向类、按模型 bias 分组）。"""
    pairs = [(r.get("model_bias"), r.get("human_bias")) for r in labels
             if r.get("model_bias") in BIASES and r.get("human_bias") in BIASES]
    if not pairs:
        return {"n": 0, "agreement": None}
    total = len(pairs)
    hit = sum(1 for m, h in pairs if m == h)
    dir_pairs = [(m, h) for m, h in pairs if m in ("bull", "bear")]
    by_model = Counter()
    by_model_hit = Counter()
    for m, h in pairs:
        by_model[m] += 1
        if m == h:
            by_model_hit[m] += 1
    return {
        "n": total,
        "agreement": round(hit / total, 4),
        "directional_n": len(dir_pairs),
        "directional_agreement": (round(sum(1 for m, h in dir_pairs if m == h) / len(dir_pairs), 4)
                                  if dir_pairs else None),
        "by_model_bias": {k: {"n": by_model[k],
                              "agreement": round(by_model_hit[k] / by_model[k], 4)}
                          for k in sorted(by_model)},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--max-conf", type=float, default=0.7)
    ap.add_argument("--out", default=str(CANDIDATES_PATH))
    ap.add_argument("--ingest", default="")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    result = {}
    rc = 0
    if a.sample:
        rows = sample_low_conf(a.days, a.n, a.max_conf)
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                       encoding="utf-8")
        result["sample"] = {"n": len(rows), "path": str(out),
                            "days": a.days, "max_conf": a.max_conf}
    if a.ingest:
        try:
            result["ingest"] = ingest(Path(a.ingest))
        except OSError as exc:
            result["ingest"] = {"error": str(exc)[:120]}
            rc = 1
    if a.report:
        result["report"] = agreement(load_labels(LABELS_PATH))
    if not result:
        ap.print_help()
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
