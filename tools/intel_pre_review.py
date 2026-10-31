# -*- coding: utf-8 -*-
"""情报机器预审 200/日（只读建议，WO-INTEL-MACHINE-20261031）。

执行拍板 2/4 的机器侧配套：人工 50（真人复核 resolve，唯一写入口）+
机器预审 200（本工具：只读 pending、输出决策建议 JSONL，**不写 news.db、
不改 status、不伪造人工复核**）。

规则（无模型/无联网，纯字段推断）：
  score = impact 权重 + 低置信加分 + 近 7 天加分 + 来源已知/新来源加分；
  排序：score desc → queued_at desc → id asc（稳定，可复现）；
  取前 --limit（上限 200）条生成 machine_decision：
    - accept          建议人工采纳 JEV 自动判定（稳定且低风险）
    - reject          建议人工驳回/改判（低置信，JEV 判定不可靠）
    - needs_evidence  证据不足（高风险/坏 JSON/缺字段/来源未知），需补证
  注意：decision 只是**机器建议**，不是人工裁决；人工仍需走
  portal/api_jev_review.py POST /api/jev/review/resolve 才落 resolved。

输出：独立 JSONL（每行一条建议），默认落 C:\\Temp，--out 可改；
      绝不复用/覆盖 news.db。默认 dry-run（只打印摘要，不写文件）；
      --write 才写 JSONL。

用法：
    python tools/intel_pre_review.py --dry-run
    python tools/intel_pre_review.py --json
    python tools/intel_pre_review.py --write --out C:\\Temp\\intel_pre_review.jsonl
    python tools/intel_pre_review.py --limit 200 --samples 10 --json
退出码：0=成功；1=参数/数据错误；2=写 JSONL 失败。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "news_collector" / "news.db"
DEFAULT_OUT_DIR = Path(r"C:\Temp")

RULE_VERSION = "intel-pre-review/v1"
KIND = "machine_pre_review"
PENDING_STATUS = "pending"
MACHINE_BUDGET = 200          # 与 api_jev_review.MACHINE_BUDGET 同口径
MAX_LIMIT = 200
DECISIONS = ("accept", "reject", "needs_evidence")

IMPACT_WEIGHT = {"high": 40, "medium": 20, "low": 5}
CONF_LOW_STRONG_BONUS = 15    # conf <= 0.4
CONF_LOW_WEAK_BONUS = 8       # 0.4 < conf < 0.6
RECENT_DAYS = 7
RECENT_BONUS = 20
KNOWN_SOURCE_BONUS = 5
NOVEL_DAYS = 7
NOVEL_BONUS = 10

_TIME_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")


def _connect_ro(db_path) -> sqlite3.Connection:
    p = Path(db_path).resolve()
    if not p.is_file():
        raise RuntimeError("news.db 不存在：%s" % p)
    con = sqlite3.connect("file:" + p.as_posix() + "?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")  # 双保险：本连接只允许读
    return con


def _parse_json_obj(raw):
    """→ (dict, bad)。坏串/非对象：({}, True)；空值：({}, False)。"""
    if raw is None or str(raw).strip() == "":
        return {}, False
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return {}, True
    if not isinstance(obj, dict):
        return {}, True
    return obj, False


def _impact_of(original_jev, news_jev) -> str:
    for raw in (original_jev, news_jev):
        obj, _bad = _parse_json_obj(raw)
        val = str(obj.get("impact") or "").strip().lower()
        if val in IMPACT_WEIGHT:
            return val
    return ""


def _parse_time(text):
    s = str(text or "").strip()
    for fmt in _TIME_FMTS:
        n = min(len(s), len(dt.datetime.now().strftime(fmt)))
        try:
            return dt.datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    return None


def _now_naive(now=None) -> dt.datetime:
    if now is None:
        return dt.datetime.now()
    if isinstance(now, dt.datetime):
        return now.replace(tzinfo=None)
    text = str(now).strip().replace(" ", "T")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("--now 不是合法 ISO8601：%r" % (now,)) from exc
    return parsed.astimezone().replace(tzinfo=None)


def _source_first_seen(con: sqlite3.Connection) -> dict:
    first = {}
    for row in con.execute("SELECT source, MIN(ts) AS t FROM news GROUP BY source"):
        src = str(row["source"] or "").strip()
        t = _parse_time(row["t"])
        if not src or t is None:
            continue
        if src not in first or t < first[src]:
            first[src] = t
    return first


def _score(item: dict, now: dt.datetime, first_seen: dict) -> dict:
    impact = _impact_of(item.get("original_jev"), item.get("news_jev"))
    score = IMPACT_WEIGHT.get(impact, 0)
    conf = item.get("conf")
    if isinstance(conf, (int, float)):
        if conf <= 0.4:
            score += CONF_LOW_STRONG_BONUS
        elif conf < 0.6:
            score += CONF_LOW_WEAK_BONUS
    queued = _parse_time(item.get("queued_at"))
    recent = bool(queued and now and (now - queued).total_seconds() <= RECENT_DAYS * 86400)
    if recent:
        score += RECENT_BONUS
    src = str(item.get("source") or "").strip()
    source_known = bool(src)
    if source_known:
        score += KNOWN_SOURCE_BONUS
    seen = first_seen.get(src) if src else None
    novel = bool(seen and (now - seen).total_seconds() <= NOVEL_DAYS * 86400)
    if novel:
        score += NOVEL_BONUS
    return {"impact": impact or "none", "source": src,
            "source_known": source_known, "novel_source": novel,
            "recent": recent, "score": score}


def _decide(item: dict) -> dict:
    """字段规则 → (machine_decision, reason_code, reason)。不调用模型。"""
    bad = bool(item.get("_jev_bad"))
    impact = item.get("impact") or ""
    conf = item.get("conf")
    if bad:
        return {"machine_decision": "needs_evidence",
                "reason_code": "bad_original_jev",
                "reason": "original_jev 不是合法 JSON 对象，需补证/重判"}
    if impact == "high":
        return {"machine_decision": "needs_evidence",
                "reason_code": "high_impact_needs_evidence",
                "reason": "impact=high（高风险结论），机器预审不代决，需人工/证据"}
    if conf is None or not isinstance(conf, (int, float)):
        return {"machine_decision": "needs_evidence",
                "reason_code": "conf_missing",
                "reason": "conf 缺失，无法判断判定稳定性，需补证"}
    if not item.get("source_known"):
        return {"machine_decision": "needs_evidence",
                "reason_code": "source_unknown",
                "reason": "news 表无对应来源/来源缺失，证据链不完整"}
    if conf <= 0.4:
        return {"machine_decision": "reject",
                "reason_code": "low_conf_reject",
                "reason": "conf=%.2f<=0.40（低于置信下限），机器建议驳回自动判定" % conf}
    if impact == "low":
        return {"machine_decision": "accept",
                "reason_code": "low_impact_accept",
                "reason": "impact=low 且 conf=%.2f>0.40，低风险，机器建议采纳" % conf}
    if conf >= 0.6:
        return {"machine_decision": "accept",
                "reason_code": "stable_judgement_accept",
                "reason": "impact=%s 且 conf=%.2f>=0.60，判定稳定，机器建议采纳"
                          % (impact, conf)}
    return {"machine_decision": "needs_evidence",
            "reason_code": "mid_conf_needs_evidence",
            "reason": "conf=%.2f 落在 0.40~0.60 中间带（impact=%s），需补证再判"
                      % (conf, impact)}


def _sanitize_title(text: str, limit: int = 60) -> str:
    t = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    return t[:limit]


def build_pre_review(db_path=None, now=None, limit=MACHINE_BUDGET,
                     samples: int = 5) -> dict:
    """只读生成机器预审建议（不写库/不写文件）。返回 items+counts 等。"""
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("--limit 必须是正整数")
    limit_requested = limit
    effective = min(limit_requested, MAX_LIMIT)
    limit_capped = limit_requested > MAX_LIMIT
    clk = _now_naive(now)
    con = _connect_ro(db_path or DEFAULT_DB)
    try:
        pending_rows = con.execute(
            "SELECT id, queued_at, conf, bias, title, original_jev "
            "FROM jev_review_queue WHERE status=?", (PENDING_STATUS,)
        ).fetchall()
        news_map = {r["id"]: r for r in con.execute(
            "SELECT id, source, ts, jev FROM news WHERE id IN "
            "(SELECT id FROM jev_review_queue WHERE status=?)", (PENDING_STATUS,))}
        first_seen = _source_first_seen(con)
        total_rows = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
        status_rows = {r[0]: r[1] for r in con.execute(
            "SELECT status, COUNT(*) FROM jev_review_queue GROUP BY status")}
    finally:
        con.close()

    items = []
    for r in pending_rows:
        nrow = news_map.get(r["id"])
        _obj, bad = _parse_json_obj(r["original_jev"])
        item = {
            "id": r["id"], "queued_at": r["queued_at"], "conf": r["conf"],
            "bias": r["bias"], "title": r["title"],
            "original_jev": r["original_jev"], "_jev_bad": bad,
            "news_jev": nrow["jev"] if nrow else None,
            "source": (nrow["source"] if nrow else "") or "",
            "news_ts": (nrow["ts"] if nrow else None),
        }
        item.update(_score(item, clk, first_seen))
        item.update(_decide(item))
        items.append(item)

    # 稳定排序：score desc → queued_at desc → id asc
    items.sort(key=lambda x: str(x.get("id") or ""))
    items.sort(key=lambda x: _parse_time(x.get("queued_at")) or dt.datetime.min,
               reverse=True)
    items.sort(key=lambda x: x["score"], reverse=True)
    processed = items[:effective]
    counts = {d: 0 for d in DECISIONS}
    for it in processed:
        counts[it["machine_decision"]] += 1
    samples_n = max(0, min(MAX_LIMIT, int(samples)))
    return {
        "ok": True, "rule_version": RULE_VERSION, "kind": KIND,
        "mode": "dry_run", "db": str(Path(db_path or DEFAULT_DB)),
        "generated_at": clk.strftime("%Y-%m-%d %H:%M:%S"),
        "limit_requested": limit_requested, "limit": effective,
        "limit_capped": limit_capped,
        "total_rows": total_rows, "status_counts": status_rows,
        "pending": len(items), "processed": len(processed),
        "decisions": counts,
        "samples": [_public_item(x) for x in processed[:samples_n]],
        "items": [_public_item(x) for x in processed],
        "db_written": False, "notes": [
            "只读：本工具不写 news.db、不改 status；machine_decision 仅为机器建议",
            "人工 50 档裁决仍走 POST /api/jev/review/resolve（唯一写入口）",
        ],
    }


def _public_item(it: dict) -> dict:
    return {
        "id": it["id"], "machine_decision": it["machine_decision"],
        "score": it["score"], "reason_code": it["reason_code"],
        "reason": it["reason"], "impact": it["impact"], "conf": it["conf"],
        "source": it["source"], "source_known": it["source_known"],
        "novel_source": it["novel_source"], "recent": it["recent"],
        "queued_at": it["queued_at"], "title": _sanitize_title(it.get("title")),
    }


def _default_out_path(clk: dt.datetime) -> Path:
    return DEFAULT_OUT_DIR / ("intel_pre_review_%s.jsonl"
                              % clk.strftime("%Y%m%d"))


def write_jsonl(payload: dict, out_path, db_path=None) -> Path:
    """把建议逐行写 JSONL（独立文件；拒绝写到 news.db 路径本身）。"""
    out = Path(out_path)
    db = Path(db_path or payload.get("db") or DEFAULT_DB).resolve()
    if out.resolve() == db:
        raise ValueError("输出路径不得是 news.db（本工具零写库）")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for it in payload["items"]:
            row = {"kind": KIND, "rule_version": RULE_VERSION,
                   "generated_at": payload["generated_at"], **it}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(out)
    return out


def _fmt(payload: dict, out_path=None) -> str:
    lines = [
        "[%s] %s  db=%s" % (payload["mode"].upper(), payload["rule_version"],
                            payload["db"]),
        "pending=%s 处理=%s（limit=%s%s） 总行=%s" % (
            payload["pending"], payload["processed"],
            payload["limit"],
            "，请求 %s 已封顶 200" % payload["limit_requested"]
            if payload["limit_capped"] else "",
            payload["total_rows"]),
        "决策: accept=%s reject=%s needs_evidence=%s" % (
            payload["decisions"]["accept"], payload["decisions"]["reject"],
            payload["decisions"]["needs_evidence"]),
        "只读保证: db_written=%s（不写 news.db/不改 status）" % payload["db_written"],
    ]
    if out_path:
        lines.append("产物: %s（mode=write）" % out_path)
    for s in payload["samples"]:
        lines.append("  %-14s score=%s id=%s conf=%s impact=%s source=%s reason=%s" % (
            s["machine_decision"], s["score"], s["id"], s["conf"],
            s["impact"], s["source"], s["reason_code"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        description="情报机器预审 200/日：只读 pending → 决策建议 JSONL（零写库）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="news.db 路径")
    ap.add_argument("--limit", type=int, default=MACHINE_BUDGET,
                    help="处理上限（默认/上限 200）")
    ap.add_argument("--out", default=None,
                    help="输出 JSONL 路径（默认 C:\\Temp\\intel_pre_review_<date>.jsonl）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只预览不写文件（默认行为；与 --write 互斥）")
    ap.add_argument("--write", action="store_true", help="写独立 JSONL（--write 才落盘）")
    ap.add_argument("--samples", type=int, default=5, help="摘要样本条数")
    ap.add_argument("--now", default=None, help="注入时钟（ISO8601，测试/回放用）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要（stdout）")
    args = ap.parse_args(argv)
    if args.dry_run and args.write:
        print(json.dumps({"ok": False, "error": "--write 与 --dry-run 互斥"},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    if args.limit < 1:
        print(json.dumps({"ok": False, "error": "--limit 必须是正整数"},
                         ensure_ascii=False), file=sys.stderr)
        return 1
    try:
        payload = build_pre_review(db_path=args.db, now=args.now,
                                   limit=args.limit, samples=args.samples)
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1

    out_path = None
    if args.write:
        try:
            target = Path(args.out) if args.out else _default_out_path(
                _now_naive(args.now))
            out_path = write_jsonl(payload, target, db_path=args.db)
            payload["mode"] = "write"
            payload["out"] = str(out_path)
            payload["written"] = len(payload["items"])
        except (OSError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
                             ensure_ascii=False), file=sys.stderr)
            return 2
    else:
        payload["mode"] = "dry_run"
        payload["out"] = str(Path(args.out)) if args.out else None

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(_fmt(payload, out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
