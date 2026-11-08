#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
learning_status_flow.py — 学习单元状态机（jsonl 真源层）。

背景（2026-11-08 老田批准接入）:
    learning_health #4 红灯根因：fact_learning_unit 只有 draft=2310，
    无 pending_review 族状态，健康报表只能回退估算。本工具在
    30_知识单元/*.jsonl（状态真源）上实现状态流转，ETL 增量同步进
    learning.db 后，health #4 走 DB 正路。

状态词表:
    draft           新采集未分诊
    pending_review  已入审阅队列（分诊后等人审）
    pass            人审通过
    revise          人审要求改写
    reject          人审否决
    expired         T 类 pit+3d 时效过期（保留 status_prev 可溯源）
    empty_claim     空转写占位（不进队列，仅标记）

合法迁移:
    draft -> pending_review | empty_claim
    pending_review -> pass | revise | reject | expired
    pass | revise -> expired          （T 类时效过期）
    expired / pass / reject 为终态（expired 可 --undo 单条回退）

命令:
    triage   draft -> pending_review（claim 非空）/ empty_claim（空转写）
    expire   unit_class=T 且 expire_at < now 且未终态 -> expired
    review   按映射文件落人审结论：jsonl 每行 {"unit_id":..., "action":"pass|revise|reject", "note":可选}
    show     只读打印各状态计数（不改文件）

用法:
    python tools/kb_corpus/learning_status_flow.py --project 视频号学习_北斗股术1688 triage
    python tools/kb_corpus/learning_status_flow.py --project ... triage --write
    python tools/kb_corpus/learning_status_flow.py --project ... review --from-map decisions.jsonl --write
    python tools/kb_corpus/learning_status_flow.py --project ... expire --write
    python tools/kb_corpus/learning_status_flow.py --project ... show

安全约束:
    默认 dry-run；--write 才落盘，落盘前自动在同目录留
    <名>.bak-YYYYMMDD-HHMM 备份；非法迁移整批拒止（不熔断半批）；
    只动 status 族字段，绝不改 claim/evidence 等内容字段。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from typing import Dict, List, Optional, Tuple

KB_ROOT = r"C:/项目/知识库"
UNIT_DIR = os.path.join(KB_ROOT, "30_知识单元")

STATES = ("draft", "pending_review", "pass", "revise", "reject", "expired", "empty_claim")
TERMINAL = ("expired", "pass", "reject")

TRANSITIONS: Dict[Tuple[str, str], bool] = {
    ("draft", "pending_review"): True,
    ("draft", "empty_claim"): True,
    ("pending_review", "pass"): True,
    ("pending_review", "revise"): True,
    ("pending_review", "reject"): True,
    ("pending_review", "expired"): True,
    ("pass", "expired"): True,
    ("revise", "expired"): True,
    ("expired", "pending_review"): True,   # 仅 --undo 使用
}

PENDING_KEYS = ("pending_review", "pending", "待审", "review", "needs_review")


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _backup(path: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    bak = f"{path}.bak-{stamp}"
    shutil.copy2(path, bak)
    return bak


def _read_jsonl(path: str) -> List[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def _write_jsonl(path: str, rows: List[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _paths(project: str) -> List[str]:
    out = []
    for suffix in ("_units_draft.jsonl", "_units_effective.jsonl"):
        p = os.path.join(UNIT_DIR, f"{project}{suffix}")
        if os.path.exists(p):
            out.append(p)
    return out


def _parse_dt(v) -> Optional[dt.datetime]:
    if not v:
        return None
    try:
        s = str(v).replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is not None:
            d = d.astimezone().replace(tzinfo=None)
        return d
    except ValueError:
        return None


def _apply_transition(rec: dict, new_status: str, note: str) -> None:
    rec["status_prev"] = rec.get("status")
    rec["status"] = new_status
    rec["status_changed_at"] = _now()
    if note:
        rec["status_note"] = note


def cmd_triage(rows_by_file, write: bool, undo: bool, allow=None) -> int:
    plan: List[Tuple[str, str, str, str]] = []   # (file, unit_id, old, new)
    for path, rows in rows_by_file.items():
        for r in rows:
            uid = r.get("unit_id", "?")
            if allow is not None and uid not in allow:
                continue
            cur = (r.get("status") or "draft")
            uid = r.get("unit_id", "?")
            if undo:
                if cur == "expired" and r.get("status_prev"):
                    plan.append((path, uid, cur, r["status_prev"]))
                continue
            claim = (r.get("claim") or "").strip()
            if not claim and r.get("is_empty_claim") is not False and not claim:
                # 空转写占位：claim 为空即视为占位行
                if cur == "draft":
                    plan.append((path, uid, cur, "empty_claim"))
                continue
            if cur == "draft":
                plan.append((path, uid, cur, "pending_review"))

    if not plan:
        print("triage: 无可迁移单元（全部已分诊或为终态）")
        return 0

    dist: Dict[str, int] = {}
    for _, _, _, new in plan:
        dist[new] = dist.get(new, 0) + 1
    print(f"triage 计划: {len(plan)} 条迁移，分布 {dist}")
    for path, uid, old, new in plan[:5]:
        print(f"  {os.path.basename(path)}: {uid}  {old} -> {new}")
    if len(plan) > 5:
        print(f"  ... 共 {len(plan)} 条")

    if not write:
        print("(dry-run，--write 落盘；落盘前自动备份)")
        return 0

    # 落盘：同 unit_id 在多文件中保持一致迁移
    by_uid: Dict[str, List[Tuple[str, str]]] = {}
    for path, uid, old, new in plan:
        by_uid.setdefault(uid, []).append((path, new))
    touched: List[str] = []
    applied = 0
    for path, rows in rows_by_file.items():
        changed = False
        for r in rows:
            hits = by_uid.get(r.get("unit_id"))
            if not hits:
                continue
            for p, new in hits:
                if p == path:
                    _apply_transition(r, new, "triage" if new != "empty_claim" else "triage(empty)")
                    applied += 1
                    changed = True
        if changed:
            touched.append(path)
    for path in touched:
        _backup(path)
        _write_jsonl(path, rows_by_file[path])
    print(f"triage 落盘: {applied} 条已迁移，备份 {len(touched)} 个文件")
    return 0


def cmd_expire(rows_by_file, write: bool, now: Optional[dt.datetime]) -> int:
    plan = []
    for path, rows in rows_by_file.items():
        for r in rows:
            cur = (r.get("status") or "draft")
            if cur in ("reject", "expired"):
                continue
            if (r.get("unit_class") or "") != "T":
                continue
            exp = _parse_dt(r.get("expire_at"))
            if exp and now and exp < now:
                plan.append((path, r.get("unit_id", "?"), cur, "expired"))
    if not plan:
        print("expire: 无 T 类过期单元（或均已是终态）")
        return 0
    print(f"expire 计划: {len(plan)} 条 -> expired")
    for path, uid, old, new in plan[:5]:
        print(f"  {os.path.basename(path)}: {uid}  {old} -> {new}")
    if len(plan) > 5:
        print(f"  ... 共 {len(plan)} 条")
    if not write:
        print("(dry-run，--write 落盘)")
        return 0
    by_uid: Dict[str, List[Tuple[str, str]]] = {}
    for path, uid, old, new in plan:
        by_uid.setdefault(uid, []).append((path, new))
    for path, rows in rows_by_file.items():
        changed = False
        for r in rows:
            hits = by_uid.get(r.get("unit_id"))
            if not hits:
                continue
            for p, new in hits:
                if p == path:
                    _apply_transition(r, new, "expire(T 类 pit+3d)")
                    changed = True
        if changed:
            _backup(path)
        if changed:
            _write_jsonl(path, rows)
    print(f"expire 落盘: {len(plan)} 条 -> expired")
    return 0


def cmd_review(rows_by_file, from_map: str, write: bool) -> int:
    if not from_map or not os.path.exists(from_map):
        print(f"[错误] --from-map 文件不存在: {from_map}", file=sys.stderr)
        return 2
    decisions = _read_jsonl(from_map)
    plan = []
    illegal = []
    unknown = []
    index = {}
    for path, rows in rows_by_file.items():
        for r in rows:
            index.setdefault(r.get("unit_id"), (path, r))
    for d in decisions:
        uid = d.get("unit_id")
        action = (d.get("action") or "").strip()
        if uid not in index:
            unknown.append(uid)
            continue
        if action not in ("pass", "revise", "reject"):
            illegal.append((uid, action, "action 词表外"))
            continue
        path, r = index[uid]
        cur = (r.get("status") or "draft")
        if not TRANSITIONS.get((cur, action)):
            illegal.append((uid, f"{cur}->{action}", "非法迁移"))
            continue
        plan.append((path, uid, cur, action, d.get("note", "")))
    if unknown:
        print(f"[错误] {len(unknown)} 个 unit_id 不在库中: {unknown[:5]}", file=sys.stderr)
    if illegal:
        print(f"[错误] {len(illegal)} 条非法迁移（整批拒止）:", file=sys.stderr)
        for uid, a, why in illegal[:5]:
            print(f"  {uid}: {a} ({why})", file=sys.stderr)
        return 2
    if not plan:
        print("review: 无可执行结论")
        return 0
    dist: Dict[str, int] = {}
    for _, _, _, new, _ in plan:
        dist[new] = dist.get(new, 0) + 1
    print(f"review 计划: {len(plan)} 条，分布 {dist}")
    if not write:
        print("(dry-run，--write 落盘)")
        return 0
    for path, uid, old, new, note in plan:
        _, r = index[uid]
        _apply_transition(r, new, note or f"review:{new}")
    touched = {p for p, _, _, _, _ in plan}
    for path in touched:
        _backup(path)
    for path, rows in rows_by_file.items():
        if path in touched:
            _write_jsonl(path, rows)
    print(f"review 落盘: {len(plan)} 条")
    return 0


def cmd_show(rows_by_file, allow=None) -> int:
    for path, rows in rows_by_file.items():
        dist: Dict[str, int] = {}
        total = 0
        for r in rows:
            if allow is not None and r.get("unit_id") not in allow:
                continue
            s = (r.get("status") or "draft")
            dist[s] = dist.get(s, 0) + 1
            total += 1
        pend = sum(v for k, v in dist.items() if k in PENDING_KEYS)
        note = f"（过滤范围 {total}/{len(rows)} 行）" if allow is not None else ""
        print(f"{os.path.basename(path)}: {len(rows)} 行，状态 {dist}，队列中 {pend} {note}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="学习单元状态机（jsonl 真源层，默认 dry-run）")
    ap.add_argument("--project", required=True, help="项目 ID（如 视频号学习_北斗股术1688）")
    ap.add_argument("cmd", choices=["triage", "expire", "review", "show"])
    ap.add_argument("--write", action="store_true", help="真正落盘（缺省 dry-run）")
    ap.add_argument("--from-map", default=None, help="review 命令的结论映射 jsonl")
    ap.add_argument("--undo", action="store_true", help="triage 配套：expired 回退 status_prev")
    ap.add_argument("--filter-ids", default=None,
                    help="unit_id 名单文件（一行一个，# 注释）：只对名单内单元做计划（分层分诊用）")
    ap.add_argument("--filter-class", default=None,
                    help="unit_class 白名单，逗号分隔（如 T,U）：只对该类单元做计划")
    args = ap.parse_args(argv)

    paths = _paths(args.project)
    if not paths:
        print(f"[错误] 找不到 {args.project} 的 units jsonl（30_知识单元）", file=sys.stderr)
        return 2
    rows_by_file = {p: _read_jsonl(p) for p in paths}

    # 过滤器：只收窄计划范围（allow 集合），绝不收窄 rows_by_file——
    # 落盘是整文件重写，收窄行集会丢掉名单外单元（2026-11-08 判例）。
    id_set = None
    if args.filter_ids:
        if not os.path.exists(args.filter_ids):
            print(f"[错误] --filter-ids 文件不存在: {args.filter_ids}", file=sys.stderr)
            return 2
        id_set = set()
        with open(args.filter_ids, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    id_set.add(ln)
        print(f"filter-ids: 名单 {len(id_set)} 个 unit_id")
    cls_set = None
    if args.filter_class:
        cls_set = {c.strip().upper() for c in args.filter_class.split(",") if c.strip()}
        print(f"filter-class: {sorted(cls_set)}")
    allow = None
    if id_set is not None or cls_set is not None:
        allow = set()
        for rows in rows_by_file.values():
            for r in rows:
                if (id_set is None or r.get("unit_id") in id_set) \
                        and (cls_set is None or (r.get("unit_class") or "").upper() in cls_set):
                    allow.add(r.get("unit_id"))
        print(f"过滤器生效: 计划范围收窄至 {len(allow)} 行（全文件行数不变）")

    if args.cmd == "show":
        return cmd_show(rows_by_file, allow) or 0
    if args.cmd == "triage":
        return cmd_triage(rows_by_file, args.write, args.undo, allow)
    if args.cmd == "expire":
        return cmd_expire(rows_by_file, args.write, dt.datetime.now())
    if args.cmd == "review":
        return cmd_review(rows_by_file, args.from_map, args.write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
