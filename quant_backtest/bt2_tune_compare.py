# -*- coding: utf-8 -*-
"""K-PUSH-011 tune_bt 自动跑批：沙盒草案 → bt2 回测对比（baseline vs override）。

设计要点：
- BASE 自愈：引擎硬编码 C:/项目/老田的AI助手 本机不存在（E: 无盘），import 时改指本仓；
  本机引擎数据已实证完整（data/ashare 5601 文件/247MB + pool + ledger）。
- 规则模板：intent 维度 → 具体参数覆盖（chanlun 用差量、risk/param 用绝对值），确定可复现。
- 达标判据（两条件同时满足）：override 总收益 ≥ baseline×1.10 且 最大回撤 ≤ baseline×1.20；
  引擎错误或样本不足（n_trades<5）→ inconclusive（留 pending_manual 防误杀，可重跑）。
- 并发：进程内 _LOCK 串行多单；store bt2_result.running 标记防跨进程重复。
"""

import os
import sys
import time
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import backtest_engine_v2 as eng

_LOCK = threading.Lock()


def _selfheal_base():
    """BASE 硬编码 C:/项目/老田的AI助手 → 本仓站根（E: 本机不存在，97 个历史 bt2 结果
    证明引擎曾在本仓路径跑通；数据完整性已实证）。"""
    if os.path.isdir(eng.BASE):
        return False
    base = os.path.dirname(_HERE)  # 站根 c:\项目\老田的AI助手
    eng.BASE = base
    eng.DATA = os.path.join(base, "quant_backtest", "data", "ashare")
    eng.POOL_SRC = os.path.join(
        base, "k_stock", "data", "poc_adapter_chanlun_20261012.json"
    )
    eng.LEDGER = os.path.join(
        base, "quant_backtest", "data", "daily_ledger", "ledger_20251013_20261012.json"
    )
    eng.OUT_DIR = os.path.join(base, "quant_backtest", "data", "bt2")
    return True


_SELFHEALED = _selfheal_base()

# ── 规则模板（intent → 参数覆盖；*_delta=相对 baseline 加减，其余=绝对值） ──
RULE_TEMPLATES = {
    "chanlun": {"top_n_delta": 2, "slot_pct_delta": 0.02, "cap_high_delta": 0.05},
    "risk": {
        "stop_pct": 0.05,
        "trail_activate": 0.05,
        "trail_drawdown": 0.035,
        "fail_buffer": 0.03,
    },
    "param": {"sig_expiry": 3, "gate_mode": "weak", "min_score": 40},
}
TUNE_BT_INTENTS = tuple(RULE_TEMPLATES)  # freq/opinion 无模板不自动跑

# 达标判据（可调）
RETURN_LIFT = 1.10  # override 总收益 ≥ baseline×1.10
DRAWDOWN_WORSE = 1.20  # 且 override 最大回撤 ≤ baseline×1.20

# 引擎键集合：vip_overrides 中含这些键时视为"上次部署配置"参与 baseline
_ENGINE_KEYS = (
    "strategy",
    "d0",
    "d1",
    "top_n",
    "slot_pct",
    "cap_mode",
    "cap_high",
    "cap_mid",
    "cap_low",
    "stop_pct",
    "trail_activate",
    "trail_drawdown",
    "gate_mode",
    "min_score",
    "sig_expiry",
    "buy_filter",
    "universe",
)


def _apply_template(base, tpl):
    ov = dict(base)
    for k, v in tpl.items():
        if k.endswith("_delta"):
            ov[k[:-6]] = float(base.get(k[:-6], 0)) + v
        else:
            ov[k] = v
    return ov


def _run_one(params, jid, progress=None):
    """run_job 封装：返回 (summary, err)。progress 默认 noop 防 None 崩溃。"""
    if progress is None:
        progress = lambda st, pc, note: None  # noqa: E731
    res, err = eng.run_job(params, jid, progress)
    if err:
        return None, err
    if not isinstance(res, dict) or "summary" not in res:
        return None, "run_job 返回无 summary: %s" % (str(res)[:120])
    return res["summary"], None


def run_compare(req_id, doc, vip=None):
    """单请求自动对比：baseline vs override 各跑一次 run_job。

    doc: tune_store 的详情（须含 intent、draft.bt2_params_override）。
    vip: vip_overrides dict（customer 上次部署配置，可空）。
    返回 verdict ∈ pass / fail / inconclusive + 对比数据；引擎错误一律 inconclusive。
    """
    intent = (doc or {}).get("intent") or "opinion"
    if intent not in TUNE_BT_INTENTS:
        return {"verdict": "inconclusive", "reason": "intent 无规则模板（%s）" % intent}
    draft = (doc or {}).get("draft") or {}
    override_hint = draft.get("bt2_params_override") or {}
    symbols = draft.get("symbols") or []

    with _LOCK:  # 进程内串行，防数据装载/回测资源竞争
        t0 = time.time()
        base = dict(eng.DEFAULTS)
        # 2026-11-10：引擎默认预设改为 v2 后，此处的「baseline」必须钉死旧口径，
        # 否则对比的基准会随默认预设漂移（本脚本的 baseline 语义=未调参的产线配置）。
        base.setdefault("preset", "none")
        # 上次部署配置覆盖 baseline（vip 含引擎键时）
        cust = (doc or {}).get("customer") or ""
        cust_vip = (vip or {}).get(cust) or {}
        if isinstance(cust_vip, dict) and any(k in cust_vip for k in _ENGINE_KEYS):
            for k in _ENGINE_KEYS:
                if k in cust_vip and cust_vip[k] not in (None, ""):
                    base[k] = cust_vip[k]
        # draft 覆盖窗口/标的
        for k in ("d0", "d1", "universe"):
            if override_hint.get(k):
                base[k] = override_hint[k]
        if symbols:
            base["manual_picks"] = ",".join(symbols)
        if not override_hint.get("universe"):
            base["universe"] = "pool76"  # 时长可控（对比同口径）

        override = _apply_template(base, RULE_TEMPLATES[intent])
        jb = time.strftime("%Y%m%d-%H%M%S")
        bj, oj = "bt2c-%s-base" % jb, "bt2c-%s-over" % jb
        bs, be = _run_one(base, bj)
        os_, oe = _run_one(override, oj)
        elapsed = round(time.time() - t0)

        if be or oe:
            return {
                "verdict": "inconclusive",
                "reason": "引擎错误 base=%s over=%s" % (str(be)[:100], str(oe)[:100]),
                "job_ids": [bj if not be else None, oj if not oe else None],
                "elapsed": elapsed,
            }
        ntr = (os_ or {}).get("n_trades") or 0
        if not bs or not os_ or ntr < 5:
            return {
                "verdict": "inconclusive",
                "reason": "样本不足或指标缺失（n_trades=%s）" % ntr,
                "baseline_summary": bs,
                "override_summary": os_,
                "job_ids": [bj, oj],
                "elapsed": elapsed,
            }
        bret = bs.get("total_return_pct") or 0
        oret = os_.get("total_return_pct") or 0
        bdd = bs.get("max_drawdown_pct") or 0
        odd = os_.get("max_drawdown_pct") or 0
        hit = oret >= bret * RETURN_LIFT and odd <= bdd * DRAWDOWN_WORSE
        return {
            "verdict": "pass" if hit else "fail",
            "reason": "收益 %.2f%%→%.2f%% 回撤 %.2f%%→%.2f%%" % (bret, oret, bdd, odd),
            "baseline_summary": bs,
            "override_summary": os_,
            "job_ids": [bj, oj],
            "elapsed": elapsed,
        }
