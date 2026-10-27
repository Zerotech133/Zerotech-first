# -*- coding: utf-8 -*-
"""宽度穿越监测器（复盘落地 2026-10-14 breadth-mon：正确路径第一条可执行动作）。

出处：《历史数据全景复盘与当前正确路径_2026-10-14》三条 A 级共识之执行面——
  当前宽度 32.64%（regime=range、信号枯竭），不抄底；等宽度上穿 45%
  穿越确认后再评估入场；假穿越防护 = 穿越后 5 个交易日内跌回 40% 以下
  则撤回（列为 W8 评审议题，本监测器先行观察、只告警不拦任何策略）。

职责：每日一次，读 reports/qd_breadth_series.json 宽度序列（平行数组、
末位最新锚，与 gate_shadow_collect.series_anchor 同一口径），对上次处理
锚之后的新增锚逐日推进状态机，产生三类事件（经 guard_alert.alert_once
推飞书，12h 去重；键含事件日期，同事件天然只发 1 次）：

  CROSS_UP      prev<45% 且 cur≥45%        → 提醒评估入场窗口（附假穿越条款）
  FAKE_EXIT     穿越后 5 个交易日内 <40%    → 提醒假穿越撤回
  CONFIRM_HOLD  穿越后满 5 个交易日仍 ≥40% → 提醒穿越确认保持

状态与幂等：
  · data/breadth_cross_state.json：last_date（已处理锚）+ pending
    （穿越观察态：cross_date/days_since）；原子写（tmp+os.replace）；
  · 首跑（无状态文件）= 播种模式：不回放 912 天历史、不告警，仅把
    last_date 置为序列最新锚（当前 32.64% 远离 45%，无未决穿越态可丢）；
  · 同锚重复处理自然跳过（date ≤ last_date 不推进）。

事件留痕：data/breadth_cross_events.jsonl 原子追加（与推送解耦：告警
失败事件照落，人工可对账）。

挂载点（注记式 try/except，失败不影响产线）：qpzm_daily_batch
.archive_daily_batch() 尾部（gate_shadow_collect 同位，每日链 14:40）。
亦可手动 CLI：python breadth_crossing_monitor.py [--dry] [--series P]

用法（测试/演练）:
  ... --dry            # 只推进状态机+落事件，不推飞书不写状态
  ... --seed           # 强制播种模式（状态丢失后重建用）
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SERIES_FP = os.path.join(HERE, "reports", "qd_breadth_series.json")
STATE_FP = os.path.join(HERE, "data", "breadth_cross_state.json")
EVENTS_FP = os.path.join(HERE, "data", "breadth_cross_events.jsonl")
LOG = "C:/Temp/breadth_cross_monitor.log"

CROSS_TH = 0.45  # 穿越确认线（观察带下限，W8 评审前观察口径）
FAKE_TH = 0.40  # 假穿越撤回线
CONFIRM_DAYS = 5  # 假穿越观察窗（交易日）


def _log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def load_series(fp=SERIES_FP):
    """宽度序列 → [(date, breadth 0-1), ...]（时间升序）。

    历史段存在 null 宽度锚（实测），非法/缺失锚跳过不阻塞
    （状态机按相邻有效锚推进，穿越判定语义不变）。
    """
    with open(fp, encoding="utf-8") as f:
        d = json.load(f)
    dates, brs = d.get("dates") or [], d.get("breadth") or []
    if not dates or len(dates) != len(brs):
        raise ValueError("series empty/mismatch")
    out = []
    for a, b in zip(dates, brs):
        try:
            out.append((str(a), float(b)))
        except (TypeError, ValueError):
            continue  # null/坏值锚跳过
    if not out:
        raise ValueError("series no valid anchors")
    return out


def load_state(fp=STATE_FP):
    """读状态；缺失/损坏 → None（调用方走播种）。"""
    try:
        with open(fp, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("last_date") else None
    except Exception:
        return None


def save_state(state, fp=STATE_FP):
    """原子写（tmp + os.replace）；失败只落日志（下轮重算）。"""
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        tmp = fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, fp)
        return True
    except Exception as e:
        _log("state save error: %s" % str(e)[:120])
        return False


def _event(kind, date, detail):
    return {
        "kind": kind,
        "date": date,
        "detail": detail,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _alert_text(ev):
    """三类事件 → 飞书文本（附复盘出处与行动建议，告警可自解释）。"""
    k, d, det = ev["kind"], ev["date"], ev["detail"]
    if k == "CROSS_UP":
        return (
            "\U0001f7e2 宽度穿越提醒 · %s\n"
            "宽度 %s 上穿 %.0f%%（%s）\n"
            "行动: 复盘口径下可开始评估抄底/入场信号；注意假穿越条款——"
            "若 %d 个交易日内跌回 %.0f%% 以下则撤回，届时另行告警。\n"
            "出处: 历史复盘 2026-10-14（regime_timeline 同款状态后20日"
            "均值+6.4%%，穿越确认优先于抄底冲动）。"
            % (
                d,
                det["breadth_txt"],
                det["th"] * 100,
                det["prev_txt"],
                CONFIRM_DAYS,
                FAKE_TH * 100,
            )
        )
    if k == "FAKE_EXIT":
        return (
            "\U0001f534 假穿越撤回提醒 · %s\n"
            "%s 穿越后第 %d 个交易日跌回 %.0f%% 以下（%s）\n"
            "行动: 撤回入场评估，回到等待状态；假穿越防护条款触发，"
            "与 2026-10-14 复盘「5日跌回<40%%撤回」一致。"
            % (
                d,
                det["cross_date"],
                det["days_since"],
                FAKE_TH * 100,
                det["breadth_txt"],
            )
        )
    if k == "CONFIRM_HOLD":
        return (
            "\U0001f535 穿越确认保持 · %s\n"
            "%s 穿越后满 %d 个交易日未跌回 %.0f%% 以下（%s）\n"
            "行动: 假穿越防护通过，穿越确认成立；按复盘口径维持"
            "信号驱动入场，宽度仅作环境背景。"
            % (d, det["cross_date"], CONFIRM_DAYS, FAKE_TH * 100, det["breadth_txt"])
        )
    return json.dumps(ev, ensure_ascii=False)


def advance(pairs, state=None, prev_br=None, seed=False):
    """状态机推进：pairs 为新锚列表（时间升序），返回 (events, state)。

    prev_br：首新锚的前一锚宽度（跨调用续接用；monitor_run 从序列
    last_date 锚取）。逐日顺序：先结算 pending（假穿越/满窗确认），
    再判新上穿（仅无观察态时；同日先结算再判新穿越，两事件可同日落）。
    """
    state = dict(state or {})
    pending = state.get("pending")
    # armed 武装位：同一穿越 episode 只告一轮。CROSS_UP 后解除（观察窗
    # 内回落再上穿、确认保持后的 40~45 区间震荡都不再重复告警）；
    # FAKE_EXIT 撤回后重新武装（翻案可再触发）；无观察态时宽度深度回落
    # <40%（新一轮下跌-修复周期）也重新武装。
    armed = bool(state.get("armed", True))
    events = []
    for date, br in pairs:
        if pending:
            pending = dict(pending)
            pending["days_since"] = int(pending.get("days_since", 0)) + 1
            if br < FAKE_TH:
                events.append(
                    _event(
                        "FAKE_EXIT",
                        date,
                        {
                            "cross_date": pending["cross_date"],
                            "days_since": pending["days_since"],
                            "breadth_txt": "%.2f%%" % (br * 100),
                        },
                    )
                )
                pending = None
                armed = True
            elif pending["days_since"] >= CONFIRM_DAYS:
                events.append(
                    _event(
                        "CONFIRM_HOLD",
                        date,
                        {
                            "cross_date": pending["cross_date"],
                            "breadth_txt": "%.2f%%" % (br * 100),
                        },
                    )
                )
                pending = None
        if pending is None and prev_br is not None and br < FAKE_TH:
            armed = True  # 深度回落重新武装
        # 仅武装且无观察态时判新上穿（观察窗内回落再上穿不重置假穿越
        # 时钟、不重复告警；假穿越撤回/深度回落后允许再触发）
        if (
            armed
            and pending is None
            and prev_br is not None
            and prev_br < CROSS_TH <= br
        ):
            events.append(
                _event(
                    "CROSS_UP",
                    date,
                    {
                        "prev_txt": "%.2f%%" % (prev_br * 100),
                        "breadth_txt": "%.2f%%" % (br * 100),
                        "th": CROSS_TH,
                    },
                )
            )
            pending = {"cross_date": date, "days_since": 0}
            armed = False
        prev_br = br
    state["pending"] = pending
    state["armed"] = armed
    if pairs:
        state["last_date"] = pairs[-1][0]
    if seed:
        # 播种：只记锚不产生事件（调用方保证 pairs 为空或忽略 events）
        events = []
    return events, state


def _append_events(events, fp=EVENTS_FP):
    """事件留痕（原子追加单次 write；失败只落日志）。"""
    if not events:
        return 0
    try:
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        fd = os.open(fp, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            payload = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return len(events)
    except Exception as e:
        _log("events append error: %s" % str(e)[:120])
        return 0


def monitor_run(
    series_fp=SERIES_FP,
    state_fp=STATE_FP,
    events_fp=EVENTS_FP,
    send=True,
    dry=False,
    seed=False,
):
    """日检入口（挂载点调用）：返回本次产生的事件列表。自身不抛。"""
    try:
        series = load_series(series_fp)
        st = load_state(state_fp)
        if st is None or seed:
            # 播种：last_date 置最新锚，不回放历史不告警
            st = {
                "last_date": series[-1][0],
                "pending": None,
                "seeded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            if not dry:
                save_state(st, state_fp)
            _log("seed last_date=%s%s" % (st["last_date"], " (forced)" if seed else ""))
            return []
        last = str(st.get("last_date"))
        pending_pairs = [(d, b) for d, b in series if str(d) > last]
        if not pending_pairs:
            return []
        # 跨调用续接：首新锚的前一锚宽度（last_date 锚），穿越判定用
        prev_br = next((b for d, b in series if str(d) == last), None)
        events, st2 = advance(pending_pairs, st, prev_br=prev_br)
        if not dry:
            save_state(st2, state_fp)
            _append_events(events, events_fp)
        for ev in events:
            text = _alert_text(ev)
            if send and not dry:
                try:
                    from guard_alert import alert_once

                    ok, _ = alert_once("breadth_%s_%s" % (ev["kind"], ev["date"]), text)
                    _log("alert %s %s sent=%s" % (ev["kind"], ev["date"], ok))
                except Exception as e:
                    _log("alert error %s: %s" % (ev["kind"], str(e)[:120]))
            else:
                _log("dry/skip alert %s %s" % (ev["kind"], ev["date"]))
        if events:
            _log(
                "monitor +%d events (last=%s -> %s)"
                % (len(events), last, st2.get("last_date"))
            )
        return events
    except Exception as e:
        _log("monitor_run fatal: %s" % str(e)[:200])
        return []


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="宽度穿越监测器（45%确认/5日假穿越防护）")
    ap.add_argument("--series", default=SERIES_FP)
    ap.add_argument("--dry", action="store_true", help="只推演不写状态不推送")
    ap.add_argument("--seed", action="store_true", help="强制播种模式")
    a = ap.parse_args(argv)
    events = monitor_run(series_fp=a.series, dry=a.dry, seed=a.seed)
    print("BREADTH_MONITOR events=%d%s" % (len(events), " (dry)" if a.dry else ""))
    for ev in events:
        print("  %s @%s %s" % (ev["kind"], ev["date"], ev["detail"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
