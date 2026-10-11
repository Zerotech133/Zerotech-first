# -*- coding: utf-8 -*-
"""飞书控制中枢（K-STOCK-135）：卡片按钮 → 后端四域真实落盘。

哲学（subscribe_action K250 同款）：
  - 纯逻辑：只读状态、写权威文件、返回 (ok, text, follow_card)，
    发送一律由调用方（feishu_bot_ws）负责；
  - 只控真实存在的开关：subscriptions.enabled / news_config 参数 /
    v2_alert ACK / devtask 看板——不自造影子状态；
  - 操作全留痕：portal/data/control_ops.jsonl（谁·何时·动了什么）；
  - 白名单：CTRL_ALLOW_OPEN_IDS，未配置回落 EXIT_ALLOW_OPEN_IDS，
    再未配置=放行（单用户部署默认宽松，M7 同款）。

卡片 value 协议（on_card 按 tag 前缀分发）：
  {"tag": "ctl:sub_toggle", "id": "sub_alert_daily", "to": "on"|"off"}
  {"tag": "ctl:param", "key": "push_max_per_run", "value": 6}   # 值必须∈档位
  {"tag": "ctl:alert_ack", "alert_id": "..."}                   # 同 lark_card_handler
  {"tag": "ctl:alert_ignore", "alert_id": "..."}                # ACK + 入忽略集
  {"tag": "ctl:alert_ack_all"}
  {"tag": "ctl:tasks"}        # 工单总览文本
  {"tag": "ctl:board"}        # 重生成 devboard.html
"""
import json
import os
import subprocess
import sys
import threading
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SUBS = os.path.join(REPO, "quant_backtest", "subscriptions.json")
NEWS_CFG = os.path.join(REPO, "news_collector", "news_config.json")
TASKS = os.path.join(REPO, ".worktrees", "tasks.json")
OPS = os.path.join(HERE, "data", "control_ops.jsonl")
JEV_DIR = os.path.join(REPO, "jev-eval")
IGNORE_FILE = os.path.join(JEV_DIR, "gate_ignored.json")
BOARD_CMD = [sys.executable, os.path.join(REPO, "tools", "devtask.py"), "board"]

# 推送参数档位（防任意值注入：value 必须 ∈ 档位集合）
PRESETS = {
    "push_max_per_run": {"label": "单轮上限", "unit": "条", "steps": [3, 6, 10]},
    "quote_alert_pct": {"label": "异动阈值", "unit": "%", "steps": [2.0, 3.0, 5.0]},
    "quote_alert_cooldown_min": {"label": "异动冷却", "unit": "min",
                                 "steps": [15, 30, 60]},
    "digest_hour": {"label": "晨报时点", "unit": "点", "steps": [7, 8, 9]},
}

_ops_lock = threading.Lock()


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def _atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def audit(operator, action, detail=""):
    """操作留痕（追加 jsonl，多线程加锁；失败不阻断控制动作）。"""
    try:
        with _ops_lock:
            os.makedirs(os.path.dirname(OPS), exist_ok=True)
            with open(OPS, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": datetime.now().strftime("%F %T"),
                                    "operator": operator or "?",
                                    "action": action, "detail": detail},
                                   ensure_ascii=False) + "\n")
    except OSError:
        pass


def allowed(open_id):
    """白名单闸门：CTRL_ALLOW_OPEN_IDS → 回落 EXIT_ALLOW_OPEN_IDS → 放行。"""
    allow = (os.environ.get("CTRL_ALLOW_OPEN_IDS", "").strip()
             or os.environ.get("EXIT_ALLOW_OPEN_IDS", "").strip())
    if not allow:
        return True
    return bool(open_id) and open_id in {x.strip() for x in allow.split(",")}


# ── 告警库（jev_common 懒加载，不可达即降级） ────────────────
_jev = None


def _db():
    global _jev
    if _jev is None:
        if JEV_DIR not in sys.path:
            sys.path.insert(0, JEV_DIR)
        import jev_common  # noqa: WPS433  懒加载：DB 断连不应拖垮控制卡
        _jev = jev_common
    return _jev


def alert_state():
    """OPEN 告警前 6 条 + 总数。DB 不可达返回 (None, 原因)。"""
    try:
        rows = _db().db_rows(
            "SELECT alert_id, level, title, coalesce(message,'') FROM v2_alert "
            "WHERE status='OPEN' ORDER BY id DESC LIMIT 6")
        total = _db().db_rows(
            "SELECT count(*) FROM v2_alert WHERE status='OPEN'")[0][0]
        return {"total": int(total),
                "items": [{"alert_id": r[0], "level": r[1],
                           "title": (r[2] or "")[:36]} for r in rows]}, None
    except Exception as e:  # noqa: BLE001
        return None, str(e)[:80]


def _alert_ack(alert_id, ignore=False, operator=""):
    """ACK 单条（lark_card_handler 同款语义：已处理/忽略都置 ACKED，
    差异=忽略额外入 gate_ignored.json，jev_gate 不再重复弹卡）。"""
    n = _db().db_execute(
        "UPDATE v2_alert SET status='ACKED', acked_at=now() "
        "WHERE alert_id=%s AND status='OPEN'", (alert_id,))
    if ignore:
        ign = _read_json(IGNORE_FILE, [])
        if alert_id not in ign:
            ign.append(alert_id)
            _atomic(IGNORE_FILE, ign[-2000:])
    audit(operator, "alert_ignore" if ignore else "alert_ack", alert_id)
    return n


# ── 四域状态读取 ────────────────────────────────────────────
def push_state():
    d = _read_json(SUBS, {"subscriptions": []})
    cfg = _read_json(NEWS_CFG, {})
    return d.get("subscriptions") or [], cfg


def tasks_state():
    d = _read_json(TASKS, {})
    doing = [(t, d[t]) for t in d if d[t].get("status") == "doing"]
    return {"total": len(d), "doing": doing}


# ── 卡片构建（v1 兼容，与 er 卡同款：value 按钮） ────────────
def _btn(text, value, ptype="default"):
    return {"tag": "button", "text": {"tag": "plain_text", "content": text},
            "type": ptype, "value": value}


def build_control_card():
    """四域状态 + 按钮的hub卡。任何读失败降级为该域一行提示。"""
    els = []
    # ① 量化交易线（写路径复用 er:* 既有回路，这里只放入口按钮）
    els.append({"tag": "markdown", "content":
                "**① 量化交易线** · 模拟盘止盈止损"})
    els.append({"tag": "action", "actions": [
        _btn("止盈单速览", {"tag": "er:list"}, "primary")]})
    # ② 预警与推送
    subs, cfg = push_state()
    lines = ["**② 预警与推送**"]
    for s in subs:
        lines.append("· %s **%s**（%s）" % (
            "✅" if s.get("enabled") else "⛔", s.get("name") or s.get("id"),
            s.get("kind") or ""))
    els.append({"tag": "markdown", "content": "\n".join(lines) or "（无订阅）"})
    for s in subs:
        sid, name = s.get("id"), (s.get("name") or sid)[:8]
        els.append({"tag": "action", "actions": [
            (_btn("关 %s" % name,
                  {"tag": "ctl:sub_toggle", "id": sid, "to": "off"}, "danger")
             if s.get("enabled") else
             _btn("开 %s" % name,
                  {"tag": "ctl:sub_toggle", "id": sid, "to": "on"}, "primary"))]})
    pline = ["推送参数：" + " ｜ ".join(
        "%s %s%s" % (PRESETS[k]["label"], cfg.get(k, "—"), PRESETS[k]["unit"])
        for k in PRESETS if k in cfg)]
    els.append({"tag": "markdown", "content": "\n".join(pline)})
    for k, p in PRESETS.items():
        cur = cfg.get(k)
        els.append({"tag": "action", "actions": [
            _btn(("%s %s%s" % (p["label"], v, p["unit"]))
                 + (" ◂" if v == cur else ""),
                 {"tag": "ctl:param", "key": k, "value": v})
            for v in p["steps"]]})
    # ③ 告警处置
    al, err = alert_state()
    if al is None:
        els.append({"tag": "markdown", "content":
                    "**③ 告警处置** · 告警库不可达（%s）" % err})
    else:
        lines = ["**③ 告警处置** · OPEN **%d** 条" % al["total"]]
        for a in al["items"]:
            lines.append("· [%s] %s" % (a["level"], a["title"]))
        els.append({"tag": "markdown", "content": "\n".join(lines)})
        for a in al["items"]:
            els.append({"tag": "action", "actions": [
                _btn("已处理", {"tag": "ctl:alert_ack",
                                "alert_id": a["alert_id"]}, "primary"),
                _btn("忽略", {"tag": "ctl:alert_ignore",
                              "alert_id": a["alert_id"]}, "default")]})
        if al["total"] > len(al["items"]):
            els.append({"tag": "action", "actions": [
                _btn("全部确认（含未展示）", {"tag": "ctl:alert_ack_all"},
                     "danger")]})
    # ④ 门户外围
    ts = tasks_state()
    els.append({"tag": "markdown", "content":
                "**④ 门户外围** · 工单 %d 张（doing %d）" % (
                    ts["total"], len(ts["doing"]))})
    els.append({"tag": "action", "actions": [
        _btn("工单总览", {"tag": "ctl:tasks"}),
        _btn("重生成看板", {"tag": "ctl:board"}, "primary")]})
    els.append({"tag": "note", "elements": [{"tag": "plain_text",
                "content": "操作留痕 portal/data/control_ops.jsonl · "
                "白名单 CTRL_ALLOW_OPEN_IDS"}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": "blue", "title": {"tag": "plain_text",
                      "content": "🎛 控制中枢 · %s" %
                      datetime.now().strftime("%m-%d %H:%M")}},
            "elements": els}


# ── 动作分发 ────────────────────────────────────────────────
def _tasks_text():
    ts = tasks_state()
    lines = ["工单总览（%d 张，doing %d）" % (ts["total"], len(ts["doing"]))]
    for tid, t in sorted(ts["doing"]):
        lines.append("· **%s** %s" % (tid, t.get("desc", ""))[:60])
    if not ts["doing"]:
        lines.append("（无进行中工单）")
    return "\n".join(lines)


def handle_ctl(val, operator=""):
    """ctl:* 动作入口 → (ok, 回执文本, follow_card)。
    follow_card 非 None 时调用方应重推控制卡（状态已变）。"""
    tag = (val or {}).get("tag", "")
    try:
        if tag == "ctl:sub_toggle":
            d = _read_json(SUBS, {"subscriptions": []})
            to = val.get("to") == "on"
            for s in d.get("subscriptions") or []:
                if s.get("id") == val.get("id"):
                    s["enabled"] = to
                    _atomic(SUBS, d)
                    audit(operator, "sub_toggle",
                          "%s->%s" % (val.get("id"), val.get("to")))
                    return True, "✅ %s 已%s" % (s.get("name") or val.get("id"),
                                                "开启" if to else "关闭"), \
                        build_control_card()
            return False, "未找到订阅 %s" % val.get("id"), None
        if tag == "ctl:param":
            key, v = val.get("key"), val.get("value")
            if key not in PRESETS:
                return False, "未知参数 %s" % key, None
            if v not in PRESETS[key]["steps"]:
                return False, "%s 档位仅支持 %s" % (
                    key, PRESETS[key]["steps"]), None
            cfg = _read_json(NEWS_CFG, {})
            cfg[key] = v
            _atomic(NEWS_CFG, cfg)
            audit(operator, "param", "%s=%s" % (key, v))
            return True, "✅ %s 已设为 %s%s" % (
                PRESETS[key]["label"], v, PRESETS[key]["unit"]), \
                build_control_card()
        if tag == "ctl:alert_ack":
            n = _alert_ack(val.get("alert_id"), False, operator)
            return (True, "✅ 已处理（%s 行）" % n, build_control_card()) \
                if n is not None else (False, "告警库不可达", None)
        if tag == "ctl:alert_ignore":
            n = _alert_ack(val.get("alert_id"), True, operator)
            return (True, "✅ 已忽略并确认（%s 行）" % n,
                    build_control_card()) \
                if n is not None else (False, "告警库不可达", None)
        if tag == "ctl:alert_ack_all":
            n = _db().db_execute(
                "UPDATE v2_alert SET status='ACKED', acked_at=now() "
                "WHERE status='OPEN'")
            audit(operator, "alert_ack_all", "rows=%s" % n)
            return True, "✅ 全部确认（%s 行）" % n, build_control_card()
        if tag == "ctl:tasks":
            audit(operator, "tasks", "view")
            return True, _tasks_text(), None
        if tag == "ctl:board":
            r = subprocess.run(BOARD_CMD, cwd=REPO, capture_output=True,
                               timeout=180)
            out = (r.stdout or b"").decode("utf-8", "replace").strip()
            audit(operator, "board", "rc=%s" % r.returncode)
            return (r.returncode == 0,
                    ("✅ 看板已重生成\n%s" % out[-120:]) if r.returncode == 0
                    else "❌ 看板生成失败：%s" % out[-120:], None)
        return False, "未知动作 %s" % tag, None
    except Exception as e:  # noqa: BLE001  回调链路绝不因单点失败断掉
        return False, "处理失败：%s" % str(e)[:100], None


if __name__ == "__main__":
    # 冒烟：只读路径（建卡 + 状态读取），不做任何写动作
    card = build_control_card()
    print("card ok:", "elements" in card, "blocks=%d" % len(card["elements"]))
    al, err = alert_state()
    print("alerts:", (al or {}).get("total"), err or "")
    print(tasks_state()["total"], "tasks")
