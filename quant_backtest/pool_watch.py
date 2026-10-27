# -*- coding: utf-8 -*-
"""选票池触发器（WO-QT-20261020-070）：todays_picks 盘中影子监控。

监控集：portal/data/todays_picks.json，tier∈{T1,T2} 且 sources 含买入类
tag（tag 含「买」，排除含「卖」「回避」）的票。

触发判定（影子期初值，模块常量可调）：
  PULL  现价首次 ≤ entry_px × PULL_FACTOR（-1%）——回踩买点
  BREAK 现价首次 ≥ entry_px × BREAK_FACTOR（+3%）——突破买点
entry_px 选取（tie-break 明确）：该票 sources 中买入类条目里取 dt 最近者；
  dt 相同取列表中靠后者（同一批多来源时后写入者视为更新）；entry_px 与
  reg（择势）取同一条 source。

择势门：选中 source 的 reg=="down" 时，break 触发降级 reg_flag_only=True
（落池留痕但不发飞书提醒——下行环境中突破不可信）；pull 不受此门约束。

轮询：计划任务每 POLL_MINUTES 分钟一次（--once 单次模式供测试/计划任务）；
同票同方向 COOLDOWN_HOURS 小时冷却（pool_watch_state.json，watch_positions
pos_alert_state 同款机制）；CN 市场交易时段门控（--force 跳过，调试用）。

触发动作（14 天影子期铁律）：
  ① 落信号池 signal_pool.append_event（schema 过 normalize_signal 契约）；
  ② 飞书提醒走 watch_positions 同款推送路径（runner.push_feishu），默认
     dry（--send 才真推）；reg_flag_only 一律不推；
  绝不调 signal_consumer/engine——不进 combo 执行。

用法: python pool_watch.py [--once] [--dry] [--send] [--now "YYYY-MM-DD HH:MM"] [--force]
  --once   单次扫描后退出（默认 30 分钟轮询循环）
  --dry    纯检测：不落池、不写冷却、不推送，只打印
  --send   真推飞书（默认 dry：落池+写冷却但不推送）
  --now    注入当前时刻（测试/回放）
  --force  跳过交易时段门控
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import signal_pool  # noqa: E402

CST = timezone(timedelta(hours=8))

# —— 影子期初值，全部可调（模块常量）——
PULL_FACTOR = 0.99  # 回踩触发线：entry_px × 0.99（-1%）
BREAK_FACTOR = 1.03  # 突破触发线：entry_px × 1.03（+3%）
COOLDOWN_HOURS = 4  # 同票同方向冷却
POLL_MINUTES = 30  # 轮询间隔
TIER_ALLOWED = ("T1", "T2")

POOL_PATH = os.path.join(ROOT, "portal", "data", "todays_picks.json")
STATE_PATH = os.path.join(HERE, "data", "pool_watch_state.json")
LOG_PATH = os.path.join(HERE, "logs", "pool_watch.log")


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.now(CST).strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def is_buy_source(src):
    """买入类 tag：含「买」，排除含「卖」「回避」。"""
    tag = str((src or {}).get("tag") or "")
    return "买" in tag and "卖" not in tag and "回避" not in tag


def pick_entry(sources):
    """买入类 source 里选 entry 基准条 → (entry_px, reg, dt)。

    tie-break：dt 最近优先（降序稳定排序取首）；dt 相同取列表中靠后者
    （同一批多来源时后写入者视为更新）。无买入条返回 None。
    """
    cands = [s for s in (sources or []) if is_buy_source(s)]
    if not cands:
        return None
    best = sorted(cands, key=lambda s: str(s.get("dt") or ""))[-1]
    return best.get("entry_px"), best.get("reg"), best.get("dt")


def load_watchlist(pool_path=POOL_PATH):
    """监控集：T1/T2 且含买入类 source 的票 → [{code,name,tier,entry_px,reg,dt}]。"""
    try:
        with open(pool_path, encoding="utf-8") as f:
            pool = json.load(f)
    except Exception as e:
        log("pool load FAIL %s" % str(e)[:120])
        return []
    out = []
    for st in pool.get("stocks") or []:
        if st.get("tier") not in TIER_ALLOWED:
            continue
        picked = pick_entry(st.get("sources"))
        if not picked or not picked[0]:
            continue
        entry_px, reg, dt = picked
        out.append(
            {
                "code": st["code"],
                "name": st.get("name") or st["code"],
                "tier": st.get("tier"),
                "entry_px": float(entry_px),
                "reg": reg,
                "dt": dt,
                "n_hit": st.get("n_hit"),
            }
        )
    return out


def to_sina(code):
    """池代码（002007.SZ）→ 新浪代码（sz002007），CN 分支同 watch_positions。"""
    if "." in code:
        num, suf = code.split(".")
        return suf.lower() + num
    return code


def fetch_quotes(codes):
    """新浪实时行情（watch_positions 同款接口，真实路径仅 main 使用）。"""
    sys.path.insert(0, os.path.join(ROOT, "news_collector"))
    from quote_snapshot import fetch_quotes as _fq

    return _fq(codes)


def mkt_open_cn(now):
    """CN 交易时段（北京时间粗粒度，同 watch_positions.mkt_open 的 CN 分支）。"""
    wd, t = now.weekday(), now.hour * 60 + now.minute
    return wd <= 4 and 9 * 60 + 15 <= t <= 15 * 60 + 15


def load_state(state_path=STATE_PATH):
    try:
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}  # 损坏 fail-open：宁重发不静默（guard_alert 判例）


def save_state(state, state_path=STATE_PATH):
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, state_path)


def in_cooldown(state, code, direction, now, hours=COOLDOWN_HOURS):
    last = (state.get(code) or {}).get(direction)
    if not last:
        return False
    try:
        return now - datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=now.tzinfo
        ) < timedelta(hours=hours)
    except Exception:
        return False


def evaluate(price, entry_px, pull_factor=PULL_FACTOR, break_factor=BREAK_FACTOR):
    """触发判定：pull 优先（价格不可能同时回踩与突破）。"""
    if price <= entry_px * pull_factor:
        return "pull"
    if price >= entry_px * break_factor:
        return "break"
    return None


def push_feishu(text):
    """watch_positions 同款推送路径（runner.push_feishu，延迟 import 零副作用）。"""
    import runner

    return runner.push_feishu(text)


def run_once(
    fetch,
    now,
    pool_path=POOL_PATH,
    state_path=STATE_PATH,
    pool_jsonl=None,
    send=False,
    dry=False,
    push=None,
    gate=True,
):
    """单轮扫描。fetch(codes)->[{code:新浪码, price:现价}]；push(text)->bool。

    返回 summary dict：{n_watch, n_trigger, triggers, pushed, appended, skipped_gate}。
    触发动作：落池（signal_pool）+ 写冷却 + （--send 且非 reg_flag_only）推送。
    """
    if gate and not mkt_open_cn(now):
        return {
            "n_watch": 0,
            "n_trigger": 0,
            "triggers": [],
            "pushed": False,
            "appended": 0,
            "skipped_gate": True,
        }
    picks = load_watchlist(pool_path)
    if not picks:
        return {
            "n_watch": 0,
            "n_trigger": 0,
            "triggers": [],
            "pushed": False,
            "appended": 0,
            "skipped_gate": False,
        }
    try:
        quotes = {q["code"]: q for q in fetch([to_sina(p["code"]) for p in picks])}
    except Exception as e:
        log("fetch FAIL %s" % str(e)[:120])
        return {
            "n_watch": len(picks),
            "n_trigger": 0,
            "triggers": [],
            "pushed": False,
            "appended": 0,
            "skipped_gate": False,
        }

    state = {} if dry else load_state(state_path)
    triggers = []
    for p in picks:
        q = quotes.get(to_sina(p["code"]))
        price = q.get("price") if q else None
        if not price:
            continue
        d = evaluate(float(price), p["entry_px"])
        if not d or in_cooldown(state, p["code"], d, now):
            continue
        # 择势门：reg=down 的 break 降级（落池不提醒），reg 与 entry_px 同源
        reg_flag_only = bool(d == "break" and p["reg"] == "down")
        triggers.append(
            dict(p, direction=d, price=float(price), reg_flag_only=reg_flag_only)
        )
        state.setdefault(p["code"], {})[d] = now.strftime("%Y-%m-%d %H:%M:%S")

    appended = 0
    pushed = False
    for t in triggers:
        if dry:
            continue
        ev = signal_pool.build_signal(
            code=t["code"],
            name=t["name"],
            direction=t["direction"],
            trigger_price=t["price"],
            entry_px=t["entry_px"],
            now=now,
            reg=t["reg"],
            source_dt=t["dt"],
            tier=t["tier"],
            n_hit=t.get("n_hit"),
            reg_flag_only=t["reg_flag_only"],
            pool_path=pool_path,
            pool_jsonl=pool_jsonl,
        )
        try:
            _, ok = signal_pool.append_event(ev, path=pool_jsonl)
        except signal_pool.signal_consumer.SignalPointError as e:
            log("append REJECT %s %s" % (t["code"], str(e)[:160]))
            continue
        appended += int(ok)
        if t["reg_flag_only"] or not send:
            continue
        ok_push = (push or push_feishu)(
            "【选票池触发·影子】%s %s\n· %s(%s) %s 触发：现 %.2f ≤/≥ 参考买 %.2f"
            " · tier %s · reg %s · 冷却 %dh · 纸面模拟不构成投资建议"
            % (
                now.strftime("%m-%d %H:%M"),
                "回踩" if t["direction"] == "pull" else "突破",
                t["name"],
                t["code"],
                t["direction"],
                t["price"],
                t["entry_px"],
                t["tier"],
                t["reg"],
                COOLDOWN_HOURS,
            )
        )
        pushed = pushed or bool(ok_push)
    if not dry:
        save_state(state, state_path)
        if triggers:
            log(
                "triggers=%s appended=%d pushed=%s"
                % (
                    json.dumps(
                        [(t["code"], t["direction"]) for t in triggers],
                        ensure_ascii=False,
                    ),
                    appended,
                    pushed,
                )
            )
    return {
        "n_watch": len(picks),
        "n_trigger": len(triggers),
        "triggers": triggers,
        "pushed": pushed,
        "appended": appended,
        "skipped_gate": False,
    }


def main():
    once = "--once" in sys.argv
    dry = "--dry" in sys.argv
    send = "--send" in sys.argv
    force = "--force" in sys.argv
    now = datetime.now(CST)
    if "--now" in sys.argv:
        raw = sys.argv[sys.argv.index("--now") + 1]
        now = (
            datetime.strptime(raw, "%Y-%m-%d %H:%M")
            if "-" in raw and raw.count("-") >= 2 and "T" not in raw
            else datetime.fromisoformat(raw)
        )
        now = now.replace(tzinfo=CST)
    while True:
        rep = run_once(fetch_quotes, now, send=send, dry=dry, gate=not force)
        print(
            "POOL_WATCH ok watch=%d trigger=%d appended=%d pushed=%s%s"
            % (
                rep["n_watch"],
                rep["n_trigger"],
                rep["appended"],
                rep["pushed"],
                " gate_skip" if rep["skipped_gate"] else "",
            )
            + (
                " "
                + json.dumps(
                    [
                        (
                            t["name"],
                            t["direction"],
                            round(t["price"], 2),
                            "flag_only" if t["reg_flag_only"] else "",
                        )
                        for t in rep["triggers"]
                    ],
                    ensure_ascii=False,
                )
                if rep["triggers"] and dry
                else ""
            )
        )
        if once or dry:
            return
        time.sleep(POLL_MINUTES * 60)


if __name__ == "__main__":
    main()
