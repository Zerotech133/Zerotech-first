# -*- coding: utf-8 -*-
"""P2 · 评论清洗：去重（精确+近重复）、PII 脱敏、噪声过滤。

输入：10_规范化/comments.jsonl
输出：20_清洗/comments_clean/part-*.parquet + 90_报告/p2_clean_report.{json,md}

规则：
- 去重：同 (video_id, comment_id) 唯一；normalized text 完全相同的跨评论去重（保留最早）。
- PII：邮箱、手机号、身份证、QQ/微信数字串 → 替换为 [EMAIL]/[PHONE]/[ID]/[QQ]/[WX]。
- 噪声：长度<2、纯数字/表情、广告关键词（加微信/带单/收费/进群/私信/代操盘）→ 标记 noise 并默认剔除。
- 保留原文与 provenance；不修改原始 ZIP。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

KB = Path(r"C:\项目\知识库")
SRC = KB / "10_规范化" / "comments.jsonl"
OUTD = KB / "20_清洗" / "comments_clean"
REP = KB / "90_报告"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")
ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
QQ_RE = re.compile(r"(?i)(?:qq|扣扣|企鹅)[:：\s]*\d{5,12}")
WX_RE = re.compile(r"(?i)(?:微信|wechat|vx|v信|加v)[:：\s]*[A-Za-z0-9_-]{5,20}")
AD_RE = re.compile(r"(加微信|加微|带单|收费|进群|私信|代操盘|开户返佣|牛股推荐|稳赚|保本)")
EMOJI_RE = re.compile(r"^[\W_]+$")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def clean_text(t: str) -> tuple[str, bool]:
    pii = False
    for rx, tag in ((EMAIL_RE, "[EMAIL]"), (PHONE_RE, "[PHONE]"), (ID_RE, "[ID]"),
                    (QQ_RE, "[QQ]"), (WX_RE, "[WX]")):
        t2, n = rx.subn(tag, t)
        pii = pii or bool(n)
        t = t2
    return t, pii


def read_jsonl(path: Path, chunk: int = 100000):
    buf = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                buf.append(json.loads(line))
            except ValueError:
                continue
            if len(buf) >= chunk:
                yield buf
                buf = []
    if buf:
        yield buf


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=int, default=100000)
    args = ap.parse_args(argv)
    OUTD.mkdir(parents=True, exist_ok=True)
    REP.mkdir(parents=True, exist_ok=True)
    stats = {"rows": 0, "dup_id": 0, "dup_text": 0, "pii": 0, "noise": 0, "kept": 0}
    seen_id: set[tuple[str, str]] = set()
    seen_text: set[str] = set()
    part = 0
    for chunk in read_jsonl(SRC, args.chunk):
        out = []
        for r in chunk:
            stats["rows"] += 1
            key = (str(r.get("video_id") or ""), str(r.get("comment_id") or ""))
            if key in seen_id:
                stats["dup_id"] += 1
                continue
            seen_id.add(key)
            text = str(r.get("text") or "")
            nt = normalize(text)
            if nt and nt in seen_text:
                stats["dup_text"] += 1
                continue
            seen_text.add(nt) if nt else None
            cleaned, pii = clean_text(text)
            stats["pii"] += 1 if pii else 0
            noise = (len(nt) < 2 or EMOJI_RE.match(nt) is not None or AD_RE.search(cleaned) is not None)
            stats["noise"] += 1 if noise else 0
            if noise:
                continue
            out.append({
                "video_id": r.get("video_id"), "comment_id": r.get("comment_id"),
                "parent": r.get("parent"), "text": cleaned,
                "author_hash": r.get("author_hash"), "like_count": r.get("like_count"),
                "published_at": r.get("published_at"), "collected_at": r.get("collected_at"),
                "snapshot_id": r.get("snapshot_id"), "pii_masked": pii,
                "text_sha1": hashlib.sha1(cleaned.encode("utf-8"), usedforsecurity=False).hexdigest()[:16],
            })
        if out:
            cols = list({k: None for r in out for k in r}.keys())
            table = pa.table({c: [r.get(c) for r in out] for c in cols})
            pq.write_table(table, OUTD / ("part-%04d.parquet" % part), compression="zstd")
            part += 1
        stats["kept"] += len(out)
        if stats["rows"] % 500000 < args.chunk:
            print(f"rows={stats['rows']} kept={stats['kept']}", flush=True)
    stats["parts"] = part
    stats["retention_pct"] = round(stats["kept"] * 100.0 / max(1, stats["rows"]), 2)
    rep = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), **stats}
    (REP / "p2_clean_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# P2 · 评论清洗报告", "",
         f"- 输入 {stats['rows']} 行；重复ID {stats['dup_id']}；重复文本 {stats['dup_text']}；噪声 {stats['noise']}；PII 命中 {stats['pii']}",
         f"- 保留 **{stats['kept']}** 行（{stats['retention_pct']}%），输出 {part} 个 parquet",
         "- PII 规则：邮箱/手机/身份证/QQ/微信 → 占位符替换；author 仅哈希。",
         "- 噪声规则：长度<2、纯符号表情、广告关键词（加微信/带单/收费/进群/私信/代操盘等）。"]
    (REP / "p2_clean_report.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
