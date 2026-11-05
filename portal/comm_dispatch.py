# -*- coding: utf-8 -*-
"""阚泽部 · 派单通道（K5.1，WO-FS-148-7）。

一句话：把「已转盯盘官，预计 5 分钟」从话术变成真投递。

通道复用（不重造）
----------------
投递走军团既有 `agent_platform/gateway/inbox_gateway.py`（WO-LEGION-S21
门面）：`submit(kind, payload, source, idempotency_key)` 落
`bridge_inbox/inbox/<id>.json`，军团侧按既有消费模式处理（田丰归口→
工位执行），处理完移入 `processed/`。回执查 `receipt(id)`。

kind 映射（用门面自带词表，不新增）
--------------------------------
    stock_consult / watch_setup → task_request（派单执行）
    complaint                   → feedback（投诉，田丰归口最高优先级）
    其余（smalltalk/unclear/…） → question（咨询位/澄清）

幂等
----
idempotency_key = 飞书 msg_id（同一条消息只投一次，飞书重推不产生双工单）。
无 msg_id 时退化为 trace_id（调试路径）。

超时升级（K5.1 设计 §2.3）
-----------------------
`sweep_overdue()` 扫 inbox 中超过 deadline_min 未处理的条目 →
调用方（bot 侧定时或下一条消息触发）发「稍等」卡；15 分钟再升级田丰。
本模块只提供判定，不自己发卡（发送仍由调用方持权，便于测试与降级）。

开关
----
`COMM_DISPATCH_ENABLED` 默认 0：关时 dispatch 返回 ok=False/reason=disabled，
调用方回 stub 话术（与 K1-K4 的开关纪律一致，线上零行为变化）。
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_AP = ROOT / "agent_platform"
if str(_AP) not in sys.path:
    sys.path.insert(0, str(_AP))

SOURCE = "feishu_comm"
DEFAULT_DEADLINE_MIN = 5
ESCALATE_AFTER_MIN = 15

# 2026-11-04 TICKET-PATROL-NOISE：hq-daily 是「A0 每日跑板」内部通知，不是客户派单。
# 它由 tools/hq_daily_run.py 每日批量写入 bridge_inbox，若进入本巡视，会在 15 分钟阈值
# 同时触发 10+ 张 stage2 卡（实测 06:31 一次 17 条），对 A0 形成卡片 burst。
# 2026-11-05：ACAD1 S1–S5 安全默认通知及其 12:52 补发提醒也是内部通知（发给席位，
# 不是客户派单），被误计入进度巡视后对 A0 反复催办；同样按 source 排除，内容仍留 inbox。
# 这里按 source 排除；内容仍保留在 inbox，供 A0 通过日报/收件箱查看。
NON_DISPATCH_SOURCES = frozenset({
    "hq-daily",
    "acad1-s1s5-safe-default-20261105",
    "acad1-s1s5-safe-default-reminder-20261105",
})

_KIND_MAP = {
    "stock_consult": "task_request",
    "watch_setup": "task_request",
    "complaint": "feedback",
    # 2026-10-26 A2-B02：涉资金诉求必须真投递人工归口，不能只靠模板说「已转」。
    "billing": "feedback",
}


def enabled():
    """派单开关：env 优先，comm_flags.json 兜底（WO-FS-152）。"""
    try:
        import comm_flags

        return comm_flags.get("dispatch")
    except Exception:  # noqa: BLE001
        return str(os.environ.get("COMM_DISPATCH_ENABLED", "0")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )


def _inbox():
    """延迟取门面模块（测试可 monkeypatch 换临时 store）。"""
    from gateway import inbox_gateway as ig

    return ig


def kind_for(intent):
    return _KIND_MAP.get(intent, "question")


def dispatch(intent_result, open_id, msg_id="", deadline_min=None):
    """真投递。→ {ok, dispatch_id, replayed, state, kind, reason, error}"""
    if not enabled():
        return {
            "ok": False,
            "reason": "disabled",
            "dispatch_id": None,
            "kind": kind_for(intent_result.get("intent")),
        }
    intent = intent_result.get("intent")
    ents = intent_result.get("entities") or {}
    trace = intent_result.get("trace_id") or ""
    dm = int(
        deadline_min
        or os.environ.get("COMM_DISPATCH_DEADLINE_MIN", DEFAULT_DEADLINE_MIN)
    )
    payload = {
        "intent": intent,
        "code": ents.get("code") or "",
        "open_id": open_id,
        "feishu_msg_id": str(msg_id or ""),
        "trace_id": trace,
        "deadline_min": dm,
        "issued_by": "kanze_comm",
    }
    idem = ("feishu-%s" % msg_id) if msg_id else ("trace-%s" % trace)
    try:
        env = _inbox().submit(
            kind_for(intent), payload, source=SOURCE, idempotency_key=idem
        )
    except Exception as ex:  # noqa: BLE001
        return {
            "ok": False,
            "reason": "submit_exception",
            "error": "%s: %s" % (type(ex).__name__, str(ex)[:120]),
            "dispatch_id": None,
            "kind": kind_for(intent),
        }
    data = env.get("data") or {}
    return {
        "ok": bool(env.get("ok")),
        "dispatch_id": data.get("id"),
        "replayed": bool(data.get("replayed")),
        "state": data.get("state"),
        "kind": kind_for(intent),
        "error": (env.get("error") or {}).get("message"),
    }


def check_receipt(dispatch_id):
    """→ {ok, state(inbox|processed|not_found)}"""
    try:
        env = _inbox().receipt(dispatch_id)
    except Exception as ex:  # noqa: BLE001
        return {
            "ok": False,
            "state": "error",
            "error": "%s: %s" % (type(ex).__name__, str(ex)[:120]),
        }
    data = env.get("data") or {}
    return {
        "ok": bool(env.get("ok")),
        "state": data.get("state", "unknown"),
        "record": data.get("record") or {},
    }


def _parse_ts(s):
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(
                str(s)[:19], fmt[: len(str(s)[:19])] if len(fmt) > 19 else fmt
            )
        except ValueError:
            continue
    return None


def sweep_overdue(now=None):
    """扫 inbox 中超 deadline 未处理的派单 → [{dispatch_id, kind, waited_min,
    escalate(bool), payload}]。只判定不发送。"""
    ig = _inbox()
    try:
        inbox_dir = Path(ig._store().inbox)
    except Exception:  # noqa: BLE001
        return []
    if not inbox_dir.exists():
        return []
    now_ts = now or time.time()
    out = []
    for f in sorted(inbox_dir.glob("*.json")):
        try:
            rec = __import__("json").loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(rec.get("source") or "") in NON_DISPATCH_SOURCES:
            continue
        ts = _parse_ts(rec.get("submitted_at"))
        if ts is None:
            continue
        waited_min = (now_ts - ts.timestamp()) / 60.0
        dm = int((rec.get("payload") or {}).get("deadline_min") or DEFAULT_DEADLINE_MIN)
        if waited_min < dm:
            continue
        out.append(
            {
                "dispatch_id": rec.get("id"),
                "kind": rec.get("kind"),
                "waited_min": round(waited_min, 1),
                "escalate": waited_min >= ESCALATE_AFTER_MIN,
                "payload": rec.get("payload") or {},
            }
        )
    return out


# ── 催办/回复闭环存储（2026-10-25，老田反馈"派单后没有催促"）────
DATA_DIR = ROOT / "portal" / "data" / "comm"
NUDGE_PATH = DATA_DIR / "dispatch_nudges.json"
REPLY_PATH = DATA_DIR / "dispatch_replies.jsonl"


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_nudges():
    try:
        return __import__("json").loads(NUDGE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_nudges(d):
    _ensure_dir()
    tmp = NUDGE_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        __import__("json").dumps(d, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    __import__("os").replace(tmp, NUDGE_PATH)


def record_of(dispatch_id):
    """→ inbox 记录 dict（不存在返回 {}）。"""
    st = check_receipt(dispatch_id)
    return (st.get("record") or {}) if st.get("ok") else {}


def requester_open_id(dispatch_id):
    return str((record_of(dispatch_id).get("payload") or {}).get("open_id") or "")


def waited_min(dispatch_id):
    rec = record_of(dispatch_id)
    ts = _parse_ts(rec.get("submitted_at"))
    if ts is None:
        return 0.0
    return (time.time() - ts.timestamp()) / 60.0


def nudge(dispatch_id, by="customer", note=""):
    """记录一次催办 → {ok,n,escalate,waited_min,state,open_id}。不发送。"""
    dispatch_id = str(dispatch_id or "").strip()
    if not dispatch_id:
        return {"ok": False, "error": "缺少工单号"}
    st = check_receipt(dispatch_id)
    rec = (st.get("record") or {}) if st.get("ok") else {}
    if not rec:
        return {"ok": False, "error": "工单不存在或已归档"}
    state = str(st.get("state") or "unknown")
    if state != "inbox":
        return {"ok": False, "error": "工单已处理（%s），无需催办" % state}
    d = _load_nudges()
    item = d.get(dispatch_id) or {"n": 0}
    n = int(item.get("n") or 0) + 1
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    item.update(
        {"n": n, "last": now, "by": str(by or ""), "note": str(note or "")[:200]}
    )
    if n == 1:
        item["first"] = now
    wait = waited_min(dispatch_id)
    escal = bool(n >= 2 or wait >= ESCALATE_AFTER_MIN)
    item["escalate"] = escal
    d[dispatch_id] = item
    _save_nudges(d)
    return {
        "ok": True,
        "dispatch_id": dispatch_id,
        "n": n,
        "escalate": escal,
        "waited_min": round(wait, 1),
        "state": state,
        "open_id": (rec.get("payload") or {}).get("open_id") or "",
        "kind": rec.get("kind") or "",
        "at": now,
    }


def nudge_info(dispatch_id):
    item = _load_nudges().get(str(dispatch_id or "").strip()) or {}
    return {
        "n": int(item.get("n") or 0),
        "last": item.get("last") or "",
        "first": item.get("first") or "",
        "escalate": bool(item.get("escalate")),
    }


def patrol_stage(dispatch_id):
    """已通知过的巡视阶段（持久化在 nudges 文件，防 bot 重启重复发）。"""
    item = _load_nudges().get(str(dispatch_id or "").strip()) or {}
    return int(item.get("stage") or 0)


def set_patrol_stage(dispatch_id, stage):
    d = _load_nudges()
    item = d.get(str(dispatch_id or "").strip()) or {"n": 0}
    item["stage"] = int(stage)
    item["stage_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    d[str(dispatch_id or "").strip()] = item
    _save_nudges(d)
    return {"ok": True, "stage": int(stage)}


def record_reply(dispatch_id, text, by=""):
    _ensure_dir()
    row = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dispatch_id": str(dispatch_id or ""),
        "by": str(by or ""),
        "text": str(text or "")[:1000],
    }
    with open(REPLY_PATH, "a", encoding="utf-8") as f:
        f.write(__import__("json").dumps(row, ensure_ascii=False) + "\n")
    return {"ok": True, "at": row["ts"]}


def replies(dispatch_id, limit=10):
    did = str(dispatch_id or "").strip()
    out = []
    try:
        with open(REPLY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = __import__("json").loads(line)
                except ValueError:
                    continue
                if str(row.get("dispatch_id")) == did:
                    out.append(row)
    except OSError:
        return []
    return out[-limit:]
