# -*- coding: utf-8 -*-
"""zhuge_memory.py — P1-3 记忆 v0（Mem0 前置；纯 stdlib，JSONL 追加式）。

- 只记「决策/教训/偏好/承诺/事实」五类；每条含 kind/text/tags/source/confidence/ts/expires_at；
- 追加式写入 `work/p1/memory/memory.jsonl`（不入库）；同文本 sha256 去重；
- recall：BM25-lite（复用 zhuge_rag.tokenize）+ 时间衰减；过滤过期；
- 不接 L5（v0 先验收）；不写沙盘/不执行；无 L2。

CLI：
  python tools/zhuge_memory.py remember --kind lesson --text "..." --tags a,b
  python tools/zhuge_memory.py recall --query "..." --k 5
  python tools/zhuge_memory.py list --limit 20
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STORE_REL = Path("work") / "p1" / "memory" / "memory.jsonl"
KINDS = ("decision", "lesson", "preference", "commitment", "fact")


def _root() -> Path:
    override = os.environ.get("ZHUGE_MEMORY_REPO")
    return Path(override).resolve() if override else REPO_ROOT


def _now_ts() -> int:
    return int(time.time())


def _now_str() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _store() -> Path:
    return _root() / STORE_REL


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:16]


def _load() -> list[dict]:
    path = _store()
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def remember(kind: str, text: str, tags=None, source: str = "manual",
             confidence: str = "medium", ttl_days: int = 0) -> dict:
    kind = (kind or "").strip()
    text = (text or "").strip()
    if kind not in KINDS:
        return {"ok": False, "error": "kind 仅支持 %s" % "/".join(KINDS)}
    if not text:
        return {"ok": False, "error": "text 为空"}
    digest = _digest(text)
    for row in _load():
        if row.get("sha256_16") == digest and not row.get("expires_at"):
            return {"ok": True, "duplicate": True, "id": row.get("id"), "kind": row.get("kind")}
    entry = {
        "id": "MEM-%s-%s" % (datetime.now().strftime("%Y%m%d%H%M%S"), digest[:6]),
        "ts": _now_ts(),
        "ts_human": _now_str(),
        "kind": kind,
        "text": text[:2000],
        "tags": [str(t).strip() for t in (tags or []) if str(t).strip()][:12],
        "source": (source or "manual")[:80],
        "confidence": confidence if confidence in ("high", "medium", "low", "unknown") else "medium",
        "sha256_16": digest,
        "expires_at": (_now_ts() + int(ttl_days) * 86400) if int(ttl_days or 0) > 0 else 0,
    }
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"ok": True, "duplicate": False, "id": entry["id"], "kind": kind}


def recall(query: str, k: int = 5) -> list[dict]:
    rows = [r for r in _load()
            if not r.get("expires_at") or int(r.get("expires_at") or 0) > _now_ts()]
    if not rows:
        return []
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        from zhuge_rag import tokenize  # noqa: E402
    except Exception:  # noqa: BLE001
        def tokenize(text):
            return [t for t in (text or "").lower().split() if len(t) >= 2]

    q_terms = set(tokenize(query))
    if not q_terms:
        return []
    now = _now_ts()
    n = len(rows)
    scored = []
    for row in rows:
        blob = "%s %s %s" % (row.get("text", ""), row.get("kind", ""), " ".join(row.get("tags") or []))
        terms = tokenize(blob)
        if not terms:
            continue
        tf = {}
        for term in terms:
            if term in q_terms:
                tf[term] = tf.get(term, 0) + 1
        if not tf:
            continue
        score = sum((freq * 2.2) / (freq + 1.2) * math.log(1 + n) for freq in tf.values())
        age_days = max(0.0, (now - int(row.get("ts") or now)) / 86400.0)
        score *= (1.0 + 1.0 / (1.0 + age_days))  # 近期轻微加权
        if str(row.get("confidence")) == "high":
            score *= 1.1
        scored.append((round(score, 4), row))
    scored.sort(key=lambda x: -x[0])
    out = []
    for score, row in scored[:max(1, k)]:
        out.append({"id": row.get("id"), "kind": row.get("kind"), "text": row.get("text"),
                    "tags": row.get("tags"), "ts_human": row.get("ts_human"),
                    "confidence": row.get("confidence"), "score": score})
    return out


def prune_expired() -> dict:
    """追加 tombstone（不改历史）；返回数量。"""
    rows = _load()
    now = _now_ts()
    expired = [r for r in rows if r.get("expires_at") and int(r["expires_at"]) <= now]
    if not expired:
        return {"ok": True, "pruned": 0}
    path = _store()
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for row in expired:
            fh.write(json.dumps({"event": "expired", "id": row.get("id"),
                                 "ts": now, "ts_human": _now_str()},
                                ensure_ascii=False) + "\n")
    return {"ok": True, "pruned": len(expired)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="zhuge memory v0")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("remember")
    r.add_argument("--kind", required=True, choices=KINDS)
    r.add_argument("--text", required=True)
    r.add_argument("--tags", default="")
    r.add_argument("--source", default="manual")
    r.add_argument("--confidence", default="medium")
    r.add_argument("--ttl-days", type=int, default=0)
    q = sub.add_parser("recall")
    q.add_argument("--query", required=True)
    q.add_argument("--k", type=int, default=5)
    l = sub.add_parser("list")
    l.add_argument("--limit", type=int, default=20)
    sub.add_parser("prune")
    args = ap.parse_args(argv)
    if args.cmd == "remember":
        out = remember(args.kind, args.text,
                       [t for t in args.tags.split(",") if t], args.source,
                       args.confidence, args.ttl_days)
    elif args.cmd == "recall":
        out = {"query": args.query, "matches": recall(args.query, args.k)}
    elif args.cmd == "list":
        out = {"rows": _load()[: max(1, args.limit)]}
    else:
        out = prune_expired()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
