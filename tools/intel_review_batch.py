# -*- coding: utf-8 -*-
"""情报 50 条人工复核批次生成器（只读；WO-INTEL-REVIEWBATCH-20261031）。

执行拍板 2/4 的人工侧配套：从 `jev_review_queue` 的 pending（当前 250）中选
≤50 条，生成**人工复核批次**（markdown 清单 + JSONL 批量件），供人工逐条裁决。

原则：
  - 只读 news.db（`mode=ro` + `PRAGMA query_only=ON`）：不写库、不改 status、
    不 resolve、不调用模型、不联网；
  - 选批优先级：machine 预审建议（needs_evidence/reject 优先）叠加其 score
    （machine_score 已含 high impact/低 conf/近期/来源因素）；
    机器 JSONL 缺失时**明确降级**：仅按 impact/conf/时效兜底排序，
    machine_decision 输出 `unavailable`，markdown/JSON 均带降级说明；
  - 输出只写 `C:/Temp`（或 --out 目录）：`intel_review_batch_<date>.md` +
    `intel_review_batch_<date>.jsonl`；默认 dry-run，`--write` 才落盘；
  - 人工裁决仍走唯一写入口 `POST /api/jev/review/resolve`（本工具不代写）。

用法：
    python tools/intel_review_batch.py --dry-run
    python tools/intel_review_batch.py --json
    python tools/intel_review_batch.py --write
    python tools/intel_review_batch.py --limit 50 --machine C:\\Temp\\intel_pre_review_20261031.jsonl
退出码：0=成功（含降级）；1=参数/数据错误；2=写批次文件失败。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import intel_pre_review as PRE  # noqa: E402  复用同一套打分/解析（单点口径）

DEFAULT_DB = ROOT / "news_collector" / "news.db"
DEFAULT_OUT_DIR = Path(r"C:\Temp")
DEFAULT_MACHINE_JSONL = Path(r"C:\Temp\intel_pre_review_20261031.jsonl")
DEFAULT_MASTER = ROOT / "quant_backtest" / "data" / "ashare" / "_names.json"

RULE_VERSION = "intel-review-batch/v1"
KIND = "human_review_batch"
MAX_LIMIT = 50                       # 人工 50 档（拍板 2/4）
DECISION_OPTIONS = ("correct", "wrong", "uncertain")
MACHINE_BONUS = {"needs_evidence": 30, "reject": 20, "accept": 0}
JEV_RAW_KEEP = 800


def _load_rows(db_path, limit_requested):
    """只读 pending 250 + news 关联，返回带打分的候选列表（不排序）。"""
    clk = PRE._now_naive(None)
    con = PRE._connect_ro(db_path)
    try:
        pending_rows = con.execute(
            "SELECT id, queued_at, conf, bias, title, stocks, original_jev "
            "FROM jev_review_queue WHERE status=?", (PRE.PENDING_STATUS,)
        ).fetchall()
        news_map = {r["id"]: r for r in con.execute(
            "SELECT id, source, ts, jev FROM news WHERE id IN "
            "(SELECT id FROM jev_review_queue WHERE status=?)",
            (PRE.PENDING_STATUS,))}
        first_seen = PRE._source_first_seen(con)
        status_counts = {r[0]: r[1] for r in con.execute(
            "SELECT status, COUNT(*) FROM jev_review_queue GROUP BY status")}
        total_rows = con.execute("SELECT COUNT(*) FROM jev_review_queue"
                                 ).fetchone()[0]
    finally:
        con.close()
    return clk, pending_rows, news_map, first_seen, status_counts, total_rows


def _jev_view(original_jev, news_jev):
    """JEV 结构化视图（人工复核要看的方向/置信/影响/理由/公司）。"""
    obj, bad = PRE._parse_json_obj(original_jev)
    if not obj and news_jev:
        obj2, bad2 = PRE._parse_json_obj(news_jev)
        if obj2:
            obj, bad = obj2, bad and bad2
    raw = str(original_jev or news_jev or "")
    return {
        "bias": str(obj.get("bias") or ""),
        "conf": obj.get("conf"),
        "impact": str(obj.get("impact") or "").lower(),
        "event_type": str(obj.get("event_type") or ""),
        "event_cn": str(obj.get("event_cn") or ""),
        "scope": str(obj.get("scope") or ""),
        "src": str(obj.get("src") or ""),
        "reason": str(obj.get("reason") or "")[:200],
        "companies": obj.get("companies") or [],
        "bad_json": bool(bad),
    }, raw[:JEV_RAW_KEEP]


def load_machine_jsonl(path) -> tuple[dict, dict]:
    """读机器预审 JSONL → ({id: record}, meta)。缺文件/坏行 = 明确降级信号。"""
    p = Path(path)
    meta = {"path": str(p), "available": False, "lines": 0, "bad_lines": 0,
            "degraded": True, "degrade_reason": ""}
    if not p.is_file():
        meta["degrade_reason"] = "机器预审 JSONL 不存在：%s" % p
        return {}, meta
    records = {}
    lines = 0
    bad = 0
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        meta["degrade_reason"] = "机器预审 JSONL 不可读：%s" % exc
        return {}, meta
    for line in text.splitlines():
        if not line.strip():
            continue
        lines += 1
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(row, dict) or not row.get("id"):
            bad += 1
            continue
        if str(row.get("machine_decision") or "") not in PRE.DECISIONS:
            bad += 1
            continue
        records[str(row["id"])] = row
    meta.update({"lines": lines, "bad_lines": bad,
                 "available": bool(records)})
    if records:
        meta["degraded"] = False
        if bad:
            meta["degrade_reason"] = "机器预审 JSONL 有 %d 行不可解析，已跳过" % bad
    else:
        meta["degrade_reason"] = "机器预审 JSONL 无有效记录（lines=%d, bad=%d）" % (
            lines, bad)
    return records, meta


def build_batch(db_path=None, machine_path=None, limit=MAX_LIMIT,
                samples: int = 3, now=None) -> dict:
    """只读生成人工复核批次（不写库/不写文件）。"""
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("--limit 必须是正整数")
    limit_requested = limit
    effective = min(limit_requested, MAX_LIMIT)
    limit_capped = limit_requested > MAX_LIMIT
    clk = PRE._now_naive(now)
    db = Path(db_path or DEFAULT_DB)
    _clk2, pending_rows, news_map, first_seen, status_counts, total_rows = \
        _load_rows(db, limit_requested)
    machine, machine_meta = load_machine_jsonl(
        machine_path or DEFAULT_MACHINE_JSONL)

    rows = []
    for r in pending_rows:
        nrow = news_map.get(r["id"])
        item = {
            "id": r["id"], "queued_at": r["queued_at"], "conf": r["conf"],
            "bias": r["bias"], "title": r["title"],
            "stocks": r["stocks"], "original_jev": r["original_jev"],
            "news_jev": nrow["jev"] if nrow else None,
            "source": (nrow["source"] if nrow else "") or "",
            "news_ts": (nrow["ts"] if nrow else None),
        }
        fallback = PRE._score(item, clk, first_seen)  # 机器缺失时的兜底口径
        mrec = machine.get(str(r["id"]))
        if mrec:
            machine_available = True
            machine_score = int(mrec.get("score") or 0)
            machine_decision = str(mrec.get("machine_decision"))
            machine_reason_code = str(mrec.get("reason_code") or "")
            machine_reason = str(mrec.get("reason") or "")
            base = machine_score
        else:
            machine_available = False
            machine_score = fallback["score"]
            machine_decision = "unavailable"
            machine_reason_code = ("machine_jsonl_missing"
                                   if not machine_meta["available"]
                                   else "not_in_machine_top")
            machine_reason = (machine_meta["degrade_reason"]
                              if not machine_meta["available"]
                              else "不在机器预审 200 档内（未生成建议）")
            base = fallback["score"]
        priority = base + MACHINE_BONUS.get(machine_decision, 0)
        jev, jev_raw = _jev_view(item["original_jev"], item["news_jev"])
        rows.append({
            "id": r["id"], "source": item["source"],
            "news_ts": item["news_ts"], "queued_at": r["queued_at"],
            "title": PRE._sanitize_title(r["title"]),
            "bias": r["bias"], "conf": r["conf"], "impact": jev["impact"] or "none",
            "jev": jev, "jev_raw": jev_raw,
            "machine_decision": machine_decision,
            "machine_reason_code": machine_reason_code,
            "machine_reason": machine_reason,
            "machine_score": machine_score,
            "machine_available": machine_available,
            "batch_priority": priority,
            "fallback_score": fallback["score"],
        })
    # 稳定排序：priority desc → queued_at desc → id asc
    rows.sort(key=lambda x: str(x.get("id") or ""))
    rows.sort(key=lambda x: PRE._parse_time(x.get("queued_at")) or dt.datetime.min,
              reverse=True)
    rows.sort(key=lambda x: x["batch_priority"], reverse=True)
    selected = rows[:effective]
    batch_id = "IRB-%s" % clk.strftime("%Y%m%d-%H%M%S")
    for idx, rec in enumerate(selected, 1):
        rec["batch_rank"] = idx
        rec["batch_id"] = batch_id
        rec["kind"] = KIND
        rec["rule_version"] = RULE_VERSION
        rec["generated_at"] = clk.strftime("%Y-%m-%d %H:%M:%S")
        rec["decision_options"] = list(DECISION_OPTIONS)
        rec["pending_fields"] = {"decision": "", "reviewer": "", "note": ""}
    counts = {
        "by_impact": _count(selected, "impact"),
        "by_machine_decision": _count(selected, "machine_decision"),
        "by_bias": _count(selected, "bias"),
    }
    payload = {
        "ok": True, "mode": "dry_run", "kind": KIND,
        "rule_version": RULE_VERSION, "batch_id": batch_id,
        "generated_at": clk.strftime("%Y-%m-%d %H:%M:%S"),
        "db": str(db.resolve()), "db_written": False,
        "machine": machine_meta,
        "total_rows": total_rows, "status_counts": status_counts,
        "pending": len(rows), "selected": len(selected),
        "limit_requested": limit_requested, "limit": effective,
        "limit_capped": limit_capped,
        "counts": counts,
        "records": selected,
        "preview_records": selected[:max(0, samples)],
        "markdown": render_markdown(selected, {
            "batch_id": batch_id, "generated_at": clk.strftime("%Y-%m-%d %H:%M:%S"),
            "db": str(db.resolve()), "machine": machine_meta,
            "pending": len(rows), "selected": len(selected),
            "limit": effective, "counts": counts,
            "rule_version": RULE_VERSION,
        }),
    }
    return payload


def _count(rows, key) -> dict:
    out = {}
    for r in rows:
        k = str(r.get(key) or "")
        out[k] = out.get(k, 0) + 1
    return out


def render_markdown(records: list, meta: dict) -> str:
    lines = [
        "# 情报人工复核批次 · %s" % meta["batch_id"],
        "",
        "- 生成时间：%s" % meta["generated_at"],
        "- 规则版本：%s（只读批次，不写 news.db、不 resolve）" % meta["rule_version"],
        "- 数据库：`%s`" % meta["db"],
        "- 候选 pending：%s；本批：%s 条（预算上限 %s）" % (
            meta["pending"], meta["selected"], meta["limit"]),
    ]
    if meta["machine"].get("degraded"):
        lines.append("- ⚠️ **机器预审降级**：%s（machine_decision=unavailable，"
                     "仅按 impact/conf/时效兜底排序）"
                     % (meta["machine"].get("degrade_reason") or "不可用"))
    else:
        lines.append("- 机器预审：`%s`（可用；needs_evidence/reject 优先入选）"
                     % meta["machine"].get("path"))
    lines += ["", "## 分布", "",
              "- 影响：`%s`" % json.dumps(meta["counts"]["by_impact"],
                                          ensure_ascii=False, sort_keys=True),
              "- 机器建议：`%s`" % json.dumps(
                  meta["counts"]["by_machine_decision"], ensure_ascii=False,
                  sort_keys=True),
              "", "## 待裁决说明", "",
              "- 每条 `[ ] correct / [ ] wrong / [ ] uncertain` 三选一；",
              "- 裁决回填唯一写入口：`POST /api/jev/review/resolve`"
              "（body: id/decision/reviewer/note），或按本单 JSONL 由人工批量核对后逐条 resolve；",
              "- 本文件与 JSONL 只是**批次清单**，不代表已复核；未 resolve 的仍是 pending。",
              "", "## 批次清单", ""]
    for rec in records:
        j = rec["jev"]
        lines.append("### #%02d · %s" % (rec["batch_rank"], rec["title"] or "(无标题)"))
        lines.append("")
        lines.append("- id：`%s`　来源：`%s`　新闻时间：%s　入队：%s" % (
            rec["id"], rec["source"] or "(缺失)", rec["news_ts"] or "—",
            rec["queued_at"]))
        lines.append("- JEV：bias=%s / conf=%s / impact=%s / event=%s%s" % (
            j["bias"] or "—", j["conf"], j["impact"] or "—",
            j["event_type"] or "—",
            (" / event_cn=" + j["event_cn"]) if j["event_cn"] else ""))
        if j["scope"] or j["src"]:
            lines.append("- 范围/来源标记：scope=%s / src=%s" % (
                j["scope"] or "—", j["src"] or "—"))
        if j["reason"]:
            lines.append("- JEV 理由：%s" % j["reason"])
        if j["companies"]:
            lines.append("- 关联公司：%s" % json.dumps(
                j["companies"], ensure_ascii=False)[:300])
        lines.append("- 机器建议：**%s**（%s）%s" % (
            rec["machine_decision"], rec["machine_reason_code"] or "—",
            ("：" + rec["machine_reason"]) if rec["machine_reason"] else ""))
        lines.append("- 优先级：batch_priority=%s（机器 score=%s / fallback=%s）" % (
            rec["batch_priority"], rec["machine_score"], rec["fallback_score"]))
        lines.append("- 待裁决：`[ ] correct`（JEV 判定正确）　`[ ] wrong`（需改判）"
                     "　`[ ] uncertain`（证据不足）　复核人：______　备注：______")
        lines.append("- 回填：`POST /api/jev/review/resolve "
                     "{\"id\":\"%s\",\"decision\":\"<correct|wrong|uncertain>\","
                     "\"reviewer\":\"<复核人>\",\"note\":\"\"}`" % rec["id"])
        lines.append("")
    return "\n".join(lines)


def _preflight_records(records, master_path):
    """批次入库前质量闸；主数据缺失时明确 skip，不伪造 name 校验。"""
    import importlib.util
    mp = Path(master_path or DEFAULT_MASTER)
    if not mp.is_file():
        return {"ok": True, "skipped": True, "reason": "master_missing",
                "master": str(mp)}
    spec = importlib.util.spec_from_file_location(
        "_batch_preflight", str(TOOLS_DIR / "intel_batch_preflight.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rep = mod.check(records, mp)
    rep["skipped"] = False
    return rep


def _write_csv(payload: dict, od: Path, db: Path) -> str:
    """人工填写用 CSV：companies 用 ; 分隔、QUOTE_ALL，避免 Excel 千分位化。"""
    stamp = str(payload.get("batch_id") or "").replace("IRB-", "")
    day = stamp.split("-")[0] if stamp else dt.datetime.now().strftime("%Y%m%d")
    path = od / ("intel_review_batch_%s.csv" % day)
    if path.resolve() == db:
        raise ValueError("输出路径不得是 news.db")
    cols = ["id", "title", "bias", "conf", "impact", "machine_decision",
            "suggested_decision", "decision", "reviewer", "note", "companies"]
    mapping = {"accept": "correct", "reject": "wrong", "needs_evidence": "uncertain"}
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for rec in payload["records"]:
            jev = rec.get("jev") or {}
            comps = rec.get("companies")
            if not isinstance(comps, list):
                comps = jev.get("companies") if isinstance(jev, dict) else []
            md = rec.get("machine_decision") or "unavailable"
            w.writerow({
                "id": rec.get("id", ""), "title": rec.get("title", ""),
                "bias": rec.get("bias", ""), "conf": rec.get("conf", ""),
                "impact": rec.get("impact", ""), "machine_decision": md,
                "suggested_decision": mapping.get(md, "uncertain"),
                "decision": "", "reviewer": "老田", "note": "",
                "companies": ";".join(str(c) for c in comps or []),
            })
    return str(path)


def write_batch(payload: dict, out_dir=None, db_path=None) -> dict:
    """写 markdown+JSONL 两个批次文件（独立文件；拒绝写到 news.db）。"""
    od = Path(out_dir or DEFAULT_OUT_DIR)
    db = Path(db_path or payload.get("db") or DEFAULT_DB).resolve()
    stamp = str(payload.get("batch_id") or "").replace("IRB-", "")
    day = stamp.split("-")[0] if stamp else dt.datetime.now().strftime("%Y%m%d")
    md_path = od / ("intel_review_batch_%s.md" % day)
    jsonl_path = od / ("intel_review_batch_%s.jsonl" % day)
    for p in (md_path, jsonl_path):
        if p.resolve() == db:
            raise ValueError("输出路径不得是 news.db（本工具零写库）")
    od.mkdir(parents=True, exist_ok=True)
    tmp_md = md_path.with_suffix(".md.tmp")
    tmp_jsonl = jsonl_path.with_suffix(".jsonl.tmp")
    tmp_md.write_text(payload["markdown"], encoding="utf-8")
    with open(tmp_jsonl, "w", encoding="utf-8", newline="\n") as fh:
        for rec in payload["records"]:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp_md.replace(md_path)
    tmp_jsonl.replace(jsonl_path)
    csv_path = _write_csv(payload, od, db)
    return {"markdown": str(md_path), "jsonl": str(jsonl_path),
            "csv": csv_path, "records": len(payload["records"])}


def _fmt(payload: dict, written=None) -> str:
    m = payload["machine"]
    lines = [
        "[%s] %s  batch=%s" % (payload["mode"].upper(), payload["rule_version"],
                               payload["batch_id"]),
        "db=%s  只读(db_written=%s)" % (payload["db"], payload["db_written"]),
        "pending=%s → 本批 %s 条（limit=%s%s）" % (
            payload["pending"], payload["selected"], payload["limit"],
            "，请求 %s 已封顶 %s" % (payload["limit_requested"], MAX_LIMIT)
            if payload["limit_capped"] else ""),
        "机器预审: %s" % ("可用 %s（lines=%s bad=%s）" % (
            m.get("path"), m.get("lines"), m.get("bad_lines"))
            if not m.get("degraded") else "⚠️ 降级：%s" % m.get("degrade_reason")),
        "分布: impact=%s  machine=%s" % (
            json.dumps(payload["counts"]["by_impact"], ensure_ascii=False,
                       sort_keys=True),
            json.dumps(payload["counts"]["by_machine_decision"], ensure_ascii=False,
                       sort_keys=True)),
    ]
    if written:
        lines.append("产物：%s" % written["markdown"])
        lines.append("      %s" % written["jsonl"])
        lines.append("      %s" % written.get("csv", ""))
    for rec in payload["preview_records"]:
        lines.append("  #%02d %-14s score=%s id=%s conf=%s impact=%s source=%s" % (
            rec["batch_rank"], rec["machine_decision"], rec["batch_priority"],
            rec["id"], rec["conf"], rec["impact"], rec["source"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(
        description="情报 50 条人工复核批次：只读 pending → markdown+JSONL（不 resolve）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="news.db 路径")
    ap.add_argument("--machine", default=str(DEFAULT_MACHINE_JSONL),
                    help="机器预审 JSONL（缺省时明确降级）")
    ap.add_argument("--limit", type=int, default=MAX_LIMIT,
                    help="批次条数（默认/上限 50）")
    ap.add_argument("--out-dir", "--out", dest="out_dir",
                    default=str(DEFAULT_OUT_DIR),
                    help="批次输出目录（默认 C:\\Temp）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只预览不写文件（默认行为；与 --write 互斥）")
    ap.add_argument("--write", action="store_true",
                    help="写 markdown+JSONL 批次文件（--write 才落盘）")
    ap.add_argument("--samples", type=int, default=3, help="摘要预览条数")
    ap.add_argument("--now", default=None,
                    help="注入时钟（ISO8601，测试/回放用）")
    ap.add_argument("--master", default=str(DEFAULT_MASTER),
                    help="证券主数据（默认 quant_backtest/data/ashare/_names.json）")
    ap.add_argument("--no-preflight", dest="preflight", action="store_false",
                    default=True, help="跳过入库前 companies/去重质量闸")
    ap.add_argument("--filter-hard", action="store_true",
                    help="剔除 hard/重复记录后写干净批次（默认阻断）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（含 records）")
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
        payload = build_batch(db_path=args.db, machine_path=args.machine,
                              limit=args.limit,
                              samples=max(0, min(MAX_LIMIT, args.samples)),
                              now=args.now)
    except (RuntimeError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 1

    if args.preflight:
        pre = _preflight_records(payload["records"], args.master)
        payload["preflight"] = pre
        if not pre.get("ok", True):
            if args.filter_hard:
                bad = {str(e.get("id")) for e in pre.get("hard_errors", [])
                       if e.get("id")}
                for grp in pre.get("duplicate_titles", []) + pre.get(
                        "duplicate_hashes", []):
                    ids = [str(x) for x in grp.get("ids", [])]
                    bad.update(ids[1:])  # 每组保留第一条
                kept = [r for r in payload["records"]
                        if str(r.get("id")) not in bad]
                removed = len(payload["records"]) - len(kept)
                payload["records"] = kept
                payload["preview_records"] = kept[:max(0, args.samples)]
                payload["preflight_filtered"] = {
                    "removed": removed, "removed_ids": sorted(bad)[:200],
                    "kept": len(kept),
                }
                if not kept:
                    payload["mode"] = "blocked_preflight"
                    print("[BLOCKED] 过滤后无可用记录", file=sys.stderr)
                    return 1
            else:
                payload["mode"] = "blocked_preflight"
                payload["out"] = None
                if args.json:
                    print(json.dumps(payload, ensure_ascii=False, indent=1))
                else:
                    print("[BLOCKED] 批次质量闸硬错误 %d 项；拒绝写入人工批次"
                          % pre["counts"]["hard"])
                    for e in pre.get("hard_errors", [])[:10]:
                        print("  - %s" % e)
                return 1

    written = None
    if args.write:
        try:
            written = write_batch(payload, out_dir=args.out_dir, db_path=args.db)
            payload["mode"] = "write"
            payload["out"] = written
            payload["written"] = written["records"]
        except (OSError, ValueError) as exc:
            print(json.dumps({"ok": False, "error": "%s: %s" % (
                type(exc).__name__, exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
    else:
        payload["mode"] = "dry_run"
        payload["out"] = None

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(_fmt(payload, written))
        if payload["machine"].get("degraded"):
            print("⚠️ 机器预审降级：%s" % payload["machine"].get("degrade_reason"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
