#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Jev 告警降噪网关（P1）：轮询 zhice v2_alert 的 OPEN 告警 → Jev 判紧急度/方向 → 达标推企业微信。

设计原则（零侵入）：
  - 只读 v2_alert 表（psql 直查，不依赖 zhice venv/ORM），不改 zhice 任何代码；
  - Jev 不可用 → 降级为"只推 CRITICAL"，绝不阻塞也绝不误推；
  - 已处理告警入 DB 表 jev_gate_seen（alert_id 主键，幂等），重启/换机不重推；
  --once 单轮模式（配计划任务/手动验证），--loop 常驻轮询。

评测依据（2026-09-21，30题×3遍）：noul/choice 100%，score 75% →
紧急度用 noul 二分（非 score 三档），方向用 choice。

环境变量：
  JEV_API_KEY        TypeSafe key（必需）
  ALERT_WEBHOOK_URL  企业微信群机器人 webhook（未配置则只打印不推送）
  JEV_BASE_URL       默认 https://api.typesafe.ai/v1
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import jev_common as jc
from jev_common import (
    C_JEV,
    db_execute,
    db_rows,
    db_run,
    install_stop,
    log,
    new_trace,
    set_module,
    with_retry,
)
import logging

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
SEEN_FILE = HERE / "gate_seen.json"  # 旧版本地 seen，启动时一次性迁入 DB 后留档改名
# P2-3 阈值可配置化：环境变量覆盖（jev-gate.env.ps1），默认值=评测校准值
POLL_SEC = int(os.environ.get("GATE_POLL_SEC", "60"))
MAX_ALERT_AGE_MIN = int(
    os.environ.get("GATE_MAX_ALERT_AGE_MIN", "120")
)  # 只看 N 分钟内新告警
URGENT_THRESHOLD = float(
    os.environ.get("GATE_URGENT_THRESHOLD", "0.8")
)  # noul P ≥ 阈值才推

API_KEY = os.environ.get("JEV_API_KEY", "")
BASE_URL = os.environ.get("JEV_BASE_URL", "https://api.typesafe.ai/v1").rstrip("/")
log(
    f"启动环境: JEV_BACKEND={os.environ.get('JEV_BACKEND', '')} "
    f"proxies={urllib.request.getproxies()}"
)
# 2026-10-16: Jev 后端分发已收敛到 jev_common.jev_ask_backend
# （JEV_BACKEND=ollama 走本地 qwen2.5:7b；typesafe.ai 451 地区屏蔽）
LARK_ALERT_OPEN_ID = os.environ.get("LARK_ALERT_OPEN_ID", "")
METRICS_PORT = 9101  # P1-6 Prometheus /metrics 端点
NOTIFY_CHANNEL = "jev_new_alert"  # P2-1 事件驱动通道（v2_alert INSERT 触发）


# 优雅退出（P1-1：收敛到 jev_common）；计划任务强杀或 Ctrl-C 时置位，本轮完成后退出。
# seen 已入 DB（每条即时持久化），无需 atexit 兜底 flush。
_STOP = install_stop()


# ── 数据库：psycopg2 直连参数化查询（P1-7，失败自动降级 docker exec） ──
def fetch_open_alerts() -> list[dict]:
    """查 MAX_ALERT_AGE_MIN 内的 OPEN 告警，返回 [{alert_id, level, title, message}]。"""
    rows = db_rows(
        "SELECT alert_id, level, title, coalesce(message,'') FROM v2_alert "
        "WHERE status='OPEN' AND created_at > now() - make_interval(mins => %s) "
        "ORDER BY id DESC LIMIT 50",
        (MAX_ALERT_AGE_MIN,),
    )
    return [
        {"alert_id": r[0], "level": r[1], "title": r[2], "message": r[3]} for r in rows
    ]


# ── Jev 判定 ────────────────────────────────────────────────
def jev_ask(state: str, questions: dict) -> dict:
    """单次 Jev 调用，返回 answers 字典。失败抛异常。
    含 3 次指数退避重试（2/4 秒），缓解短暂网络抖动导致的整轮跳过。
    2026-10-16: 后端分发收敛到 jev_common.jev_ask_backend
    （JEV_BACKEND=ollama 走本地模型，typesafe.ai 451 屏蔽）。"""
    try:
        ans = with_retry(
            lambda: jc.jev_ask_backend(state, questions),
            retries=3,
            base_sec=2.0,
            label="jev ",
        )
        C_JEV.labels("gate", "1").inc()
        return ans
    except Exception:
        C_JEV.labels("gate", "0").inc()
        raise


def judge(alert: dict) -> dict | None:
    """一道告警的完整判定：紧急度（noul）+ 类别（choice）。失败返回 None。"""
    state = f"[{alert['level']}] {alert['title']}\n{alert['message']}"
    questions = {
        "is_urgent": {
            "type": "noul",
            "instructions": "这条告警需要人工立即关注（数据缺失/执行失败/风控触发等），"
            "而非例行提示或可延后处理的信息。",
        },
        "category": {
            "type": "choice",
            "instructions": "这条告警属于以下哪一类？",
            "criteria": {
                "risk": "风控/止损/暴露超限——交易执行层面",
                "data": "行情/数据采集异常——数据质量层面",
                "sys": "系统/工作流故障——运行层面",
                "info": "例行提示/统计汇总——无需行动",
            },
        },
    }
    ans = jev_ask(state, questions)
    urgent_p = ans.get("is_urgent", {}).get("noul", 0.0)
    cat = ans.get("category", {}).get("choice", "unknown")
    cat_p = (ans.get("category", {}).get("probabilities") or {}).get(cat, 0.0)
    return {"urgent_p": urgent_p, "category": cat, "category_p": cat_p}


# ── 推送 ────────────────────────────────────────────────────
_CAT_LABEL = {
    "risk": "交易风控",
    "data": "数据质量",
    "sys": "系统运行",
    "info": "例行",
    "unknown": "未分类",
}


def _build_alert_card(alert: dict, verdict: dict) -> dict:
    """构造飞书告警交互卡片（K-PUSH-009：v3 红域统一卡）。

    K-STOCK-370 判例翻案（2026-10-17 14:25 实证）：jev 卡片域 2.0
    behaviors callback 点击正常投递（lark-cli consume 链实收事件，
    当时"平台侧零投递"为 SDK 丢 CARD 帧同期误诊）——卡面升 v3，
    按钮双键 value（action_id=jev_ack/jev_ignore 供 bot ws dispatch
    审计 + action=ack/ignore 供 lark_card_handler 老链零改动消费）。"""
    level = alert.get("level", "INFO")
    cat = verdict.get("category", "unknown")
    cat_label = _CAT_LABEL.get(cat, cat)
    urgent_p = verdict.get("urgent_p", 0.0)
    alert_id = alert.get("alert_id", "")
    title = alert.get("title", "")
    message = alert.get("message", "")[:300]

    _portal = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "portal"
    )
    if _portal not in sys.path:
        sys.path.insert(0, _portal)
    from feishu_card_v2 import build_v3

    card, _audit = build_v3(
        "alert",
        "告警通知 · %s" % level,
        source="jev-eval 盯盘预警",
        rows=[("紧急度", "%.2f" % urgent_p), ("类别", cat_label), ("级别", level)],
        md="**%s**\n\n%s" % (title, message),
        buttons=[
            (
                "✓ 已处理",
                ("action", "jev_ack", {"alert_id": alert_id, "action": "ack"}),
                "primary",
            ),
            (
                "忽略",
                ("action", "jev_ignore", {"alert_id": alert_id, "action": "ignore"}),
                "default",
            ),
        ],
        tags="#jev #alert",
    )
    return card


def push_feishu(alert: dict, verdict: dict) -> bool:
    """飞书交互卡片推送，双通道（K-STOCK-401/403 判例对齐 backup_alert）。

    ① OpenAPI 直连：apps[0] 凭证 + push_targets("个人") 的 apps[0] 签发
       open_id——app 维度自洽配对。2026-10-16 换机后 JEV_CARD_APP_* 未部署，
       lark-cli 注入 apps[0] 凭证发 jev 签发 open_id 必 99992361（15:08 实证）。
    ② lark-cli 回退：LARK_ALERT_OPEN_ID（jev 签发）——待 jev app 凭证
       部署本机后 lark_env 注入即成正确配对，届时可升回主通道。
    双路全败 → False（调用方重试 → 死信 → _escalate_dlq 升级）。
    """
    global _LAST_PUSH_ERR
    card_json = json.dumps(_build_alert_card(alert, verdict), ensure_ascii=False)

    # 路① OpenAPI 直连（apps[0] + push_targets 自洽配对，无 lark-cli 依赖）
    try:
        _root = os.path.dirname(os.path.abspath(__file__))
        if os.path.join(os.path.dirname(_root), "news_collector") not in sys.path:
            sys.path.insert(0, os.path.join(os.path.dirname(_root), "news_collector"))
        import push_targets
        from feishu_client import send_msg

        _oid = push_targets.target_id("个人")
        if _oid:
            _ok, _why = send_msg(card_json, "interactive", receive_id=_oid)
            if _ok:
                _LAST_PUSH_ERR = ""
                return True
            log(f"OpenAPI 直连失败: {str(_why)[:120]}", level="warn")
        else:
            log("push_targets 无 '个人' 收件人，OpenAPI 路跳过", level="warn")
    except Exception as e:  # noqa: BLE001
        log(f"OpenAPI 直连异常: {str(e)[:120]}", level="warn")

    # 路② lark-cli 回退（原通道：3 次指数退避重试）
    if not LARK_ALERT_OPEN_ID:
        _LAST_PUSH_ERR = "OpenAPI 直连失败且 LARK_ALERT_OPEN_ID 未配置"
        return False

    def _send() -> bool:
        jc.lark_cli_json(
            [
                "im",
                "+messages-send",
                "--as",
                "bot",
                "--user-id",
                LARK_ALERT_OPEN_ID,
                "--msg-type",
                "interactive",
                "--content",
                card_json,
            ],
            timeout=20,
            kind="push",
        )
        return True

    try:
        ok = with_retry(_send, retries=3, base_sec=2.0, label="推送")
        _LAST_PUSH_ERR = ""
        return ok
    except Exception as e:  # noqa: BLE001
        _LAST_PUSH_ERR = str(e)[:300]
        log(f"飞书推送最终失败: {str(e)[:200]}", level="error")
        return False


# ── 推送死信：失败审计 + 有界重试 ────────────────────────────
DEAD_LETTER = HERE / "push_dead_letter.jsonl"
MAX_PUSH_ATTEMPTS = 5  # 连续 N 轮推送失败后放弃（防无限循环；重启进程计数归零可重试）

_PUSH_FAILS: dict = {}  # alert_id → 连续失败轮数（仅内存）
_LAST_PUSH_ERR: str = ""  # push_feishu 最近一次失败原因（死信/升级告警取证）


def _escalate_dlq(alert: dict, reason: str) -> None:
    """死信升级告警（K-STOCK-401）：告警通道自身失守时的「告警的告警」。

    2026-09-28→10-14 实证教训：旧 lark-cli 二进制不认外部凭证注入、回落
    apps[0] 凭证发 jev app 签发的 open_id（99992361 cross app），17 天 41
    条紧急告警进死信零感知。故升级必须与 lark-cli 二进制解耦，双路 OpenAPI：
      ① jev app 凭证（env JEV_CARD_APP_*）+ LARK_ALERT_OPEN_ID——app 原生
         配对，二进制级故障期全程可用（urllib 直连不依赖 lark-cli）；
      ② apps[0] + config.json users[0] 的同 app open_id——jev 凭证也失守
         时的异 app 兜底（open_id 跨 app 不可用，须用各自签发的）。
    纯文本无按钮；两路全败仅日志（不递归、不反噬主链路）。
    """
    txt = "🚨 告警通道失守：jev 推送 %d 轮失败已入死信\n%s | %s\n原因: %s" % (
        MAX_PUSH_ATTEMPTS,
        alert.get("level", "?"),
        (alert.get("title") or "")[:60],
        (_LAST_PUSH_ERR or reason)[:160],
    )
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(os.path.dirname(here), "news_collector"))
    try:
        from feishu_client import send_msg
    except Exception as e:  # noqa: BLE001
        log(f"死信升级告警 import 失败: {str(e)[:80]}", level="warn")
        return
    # 路①：jev app 原生配对
    app_id = os.environ.get("JEV_CARD_APP_ID", "").strip()
    app_secret = os.environ.get("JEV_CARD_APP_SECRET", "").strip()
    if app_id and app_secret and LARK_ALERT_OPEN_ID:
        try:
            ok, why = send_msg(
                json.dumps({"text": txt}),
                "text",
                receive_id=LARK_ALERT_OPEN_ID,
                app_id=app_id,
                app_secret=app_secret,
            )
            if ok:
                log("死信升级告警(jev app 路径) 送达", alert_id=alert.get("alert_id"))
                return
            log(f"jev app 路径失败: {why[:80]}", level="warn")
        except Exception as e:  # noqa: BLE001
            log(f"jev app 路径异常: {str(e)[:80]}", level="warn")
    # 路②：apps[0] + 同 app 自签 open_id（config.json users[0]）
    try:
        cfgp = os.path.join(os.path.expanduser("~"), ".lark-cli", "config.json")
        users = json.load(open(cfgp, encoding="utf-8"))["apps"][0].get("users") or []
        oid2 = next((u.get("userOpenId") for u in users if u.get("userOpenId")), "")
        if oid2:
            ok, why = send_msg(json.dumps({"text": txt}), "text", receive_id=oid2)
            log(
                "死信升级告警(apps[0] 路径) " + ("送达" if ok else f"失败: {why[:80]}"),
                alert_id=alert.get("alert_id"),
                level="info" if ok else "warn",
            )
            return
    except Exception as e:  # noqa: BLE001
        log(f"apps[0] 路径异常: {str(e)[:80]}", level="warn")
    log("死信升级告警两路全败（详见上方 warn）", level="warn")


def _append_dead_letter(alert: dict, verdict: dict, reason: str, final: bool) -> None:
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "alert_id": alert["alert_id"],
        "level": alert["level"],
        "title": alert["title"],
        "verdict": verdict,
        "reason": reason,
        "final": final,
        "err": _LAST_PUSH_ERR[:300],
    }  # K-STOCK-401：失败原因入死信（此前仅日志可查）
    try:
        with DEAD_LETTER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        log(f"死信写入失败: {e}")
    _trim_dead_letter()  # R12：追加后顺带按日节流轮转


# ── 死信轮转（R12：仅追加的审计日志，无生产读方，TTL 清理）───
DEAD_LETTER_TTL_DAYS = 30  # K-STOCK-401 事故复盘需 17 天跨度，30 天留足余量
_last_trim = ""  # 最近一次清理日（按日节流，避免每次追加都全量重写）


def _trim_dead_letter() -> None:
    """按 ts 保留近 DEAD_LETTER_TTL_DAYS 天，其余清理。

    按日节流（进程内）；坏/缺 ts 保守保留不误删。重写用原子写防截断。
    """
    global _last_trim
    today = datetime.now().date().isoformat()
    if _last_trim == today or not DEAD_LETTER.exists():
        return
    _last_trim = today
    cutoff = datetime.now() - timedelta(days=DEAD_LETTER_TTL_DAYS)
    keep, drop = [], 0
    for line in DEAD_LETTER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ts = json.loads(line).get("ts", "")
            if ts and datetime.fromisoformat(ts) < cutoff:
                drop += 1
                continue
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "jev-eval/jev_gate.py", exc)
        keep.append(line)
    if drop:
        jc.atomic_write_text(DEAD_LETTER, "\n".join(keep) + "\n")
        log(
            f"死信轮转清理 {drop} 条（保留 {len(keep)} 条，TTL {DEAD_LETTER_TTL_DAYS} 天）"
        )


def _push_with_dlq(alert: dict, verdict: dict) -> bool:
    """推送包装（P1-4）。返回 True=已送达或已达放弃上限（调用方标 seen）；
    False=本轮失败但应保留待下轮重试（调用方不标 seen）。全程留死信审计。"""
    aid = alert["alert_id"]
    if push_feishu(alert, verdict):
        _PUSH_FAILS.pop(aid, None)
        return True
    n = _PUSH_FAILS.get(aid, 0) + 1
    if n >= MAX_PUSH_ATTEMPTS:
        _append_dead_letter(alert, verdict, f"attempt {n}", final=True)
        log(f"{aid} 连续 {n} 轮推送失败，放弃（死信已记终态）")
        _PUSH_FAILS.pop(aid, None)
        _escalate_dlq(alert, f"attempt {n}")  # K-STOCK-401：终态死信必须升级
        return True
    _PUSH_FAILS[aid] = n
    _append_dead_letter(alert, verdict, f"attempt {n}", final=False)
    log(f"{aid} 推送失败（{n}/{MAX_PUSH_ATTEMPTS}），下轮重试")
    return False


# ── 状态持久化（seen 入库：重启/换机/多实例均不重推）────────
SEEN_RETENTION_DAYS = 7  # 覆盖最大告警窗口（默认 120min）的充分余量

_seen_ready = False


def ensure_seen_table() -> bool:
    """幂等建 jev_gate_seen 表 + 清理过期行 + 一次性迁移旧 gate_seen.json。"""
    global _seen_ready
    if _seen_ready:
        return True
    try:
        db_run(
            "CREATE TABLE IF NOT EXISTS jev_gate_seen ("
            "alert_id varchar(64) PRIMARY KEY, "
            "seen_at timestamptz NOT NULL DEFAULT now())"
        )
        db_execute(
            "DELETE FROM jev_gate_seen WHERE seen_at < now() - make_interval(days => %s)",
            (SEEN_RETENTION_DAYS,),
        )
        # 一次性迁移本地 JSON（老版本遗留），成功后改名留档
        if SEEN_FILE.exists():
            try:
                old = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
                migrated = 0
                for aid in old if isinstance(old, list) else []:
                    if isinstance(aid, str) and 0 < len(aid) <= 64:
                        db_execute(
                            "INSERT INTO jev_gate_seen(alert_id) VALUES (%s) "
                            "ON CONFLICT DO NOTHING",
                            (aid,),
                        )
                        migrated += 1
                SEEN_FILE.rename(SEEN_FILE.with_suffix(".json.imported"))
                log(
                    f"seen 本地 JSON 迁移入库完成：{migrated} 条（留档 gate_seen.json.imported）"
                )
            except Exception as e:  # noqa: BLE001
                log("seen JSON 迁移失败（继续用 DB）", err=str(e)[:120], level="warn")
        _seen_ready = True
        return True
    except Exception as e:  # noqa: BLE001
        log("jev_gate_seen 建表失败", err=str(e)[:150], level="error")
        return False


def load_seen() -> set:
    """从 DB 加载已处理 alert_id 集合（表已按保留期清理，全量读取）。"""
    rows = db_rows("SELECT alert_id FROM jev_gate_seen")
    return {r[0] for r in rows}


def mark_seen(alert_id: str) -> None:
    """单条即时持久化（幂等，主键冲突忽略）。失败抛异常 → 调用方不吞。"""
    db_execute(
        "INSERT INTO jev_gate_seen(alert_id) VALUES (%s) ON CONFLICT DO NOTHING",
        (alert_id,),
    )


# ── 用户忽略模式（K-STOCK-404 R3 闭环） ─────────────────────
IGNORE_FILE = HERE / "gate_ignored.json"
IGNORE_TTL_DAYS = 14  # 忽略项过期恢复推送（防无限期静默漏告警；到期可再忽略）
_IGN: set = set()  # 本轮 run_once 生效的忽略 title 集（每轮刷新）


def _ignored_titles() -> set:
    """读 gate_ignored.json 取未过期且带 title 的忽略模式集。

    R3 闭环背景：该文件原先只有写入方（卡片「忽略」/控制中枢）而无任何
    读取方——按 alert_id 忽略也无意义（点击时 DB 已 ACKED，天然不重推）。
    现语义=按 title 精确匹配抑制**同类新告警**（告警 title 为模板化前缀，
    同规则反复产生新 alert_id）。每轮 run_once 调用一次（60s 轮询，忽略
    生效延迟≤1 分钟，文件小无需缓存）。
    """
    if not IGNORE_FILE.exists():
        return set()
    try:
        raw = json.loads(IGNORE_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        log(
            f"gate_ignored.json 读取失败（忽略集按空处理）: {str(e)[:80]}", level="warn"
        )
        return set()
    titles, cutoff = set(), datetime.now() - timedelta(days=IGNORE_TTL_DAYS)
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict):
            continue  # 旧格式 str 元素：无 title 无抑制能力，跳过
        t = (it.get("title") or "").strip()
        if not t:
            continue
        try:
            if datetime.fromisoformat(it.get("ts") or "") < cutoff:
                continue  # 过期：恢复推送
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "jev-eval/jev_gate.py", exc)
        titles.add(t)
    return titles


# ── 主循环 ──────────────────────────────────────────────────
def _handle_alert(a: dict, dry: bool) -> None:
    """单告警处理链路（P1-2：调用方以 new_trace 包裹，日志全程携带同一 trace_id）。"""
    # CRITICAL 直推不过 Jev（评测结论：兜底通道不能依赖外部模型）
    if a["level"] == "CRITICAL":
        log("CRITICAL 直推", alert_id=a["alert_id"], title=a["title"][:40])
        if not dry and not _push_with_dlq(
            a, {"urgent_p": 1.0, "category": "sys", "category_p": 1.0}
        ):
            return  # 推送失败：不标 seen，下轮重试（P1-4）
        if not dry:  # K-STOCK-401：dry 只演练不落状态，防真实告警被 seen 永久吞掉
            mark_seen(a["alert_id"])
        return

    # K-STOCK-404：用户忽略的同类告警（title 匹配）不再推送/不再消耗 Jev
    # 额度。仅豁免 CRITICAL（上方已 return）——告警安全侧：宁可多推不漏推。
    if _IGN and a["title"] in _IGN:
        log("用户已忽略该类告警，抑制", alert_id=a["alert_id"], title=a["title"][:40])
        if not dry:
            mark_seen(a["alert_id"])
        return

    verdict = None
    if API_KEY and not dry:
        try:
            verdict = judge(a)
        except Exception as e:  # noqa: BLE001
            log(
                "Jev 失败（跳过本轮）",
                alert_id=a["alert_id"],
                err=str(e)[:120],
                level="warn",
            )
            return  # 不标 seen，下轮重试
    elif dry:
        verdict = {"urgent_p": -1, "category": "dry", "category_p": 0}

    if verdict is None:
        # 无 key：保守起见不推，等配置。2026-10-16 审计：原 mark_seen 会
        # 把告警永久吞掉（key 恢复后也不再重判）——不标 seen，留待重判。
        log(
            "无 Jev key，跳过（不标 seen，留待 key 就绪重判）",
            alert_id=a["alert_id"],
            level=a["level"],
            title=a["title"][:40],
        )
        return

    if verdict["urgent_p"] >= URGENT_THRESHOLD:
        log(
            "推送",
            alert_id=a["alert_id"],
            urgent_p=round(verdict["urgent_p"], 2),
            category=verdict["category"],
            title=a["title"][:40],
        )
        if not dry and not _push_with_dlq(a, verdict):
            return  # 推送失败：不标 seen，下轮重试（P1-4）
    else:
        log(
            "抑制",
            alert_id=a["alert_id"],
            urgent_p=round(verdict["urgent_p"], 2),
            title=a["title"][:40],
        )
    if not dry:  # K-STOCK-401：dry 只演练不落状态（同上）
        mark_seen(a["alert_id"])


def run_once(dry: bool = False) -> None:
    global _IGN
    if not ensure_seen_table():
        return  # 表建不起来 = DB 不可用，本轮放弃（fetch 同样会失败）
    _IGN = _ignored_titles()  # K-STOCK-404：每轮刷新（忽略生效延迟≤1 轮询周期）
    seen = load_seen()
    alerts = fetch_open_alerts()
    fresh = [a for a in alerts if a["alert_id"] not in seen]
    if not fresh:
        log(f"无新告警（OPEN 总数 {len(alerts)}，2h 窗口）")
        return

    for a in fresh:
        if _STOP["flag"]:
            break  # 收到退出信号，立即跳出（已处理的 seen 均已即时入库）
        with new_trace("gate"):  # 单告警链路串联：判定→推送→落盘
            try:
                _handle_alert(a, dry)
            except Exception as e:  # noqa: BLE001
                log(
                    "单告警处理异常（不标 seen，下轮重试）",
                    alert_id=a["alert_id"],
                    err=str(e)[:150],
                    level="error",
                )


# ── P2-1 事件驱动：LISTEN/NOTIFY 秒级感知新告警，轮询兜底 ──
def ensure_notify_trigger() -> bool:
    """幂等建 NOTIFY 触发器（v2_alert INSERT → pg_notify），DB 重建后启动自愈。"""
    ddl = f"""
CREATE OR REPLACE FUNCTION jev_alert_notify() RETURNS trigger AS $fn$
BEGIN
  PERFORM pg_notify('{NOTIFY_CHANNEL}', NEW.alert_id);
  RETURN NEW;
END $fn$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_jev_alert_notify ON v2_alert;
CREATE TRIGGER trg_jev_alert_notify AFTER INSERT ON v2_alert
FOR EACH ROW EXECUTE FUNCTION jev_alert_notify();
"""
    try:
        import psycopg2

        c = psycopg2.connect(**jc.DB_DSN)
        c.autocommit = True
        with c.cursor() as cur:
            cur.execute(ddl)
        c.close()
        return True
    except Exception as e:  # noqa: BLE001
        log(f"触发器建立失败（退回纯轮询）: {str(e)[:100]}", level="warn")
        return False


def _listen_conn():
    """独立 LISTEN 连接（select 独占等待，不可与查询连接复用）。"""
    import psycopg2

    c = psycopg2.connect(**jc.DB_DSN)
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute(f"LISTEN {NOTIFY_CHANNEL}")
    return c


def poll_loop() -> None:
    """纯轮询兜底模式（psycopg2 不可用 / trigger 建立失败时）。"""
    while not _STOP["flag"]:
        try:
            run_once()
        except Exception as e:  # noqa: BLE001
            log(f"轮询异常: {e}", level="error")
        for _ in range(POLL_SEC):
            if _STOP["flag"]:
                return
            time.sleep(1)


def event_loop() -> None:
    """NOTIFY 驱动 + 周期兜底轮询；断连自动重建 LISTEN 连接。"""
    import select as _select

    while not _STOP["flag"]:
        try:
            c = _listen_conn()
        except Exception as e:  # noqa: BLE001
            log(f"LISTEN 建连失败，退回纯轮询: {str(e)[:100]}", level="warn")
            poll_loop()
            return
        log(f"事件驱动模式 LISTEN={NOTIFY_CHANNEL}（{POLL_SEC}s 轮询兜底）")
        try:
            while not _STOP["flag"]:
                fired = _select.select([c], [], [], POLL_SEC)[0]
                if fired:
                    c.poll()
                    n = 0
                    while c.notifies:
                        c.notifies.pop(0)
                        n += 1
                    if n:
                        log(f"NOTIFY 新告警通知 {n} 条")
                try:
                    run_once()  # 有通知立刻跑；超时（无通知）兜底跑一轮
                except Exception as e:  # noqa: BLE001
                    log(f"轮询异常: {e}", level="error")
        except Exception as e:  # noqa: BLE001
            log(f"LISTEN 连接断开，5s 后重建: {str(e)[:100]}", level="warn")
            time.sleep(5)


def main() -> None:
    set_module("gate")
    jc.log_db_channel()  # JEV-DSN-20261105：启动自检（脱敏 DSN + 实际通道，不连库）
    args = sys.argv[1:]
    if "--once" in args or "--dry" in args:
        _trim_dead_letter()  # R12：一次性模式也顺带轮转（历史积压在此清理）
        run_once(dry="--dry" in args)
        return
    if not API_KEY:
        sys.exit("缺少 JEV_API_KEY（或用 --dry 先看链路）")
    jc.start_metrics(METRICS_PORT)
    mode = "事件驱动" if ensure_notify_trigger() else "纯轮询"
    log(
        f"网关启动 模式={mode} 轮询={POLL_SEC}s 阈值={URGENT_THRESHOLD} "
        f"metrics=127.0.0.1:{METRICS_PORT} "
        f"飞书推送={'已配置' if LARK_ALERT_OPEN_ID else '未配置(仅日志)'}"
    )
    _trim_dead_letter()  # R12：常驻启动时轮转一次（历史积压在此清理）
    if mode == "事件驱动":
        event_loop()
    else:
        poll_loop()
    log("网关退出")


if __name__ == "__main__":
    main()
