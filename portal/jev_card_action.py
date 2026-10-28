# -*- coding: utf-8 -*-
"""盯盘预警卡片动作统一 handler（W3 清债：9104 老链迁移目标，2026-10-24）。

原链：`jev-eval/lark_card_handler.py`（lark-cli `event consume card.action.trigger`
独立进程，端口 9104）消费 value {"action":"ack|ignore"}，写 jev DB
（v2_alert.status=ACKED）+ gate_ignored.json + 更新卡片。
新链：bot ws card.action.trigger → `card_actions.dispatch` → 本模块。

边界（与老链口径一致）：
  · 操作者白名单：env LARK_ALLOWED_OPERATORS（逗号分隔 open_id）优先；
    未配置时仅允许 LARK_ALERT_OPEN_ID 本人；空集 = 拒绝（不放行所有）。
  · 「已处理」「忽略」都置 ACKED；差异在 gate_ignored.json（忽略 = 不再推送）。
  · 卡片视觉更新由 WS 回执承担；本模块只做数据状态 + 审计。失败不伪装成功。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
JEV_DIR = os.path.join(os.path.dirname(HERE), "jev-eval")
IGNORE_FILE = os.path.join(JEV_DIR, "gate_ignored.json")
IGNORE_CAP = 2000
ACTIONS = ("jev_ack", "jev_ignore")


def _jev_common():
    if JEV_DIR not in sys.path:
        sys.path.insert(0, JEV_DIR)
    import jev_common as jc  # noqa: WPS433

    return jc


def _push_owner_open_id():
    """推送收件人「个人」的 open_id（老田本人，app 域与现役 bot 一致）。"""
    cfg = os.path.join(os.path.dirname(HERE), "news_collector", "news_config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            for t in json.load(f).get("push_targets") or []:
                if t.get("name") == "个人" and t.get("id"):
                    return str(t["id"]).strip()
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/jev_card_action.py", exc)
    return ""


def allowed_operators():
    """白名单：env LARK_ALLOWED_OPERATORS → LARK_ALERT_OPEN_ID → push_targets.个人。

    2026-10-24 W3：bot 进程环境没有 LARK_ALERT_OPEN_ID（那是 jev 老链的
    env），若只认 env 会把老田也拒掉——故补 push_targets 个人作为最终回退
    （数据源仍是单一收件人表，不写死 open_id）。
    """
    explicit = {
        s.strip()
        for s in os.environ.get("LARK_ALLOWED_OPERATORS", "").split(",")
        if s.strip()
    }
    if explicit:
        return explicit
    recipient = os.environ.get("LARK_ALERT_OPEN_ID", "").strip()
    if recipient:
        return {recipient}
    owner = _push_owner_open_id()
    return {owner} if owner else set()


def is_allowed(who):
    allowed = allowed_operators()
    return bool(who) and who in allowed


def fetch_alert_title(alert_id):
    """查 v2_alert.title 供忽略记录；失败返回 ""。"""
    try:
        rows = _jev_common().db_rows(
            "SELECT title FROM v2_alert WHERE alert_id=%s", (alert_id,)
        )
    except Exception:  # noqa: BLE001
        return ""
    if not rows:
        return ""
    row = rows[0]
    if isinstance(row, dict):
        return str(row.get("title") or "")
    return str(row[0]) if row else ""


def ack_alert_in_db(alert_id):
    """v2_alert 置 ACKED（仅 OPEN 行）；UPDATE 0 行视为成功（幂等）。"""
    try:
        _jev_common().db_execute(
            "UPDATE v2_alert SET status='ACKED', acked_at=now() "
            "WHERE alert_id=%s AND status='OPEN'",
            (alert_id,),
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def load_ignored(path=None):
    path = path or IGNORE_FILE
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for it in raw if isinstance(raw, list) else []:
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, str):
            out.append({"alert_id": it, "title": "", "ts": ""})
    return out


def save_ignored(items, path=None):
    path = path or IGNORE_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(list(items)[-IGNORE_CAP:], f, ensure_ascii=False)
    os.replace(tmp, path)


def handle_action(
    action_id, payload=None, who="", ack_fn=None, title_fn=None, ignore_file=None
):
    """→ (ok, msg)。ack_fn/title_fn/ignore_file 可注入（单测用）。"""
    payload = dict(payload or {})
    alert_id = str(payload.get("alert_id") or payload.get("id") or "").strip()
    if not alert_id:
        return False, "缺少 alert_id，动作未执行"
    if action_id not in ACTIONS:
        return False, "不支持的动作：%s" % action_id
    if not is_allowed(who):
        return False, (
            "操作者不在允许名单（配置 LARK_ALLOWED_OPERATORS 或 LARK_ALERT_OPEN_ID）"
        )
    ok = (ack_fn or ack_alert_in_db)(alert_id)
    if not ok:
        return False, "数据库回写失败（按钮保留，可重试）"
    if action_id == "jev_ack":
        return True, "已处理（ACKED）"
    title = (title_fn or fetch_alert_title)(alert_id)
    try:
        items = [
            it for it in load_ignored(ignore_file) if it.get("alert_id") != alert_id
        ]
        items.append(
            {
                "alert_id": alert_id,
                "title": title,
                "ts": datetime.now().isoformat(timespec="seconds"),
            }
        )
        save_ignored(items, ignore_file)
    except OSError as e:
        return False, "忽略集写入失败：%s" % e
    return True, "已忽略（后续不再推送该告警）"


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "actions": ACTIONS,
                "ignore_file": IGNORE_FILE,
                "allowed": sorted(allowed_operators()),
            },
            ensure_ascii=False,
            indent=1,
        )
    )
