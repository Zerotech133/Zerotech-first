#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vol_target 实盘 A/B 对照采集位（决议②落地，A1 拍板 20261015）。

状态机（随日批值守，纯纸面记录零交易行为）：
  waiting_for_cross —— 宽度 <45% 穿越未确认：每日只记 {date, breadth,
                       phase}；穿越监测器（breadth_cross_state.json）
                       出现 CONFIRM_HOLD 后自动翻相；
  tracking          —— 穿越确认：b 池信号按 hash(code)%2 交错切 A/B
                       两虚拟子账本（A 开 vol_target 缩放、B 等权），
                       日频记净值/回撤/仓位/换手差异，评估期 60 交易日
                       （对齐 G3 影子窗）。

预注册判据在启动首日冻结进 config（sha256 自证此后未改），转正评审
只认冻结版——防事后归因。
"""

import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SERIES_FP = os.path.join(HERE, "reports", "qd_breadth_series.json")
BC_STATE_FP = os.path.join(HERE, "data", "breadth_cross_state.json")
BC_EVENTS_FP = os.path.join(HERE, "data", "breadth_cross_events.jsonl")
STATE_FP = os.path.join(HERE, "data", "voltarget_ab_state.json")
OUT_FP = os.path.join(HERE, "data", "voltarget_ab.jsonl")
CONFIG_FP = os.path.join(HERE, "data", "voltarget_ab_config.json")

EVAL_DAYS = 60          # 评估期（对齐 G3 影子窗）
CROSS_TH = 0.45

# 预注册转正判据（b建仓评估启动包 §4，20261015 冻结）
PRE_REGISTRATION = {
    "frozen_at": "2026-10-15",
    "eval_days": EVAL_DAYS,
    "split": "hash(code) % 2 交错切 A/B 子账本",
    "group_a": "vol_target on (scale=clip(0.15/rv20, 0.25, 1.0))",
    "group_b": "等权（现行为）",
    "promote_rule": "A 组回撤显著小于 B 组（同窗对比）且收益损失 < "
                    "回撤改善的 1/3，则全池转 A",
    "source": "b建仓评估启动包_2026-10-15.md §4（决议②）",
}

CONFIG_TEMPLATE = {
    "pre_registration": PRE_REGISTRATION,
    "vol_target": {"enabled": True, "target_vol": 0.15,
                   "lookback": 20, "floor": 0.25, "cap": 1.0},
    "phase_started": None,
    "config_sha256": None,
}


def _load(fp):
    try:
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _lake_last(series):
    for d, b in zip(reversed(series.get("dates") or []),
                    reversed(series.get("breadth") or [])):
        if isinstance(b, (int, float)):
            return str(d), float(b)
    return None, None


def _cross_confirmed(events_fp=None):
    """穿越监测事件流 → 是否已发生 CONFIRM_HOLD（一次性闩锁判定）。

    监测器状态文件只存 armed/pending（确认后即清），确认历史只在
    events jsonl（breadth_crossing_monitor._append_events）。
    """
    fp = events_fp or BC_EVENTS_FP      # 调用时取全局（默认参数会绑定定义期）
    try:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                if '"CONFIRM_HOLD"' in line:
                    return True
    except OSError:
        pass
    return False


def ensure_config(fp=CONFIG_FP):
    """首日冻结预注册判据；已存在则校验 sha 未被改。"""
    if os.path.exists(fp):
        cfg = _load(fp)
        sha = cfg.pop("config_sha256", None)
        calc = hashlib.sha256(json.dumps(cfg, sort_keys=True,
                                         ensure_ascii=False).encode()
                              ).hexdigest()[:16]
        return cfg, (sha == calc), calc
    cfg = dict(CONFIG_TEMPLATE)
    cfg["pre_registration"] = json.loads(json.dumps(PRE_REGISTRATION))
    body = {k: v for k, v in cfg.items() if k != "config_sha256"}
    cfg["config_sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fp)
    return cfg, True, cfg["config_sha256"]


def advance(series_fp=SERIES_FP, state_fp=STATE_FP,
            out_fp=OUT_FP, config_fp=CONFIG_FP):
    """日批尾推进：返回本次事件行（无事件返回状态行）。"""
    cfg, intact, sha = ensure_config(config_fp)
    series = _load(series_fp)
    date, br = _lake_last(series)
    if not date:
        return None
    st = _load(state_fp)
    if st.get("date") == date and st.get("phase") == "tracking":
        pass                      # 同日已翻相，幂等保留
    phase = st.get("phase") or "waiting_for_cross"
    event = None
    if phase == "waiting_for_cross" and _cross_confirmed():
        phase = "tracking"
        event = "PHASE_TRACKING"
    row = {"date": date, "breadth": round(br, 4), "phase": phase,
           "eval_days": EVAL_DAYS, "config_sha256": sha}
    if event:
        row["event"] = event
    # 幂等：同日同行覆盖
    rows = []
    if os.path.exists(out_fp):
        with open(out_fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        r = json.loads(line)
                        if str(r.get("date")) != date:
                            rows.append(r)
                    except Exception:
                        continue
    rows.append(row)
    tmp = out_fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, out_fp)
    st_out = {"date": date, "phase": phase}
    tmp2 = state_fp + ".tmp"
    with open(tmp2, "w", encoding="utf-8") as f:
        json.dump(st_out, f, ensure_ascii=False)
    os.replace(tmp2, state_fp)
    return row


if __name__ == "__main__":
    print(json.dumps(advance(), ensure_ascii=False))
