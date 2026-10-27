# -*- coding: utf-8 -*-
"""赵云记忆 v2：四层记忆（working/episodic/semantic/procedural）+ 混合检索 + 检索评测。

设计：
  · SQLite 单文件（stdlib），表 memories + FTS5 索引；
  · 检索 = FTS5 BM25 候选 + 字符 bigram 相似度重排（中文友好），无向量依赖；
  · 四层：working（当前任务/会话态）、episodic（事件/请求/决策）、
    semantic（经验/判例）、procedural（技能/命令）；
  · 摄取：经验库 _index.jsonl、审批请求、技能注册表；幂等（id 去重）；
  · 评测：golden 查询集 → hit@1/hit@3/MRR，结果落 data/memory/eval_latest.json；
  · 删除/隔离仍走审批（本模块只做 prune **建议**，dry_run 默认 True）。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
PROFILE = Path.home() / ".hermes" / "profiles" / "zhaoyun"
DB = PLATFORM / "data" / "memory" / "memory.sqlite"
EVAL_PATH = PLATFORM / "memory" / "eval_queries.json"
EVAL_OUT = PLATFORM / "data" / "memory" / "eval_latest.json"
LAYERS = ("working", "episodic", "semantic", "procedural")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def connect(db: Path | None = None) -> sqlite3.Connection:
    p = Path(db) if db else DB
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS memories(
        id TEXT PRIMARY KEY, layer TEXT NOT NULL, scope TEXT DEFAULT 'zhaoyun',
        cue TEXT NOT NULL, content TEXT NOT NULL, source TEXT DEFAULT '',
        confidence REAL DEFAULT 0.8, tags TEXT DEFAULT '', created_at TEXT NOT NULL,
        last_used_at TEXT, use_count INTEGER DEFAULT 0, ttl_days INTEGER DEFAULT 365)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_layer ON memories(layer)")
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts "
                     "USING fts5(id UNINDEXED, cue, content)")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def add(layer: str, cue: str, content: str, source: str = "",
        confidence: float = 0.8, tags: list | None = None,
        ttl_days: int = 365, mid: str | None = None,
        db: Path | None = None) -> dict:
    if layer not in LAYERS:
        return {"ok": False, "error": "bad_layer", "allowed": list(LAYERS)}
    mid = str(mid or ("%s:%s" % (layer, abs(hash(cue + content)) % 10 ** 10)))
    conn = connect(db)
    try:
        conn.execute(
            "INSERT INTO memories(id,layer,cue,content,source,confidence,tags,"
            "created_at,ttl_days) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET cue=excluded.cue,content=excluded.content,"
            "confidence=excluded.confidence,tags=excluded.tags",
            (mid, layer, cue, content, source, float(confidence),
             ",".join(tags or []), _now(), int(ttl_days)))
        try:
            conn.execute("DELETE FROM memories_fts WHERE id=?", (mid,))
            conn.execute("INSERT INTO memories_fts(id,cue,content) VALUES(?,?,?)",
                         (mid, cue, content))
        except sqlite3.OperationalError:
            pass
        conn.commit()
        return {"ok": True, "id": mid, "layer": layer}
    finally:
        conn.close()


def _bigrams(text: str) -> set:
    t = re.sub(r"\s+", "", str(text or "").lower())
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _similarity(a: str, b: str) -> float:
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / max(1, len(ga | gb))


def _fts_ids(conn, query: str) -> set:
    try:
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", query)
        if not tokens:
            return set()
        expr = " OR ".join('"%s"' % t.replace('"', "") for t in tokens[:8])
        rows = conn.execute(
            "SELECT id FROM memories_fts WHERE memories_fts MATCH ? LIMIT 50",
            (expr,)).fetchall()
        return {r["id"] for r in rows}
    except sqlite3.OperationalError:
        return set()


def search(query: str, layer: str | None = None, limit: int = 5,
           db: Path | None = None) -> dict:
    limit = max(1, min(int(limit or 5), 20))
    conn = connect(db)
    try:
        sql = "SELECT * FROM memories"
        args: list = []
        if layer:
            sql += " WHERE layer=?"
            args.append(layer)
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        fts = _fts_ids(conn, query)
    finally:
        conn.close()
    scored = []
    q_tokens = [t for t in re.split(r"[\s,，。;；、]+", str(query or "")) if t]
    for r in rows:
        hay = r["cue"] + " " + r["content"]
        sim = max(_similarity(query, r["cue"]), _similarity(query, hay))
        coverage = (sum(1 for t in q_tokens if t in hay) / len(q_tokens)) if q_tokens else 0.0
        sub = 0.15 if (query and query in hay) else 0.0
        score = 0.35 * sim + 0.30 * coverage + 0.10 * sub + \
            0.15 * float(r.get("confidence") or 0) + (0.10 if r["id"] in fts else 0.0)
        if score <= 0:
            continue
        scored.append({"id": r["id"], "layer": r["layer"], "cue": r["cue"],
                       "content": str(r["content"])[:300], "source": r["source"],
                       "confidence": r["confidence"], "score": round(score, 4),
                       "fts_hit": r["id"] in fts})
    scored.sort(key=lambda x: (-x["score"], x["id"]))
    out = scored[:limit]
    return {"ok": True, "query": query, "layer": layer, "count": len(out),
            "used_fts": bool(fts), "total_candidates": len(rows), "items": out}


def stats(db: Path | None = None) -> dict:
    conn = connect(db)
    try:
        counts = {lay: 0 for lay in LAYERS}
        for r in conn.execute("SELECT layer, COUNT(*) n FROM memories GROUP BY layer"):
            counts[r["layer"]] = int(r["n"])
        total = sum(counts.values())
        latest = conn.execute("SELECT MAX(created_at) t FROM memories").fetchone()["t"]
    finally:
        conn.close()
    ev = None
    try:
        ev = json.loads(EVAL_OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"ok": True, "db": str(DB if db is None else db), "total": total,
            "layers": counts, "latest_created_at": latest, "eval": ev,
            "note": "删除/隔离须走审批（本模块只出 prune 建议）"}


def prune(dry_run: bool = True, db: Path | None = None) -> dict:
    conn = connect(db)
    try:
        now = time.time()
        items = []
        for r in conn.execute("SELECT * FROM memories").fetchall():
            created = r["created_at"]
            try:
                t = time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, TypeError):
                t = now
            expired = (now - t) / 86400 > int(r["ttl_days"] or 365)
            stale_low = (float(r["confidence"] or 0) < 0.3
                         and int(r["use_count"] or 0) == 0)
            if expired or stale_low:
                items.append({"id": r["id"], "layer": r["layer"],
                              "reason": "ttl_expired" if expired else "stale_low_confidence"})
        if not dry_run:
            for it in items:
                conn.execute("DELETE FROM memories WHERE id=?", (it["id"],))
                try:
                    conn.execute("DELETE FROM memories_fts WHERE id=?", (it["id"],))
                except sqlite3.OperationalError:
                    pass
            conn.commit()
        return {"ok": True, "dry_run": bool(dry_run), "count": len(items),
                "items": items[:50]}
    finally:
        conn.close()


# ── 摄取 ───────────────────────────────────────────────────────────────────
def ingest_experience(profile: Path | None = None) -> dict:
    prof = Path(profile) if profile else PROFILE
    p = prof / "experience" / "_index.jsonl"
    added = 0
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"ok": False, "error": "experience_unreadable", "added": 0}
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        eid = str(e.get("entry_id") or "")
        if not eid:
            continue
        add("semantic", str(e.get("cue") or eid), str(e.get("lesson") or e.get("cue") or ""),
            source="experience:%s" % eid, confidence=float(e.get("confidence") or 0.8),
            tags=["supervision", str(e.get("domain") or "")], mid="semantic:%s" % eid)
        added += 1
    return {"ok": True, "added": added, "source": str(p)}


def ingest_requests(platform: Path | None = None) -> dict:
    d = Path(platform or PLATFORM) / "data" / "zhaoyun_requests"
    added = 0
    if d.is_dir():
        for f in sorted(d.glob("ZYREQ-*.json"))[-200:]:
            try:
                r = json.loads(f.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            rid = str(r.get("id") or f.stem)
            add("episodic", "%s %s" % (r.get("kind"), r.get("reason") or ""),
                json.dumps({"status": r.get("status"), "kind": r.get("kind"),
                            "reason": r.get("reason"), "payload": r.get("payload")},
                           ensure_ascii=False),
                source="request:%s" % rid, confidence=0.9,
                tags=["approval", str(r.get("kind") or "")], mid="episodic:%s" % rid)
            added += 1
    return {"ok": True, "added": added}


def ingest_skills(platform: Path | None = None) -> dict:
    plat = Path(platform or PLATFORM)
    p = plat / "skills" / "registry.json"
    added = 0
    try:
        reg = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": "skills_registry_unreadable", "added": 0}
    for s in reg.get("skills") or []:
        name = str(s.get("name") or "")
        if not name:
            continue
        add("procedural", "技能 %s：%s" % (name, s.get("status") or ""),
            s.get("verify_cmd") or "", source="skill:%s" % name,
            confidence=0.9, tags=["skill", name], mid="procedural:%s" % name)
        added += 1
    return {"ok": True, "added": added}


def ingest_all(platform: Path | None = None, profile: Path | None = None) -> dict:
    a = ingest_experience(profile)
    b = ingest_requests(platform)
    c = ingest_skills(platform)
    return {"ok": True, "experience": a, "requests": b, "skills": c,
            "stats": stats()}


# ── 检索评测 ───────────────────────────────────────────────────────────────
def run_eval(queries_path: Path | None = None, db: Path | None = None) -> dict:
    path = Path(queries_path) if queries_path else EVAL_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return {"ok": False, "error": "queries_unreadable", "detail": str(e)[:120]}
    results = []
    hit1 = hit3 = 0
    rr = 0.0
    for q in data.get("queries") or []:
        query = str(q.get("query") or "")
        expected = [str(x) for x in q.get("expected_any") or []]
        rep = search(query, limit=3, db=db)
        rank = 0
        for i, item in enumerate(rep["items"], 1):
            hay = item["cue"] + " " + item["content"]
            if any(e in hay for e in expected):
                rank = i
                break
        if rank == 1:
            hit1 += 1
        if rank and rank <= 3:
            hit3 += 1
        rr += (1.0 / rank) if rank else 0.0
        results.append({"query": query, "rank": rank,
                        "top": [{"id": it["id"], "cue": it["cue"],
                                 "score": it["score"]} for it in rep["items"]]})
    total = len(results)
    rep = {"ok": True, "generated_at": _now(), "total": total,
           "hit@1": hit1, "hit@3": hit3,
           "hit1_rate": round(hit1 / total, 3) if total else 0.0,
           "hit3_rate": round(hit3 / total, 3) if total else 0.0,
           "mrr": round(rr / total, 3) if total else 0.0,
           "results": results}
    EVAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    EVAL_OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="赵云记忆 v2")
    ap.add_argument("--ingest", action="store_true")
    ap.add_argument("--search", default="")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.ingest:
        rep = ingest_all()
    elif args.stats:
        rep = stats()
    elif args.eval:
        rep = run_eval()
    elif args.prune:
        rep = prune(dry_run=not args.apply)
    elif args.search:
        rep = search(args.search)
    else:
        rep = stats()
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
