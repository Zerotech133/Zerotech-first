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
非交易日（2026-10-26 起）: 按 trading_cal 整轮跳过——不 fetch / 不推送 / 不写
    状态文件，只写 rc=0 + upstream_ok=None 心跳，与交易日上游故障明确区分。
"""

import datetime
import json
import os
import re
import sys
import time
import urllib.request

import trading_cal  # 同目录模块：脚本运行时脚本目录在 sys.path；测试亦插入 HERE
import logging
from tools.safe_urlopen import safe_urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
ALERTS_URL = "http://127.0.0.1:8091/api/alerts"
STATE_PATH = os.path.join(HERE, "outbox", "alert_watch_state.json")
DAILY_CAP = 3

# 主动拒跑用的退出码：上游证据不足，本脚本选择不工作——这不是故障。
# 与 1（一般失败）分开，账本/健康卡才能一眼区分"我坏了"和"数据不可信"。
# 约定值必须与 agent_platform/tools/task_exit_audit.py 的
# EXIT_SEMANTICS[3] 保持一致。
EXIT_UPSTREAM_UNTRUSTED = 3

# 执行心跳（2026-10-25 EXEC-R8）：每次运行结束都写，含 rc=3 拒跑路径。
# 状态文件 alert_watch_state.json 只在"上游校验通过"时写，所以它衡量的是
# "最后一次成功"，无法证明"进程还在跑"；心跳文件专门证明后者，健康卡的
# ops.alert_watch_liveness 用它区分「本体在跑但上游不可信」与「真的停了」。
# 固定 C:\Temp（跨会话可读）；环境变量覆盖仅供测试，不影响生产默认路径。
HEARTBEAT_PATH = os.environ.get(
    "ALERT_WATCH_HEARTBEAT", os.path.join(r"C:\Temp", "alert_watch_heartbeat.json")
)

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
    except Exception as e:
        print(
            "ALERT_WATCH_WARN state load err %s" % str(e)[:100]
        )  # C2批2 吞错落盘（状态损坏会重复告警）
        return {}


def _save_state(st):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def _write_heartbeat(rc, upstream_ok, routes_count, note):
    """原子写执行心跳（tmp + os.replace）。与状态文件语义完全独立。

    心跳写失败只告警、不抛出——看门狗不能因为写自己的心跳而弄死主流程。
    字段：ts/epoch/rc/upstream_ok/routes_count/note（供健康卡判「本体是否在跑」）。
    upstream_ok 取 None = 非交易日不适用（trade_cal 跳过路径，EOD 源本就不出数），
    与 False（有交易日义务但上游不可信/拒跑）严格区分：健康卡据此把「按设计静默」
    与「真故障」分开，避免周末被记成连续故障。
    """
    payload = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": int(time.time()),
        "rc": int(rc),
        "upstream_ok": None if upstream_ok is None else bool(upstream_ok),
        "routes_count": int(routes_count),
        "note": str(note)[:200],
    }
    try:
        d = os.path.dirname(HEARTBEAT_PATH)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = HEARTBEAT_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, HEARTBEAT_PATH)
    except Exception as exc:  # noqa: BLE001
        print("ALERT_WATCH_WARN heartbeat write %s" % str(exc)[:100])
    return payload


def _skip_reason(now=None):
    """非交易日返回跳过原因，交易日返回 None。

    now 缺省取本机当天（date.today()），便于测试注入任意日期。
    周末/节假日不比对、不推送、不写状态文件：EOD 源按设计不在非交易日产出，
    静默是预期行为而非上游故障；心跳另写 upstream_ok=None 让健康卡区分二者。
    """
    if now is None:
        day = datetime.date.today()
    elif isinstance(now, datetime.datetime):
        day = now.date()
    elif isinstance(now, datetime.date):
        day = now
    elif isinstance(now, str):
        try:
            day = datetime.date.fromisoformat(now)
        except ValueError:
            return None  # 无法判定日期 → fail-open 不跳过，按原路径跑
    else:
        return None
    if trading_cal.is_trading_day(day):
        return None
    return (
        "non_trading_day: %s 非交易日（trade_cal/周末），"
        "无 EOD 源预期，按设计静默" % day.isoformat()
    )


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
    if cur - prev >= rule.get("diff", 1):  # diff=1 即任何增加触发
        return "%s → %s（+%d）" % (prev, cur, cur - prev)
    if rule.get("abs") and cur >= rule["abs"] and prev < rule["abs"]:
        return "首次达 %d（现 %d）" % (rule["abs"], cur)
    return None


def main():
    import alert_push

    reason = _skip_reason()
    if reason:
        # 周末/节假日：EOD 源按设计不出数，不是上游故障。不写状态文件
        # （保住最后一次交易日的熔断/基线），不推送；心跳 rc=0 + upstream_ok=None
        # 让健康卡把「按设计静默」与「交易日上游不可信(rc=3, upstream_ok=False)」分开。
        print("ALERT_WATCH_SKIP " + reason)
        _write_heartbeat(0, None, 0, reason)
        return 0
    today = time.strftime("%Y-%m-%d")
    try:
        with safe_safe_urlopen(ALERTS_URL, timeout=90) as r:
            res = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print("ALERT_WATCH_WARN portal fetch %s" % str(e)[:100])
        res = None

    validated = alert_push.validated_alerts(res, include_baseline=True)
    if validated is None:
        # The local import has its own source-health check; never trust a
        # legacy fallback payload just because the HTTP request failed.
        res = alert_push._fallback_alerts()
        validated = alert_push.validated_alerts(res, include_baseline=True)
    if validated is None:
        print("ALERT_WATCH_SOURCE_UNAVAILABLE invalid source_health")
        # exit 3 = 主动拒跑（上游证据不足），不是故障。
        # 用 1 会让它在账本里和"脚本崩溃"长得一模一样——2026-10-22 实测它
        # 因此静默了 4 天（10-18 18:15 之后状态文件再没更新，而卡上只显示
        # "9 个任务非零退出"）。约定见 task_exit_audit.EXIT_SEMANTICS。
        # 2026-10-25 EXEC-R8：拒跑也要写执行心跳（状态文件语义不变）。
        _write_heartbeat(
            EXIT_UPSTREAM_UNTRUSTED,
            False,
            0,
            "invalid source_health: 6 源完成时间证据不足，正确拒跑",
        )
        return EXIT_UPSTREAM_UNTRUSTED
    res, bad = validated
    if bad and not res["alerts"]:
        print("ALERT_WATCH_SOURCE_UNAVAILABLE partial coverage without alerts")
        _write_heartbeat(
            EXIT_UPSTREAM_UNTRUSTED,
            False,
            0,
            "partial coverage without alerts: 上游不可信，正确拒跑",
        )
        return EXIT_UPSTREAM_UNTRUSTED

    st = _load_state()
    if st.get("date") != today:  # 跨日：重置熔断计数，保留基线值
        st = {
            "date": today,
            "routes": {
                k: {"num": v.get("num"), "raw": v.get("raw")}
                for k, v in (st.get("routes") or {}).items()
            },
        }
    routes = st.setdefault("routes", {})
    first_run = not routes
    healthy_names = {
        alert_push.SOURCE_NAMES[source]
        for source in (alert_push.EOD_SOURCES)
        if source not in bad
    }
    hits = []
    for a in res.get("alerts") or []:
        nm = a.get("name", "")
        rule = RULES.get(nm)
        if not rule or nm not in healthy_names:
            continue
        raw = str(a.get("value", ""))
        num = _parse_num(nm, raw)
        prev = routes.get(nm) or {}
        fired = (
            None
            if first_run
            else _check(rule, prev.get("num"), num, prev.get("raw"), raw)
        )
        if fired:
            pushed = int(prev.get("pushed") or 0)
            if pushed < DAILY_CAP:
                # K-STOCK-414：暂不推进基线——推送成功才写（见下方），
                # 否则失败轮次后 prev==cur 永不再触发，预警正文永久丢失
                hits.append((nm, fired, a, num, raw))
                routes[nm] = prev
                continue
            # 达熔断也更新基线，避免次日放开后拿旧值狂补
        prev["num"], prev["raw"] = num, raw
        routes[nm] = prev

    if hits and not first_run:
        import card_build

        targets = alert_push._targets(alert_push._cfg())
        # K42-c：不做内网接入——卡内不放下钻按钮/门户二维码（外网飞书点不开）
        lines = ["**%s** %s" % (nm, fired) for nm, fired, _a, _num, _raw in hits]
        if bad:
            lines.insert(
                0,
                "**⚠️ 覆盖不足：%s 源不可用；仅展示已验证源，不代表全量。**"
                % "、".join(alert_push.SOURCE_NAMES[source] for source in bad),
            )
        card = card_build.build_card3(  # K-PUSH-008：v3 红域预警
            "alert",
            "⚡ 即时预警",
            time.strftime("%Y-%m-%d %H:%M") + " · 关键变化触发",
            ["\n".join(lines)],
            footer="AI工作站 · 阈值监听（每 30 分钟比对）· 不构成投资建议",
        )
        # 2026-10-25 P2/P3：统一分装（失败回落旧卡）
        try:
            sys.path.insert(
                0,
                os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "portal",
                ),
            )
            from push_unified_adapter import adapt as _adapt
            from feishu_card_v2 import m_url as _m

            _u = _m("entry")
            _btn = [("📱 移动端总览（H5）", _u, "primary")] if _u else None
            card = _adapt(
                "alertwatch",
                card,
                "alert",
                "即时预警",
                ["\n".join(lines)],
                ts=time.strftime("%Y-%m-%d %H:%M"),
                source="阈值监听",
                buttons=_btn,
                level="bad",
                footer="AI工作站 · 阈值监听（每 30 分钟比对）· 不构成投资建议",
            )
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/alert_watch.py", exc)
        try:
            tres = alert_push.push_card_multi(card, targets=targets)
        except Exception as exc:
            print("ALERT_WATCH_WARN card delivery %s" % str(exc)[:100])
            tres = {target["name"]: False for target in targets}
        ok = any(tres.values())
        if not ok:  # 文本兜底
            try:
                tres = alert_push.push_text_multi(
                    "⚡ 即时预警 "
                    + time.strftime("%m-%d %H:%M")
                    + "\n"
                    + "\n".join(lines),
                    targets,
                )
                ok = any(tres.values())
            except Exception as exc:
                print("ALERT_WATCH_WARN text delivery %s" % str(exc)[:100])
        if ok:  # 投递成功才扣每日额度及推进基线；失败保旧值下一轮重试
            for nm2, _f, _a, num2, raw2 in hits:
                p2 = routes.setdefault(nm2, {})
                p2["num"], p2["raw"] = num2, raw2
                p2["pushed"] = int(p2.get("pushed") or 0) + 1
        _save_state(st)
        print(
            "ALERT_WATCH_FIRED %d routes ok=%s status=%s %s"
            % (
                len(hits),
                ok,
                "sent" if ok else "failed",
                json.dumps(tres, ensure_ascii=False),
            )
        )
        _write_heartbeat(
            0 if ok else 1,
            True,
            len(hits),
            "fired routes=%d pushes_ok=%s" % (len(hits), ok),
        )
        return 0 if ok else 1

    _save_state(st)
    print("ALERT_WATCH_QUIET%s" % (" (baseline init)" if first_run else ""))
    _write_heartbeat(0, True, 0, "quiet" + (" (baseline init)" if first_run else ""))
    return 0


if __name__ == "__main__":
    try:
        _rc = main()
    except SystemExit:
        raise
    except Exception as _exc:  # noqa: BLE001
        _write_heartbeat(1, False, 0, "exception: %s" % str(_exc)[:120])
        raise
    sys.exit(_rc)
