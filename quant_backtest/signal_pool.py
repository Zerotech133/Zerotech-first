# -*- coding: utf-8 -*-
"""信号池落账（WO-QT-20261020-071）：触发事件 append-only jsonl 落池。

todays_picks 池触发器（pool_watch，WO-070）产出的触发事件统一落本池：
  quant_backtest/data/signal_pool.jsonl（一行一事件，append-only，
  不 update 不 delete——与 k_stock/paper 拒单留痕同款铁律）。

事件 schema 对齐 k_stock/paper/signal_consumer.normalize_signal 契约
（WO-005 §3/§4，只 import 不改）：append_event 落账前先过 normalize_signal
全量校验（必填字段/kind 负面清单/signal_id 字符集/时效/触发证据/池印记），
校验失败抛 SignalPointError（closed on failure，禁静默降级）——影子期
落进池里的每一条都必须是将来可无损接入消费链的合法信号点。

池印记（decision_snapshot.normalize_pool_stamp 口径）：
  pool_id="todays_picks"、pool_version=0（文件型池=软版本 0，见
  decision_snapshot 模块注），附 scan_run_date/generated_at 供回放。

signal_id 确定性生成（幂等防重）：{pool}.{code}.{方向}.{yyyymmdd}.r{轮次}，
轮次 = 当日该票同方向在池中已有事件数 + 1；append_event 对同 signal_id
查重，重复追加静默跳过（返回 appended=False），不产生重复行。

只读接口 read_events(since=None) 供后续消费端/复盘：尾行截断/坏行跳过
（日志域常见态，不炸），since 为 ISO 时刻文本，按 triggered_at 过滤。

边界：影子期 14 天内本模块只落账，绝不调 signal_consumer 消费链/engine
（不进 combo 执行）；不碰生产 k_stock/data 下任何文件。
"""
import json
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAPER_DIR = os.path.join(ROOT, "k_stock", "paper")
if PAPER_DIR not in sys.path:
    sys.path.insert(0, PAPER_DIR)

import signal_consumer  # noqa: E402  契约校验（只 import 不改）

POOL_ID = "todays_picks"
DATA_DIR = os.path.join(HERE, "data")
DEFAULT_POOL_JSONL = os.path.join(DATA_DIR, "signal_pool.jsonl")

# 影子期证据不足（无人工复核），置信度按中位定值；时效与触发器冷却同窗
SIGNAL_CONFIDENCE = 0.5
VALID_HOURS = 4
RULE_VERSION = "1"


def _pool_jsonl_path(path=None):
    return path or DEFAULT_POOL_JSONL


def build_signal(*, code, name, direction, trigger_price, entry_px, now,
                 reg=None, source_dt=None, tier=None, n_hit=None,
                 reg_flag_only=False, pool_path=None, pool_jsonl=None):
    """构造一条合法信号点事件（过 normalize_signal 校验的确定性产物）。

    direction: "pull"（回踩 -1%）| "break"（突破 +3%）
    now: 触发时刻 datetime（触发器注入，CST）
    pool_path: todays_picks.json 路径（取 scan_run_date/generated_at 做印记）
    pool_jsonl: 池文件路径（轮次计数用，测试注入 tmp_path）
    """
    meta = pool_meta(pool_path)
    date_s = now.strftime("%Y%m%d")
    n = count_events(code, direction, date_s, path=_pool_jsonl_path(pool_jsonl))
    rule_id = "pool_watch_%s" % direction
    ev = {
        "signal_id": "%s.%s.%s.%s.r%d" % (POOL_ID, code, direction,
                                          date_s, n + 1),
        "kind": signal_consumer.SIGNAL_KIND_TRIGGER,
        "stock": code,
        "name": name,
        "action": "BUY",           # 回踩买/突破买均为入场类触发
        "confidence": SIGNAL_CONFIDENCE,
        "rule_id": rule_id,
        "rule_version": RULE_VERSION,
        "triggered_at": now.isoformat(timespec="seconds"),
        "valid_until": (now + timedelta(hours=VALID_HOURS)
                        ).isoformat(timespec="seconds"),
        "evidence": {
            "trigger_price": float(trigger_price),
            "entry_px": float(entry_px),
            "direction": direction,
            "reg": reg,
            "source_dt": source_dt,
            "tier": tier,
            "n_hit": n_hit,
            "reg_flag_only": bool(reg_flag_only),
        },
        "pool_stamp": {
            "pool_id": POOL_ID,
            "pool_version": 0,     # 文件型池软版本（decision_snapshot 口径）
            "scan_run_date": meta.get("scan_run_date"),
            "generated_at": meta.get("generated_at"),
        },
        "direction": direction,
        "reg_flag_only": bool(reg_flag_only),
    }
    return ev


def pool_meta(pool_path):
    """todays_picks.json 顶层印记（scan_run_date/generated_at），读失败降级空。"""
    try:
        with open(pool_path, encoding="utf-8") as f:
            d = json.load(f)
        return {"scan_run_date": d.get("scan_run_date"),
                "generated_at": d.get("generated_at")}
    except Exception:
        return {}


def count_events(code, direction, date_s, path=None):
    """池中该票该方向该日期已有事件数（signal_id 前缀精确计数）。"""
    prefix = "%s.%s.%s.%s." % (POOL_ID, code, direction, date_s)
    return sum(1 for e in read_events(path=path)
               if str(e.get("signal_id", "")).startswith(prefix))


def append_event(event, path=None):
    """落账一行（append-only）。先过 normalize_signal 全量校验（closed on
    failure），再查重（同 signal_id 不重复落）。返回 (规范化事件, appended)。"""
    sig = signal_consumer.normalize_signal(event)
    p = _pool_jsonl_path(path)
    if any(e.get("signal_id") == sig["signal_id"] for e in read_events(path=p)):
        return sig, False
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(sig, ensure_ascii=False) + "\n")
    return sig, True


def read_events(path=None, since=None):
    """只读：全部事件（since=ISO 时刻文本时按 triggered_at >= since 过滤）。

    坏行/尾行截断跳过不炸（append-only jsonl 的日志域常见态）。"""
    p = _pool_jsonl_path(path)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if since and str(e.get("triggered_at", "")) < since:
                continue
            out.append(e)
    return out
