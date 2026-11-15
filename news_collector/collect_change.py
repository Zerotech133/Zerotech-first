# -*- coding: utf-8 -*-
"""采集类变更呈批通道（L2 → INTEL0 马超）。

背景（老田 2026-11-14 定）：
  采集环节不再上升到老田审批，L2 类变更（计划任务注册/改时间/改参数/停用、
  队列扩缩、SLA/停轮/降速阈值、新源接入、分母口径、分片调度）由情报主管马超配准，
  配准后即可执行。L1 本席自主，L3 仍归老田。

  「免人工审批」≠「免留痕」：每一次 L2 变更仍须出五要素（命令全文/影响面/
  不可逆点/替代方案/回滚预案），落本文件，记配准人与时间。

为什么要一个 CLI 而不是直接在会话里口头批：
  本席（OSINT1 马良）与马超不在同一会话，没有实时消息通道。此文件即通道——
  马超的会话用同一 CLI 读待批件、写配准结论，本席随后按结论执行。
  状态机由代码守卫，避免「口头同意但缺五要素」就执行的情况。

用法：
  python collect_change.py list                     # 列全部（可按 --status 过滤）
  python collect_change.py show CR-20261114-01      # 看单条全文
  python collect_change.py add --json payload.json  # 新建（五要素不全则拒绝入库）
  python collect_change.py decide CR-20261114-01 --status approved \
         --by "INTEL0 马超" --note "同意，按 A 方案"   # 配准/驳回
  python collect_change.py apply CR-20261114-01 --note "已注册任务，读回核对通过"
  python collect_change.py rollback CR-20261114-01 --note "撞限流升级，回滚"

只读写本文件，不碰采集器、不动调度。退出码：0 正常；2 业务拒绝（五要素不全、
状态迁移非法）；1 运行错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REQ_F = os.path.join(HERE, "collect_change_request.json")

SCHEMA = "collect_change_request/v1"
LEVELS = ("L1", "L2", "L3")
STATUSES = ("pending", "approved", "rejected", "applied", "rolled_back")
# 五要素的键与中文名（顺序即呈批件里的展示顺序）
FIVE_KEYS = (
    ("command", "命令全文"),
    ("impact", "影响面"),
    ("irreversible", "不可逆点"),
    ("alternatives", "替代方案"),
    ("rollback", "回滚预案"),
)
# 状态机：只允许这些迁移。approved 未 apply 前可被驳回；applied 后只能回滚。
TRANSITIONS = {
    "pending": {"approved", "rejected"},
    "approved": {"applied", "rejected"},
    "applied": {"rolled_back"},
    "rejected": set(),
    "rolled_back": set(),
}


# ------------------------------------------------------------------ 纯函数
def missing_five(rec):
    """返回五要素中缺失（空/空白）的键名列表。空列表 = 齐备。"""
    five = rec.get("five") or {}
    out = []
    for k, _label in FIVE_KEYS:
        v = five.get(k)
        if not isinstance(v, str) or not v.strip():
            out.append(k)
    return out


def can_transition(src, dst):
    """状态迁移是否合法。非法一律拒绝，不做「宽松放行」。"""
    return dst in TRANSITIONS.get(src, set())


def next_id(requests, today=None):
    """生成当日流水号 CR-YYYYMMDD-NN（同日顺延，不复用撤销掉的号）。"""
    day = (today or datetime.now()).strftime("%Y%m%d")
    prefix = "CR-%s-" % day
    used = [r.get("id", "") for r in requests]
    n = 0
    for rid in used:
        if rid.startswith(prefix):
            try:
                n = max(n, int(rid[len(prefix):]))
            except ValueError:
                continue
    return "%s%02d" % (prefix, n + 1)


def decide(rec, status, by, note, now=None):
    """写入配准结论（就地改 rec 并返回）。校验失败抛 ValueError。

    校验项（fail-closed）：
      1. 目标状态合法（approved / rejected）；
      2. 五要素齐备 —— 缺一项就不许批，避免「先批后补」；
      3. 配准人非空 —— 留痕必须能追到人。
    """
    status = (status or "").strip()
    if status not in ("approved", "rejected"):
        raise ValueError("decide 只接受 approved / rejected，收到 %r" % status)
    cur = rec.get("status") or "pending"
    if not can_transition(cur, status):
        raise ValueError("状态迁移非法：%s → %s" % (cur, status))
    by = (by or "").strip()
    if not by:
        raise ValueError("配准人不能为空（留痕必须可追溯）")
    if status == "approved":
        miss = missing_five(rec)
        if miss:
            raise ValueError("五要素不全，拒绝配准：缺 %s"
                             % "、".join(dict(FIVE_KEYS)[k] for k in miss))
    ts = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    rec["status"] = status
    rec["decided_at"] = ts
    rec["decided_by"] = by
    rec["decision_note"] = (note or "").strip()
    return rec


def mark(rec, status, note=None, now=None):
    """执行留痕：approved → applied → rolled_back。"""
    status = (status or "").strip()
    cur = rec.get("status") or "pending"
    if not can_transition(cur, status):
        raise ValueError("状态迁移非法：%s → %s" % (cur, status))
    ts = (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    rec["status"] = status
    rec["applied_at" if status == "applied" else "rolled_back_at"] = ts
    if note:
        rec["apply_note" if status == "applied" else "rollback_note"] = note.strip()
    return rec


def stats(requests):
    out = {s: 0 for s in STATUSES}
    for r in requests:
        s = r.get("status") or "pending"
        out[s] = out.get(s, 0) + 1
    return out


# ------------------------------------------------------------------ 存取
def load(path=None):
    p = path or REQ_F
    if not os.path.exists(p):
        return {"schema": SCHEMA, "requester": "OSINT1 马良",
                "approver": "INTEL0 马超", "requests": []}
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or not isinstance(d.get("requests"), list):
        raise ValueError("呈批件文件结构异常：%s" % p)
    return d


def save(data, path=None):
    p = path or REQ_F
    data["schema"] = SCHEMA
    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def find(requests, rid):
    for r in requests:
        if r.get("id") == rid:
            return r
    return None


# ------------------------------------------------------------------ 渲染
def render_one(rec, width=0):
    lines = ["[%s] %s  %s  (%s)" % (rec.get("id"), rec.get("status"),
                                    rec.get("title") or "", rec.get("level") or "-")]
    five = rec.get("five") or {}
    for k, label in FIVE_KEYS:
        v = (five.get(k) or "").strip()
        lines.append("  %s：%s" % (label, v if v else "（缺）"))
    if rec.get("decided_by"):
        lines.append("  配准：%s @ %s  %s" % (rec["decided_by"], rec.get("decided_at"),
                                            rec.get("decision_note") or ""))
    if rec.get("applied_at"):
        lines.append("  执行：%s  %s" % (rec["applied_at"], rec.get("apply_note") or ""))
    if rec.get("rolled_back_at"):
        lines.append("  回滚：%s  %s" % (rec["rolled_back_at"],
                                       rec.get("rollback_note") or ""))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="采集类变更呈批（L2 → INTEL0 马超）")
    ap.add_argument("--file", default=REQ_F, help="呈批件路径（默认同目录 json）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("--status", default="", help="按状态过滤")

    p = sub.add_parser("show")
    p.add_argument("id")

    p = sub.add_parser("add")
    p.add_argument("--json", required=True, help="新呈批件 payload 文件（JSON）")

    p = sub.add_parser("decide")
    p.add_argument("id")
    p.add_argument("--status", required=True, choices=["approved", "rejected"])
    p.add_argument("--by", required=True)
    p.add_argument("--note", default="")

    p = sub.add_parser("apply")
    p.add_argument("id")
    p.add_argument("--note", default="")

    p = sub.add_parser("rollback")
    p.add_argument("id")
    p.add_argument("--note", default="")

    args = ap.parse_args(argv)
    data = load(args.file)
    reqs = data["requests"]

    if args.cmd == "list":
        st = stats(reqs)
        print("呈批件：%s" % args.file)
        print("状态汇总：" + "  ".join("%s=%d" % (k, v) for k, v in st.items() if v))
        for r in reqs:
            if args.status and (r.get("status") or "pending") != args.status:
                continue
            print("  %-16s %-11s %-4s %s" % (r.get("id"), r.get("status"),
                                            r.get("level") or "-",
                                            r.get("title") or ""))
        return 0

    if args.cmd == "show":
        rec = find(reqs, args.id)
        if not rec:
            print("未找到 %s" % args.id)
            return 2
        print(render_one(rec))
        return 0

    if args.cmd == "add":
        with open(args.json, encoding="utf-8") as f:
            rec = json.load(f)
        miss = missing_five(rec)
        if miss:
            print("拒绝入库：五要素不全，缺 %s"
                  % "、".join(dict(FIVE_KEYS)[k] for k in miss))
            return 2
        rec.setdefault("level", "L2")
        if rec["level"] not in LEVELS:
            print("拒绝入库：level 必须是 %s" % "/".join(LEVELS))
            return 2
        rec.setdefault("status", "pending")
        rec["id"] = rec.get("id") or next_id(reqs)
        rec.setdefault("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        rec.setdefault("requester", data.get("requester"))
        rec.setdefault("approver", data.get("approver"))
        if find(reqs, rec["id"]):
            print("拒绝入库：id 已存在 %s" % rec["id"])
            return 2
        reqs.append(rec)
        save(data, args.file)
        print("已入库 %s（pending，待 INTEL0 马超配准）" % rec["id"])
        return 0

    rec = find(reqs, args.id)
    if not rec:
        print("未找到 %s" % args.id)
        return 2

    try:
        if args.cmd == "decide":
            decide(rec, args.status, args.by, args.note)
            print("%s → %s（配准人 %s）" % (rec["id"], rec["status"], args.by))
        elif args.cmd == "apply":
            mark(rec, "applied", args.note)
            print("%s → applied" % rec["id"])
        elif args.cmd == "rollback":
            mark(rec, "rolled_back", args.note)
            print("%s → rolled_back" % rec["id"])
    except ValueError as e:
        print("拒绝：%s" % e)
        return 2
    save(data, args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
