# -*- coding: utf-8 -*-
"""K-PUSH-004：交互动作统一注册表（按钮归档）。

2026-10-17 勘察实证三断点：
  - 卡片回调三条互不相通消费链：feishu_bot_ws（tag 前缀 er:*/ctl:*/dim:*）、
    api_misc 8091 HTTP（act:）、jev lark_card_handler（action:）
  - 按钮 value 三套命名并存（tag / action / act）
  - 无集中注册表，动作盘点只能全文搜码

本模块 = 交互动作单一事实源：
  - ACTIONS 内置注册（存量动作全登记，页面可启停，覆盖层持久化）
  - log_event() → work/action_log.jsonl（who/when/card/result 全审计）
  - dispatch() → bot 统一分发入口（v3 卡 value 用 {"action_id": ...}；
    老前缀 value 继续走原链，兼容存量在飞卡）
  - scan_card()/check_card() → HUB /api/send 卡片按钮校验：
    默认 warn（观察记流水不拦截），enforce 后未注册拒发

判例：WORK = portal/work（承 push_flow.py）。
"""

import datetime
import json
import os
import sys
import threading
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
LOG_PATH = os.path.join(WORK, "action_log.jsonl")
OVERLAY_PATH = os.path.join(WORK, "card_actions.json")

_LOCK = threading.Lock()

# ── 内置注册表：存量动作全登记 ──────────────────────────────
# kind: card=卡片按钮回调 / h5=H5 页面动作 / cmd=bot 文本指令 / legacy=直连未过闸
# handler: 消费链路（描述性，非可调用——老链各自分发，仅 tune_* 走 dispatch）
DEFAULT_ACTIONS = {
    # S2 签字卡（D3）：value {"tag":"approval:approve|reject|escalate","task":...}
    "approval_approve": ("签字 · 批准（S2）", "审批签字", "card", "bot ws → _handle_approval_card → approval_control"),
    "approval_reject": ("签字 · 驳回", "审批签字", "card", "同上"),
    "approval_escalate": ("签字 · 转人工/升级", "审批签字", "card", "同上"),
    # 调优闭环（v3 新链：value {"action_id":"tune_approve","req_id":...}）
    "tune_approve": (
        "审批 · 通过",
        "调优闭环",
        "card",
        "bot ws → dispatch → tune_gate.approve",
    ),
    "tune_reject": (
        "审批 · 驳回",
        "调优闭环",
        "card",
        "bot ws → dispatch → tune_gate.reject",
    ),
    # 通知指挥（W37 公告审批：value {"action_id":"decision_*","req_id":<决策id>}）
    "decision_approve": (
        "公告审批 · 确认",
        "通知指挥",
        "card",
        "bot ws → dispatch → approval_card.handle_card_action",
    ),
    "decision_reject": ("公告审批 · 驳回", "通知指挥", "card", "同上"),
    # 盯盘止盈 er 域（老链：feishu_bot_ws._handle_card tag 分支自行消费，
    # value 无 action_id 不会触达 dispatch，记账备查）
    "er:list": ("止盈 · 列表", "盯盘止盈", "card", "bot ws 老链"),
    "er:help": ("止盈 · 帮助", "盯盘止盈", "card", "bot ws 老链"),
    "er:choice": ("止盈 · 档位选择", "盯盘止盈", "card", "bot ws 老链"),
    "er:set": ("止盈 · 设定", "盯盘止盈", "card", "bot ws 老链"),
    "er:clear": ("止盈 · 清线", "盯盘止盈", "card", "bot ws 老链"),
    "er:clear2": ("止盈 · 清线确认", "盯盘止盈", "card", "bot ws 老链"),
    "er:noop": ("止盈 · 空操作", "盯盘止盈", "card", "bot ws 老链"),
    # 控制面板 ctl 域（老链：feishu_bot_ws → feishu_control，tag 分支
    # 自行消费，value 无 action_id 不会触达 dispatch，记账备查）
    "ctl:sub_toggle": ("订阅启停", "控制面板", "card", "bot ws 老链"),
    "ctl:param": ("参数档位", "控制面板", "card", "bot ws 老链"),
    "ctl:alert_ack": ("预警确认", "控制面板", "card", "bot ws 老链"),
    "ctl:alert_ignore": ("预警忽略", "控制面板", "card", "bot ws 老链"),
    "ctl:alert_ack_all": ("预警全部确认", "控制面板", "card", "bot ws 老链"),
    "ctl:tasks": ("任务面板", "控制面板", "card", "bot ws 老链"),
    "ctl:board": ("看板打开", "控制面板", "card", "bot ws 老链"),
    # 盯盘预警 jev（W3 清债 2026-10-24：已从 lark-cli 老链迁入统一分发；
    # value 兼容 {"action_id":"jev_ack"} 与老卡 {"action":"ack"} 经 bot 归一）
    "jev_ack": (
        "预警 · 已处理",
        "盯盘预警",
        "card",
        "bot ws → dispatch → jev_card_action.handle_action",
    ),
    "jev_ignore": (
        "预警 · 忽略",
        "盯盘预警",
        "card",
        "bot ws → dispatch → jev_card_action.handle_action",
    ),
    # 订阅买点（P3.11-lite 已迁移：bot ws → dispatch → 真 handler；
    # 8091 api_misc HTTP 链并行在役，两链同源 subscribe_from_card）
    "subscribe_buypoint": (
        "订阅买点",
        "量化订阅",
        "card",
        "bot ws → dispatch → subscribe_action.subscribe_from_card"
        "（8091 HTTP 链并行在役）",
    ),
    "unsubscribe_buypoint": (
        "退订买点",
        "量化订阅",
        "card",
        "bot ws → dispatch → subscribe_action.subscribe_from_card"
        "（8091 HTTP 链并行在役）",
    ),
    # 工单催办（2026-10-25：派单卡「催一下」→ 计数 + 提醒处理人）
    "dispatch_nudge": (
        "工单 · 催一下",
        "通信/工单",
        "card",
        "bot ws → dispatch → ticket_loop.nudge",
    ),
    # H5 动作（share/H5 页面）
    "subscribe_add": ("H5 · 订阅", "H5 精选页", "h5", "share 链路"),
    "picks_share": ("H5 · 分享", "精选推送", "h5", "share_tokens"),
    "beacon_view": ("H5 · 阅读埋点", "H5 精选页", "h5", "share 链路"),
    # bot 文本指令（handle_cmd 老链）
    "cmd:tune": ("调优 指令", "bot 指令", "cmd", "bot ws handle_cmd"),
    "cmd:approve": ("审批 指令", "bot 指令", "cmd", "bot ws handle_cmd"),
    "cmd:chanlun": ("缠论 指令", "bot 指令", "cmd", "bot ws handle_cmd"),
    "cmd:control": ("控制/面板 指令", "bot 指令", "cmd", "bot ws handle_cmd"),
    "cmd:stock": ("选股 指令", "bot 指令", "cmd", "bot ws handle_cmd"),
    "cmd:detail": ("详情 指令", "bot 指令", "cmd", "bot ws handle_cmd"),
    "cmd:tpsl": ("止盈/止损 指令", "bot 指令", "cmd", "bot ws handle_cmd"),
    "cmd:help": ("帮助 指令", "bot 指令", "cmd", "bot ws handle_cmd"),
}


# ── 持久层 ──────────────────────────────────────────────────


def _load_overlay():
    """覆盖层：{"enforce": bool, "disabled": [action_id...]}"""
    try:
        with open(OVERLAY_PATH, encoding="utf-8") as f:
            o = json.load(f)
    except Exception:
        o = {}
    return {
        "enforce": bool(o.get("enforce", False)),
        "disabled": list(o.get("disabled") or []),
    }


def _save_overlay(o):
    tmp = OVERLAY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OVERLAY_PATH)


def set_enforce(on):
    with _LOCK:
        o = _load_overlay()
        o["enforce"] = bool(on)
        _save_overlay(o)
    return o["enforce"]


def toggle(action_id, enabled):
    """页面启停。禁用后：v3 卡拒造拒发（enforce 下老链值也拒发）。"""
    with _LOCK:
        o = _load_overlay()
        d = set(o["disabled"])
        if enabled:
            d.discard(action_id)
        elif action_id in DEFAULT_ACTIONS:
            d.add(action_id)
        o["disabled"] = sorted(d)
        _save_overlay(o)
    return action_id not in o["disabled"]


def lookup(action_id):
    """→ dict(name/domain/kind/handler/enabled) 或 None（未注册）。"""
    a = DEFAULT_ACTIONS.get(action_id)
    if not a:
        return None
    return {
        "id": action_id,
        "name": a[0],
        "domain": a[1],
        "kind": a[2],
        "handler": a[3],
        "enabled": action_id not in _load_overlay()["disabled"],
    }


def enforce_on():
    return _load_overlay()["enforce"]


# ── 审计流水：action_log.jsonl（who/when/card/result）────────


def log_event(action_id, who="", card="", result="", extra=None):
    rec = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "date": datetime.date.today().isoformat(),
        "action": action_id,
        "who": who,
        "card": str(card)[:60],
        "result": str(result)[:80],
    }
    if extra:
        rec["extra"] = str(extra)[:80]
    with _LOCK:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def recent_events(limit=30):
    try:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/card_actions.py", exc)
    out.reverse()
    return out


def today_counts():
    """今日触发计数（action_log 聚合）。"""
    today = datetime.date.today().isoformat()
    cnt = {}
    try:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("date") == today:
                    cnt[r.get("action", "?")] = cnt.get(r.get("action", "?"), 0) + 1
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/card_actions.py", exc)
    return cnt


def stats():
    """8097 按钮归档分区数据。"""
    tc = today_counts()
    rows = []
    for aid, a in sorted(DEFAULT_ACTIONS.items()):
        rows.append(
            {
                "id": aid,
                "name": a[0],
                "domain": a[1],
                "kind": a[2],
                "handler": a[3],
                "today": tc.get(aid, 0),
                "enabled": aid not in _load_overlay()["disabled"],
            }
        )
    return {
        "actions": rows,
        "total": len(rows),
        "enforce": enforce_on(),
        "events": recent_events(30),
    }


# ── 卡片按钮提取与 HUB 校验 ─────────────────────────────────


def extract_action(value):
    """从按钮 value 提取动作标识。兼容三套存量命名 + v3：
    v3 {"action_id":...} / {"action":"ack"} / {"act":"..."} / {"tag":"er:set"}"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            v = value.strip()
            return v or None
    if isinstance(value, dict):
        for key in ("action_id", "action", "act", "tag"):
            v = value.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _walk_buttons(node, out):
    """递归找 button 节点（v1 elements + schema 2.0 body 通用）。"""
    if isinstance(node, dict):
        if node.get("tag") == "button":
            out.append(node)
        for v in node.values():
            _walk_buttons(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_buttons(v, out)


def _norm_action(raw):
    """提取值 → 注册表 id 归一（存量别名映射）。"""
    alias = {
        "ack": "jev_ack",
        "ignore": "jev_ignore",
        "subscribe_buypoint": "subscribe_buypoint",
        "unsub": "unsubscribe_buypoint",
        "unsubscribe": "unsubscribe_buypoint",
    }
    if raw in alias:
        return alias[raw]
    if raw.startswith("dim:"):
        return "dim:*"
    return raw


def scan_card(card):
    """卡片 dict → [(action_id_raw, normalized)]（全部按钮，URL 按钮跳过）。"""
    out_nodes = []
    _walk_buttons(card, out_nodes)
    found = []
    for b in out_nodes:
        beh = b.get("behaviors")
        if beh and any(
            isinstance(x, dict) and x.get("type") == "open_url" for x in beh
        ):
            continue  # 纯 URL 跳转按钮，非回调动作
        raw = extract_action(b.get("value"))
        if not raw and beh:  # schema 2.0：回调 value 在 behaviors[callback].value
            for x in beh:
                if isinstance(x, dict) and x.get("type") == "callback":
                    raw = extract_action(x.get("value"))
                    break
        if raw:
            found.append((raw, _norm_action(raw)))
    return found


def check_card(card):
    """HUB 发卡校验 → (ok, unregistered_ids, warned_ids)。
    warn 模式（默认）：未注册仅记流水不拦；enforce：任一未注册即拒发。"""
    found = scan_card(card)
    unreg, disabled = [], []
    for raw, norm in found:
        info = lookup(norm) if not norm.endswith("*") else None
        if norm == "dim:*":
            continue  # 旧 v1 维度卡前缀族，登记放行
        if info is None:
            unreg.append(raw)
            log_event(
                "warn:unregistered",
                card=raw,
                result="未注册动作"
                + ("（enforce 拒发）" if enforce_on() else "（warn 观察）"),
            )
        elif not info["enabled"]:
            disabled.append(raw)
            log_event(
                "warn:disabled",
                card=raw,
                result="动作已禁用"
                + ("（enforce 拒发）" if enforce_on() else "（warn 观察）"),
            )
    hard = enforce_on() and (unreg or disabled)
    why = ""
    if hard:
        why = "unregistered actions: %s; disabled: %s" % (
            ",".join(unreg),
            ",".join(disabled),
        )
    return (not hard), why or "ok", unreg + disabled


# ── 统一分发（v3 新链，bot 调用）─────────────────────────────


def dispatch(action_id, payload=None, who=""):
    """统一分发入口。返回 (ok, msg)。已接 tune_* / decision_* / 买点订阅退订；
    其余注册动作 P3 渐进迁移（未迁移者不再伪装成功）。"""
    payload = payload or {}
    info = lookup(action_id)
    if not info:
        log_event(action_id, who=who, result="dispatch: 未注册")
        return False, "未注册动作：%s" % action_id
    if not info["enabled"]:
        log_event(action_id, who=who, result="dispatch: 已禁用")
        return False, "动作已禁用：%s" % action_id
    try:
        if action_id in ("tune_approve", "tune_reject"):
            import tune_gate

            rid = payload.get("req_id") or ""
            if action_id == "tune_approve":
                r = tune_gate.approve(rid, open_id=who, note="卡片按钮审批")
                res = "ok" if r.get("ok") else str(r.get("error"))[:80]
            else:
                r = tune_gate.reject(
                    rid, open_id=who, reason="卡片按钮驳回（如需补充原因请用文本指令）"
                )
                res = "rejected" if r.get("ok") else str(r.get("error"))[:80]
            log_event(action_id, who=who, card=rid, result=res)
            return bool(r.get("ok")), r.get("error") or (
                "已通过，见回执卡"
                if action_id == "tune_approve"
                else "已驳回，见回执卡"
            )
        if action_id in ("decision_approve", "decision_reject"):
            import approval_card  # 函数内 import 防环（approval_card ← 本模块）

            ok2, msg2 = approval_card.handle_card_action(
                action_id, payload.get("req_id") or "", who
            )
            log_event(
                action_id,
                who=who,
                card=str(payload.get("req_id") or "")[:60],
                result=("ok" if ok2 else str(msg2))[:80],
            )
            return bool(ok2), msg2
        if action_id == "dispatch_nudge":
            import ticket_loop

            did = str((payload or {}).get("dispatch_id") or "")
            r = ticket_loop.nudge(did, by=who or "card")
            log_event(
                action_id,
                who=who,
                card=did,
                result="ok" if r.get("ok") else str(r.get("error"))[:80],
            )
            if not r.get("ok"):
                return False, r.get("error") or "催办失败"
            return True, "已催办第 %d 次（工单 %s，已提醒处理人）" % (r["n"], did)
        if action_id in ("jev_ack", "jev_ignore"):
            # W3 清债：9104 lark-cli 老链迁入统一分发（数据状态 + 审计；
            # 卡片视觉更新由 WS 回执承担）。老卡 value {"action":"ack"}
            # 由 bot 侧归一为 jev_ack/jev_ignore 后进入本分支。
            import jev_card_action

            ok2, msg2 = jev_card_action.handle_action(action_id, payload, who)
            log_event(
                action_id,
                who=who,
                card=str(payload.get("alert_id") or "")[:60],
                result=("ok" if ok2 else str(msg2))[:80],
            )
            return bool(ok2), msg2
        if action_id in ("subscribe_buypoint", "unsubscribe_buypoint"):
            # P3.11-lite（WO-FS-141）：买点订阅/退订迁入真 handler。
            # 口径差：注册表 act 是 unsubscribe_buypoint，而
            # subscribe_from_card 退订分支只认 act in ("unsubscribe","unsub")
            # （subscribe_action.py:121）——退订必须先归一 act，否则误执行成订阅。
            val = dict(payload) if isinstance(payload, dict) else {}
            if action_id == "unsubscribe_buypoint":
                val["act"] = "unsubscribe"
            qb = os.path.join(os.path.dirname(HERE), "quant_backtest")
            if qb not in sys.path:
                sys.path.insert(0, qb)
            try:
                import subscribe_action
            except Exception as e:  # closed on failure：import 失败不伪装成功
                log_event(
                    action_id,
                    who=who,
                    card=str(val.get("code") or "")[:60],
                    result="subscribe_action import 失败: %s" % e,
                )
                return False, "订阅模块不可用（%s），动作未执行" % e
            try:
                ok2, msg2, card2 = subscribe_action.subscribe_from_card(
                    val, operator=who
                )
            except Exception as e:  # closed on failure：handler 异常不伪装成功
                log_event(
                    action_id,
                    who=who,
                    card=str(val.get("code") or "")[:60],
                    result="handler 异常: %s" % e,
                )
                return False, "订阅动作执行异常：%s" % e
            # 第三返回值 card（更新卡）WS 链暂无更新卡通道，丢弃记账；
            # 回执由 subscribe_from_card 内部确认卡承担（8091 链同源）。
            log_event(
                action_id,
                who=who,
                card=str(val.get("code") or "")[:60],
                result=("ok" if ok2 else str(msg2))[:80],
            )
            return bool(ok2), msg2
        # 已注册但尚未迁入统一分发的老链动作：不再返回 True 伪装成功
        # （fail-open 缺陷，O-D 取证判例）。bot ws 只对带 action_id 的 value
        # 调 dispatch（feishu_bot_ws._handle_card），er:/ctl:/jev 等 tag 域
        # 动作由各自老链消费、不会触达本分支；此处 False + 明确文案兜底。
        log_event(action_id, who=who, result="dispatch: 老链动作未迁移，无统一 handler")
        return False, "老链动作未迁移，HTTP 链可达时由 8091 处理：%s" % action_id
    except Exception as e:
        log_event(action_id, who=who, result="dispatch 异常: %s" % e)
        return False, "分发异常：%s" % e
