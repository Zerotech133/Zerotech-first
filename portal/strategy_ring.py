# -*- coding: utf-8 -*-
"""W43 策略工单环 MVP（2026-10-26）：选股方法 → 工单 → LLM 草案 → 回测留桩
→ lookahead 检测 → 策略卡 → 影子 30 天台账。

铁律（军机驿 W43）：
  · AI 产出是**候选策略**，不是交易指令；本模块不接任何交易/下单/持仓代码；
  · 回测未接通时诚实标 not_connected（不假绿），不影响工单流转；
  · 影子期 ≥30 天 + 人工拍板后才有资格讨论上线（由 approval 流程另行处理）。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
ORDERS = os.path.join(WORK, "strategy_orders.jsonl")
SHADOW = os.path.join(WORK, "strategy_shadow.jsonl")
SHADOW_DAYS = 30

STATES = [
    "received",
    "parsed",
    "drafted",
    "lookahead_checked",
    "backtested",
    "card_sent",
    "shadowing",
    "approved",
    "rejected",
]


def _append(path, row):
    os.makedirs(WORK, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rows(path):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def create_order(method_text, source="feishu", operator=""):
    method = str(method_text or "").strip()
    if len(method) < 10:
        return {"ok": False, "error": "选股方法过短（至少 10 字）"}
    oid = "STR-" + uuid.uuid4().hex[:10]
    row = {
        "id": oid,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "source": str(source)[:40],
        "operator": str(operator)[:80],
        "method": method[:4000],
        "status": "received",
        "steps": [],
        "evidence": "D",
        "note": "候选策略，非交易指令；影子 30 天后人工拍板",
    }
    _append(ORDERS, row)
    return {"ok": True, "order": row}


def _last_order(order_id):
    for r in reversed(_rows(ORDERS)):
        if r.get("id") == order_id:
            return r
    return None


def _update(order_id, **kw):
    row = _last_order(order_id)
    if row is None:
        return None
    row.update(kw)
    row["updated"] = datetime.now().isoformat(timespec="seconds")
    _append(ORDERS, row)
    return row


def _heuristic_spec(method):
    """无 LLM 时的规则抽取：给出可审阅的候选结构（诚实标注 heuristic）。"""
    text = str(method or "")
    rules = []
    for kw, rule in (
        ("均线", "均线多头排列（MA5>MA10>MA20）"),
        ("放量", "成交量放大（量比>1.5）"),
        ("低吸", "回踩支撑低吸（不追高）"),
        ("涨停", "涨停板/连板强度过滤"),
        ("换手", "换手率区间过滤"),
        ("市值", "市值区间过滤"),
        ("RPS", "RPS 相对强度过滤"),
    ):
        if kw in text:
            rules.append(rule)
    return {
        "entry": rules[:8] or ["（原文未识别到明确规则，需人工补全）"],
        "exit": ["固定止盈/止损（待人工设定）"],
        "filters": ["排除 ST/停牌/新股"],
        "evidence": "D",
        "generated_by": "heuristic",
    }


def infer_buy_filter(method=""):
    """从方法文字推断买点过滤（nl_backtest 规则对「二买/三买」覆盖不全时兜底）。"""
    t = str(method or "")
    if "二三买" in t or "2/3买" in t or "2,3买" in t:
        return "23"
    if "二买" in t or "第二类买" in t or "2买" in t:
        return "2"
    if "三买" in t or "第三类买" in t or "3买" in t:
        return "3"
    return ""


def draft(order_id):
    row = _last_order(order_id)
    if row is None:
        return {"ok": False, "error": "工单不存在"}
    spec = _heuristic_spec(row["method"])
    row = _update(
        order_id,
        status="drafted",
        spec=spec,
        steps=row.get("steps", [])
        + [{"step": "draft", "ts": row["ts"], "evidence": spec["evidence"]}],
    )
    return {"ok": True, "order": row}


def lookahead_check(order_id):
    row = _last_order(order_id)
    if row is None:
        return {"ok": False, "error": "工单不存在"}
    text = json.dumps(row.get("spec") or {}, ensure_ascii=False) + row["method"]
    leaks = [
        w
        for w in ("未来", "次日收盘", "收盘后买入", "后复权未来", "lookahead", "peek")
        if w in text
    ]
    plan = row.get("backtest_plan") or {}
    pit_ok = bool(
        (plan.get("pit") or {}).get("hist_st") and (plan.get("pit") or {}).get("pit_st")
    )
    res = {
        "pass": not leaks and (pit_ok or not plan),
        "leaks": leaks,
        "pit_params": pit_ok,
        "note": "关键词级前视检测 + PIT 参数（hist_st/pit_st）检查；"
        "Freqtrade lookahead 适配器后续接入",
    }
    row = _update(
        order_id,
        status="lookahead_checked",
        lookahead=res,
        steps=row.get("steps", [])
        + [
            {"step": "lookahead", "ts": row["ts"], "pass": res["pass"], "evidence": "B"}
        ],
    )
    return {"ok": res["pass"], "order": row, "lookahead": res}


def _qbt_path():
    import sys as _sys

    root = os.path.dirname(HERE)
    qbt = os.path.join(root, "quant_backtest")
    if qbt not in _sys.path:
        _sys.path.insert(0, qbt)
    return qbt


def _default_window(days=90):
    from datetime import date

    end = date.today()
    start = end - timedelta(days=days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def backtest(order_id, execute=False, d0=None, d1=None):
    """回测适配器：nl_backtest 解析选股方法 → backtest_engine_v2.run_job。

    默认只做「回测规划」（解析参数 + PIT 防护 + 窗口），执行需显式
    execute=True 且 STRATEGY_BACKTEST_ENABLED=1（避免自动占用共享数据根）。
    """
    row = _last_order(order_id)
    if row is None:
        return {"ok": False, "error": "工单不存在"}
    try:
        _qbt_path()
        import nl_backtest

        parsed = nl_backtest.parse_nl(row["method"], use_llm=False)
        params = dict(parsed.get("params") or {})
    except Exception as e:  # noqa: BLE001
        res = {
            "status": "adapter_unavailable",
            "evidence": "D",
            "why": "回测解析器不可用：%s" % str(e)[:120],
        }
        row = _update(
            order_id,
            status="backtested",
            backtest=res,
            steps=row.get("steps", [])
            + [{"step": "backtest", "ts": row["ts"], "status": res["status"]}],
        )
        return {"ok": True, "order": row, "backtest": res}

    # 「二买/三买」规则兜底（nl_backtest 对买点覆盖不全时）
    if not params.get("buy_filter"):
        _bf = infer_buy_filter(row["method"])
        if _bf:
            params["buy_filter"] = _bf

    d0 = d0 or _default_window()[0]
    d1 = d1 or _default_window()[1]
    # 显式窗口优先于自然语言解析出的日期（否则会撞不到事件缓存）
    params["d0"] = str(d0).replace("-", "")
    params["d1"] = str(d1).replace("-", "")
    params.setdefault("strategy", "chanlun")
    params.setdefault("universe", "market")
    params.setdefault("buy_filter", "3")
    params.setdefault("top_n", 10)
    params.setdefault("holding_max_days", 10)
    plan = {
        "params": params,
        "window": [d0, d1],
        "pit": {"hist_st": True, "pit_st": True},
        "notes": parsed.get("notes") or [],
        "unknown": parsed.get("unknown") or [],
        "evidence": "B",
    }
    res = {
        "status": "planned",
        "plan": plan,
        "evidence": "B",
        "why": "回测入参已解析；执行需显式开启（避免占用共享数据根）",
        "executable": str(os.environ.get("STRATEGY_BACKTEST_ENABLED", "0")) == "1",
    }
    if execute and res["executable"]:
        try:
            import backtest_engine_v2 as be

            job_id = "STRBT-" + uuid.uuid4().hex[:8]
            _r = be.run_job(params, job_id)
            _result, _err = _r if isinstance(_r, tuple) and len(_r) == 2 else (_r, None)
            if _err:
                res = {
                    "status": "failed",
                    "job_id": job_id,
                    "why": str(_err)[:300],
                    "plan": plan,
                    "evidence": "D",
                }
            else:
                _summary = {}
                if isinstance(_result, dict):
                    for k in (
                        "metrics",
                        "summary",
                        "total_return",
                        "annual_return",
                        "max_drawdown",
                        "win_rate",
                        "trades",
                        "equity",
                        "stats",
                    ):
                        if k in _result:
                            _summary[k] = _result[k]
                    _summary["keys"] = sorted(_result.keys())[:20]
                res = {
                    "status": "done",
                    "job_id": job_id,
                    "result": _summary or str(_result)[:300],
                    "plan": plan,
                    "evidence": "B",
                }
        except Exception as e:  # noqa: BLE001
            res = {
                "status": "failed",
                "why": "%s: %s" % (type(e).__name__, str(e)[:160]),
                "plan": plan,
                "evidence": "D",
            }
    row = _update(
        order_id,
        status="backtested",
        backtest=res,
        backtest_plan=plan,
        steps=row.get("steps", [])
        + [
            {
                "step": "backtest",
                "ts": row["ts"],
                "status": res["status"],
                "evidence": res.get("evidence"),
            }
        ],
    )
    return {"ok": res.get("status") != "failed", "order": row, "backtest": res}


def make_card(order_id):
    row = _last_order(order_id)
    if row is None:
        return {"ok": False, "error": "工单不存在"}
    spec = row.get("spec") or {}
    md = (
        "**候选策略 · 非交易指令**\n"
        "入口规则：%s\n退出规则：%s\n过滤：%s\n"
        "回测：%s\n前视检测：%s"
        % (
            "；".join(spec.get("entry") or []) or "待补全",
            "；".join(spec.get("exit") or []) or "待补全",
            "；".join(spec.get("filters") or []) or "待补全",
            "%s%s"
            % (
                (row.get("backtest") or {}).get("status", "未回测"),
                (" · %s" % (row.get("backtest_plan") or {}).get("window"))
                if (row.get("backtest_plan") or {}).get("window")
                else "",
            ),
            "通过" if (row.get("lookahead") or {}).get("pass") else "待检",
        )
    )
    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": "🧪 策略工单 %s" % row["id"]},
        },
        "elements": [
            {"tag": "markdown", "content": md},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "影子期 %d 天 + 人工拍板后才可讨论上线；"
                        "本卡不构成交易指令" % SHADOW_DAYS,
                    }
                ],
            },
        ],
    }
    row = _update(
        order_id,
        status="card_sent",
        card=card,
        steps=row.get("steps", []) + [{"step": "card", "ts": row["ts"]}],
    )
    return {"ok": True, "order": row, "card": card}


def shadow_start(order_id, today=None):
    row = _last_order(order_id)
    if row is None:
        return {"ok": False, "error": "工单不存在"}
    _bt = row.get("backtest") or {}
    _bt_status = _bt.get("status")
    if _bt_status != "done":
        return {
            "ok": False,
            "error": "回测未完成（当前 %s），不得进入影子期" % (_bt_status or "未回测"),
        }
    _summary = (_bt.get("result") or {}).get("summary") or {}
    _tr = _summary.get("total_return_pct")
    if isinstance(_tr, (int, float)) and _tr <= 0:
        _update(
            order_id,
            status="rejected",
            reason="回测收益 %.2f%% ≤ 0，拒绝进入影子期" % _tr,
        )
        return {"ok": False, "error": "回测收益 %.2f%% ≤ 0，拒绝进入影子期" % _tr}
    today = today or date.today()
    end = today + timedelta(days=SHADOW_DAYS)
    rec = {
        "id": order_id,
        "start": today.isoformat(),
        "end": end.isoformat(),
        "days": SHADOW_DAYS,
        "status": "shadowing",
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    _append(SHADOW, rec)
    row = _update(
        order_id,
        status="shadowing",
        shadow=rec,
        steps=row.get("steps", [])
        + [{"step": "shadow_start", "ts": row["ts"], "end": end.isoformat()}],
    )
    return {"ok": True, "order": row, "shadow": rec}


def shadow_status(order_id):
    for r in reversed(_rows(SHADOW)):
        if r.get("id") == order_id:
            try:
                end = date.fromisoformat(r["end"])
                left = (end - date.today()).days
            except (KeyError, ValueError):
                left = None
            return {
                "ok": True,
                "shadow": r,
                "days_left": left,
                "mature": bool(left is not None and left <= 0),
            }
    return {"ok": False, "error": "无影子记录"}


def pipeline(order_id):
    """MVP 流水线：draft → lookahead → backtest → card → shadow_start。"""
    for fn in (draft, lookahead_check, backtest, make_card, shadow_start):
        r = fn(order_id)
        if not r.get("ok"):
            return {"ok": False, "failed_at": fn.__name__, "result": r}
    return {"ok": True, "order": _last_order(order_id)}


def list_orders(limit=20):
    return _rows(ORDERS)[-limit:]
