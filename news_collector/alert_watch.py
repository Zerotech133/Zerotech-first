# -*- coding: utf-8 -*-
"""五路预警阈值监听（K42-b）：扫描任务出新结果即比对，关键变化立刻推飞书。

与 alert_push 的分工:
    alert_push  定时定量（晨 7:10 / 盘后 16:05）全量日报卡
    alert_watch 每 30 分钟比对五路预警值，触发规则才推「⚡ 即时预警」卡

触发规则（保守防轰炸，每路每日熔断 ≤3 推）:
    盘前倾向    值翻转（偏多↔偏空）即触发（每日一次天然低频）
    缠论信号    买点数较上次 +5，或首次 ≥40
    九维警报池  数量较上次增加
    弱于板块在案 数量较上次增加
    共振做多    数量较上次 +3

状态: outbox/alert_watch_state.json（跨日自动重置熔断计数；值不变零写盘）。
调度: 计划任务每 30 分钟（数据源为盘前/盘后落盘快照，非交易时段静默比对）。
"""
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
ALERTS_URL = "http://127.0.0.1:8091/api/alerts"
STATE_PATH = os.path.join(HERE, "outbox", "alert_watch_state.json")
DAILY_CAP = 3

RULES = {
    "盘前倾向": {"kind": "flip"},
    "缠论信号": {"kind": "num", "diff": 5, "abs": 40},
    "九维警报池": {"kind": "num", "diff": 1},
    "弱于板块在案": {"kind": "num", "diff": 1},
    "共振做多": {"kind": "num", "diff": 3},
}


def _load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def _parse_num(name, value):
    if name == "缠论信号":
        m = re.search(r"买\s*(\d+)", value or "")
        return int(m.group(1)) if m else None
    m = re.search(r"(\d+)", value or "")
    return int(m.group(1)) if m else None


def _check(rule, prev, cur, prev_raw, cur_raw):
    """返回触发说明 str 或 None。"""
    if rule["kind"] == "flip":
        if prev_raw and cur_raw and prev_raw != cur_raw:
            return "%s → %s" % (prev_raw, cur_raw)
        return None
    if prev is None or cur is None:
        return None
    if cur - prev >= rule.get("diff", 1):   # diff=1 即任何增加触发
        return "%s → %s（+%d）" % (prev, cur, cur - prev)
    if rule.get("abs") and cur >= rule["abs"] and prev < rule["abs"]:
        return "首次达 %d（现 %d）" % (rule["abs"], cur)
    return None


def main():
    today = time.strftime("%Y-%m-%d")
    try:
        with urllib.request.urlopen(ALERTS_URL, timeout=90) as r:
            res = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("ALERT_WATCH_FAIL fetch %s" % str(e)[:100])
        return 1

    st = _load_state()
    if st.get("date") != today:            # 跨日：重置熔断计数，保留基线值
        st = {"date": today,
              "routes": {k: {"num": v.get("num"), "raw": v.get("raw")}
                         for k, v in (st.get("routes") or {}).items()}}
    routes = st.setdefault("routes", {})
    first_run = not routes
    hits = []
    for a in res.get("alerts") or []:
        nm = a.get("name", "")
        rule = RULES.get(nm)
        if not rule:
            continue
        raw = str(a.get("value", ""))
        num = _parse_num(nm, raw)
        prev = routes.get(nm) or {}
        fired = None if first_run else _check(
            rule, prev.get("num"), num, prev.get("raw"), raw)
        if fired:
            pushed = int(prev.get("pushed") or 0)
            if pushed < DAILY_CAP:
                hits.append((nm, fired, a))
                prev["pushed"] = pushed + 1
            # 达熔断也更新基线，避免次日放开后拿旧值狂补
        prev["num"], prev["raw"] = num, raw
        routes[nm] = prev

    if hits and not first_run:
        import card_build
        import alert_push
        targets = alert_push._targets(alert_push._cfg())
        # K42-c：不做内网接入——卡内不放下钻按钮/门户二维码（外网飞书点不开）
        lines = ["**%s** %s" % (nm, fired) for nm, fired, _ in hits]
        card = card_build.build_card(
            "⚡ 即时预警", time.strftime("%Y-%m-%d %H:%M") + " · 关键变化触发",
            "red", ["\n".join(lines)],
            footer="AI工作站 · 阈值监听（每 30 分钟比对）· 不构成投资建议",
            use_schema2=True)
        tres = alert_push.push_card_multi(card, targets=targets)
        ok = any(tres.values())
        if not ok:  # 文本兜底
            tres = alert_push.push_text_multi(
                "⚡ 即时预警 " + time.strftime("%m-%d %H:%M") + "\n"
                + "\n".join(lines), targets)
            ok = any(tres.values())
        _save_state(st)
        print("ALERT_WATCH_FIRED %d routes ok=%s %s" % (
            len(hits), ok, json.dumps(tres, ensure_ascii=False)))
        return 0 if ok else 1

    _save_state(st)
    print("ALERT_WATCH_QUIET%s" % (" (baseline init)" if first_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
