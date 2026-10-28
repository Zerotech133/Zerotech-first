# -*- coding: utf-8 -*-
"""通知指挥四态队列只读聚合出口 mixin（K-ORG-201 W36，2026-10-19）。

GET /api/notify/queue   零参数
    把三本在役账本聚合为「通知指挥」四态队列（review/auto/blocked/idle，
    映射走 notify_contracts.map_decision_state，单一事实源）：
      1. 决策 DB  portal/work/notification_decisions.sqlite3
         （sqlite3 URI mode=ro 只读，沿 api_daily_data/api_dataops 先例；
          sent→auto，recorded→review，attempted/gate_or_send_failed/
          unknown_after_attempt/未知→blocked 诚实呈现不猜成功）
      2. 发送流水 portal/work/push_flow.jsonl（push_flow.py FLOW_PATH）
         近 7 天 ok=0 行 → blocked（每行一条，kind 原样）；今日 ok=1 行
         按 (kind,target) 聚合 → auto（上限 50 条，超出合并为一条汇总；
         auto: 前缀 = 底座追溯账，title 标注「底座埋点」）
      3. deadline 旗标 portal/work/deadline_pending.json
         （deadline_watchdog._write_flag 结构：{generated_at, pending:[...],
          attempts}；pending 每条 → blocked，source=deadline_flag，reason
          用其自带字段拼接；文件缺失 = 合法空态不报错）
      4. 矩阵发送账 portal/work/matrix_send_log.json（live 真实发送，
         push_matrix_send._append_log 条目 {date,market,window,group,ts}）
         → auto item（evidence_lifecycle=live_sent）；
         matrix_send_preview.json（DRYRUN 预演）不进队列（演练≠真实发送），
         仅计入 meta.drills。idle 无独立账本：count 恒 0，legend 诚实标注
         「无变化留痕账本未建（R6 事件总线预留）」，不造数据。

只读纪律（沿 api_daily_data/api_capability 判例）：mode=ro / fail-visible
（一源坏不拖垮整卡，errors 独立透出，ok 仍 true）/ 参数白名单空集——带任何
查询参数返回 400（api_perf 判例）；全程不 open(...,"w")、不 sqlite 写、
不 mkdir。零新决策权：本出口只读账本，不触发任何发送/审批/重试。
证据分级（W36 验收）：决策行 evidence_lifecycle——sent/真实发送行=live_sent，
其余=drilled；送达证据走 map_delivery_evidence（sent=api_accepted，失败=
dead_letter，空=暂无凭证不硬贴）。
路径锚：REPO = env AIOS_ROOT 可覆盖（仅测试用，调用时读取，monkeypatch
.setenv 即生效），默认 C:/aios（沿 api_daily_data.py 头部先例）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import notify_contracts as nc

CST = timezone(timedelta(hours=8))

FLOW_BLOCK_WINDOW_DAYS = 7   # ok=0 阻断行回看窗口（自然日，含今日）
FLOW_AUTO_CAP = 50           # 今日 ok=1 聚合条数上限，超出合并一条汇总
MAX_ITEMS = 200              # 队列总条数上限（review 优先截断）

_STATE_ORDER = {"review": 0, "blocked": 1, "auto": 2, "idle": 3}

# decision DB category → 中文（同 notification_decision.CATEGORIES 口径，
# 本地静态映射避免 import 其供钥逻辑）
_DECISION_CAT_LABELS = {
    "os": "OS 系统通知",
    "ai_finance_daily": "固定 AI 金融日报",
    "alert": "预警",
    "watch_strategy": "盯盘策略",
    "announcement": "管理公告",
}


def _repo() -> Path:
    return Path(os.environ.get("AIOS_ROOT", "C:/aios"))


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _ts_epoch(s) -> float:
    """宽松时间戳（排序用）：解析失败归 0 沉底，不抛。"""
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


def _item(**kw) -> dict:
    # P2 分域：domain 徽章字段（owner/ops/customer）——kw 显式传入且非空
    # 才渲染到 item；缺省不出现在键集合里（旧 payload 结构零变化）
    dom = str(kw.pop("domain", "") or "")
    base = {"id": "", "state": "auto", "state_label": "", "title": "",
            "kind": "", "target": "", "created_at": "", "send_at": "",
            "source": "", "reason": "", "evidence_lifecycle": "",
            "evidence_delivery": "", "agent_source": "", "caller": "",
            "outcome": "", "basis": "", "category": "", "doc": ""}
    base.update(kw)
    if dom:
        base["domain"] = dom
    base["state_label"] = nc.QUEUE_STATE_LABELS.get(base["state"],
                                                    base["state"])
    return base


# ---------- 数据源 1：决策 DB（mode=ro） ----------

def decision_items(errors: list) -> list:
    out = []
    db = _repo() / "portal" / "work" / "notification_decisions.sqlite3"
    if not db.exists():
        errors.append("决策 DB: 不存在（首次决策前为合法空态）%s" % db)
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        rows = con.execute(
            "SELECT id, category, target_name, send_at, reason, created_at,"
            " status, outcome, agent_source, caller, basis, doc_url, doc_title"
            " FROM decisions"
        ).fetchall()
        con.close()
    except Exception as e:  # noqa: BLE001
        errors.append("决策 DB: 只读查询失败 %r" % e)
        return out
    for r in rows:
        status = str(r[6] or "")
        cat = str(r[1] or "")
        doc_url = str(r[11] or "")
        # K-ORG-203：文档引用随行携带（只存 URL+标题，未核实原样透出）
        doc = {"url": doc_url, "title": str(r[12] or "")} if doc_url else ""
        out.append(_item(
            id="decision:%s" % r[0],
            state=nc.map_decision_state(status),
            title="%s → %s" % (_DECISION_CAT_LABELS.get(
                cat, cat or "未知分类"), str(r[2] or "")),
            kind="decision:%s" % cat,
            target=str(r[2] or ""),
            created_at=str(r[5] or ""),
            send_at=str(r[3] or ""),
            source="decision_db",
            reason=str(r[4] or ""),
            # W36 验收：sent=真实发送 → live_sent；其余决策行 → drilled
            evidence_lifecycle="live_sent" if status == "sent" else "drilled",
            evidence_delivery=nc.map_delivery_evidence(None, status),
            agent_source=str(r[8] or ""),
            caller=str(r[9] or ""),
            outcome=str(r[7] or ""),
            basis=str(r[10] or ""),
            category=cat,
            doc=doc,
        ))
    return out


# ---------- 数据源 2：发送流水 push_flow.jsonl ----------

def flow_items(errors: list) -> list:
    out = []
    p = _repo() / "portal" / "work" / "push_flow.jsonl"
    if not p.exists():
        errors.append("发送流水: 不存在（尚无真实发送留痕）%s" % p)
        return out
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # noqa: PERF203
        errors.append("发送流水: 读取失败 %r" % e)
        return out
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue  # 坏行跳过（push_flow 读侧同款容错）
        if isinstance(r, dict):
            rows.append(r)
    today = _today()
    week_floor = (datetime.now() - timedelta(
        days=FLOW_BLOCK_WINDOW_DAYS - 1)).strftime("%Y-%m-%d")
    blocked, today_ok = [], {}
    clusters: dict = {}   # (kind, target) -> [代表行号, 代表行, 簇大小]
    for i, r in enumerate(rows):
        day = str(r.get("date") or str(r.get("ts") or "")[:10] or "")
        if r.get("ok"):
            if day == today:
                key = (str(r.get("kind") or ""), str(r.get("target") or ""))
                g = today_ok.setdefault(
                    key, {"n": 0, "count": 0, "ts": "", "domains": set()})
                g["n"] += 1
                try:
                    g["count"] += int(r.get("count") or 1)
                except (TypeError, ValueError):
                    g["count"] += 1
                ts = str(r.get("ts") or "")
                if ts > g["ts"]:
                    g["ts"] = ts
                dom = str(r.get("domain") or "")
                if dom:
                    g["domains"].add(dom)
        elif (not day) or day >= week_floor:
            # 近 7 天失败行全量上墙；日期不可解析也保守纳入（宁可多报）
            blocked.append((i, r))
    for i, r in blocked:
        kind = str(r.get("kind") or "")
        # 闸门主动拦截（channel=hub-gate / kind 带 |deny:，push_admin_server
        # _flow_gate_deny 判例）= 策略内正常抑制（静默窗/配额/按钮注册），
        # 按原型「不打扰：静默时段只记录决策与原因」归 idle，不冒充异常阻断；
        # 只有走到真实发送且失败的行才 = blocked（投递异常）。
        deny_at = kind.find("|deny:")
        is_gate = r.get("channel") == "hub-gate" or deny_at >= 0
        if is_gate:
            out.append(_item(
                id="flow:%d" % i,
                state="idle",
                title="%s 闸门抑制（%s）" % (kind[:deny_at] or "hub",
                                            kind[deny_at + 6:]),
                kind=kind,
                target=str(r.get("target") or ""),
                created_at=str(r.get("ts") or r.get("date") or ""),
                source="push_flow",
                reason="发送被闸门策略内拦截（近 %d 天回看）：channel=%s——静默窗/"
                       "配额/按钮注册校验等主动抑制，非投递异常"
                       % (FLOW_BLOCK_WINDOW_DAYS, r.get("channel") or ""),
                evidence_delivery="",
                domain=str(r.get("domain") or ""),
            ))
            continue
        # 方案 A 投影折叠（2026-10-20 治理执行）：同 (kind, target) 失败簇
        # 合并为簇代表条（取最新一行），cluster_size 记簇内条数。原始流水账
        # 逐行永久保留，出队靠 FLOW_BLOCK_WINDOW_DAYS 天然 T+7 滚窗，投影
        # 零写路径、完全可逆。
        key = (kind, str(r.get("target") or ""))
        prev = clusters.get(key)
        if prev is None:
            clusters[key] = [i, r, 1]
        else:
            ts = str(r.get("ts") or r.get("date") or "")
            rep_ts = str(prev[1].get("ts") or prev[1].get("date") or "")
            if _ts_epoch(ts) > _ts_epoch(rep_ts):
                prev[0], prev[1] = i, r
            prev[2] += 1
    for (kind, target), (i, r, size) in clusters.items():
        out.append(_item(
            id="flow:%d" % i,
            state="blocked",
            title="%s 投递失败" % (kind or "未知类型"),
            kind=kind,
            target=target,
            created_at=str(r.get("ts") or r.get("date") or ""),
            source="push_flow",
            reason="发送流水 ok=0（近 %d 天回看）：channel=%s tenant=%s；"
                   "同 kind+target 失败簇共 %d 条，T+7 滚窗逐条自动出队，"
                   "逐行明细见原始账 push_flow.jsonl"
                   % (FLOW_BLOCK_WINDOW_DAYS, r.get("channel") or "",
                      r.get("tenant") or "", size),
            evidence_delivery="dead_letter",
            domain=str(r.get("domain") or ""),
            cluster_size=size,
        ))
    groups = sorted(today_ok.items(), key=lambda kv: -_ts_epoch(kv[1]["ts"]))
    for (kind, target), g in groups[:FLOW_AUTO_CAP]:
        label = "底座埋点 · %s" % kind if kind.startswith("auto:") else kind
        doms = g.get("domains") or set()
        out.append(_item(
            id="flowauto:%s|%s" % (kind, target),
            state="auto",
            title="%s 已发送" % (label or "未知类型"),
            kind=kind,
            target=target,
            created_at=g["ts"],
            source="push_flow",
            reason="今日已发送 %d 次（count 合计 %d）" % (g["n"], g["count"]),
            evidence_lifecycle="live_sent",
            evidence_delivery="api_accepted",
            domain=",".join(sorted(doms)) if doms else "",
        ))
    if len(groups) > FLOW_AUTO_CAP:
        rest = groups[FLOW_AUTO_CAP:]
        out.append(_item(
            id="flowauto:merged",
            state="auto",
            title="今日其余发送汇总（%d 组合并）" % len(rest),
            target="多目标",
            created_at=max(g["ts"] for _, g in rest),
            source="push_flow",
            reason="超出队列单类展示上限 %d：其余 %d 组 (kind,target) 合并计，"
                   "kinds=%s" % (FLOW_AUTO_CAP, len(rest),
                                 ",".join(sorted({k for (k, _t), _ in rest}))[:200]),
            evidence_lifecycle="live_sent",
            evidence_delivery="api_accepted",
        ))
    return out


# ---------- 数据源 3：deadline 旗标 ----------

def deadline_items(errors: list) -> list:
    """pending 每条 → blocked；文件缺失 = 合法空态（巡检全部就绪/尚无巡检）。"""
    out = []
    p = _repo() / "portal" / "work" / "deadline_pending.json"
    if not p.exists():
        return out
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:  # noqa: PERF203
        errors.append("deadline 旗标: 读取失败（损坏不吞）%r" % e)
        return out
    pending = data.get("pending") if isinstance(data, dict) else None
    for it in pending or []:
        if not isinstance(it, dict):
            continue
        mkt = str(it.get("market") or "")
        win = str(it.get("window") or "")
        out.append(_item(
            id="deadline:%s:%s" % (mkt, win),
            state="blocked",
            title="数据锚到点未就绪 · %s / %s" % (mkt or "?", win or "?"),
            kind="deadline_watch",
            target=mkt,
            created_at=str(data.get("generated_at") or ""),
            send_at=str(it.get("deadline") or ""),
            source="deadline_flag",
            reason="锚 %s 状态 %s（%s），截止 %s，连续未就绪 %s 次%s" % (
                it.get("anchor") or "?", it.get("status") or "?",
                it.get("detail") or "无详情", it.get("deadline") or "?",
                it.get("attempts", 0),
                "，已升级 L3 红卡" if it.get("escalated") else ""),
        ))
    return out


# ---------- 数据源 4：矩阵发送账（live log）+ 预演账（drills） ----------

def matrix_items(errors: list):
    """返回 (items, drills)：live log 条目 → auto item；
    preview（DRYRUN）不进队列，仅计 drills（would_send+skipped 全账）。"""
    out, drills = [], 0
    log_p = _repo() / "portal" / "work" / "matrix_send_log.json"
    if log_p.exists():
        try:
            data = json.loads(log_p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:  # noqa: PERF203
            errors.append("矩阵发送账: 读取失败 %r" % e)
            data = None
        if isinstance(data, list):
            for i, e in enumerate(data):
                if not isinstance(e, dict):
                    continue
                mkt = str(e.get("market") or "")
                out.append(_item(
                    id="matrixlog:%d" % i,
                    state="auto",
                    title="矩阵 live 发送 · %s / %s"
                          % (mkt or "?", e.get("window") or "?"),
                    kind="matrix:%s" % (mkt or "unknown"),
                    target=str(e.get("group") or ""),
                    created_at=str(e.get("ts") or ""),
                    send_at=str(e.get("ts") or ""),
                    source="matrix_log",
                    reason="live 门禁生效的真实发送留痕（%s 市场 %s 窗口）"
                           % (mkt or "?", e.get("window") or "?"),
                    evidence_lifecycle="live_sent",
                    evidence_delivery="api_accepted",
                    category="matrix",
                ))
        elif data is not None:
            errors.append("矩阵发送账: 结构异常（应为数组）")
    prev_p = _repo() / "portal" / "work" / "matrix_send_preview.json"
    if prev_p.exists():
        try:
            prev = json.loads(prev_p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:  # noqa: PERF203
            errors.append("矩阵预演账: 读取失败 %r" % e)
        else:
            if isinstance(prev, dict):
                drills = (len(prev.get("would_send") or [])
                          + len(prev.get("skipped") or []))
            else:
                errors.append("矩阵预演账: 结构异常（应为对象）")
    return out, drills


def notify_queue_payload() -> dict:
    """四态队列聚合 payload（纯读，一源坏不拖垮整卡）。"""
    errors: list = []
    decisions = decision_items(errors)
    flow = flow_items(errors)
    deadlines = deadline_items(errors)
    matrix, drills = matrix_items(errors)
    items = decisions + flow + deadlines + matrix
    states = {s: 0 for s in nc.QUEUE_STATES}
    for it in items:
        if it["state"] in states:
            states[it["state"]] += 1
    # review 优先 → blocked → auto → idle，组内按时间倒序；总条数上限 200
    items.sort(key=lambda it: (_STATE_ORDER.get(it["state"], 9),
                               -_ts_epoch(it.get("send_at")
                                          or it.get("created_at") or "")))
    truncated = len(items) > MAX_ITEMS
    items = items[:MAX_ITEMS]
    ch = nc.load_channels()
    flow_blocked = [it for it in flow if it["state"] == "blocked"]
    raw_blocked = (sum(int(it.get("cluster_size") or 1) for it in flow_blocked)
                   + sum(1 for it in decisions if it["state"] == "blocked")
                   + sum(1 for it in deadlines if it["state"] == "blocked"))
    return {
        "ok": True,
        "endpoint": "/api/notify/queue",
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "states": states,
        "items": items,
        "channels": ch["channels"],
        "channels_errors": ch["errors"],
        "meta": {
            "drills": drills,
            "truncated": truncated,
            "blocked_archive": {
                # 方案 A（T+7 过期归档，2026-10-20 治理执行）：push_flow
                # ok=0 行按近 7 天回看自然滚窗，超窗自动出队；原始账永久
                # 保留不删不改，投影零写路径。blocked 展示按同 kind+target
                # 折叠为簇代表，raw_blocked 保存折叠前真实总量。
                "rule": "T+7：push_flow ok=0 行近 %d 天回看，超窗自动出队；"
                        "原始账永久保留，投影零写路径" % FLOW_BLOCK_WINDOW_DAYS,
                "raw_blocked": raw_blocked,
                "cluster_count": len(flow_blocked),
                "clustered_source": "push_flow",
                "cluster_key": "kind+target",
            },
            "sources": {
                "decision_db": len(decisions),
                "push_flow": len(flow),
                "deadline_flag": len(deadlines),
                "matrix_log": len(matrix),
            },
        },
        "legend": {
            "queue_states": dict(nc.QUEUE_STATE_LABELS),
            "evidence_lifecycle": dict(nc.EVIDENCE_LIFECYCLE_LABELS),
            "evidence_delivery": dict(nc.EVIDENCE_DELIVERY_LABELS),
            "idle_note": "idle 无独立「无变化留痕」账本（R6 事件总线预留），"
                         "不造数据；闸门策略内抑制（静默窗/配额，"
                         "channel=hub-gate）按不打扰归 idle",
        },
        "errors": errors,
    }


class Handler:
    def notify_queue(self, qs):
        # 零参数出口：参数白名单空集，带任何参数走 400（api_perf 判例）；
        # qs 双形态兼容（str=portal_api 真实分发 / dict=stub 测试，api_perf 教训）
        if isinstance(qs, str):
            bad = bool(qs.strip())
        else:
            bad = bool(qs)
        if bad:
            self._json({"ok": False, "error": "本端点零参数"}, 400)
            return
        self._json(notify_queue_payload())
