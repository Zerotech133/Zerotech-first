# -*- coding: utf-8 -*-
"""模拟仓门户投影层（纯标准库，只读 URI，零 pandas 依赖）。

供 portal_api /api/paper_status 消费；CLI 自测：
  python k_stock/paper/portal_status.py            # 最新状态
  python k_stock/paper/portal_status.py 2026-10-09 # 任意历史日回看
数据全部来自 mark 快照（行情依赖在快照时点固化），页面天然滞后至最近一次 mark 属预期。

v0.2 平台化扩展（K280）：
- status(): 持仓/挂单/成交投影带 strategy 标记；新增 stops 止损清单（现价+触发距离）
- snapshot_days(): 全部快照日列表（供前端日期选择器）
- history(day): 任意历史日回看——当日净值 + 持仓快照 + 当日成交/终态订单

v0.3 多账户口径：门户主页面 = main 交易台投影。全部查询锁 account_id='main'，
交易员子账本（taishan/chaser/gunner…）不经此层暴露（v0.3 训练期隔离，上岗后另开视图）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "paper.db"

MAIN = "main"  # 门户投影固定口径：主交易台


def _pending_health(rows: list, snap_date: str) -> list:
    """077q pending 健康度标注：同票多批 / 撮合日已过未撮 → warn 字段
    （前端红警示；077q 事故即靠此可见——当时页面完全看不出异常）。"""
    from collections import Counter

    cnt = Counter((r["stock"], r["side"]) for r in rows)
    snap = snap_date.replace("-", "")
    for r in rows:
        warns = []
        n = cnt.get((r["stock"], r["side"]), 0)
        if n > 1:
            warns.append(f"同票多批 ×{n}")
        if r["plan_date"] and snap > str(r["plan_date"]):
            warns.append(f"撮合日已过（快照 {snap_date}）")
        r["warn"] = "；".join(warns)
    return rows


def _connect() -> sqlite3.Connection | None:
    """只读连接；库不存在返回 None（fail-soft，由调用方给错误块）。"""
    if not DB.exists():
        return None
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def status() -> dict:
    con = _connect()
    if con is None:
        return {"ok": False, "error": f"paper.db 不存在（模拟仓未初始化）: {DB}"}
    try:
        marks = con.execute(
            "SELECT date,total,cash,market_value,pos_pct,pending,verify_ok "
            "FROM equity_marks WHERE account_id=? ORDER BY date",
            (MAIN,),
        ).fetchall()
        if not marks:
            return {"ok": False, "error": "无快照（跑 paper/cli.py mark 生成）"}
        latest = marks[-1]
        base = marks[0]["total"] or 1.0
        snap_date = latest["date"]
        positions = con.execute(
            "SELECT stock,qty,avg_cost,last,market_value,pnl,available,strategy,account_id "
            "FROM positions_snapshot WHERE account_id=? AND date=? ORDER BY stock",
            (MAIN, snap_date),
        ).fetchall()
        pending = con.execute(
            "SELECT id,ts,source,strategy,order_kind,conf,stock,side,qty,plan_date,reason "
            "FROM orders WHERE account_id=? AND status='pending' ORDER BY id",
            (MAIN,),
        ).fetchall()
        recent = con.execute(
            "SELECT id,ts,source,strategy,order_kind,ref,stock,side,qty,status,"
            "plan_date,fill_date,filled_price,fee,reason "
            "FROM orders WHERE account_id=? ORDER BY id DESC LIMIT 20",
            (MAIN,),
        ).fetchall()
        # 止损清单：positions.stop_price LEFT JOIN 最新快照取现价，算触发距离
        last_snap = con.execute(
            "SELECT MAX(date) AS d FROM positions_snapshot WHERE account_id=?", (MAIN,)
        ).fetchone()["d"]
        stops: list[dict] = []
        if last_snap:
            rows = con.execute(
                "SELECT p.stock,p.qty,p.stop_price,p.strategy,s.last "
                "FROM positions p LEFT JOIN positions_snapshot s "
                "  ON s.stock=p.stock AND s.date=? AND s.account_id=p.account_id "
                "WHERE p.account_id=? AND p.stop_price IS NOT NULL ORDER BY p.stock",
                (last_snap, MAIN),
            ).fetchall()
            for r in rows:
                d = dict(r)
                d["distance_pct"] = (
                    round((r["last"] / r["stop_price"] - 1) * 100, 2)
                    if r["last"]
                    else None
                )  # 与 engine.stops() 同口径：负=已跌破止损
                stops.append(d)
        return {
            "ok": True,
            "as_of": snap_date,
            "account_id": MAIN,
            "latest": {
                "date": latest["date"],
                "total": latest["total"],
                "cash": latest["cash"],
                "market_value": latest["market_value"],
                "pos_pct": latest["pos_pct"],
                "pending": latest["pending"],
                "verify_ok": bool(latest["verify_ok"]),
                "cum_pct": round((latest["total"] / base - 1) * 100, 2),
            },
            "curve": [
                {
                    "date": m["date"],
                    "total": m["total"],
                    "cum_pct": round((m["total"] / base - 1) * 100, 2),
                }
                for m in marks
            ],
            "positions": [dict(r) for r in positions],
            "stops": stops,
            "pending_orders": _pending_health([dict(r) for r in pending], snap_date),
            "recent_orders": [dict(r) for r in recent],
        }
    finally:
        con.close()


def pick_status() -> dict:
    """选票通道（pick_bridge 子账本，K-STOCK-095/097）投影：
    pending/近期成交/权益 marks。10-16 实弹前恒空属预期；
    /paper 页展示位与 /market 绩效表的数据源。"""
    con = _connect()
    if con is None:
        return {"ok": False, "error": "paper.db 不存在（模拟仓未初始化）"}
    try:
        marks = con.execute(
            "SELECT date,total,cash,market_value,pos_pct,pending,verify_ok "
            "FROM equity_marks WHERE account_id='pick_bridge' ORDER BY date"
        ).fetchall()
        pending = [
            dict(r)
            for r in con.execute(
                "SELECT id,ts,strategy,conf,stock,side,qty,plan_date,reason "
                "FROM orders WHERE account_id='pick_bridge' AND status='pending' ORDER BY id"
            )
        ]
        recent = [
            dict(r)
            for r in con.execute(
                "SELECT id,ts,ref,stock,side,qty,status,plan_date,fill_date,"
                "filled_price,fee,reason FROM orders WHERE account_id='pick_bridge' "
                "ORDER BY id DESC LIMIT 20"
            )
        ]
        snap = marks[-1]["date"] if marks else datetime.now().strftime("%Y-%m-%d")
        return {
            "ok": True,
            "marks": [dict(m) for m in marks],
            "pending_orders": _pending_health(pending, snap),
            "recent_orders": recent,
        }
    finally:
        con.close()


def judge_status(days: int = 5) -> dict:
    """端3 评判 Agent（judge）投影，077w：读 data/judge_reviews/ 最新 N 天 JSON。
    异常/缺失降级 ok=False，不阻塞 /api/paper_status。"""
    rdir = Path(__file__).resolve().parent.parent / "data" / "judge_reviews"
    try:
        files = sorted(rdir.glob("*.json"))[-days:]
        if not files:
            return {"ok": True, "reviews": []}
        reviews = []
        for f in files:
            d = json.loads(f.read_text(encoding="utf-8"))
            reviews.append(
                {
                    "date": d.get("date") or f.stem,
                    "score": d.get("score"),
                    "grade": d.get("grade"),
                    "findings": d.get("findings", []),
                    "narrative": d.get("narrative"),
                }
            )
        return {"ok": True, "reviews": reviews}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": repr(e)}


def strategist_status() -> dict:
    """端1 策略源影子路由建议投影，077w：读 data/strategist_latest.json。
    影子声明原样透传（前端标注「仅建议」）；缺失/异常降级 ok=False。"""
    fp = Path(__file__).resolve().parent.parent / "data" / "strategist_latest.json"
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
        return {
            "ok": True,
            "generated_at": d.get("generated_at"),
            "days": d.get("days"),
            "mode": d.get("mode"),
            "sources": [
                {
                    "name": k,
                    "score": v.get("score"),
                    "grade": v.get("grade"),
                    "suggestion": v.get("suggestion"),
                }
                for k, v in (d.get("sources") or {}).items()
            ],
            "note": d.get("note"),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": repr(e)}


def market_status() -> dict:
    """K-STOCK-101 M3 商业页面投影（纯只读，零子进程）：
    tiers.json 五级价目 + trainees/*.json 档案（tier/绑定模型/月价锚）
    + 各账本战绩（equity_marks 最新快照）。合规口径：模拟业绩展示、
    不承诺收益。缺失降级 ok=False。"""
    paper_dir = Path(__file__).resolve().parent
    try:
        tiers_d = json.loads((paper_dir / "tiers.json").read_text(encoding="utf-8"))
        tiers = {
            k: {
                "label": v.get("label"),
                "years": v.get("years"),
                "llm_binding": v.get("llm_binding"),
                "daily_cost_cap": v.get("daily_cost_cap"),
                "price_month": v.get("price_month"),
                "price_note": v.get("price_note"),
                "cost_tier": v.get("cost_tier"),
                "params": v.get("params"),
                "desc": v.get("desc"),
            }
            for k, v in (tiers_d.get("tiers") or {}).items()
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"tiers.json: {e!r}"}
    con = _connect()
    traders = []
    if con is not None:
        try:
            tdir = paper_dir / "trainees"
            for fn in sorted(tdir.glob("*.json")):
                try:
                    x = json.loads(fn.read_text(encoding="utf-8"))
                except Exception:
                    continue
                tid = x.get("id")
                if not tid:
                    continue
                tier = x.get("tier") or "T2"
                tv = tiers.get(tier) or {}
                marks = con.execute(
                    "SELECT date,total,pos_pct FROM equity_marks "
                    "WHERE account_id=? ORDER BY date",
                    (tid,),
                ).fetchall()
                base = marks[0]["total"] if marks else None
                last = marks[-1] if marks else None
                traders.append(
                    {
                        "id": tid,
                        "name": x.get("name"),
                        "style": x.get("style"),
                        "desc": x.get("desc"),
                        "status": x.get("status"),
                        "tier": tier,
                        "tier_label": tv.get("label"),
                        "price_month": tv.get("price_month"),
                        "llm_binding": x.get("llm_binding") or tv.get("llm_binding"),
                        "days": len(marks),
                        "total": last["total"] if last else None,
                        "cum_pct": (
                            round((last["total"] / base - 1) * 100, 2)
                            if marks and base
                            else None
                        ),
                    }
                )
        finally:
            con.close()
    return {
        "ok": True,
        "tiers": tiers,
        "traders": traders,
        "note": "模拟盘真实业绩展示，不构成收益承诺；聘请=订阅 AI 交易员，非购买 tokens",
    }


def snapshot_days() -> list[str]:
    """主交易台全部快照日（升序），供前端日期选择器；库/快照缺失返回空列表。"""
    con = _connect()
    if con is None:
        return []
    try:
        return [
            r["date"]
            for r in con.execute(
                "SELECT date FROM equity_marks WHERE account_id=? ORDER BY date",
                (MAIN,),
            ).fetchall()
        ]
    finally:
        con.close()


def history(day: str) -> dict:
    """任意历史日回看（main 口径）：当日净值点 + 持仓快照 + 当日成交与终态订单。

    订单口径：fill_date=当日（实际成交）∪ plan_date=当日且终态为
    rejected/cancelled（当日计划但未成交的终态）；pending 不进历史视图
    （它们属于「未来」，在 status() 里看）。
    """
    con = _connect()
    if con is None:
        return {"ok": False, "error": f"paper.db 不存在（模拟仓未初始化）: {DB}"}
    try:
        marks = con.execute(
            "SELECT date,total FROM equity_marks WHERE account_id=? ORDER BY date",
            (MAIN,),
        ).fetchall()
        if not marks:
            return {"ok": False, "error": "无快照（跑 paper/cli.py mark 生成）"}
        base = marks[0]["total"] or 1.0
        days = [m["date"] for m in marks]
        mark = next((m for m in marks if m["date"] == day), None)
        if mark is None:
            return {"ok": False, "error": f"无该日快照: {day}", "available_days": days}
        positions = con.execute(
            "SELECT stock,qty,avg_cost,last,market_value,pnl,available,strategy,account_id "
            "FROM positions_snapshot WHERE account_id=? AND date=? ORDER BY stock",
            (MAIN, day),
        ).fetchall()
        orders = con.execute(
            "SELECT id,ts,source,strategy,order_kind,ref,stock,side,qty,status,"
            "plan_date,fill_date,filled_price,fee,reason "
            "FROM orders WHERE account_id=? AND (fill_date=? "
            "   OR (plan_date=? AND status NOT IN ('pending','filled'))) "
            "ORDER BY id",
            (MAIN, day, day),
        ).fetchall()
        return {
            "ok": True,
            "day": day,
            "account_id": MAIN,
            "mark": {
                "total": mark["total"],
                "cum_pct": round((mark["total"] / base - 1) * 100, 2),
            },
            "positions": [dict(r) for r in positions],
            "orders": [dict(r) for r in orders],
        }
    finally:
        con.close()


def _market_status(currency: str, prefix: str) -> dict:
    """分市场独立账本聚合投影（us_status/hk_status 共用体，行为与原 us_status 等价）。

    <prefix>_<...> 账本各一份迷你 status；<CURRENCY> 原币口径，不与 main 混算
    （币种隔离是 044 设计前提）；无账本时 ok=True 且 accounts=[]（前端隐藏
    区块，不算错误）。"""
    con = _connect()
    if con is None:
        return {"ok": False, "error": f"paper.db 不存在: {DB}"}
    try:
        aids = [
            r["account_id"]
            for r in con.execute(
                f"SELECT DISTINCT account_id FROM account WHERE account_id LIKE '{prefix}%' "  # nosec B608 - 值参数化/内部标识符（已抽查）
                "ORDER BY account_id"
            )
        ]
        out = {"ok": True, "currency": currency, "accounts": []}
        for aid in aids:
            marks = con.execute(
                "SELECT date,total,cash,market_value,pos_pct,pending,verify_ok "
                "FROM equity_marks WHERE account_id=? ORDER BY date",
                (aid,),
            ).fetchall()
            base = (marks[0]["total"] or 1.0) if marks else 1.0
            last_snap = (
                con.execute(
                    "SELECT MAX(date) AS d FROM positions_snapshot WHERE account_id=?",
                    (aid,),
                ).fetchone()["d"]
                if marks
                else None
            )
            positions = (
                con.execute(
                    "SELECT stock,qty,avg_cost,last,market_value,pnl,strategy "
                    "FROM positions_snapshot WHERE account_id=? AND date=? ORDER BY stock",
                    (aid, last_snap),
                ).fetchall()
                if last_snap
                else []
            )
            pending = con.execute(
                "SELECT id,stock,side,qty,plan_date,conf,strategy,reason "
                "FROM orders WHERE account_id=? AND status='pending' ORDER BY id",
                (aid,),
            ).fetchall()
            stops: list[dict] = []
            if last_snap:
                for r in con.execute(
                    "SELECT p.stock,p.qty,p.stop_price,p.strategy,s.last "
                    "FROM positions p LEFT JOIN positions_snapshot s "
                    "  ON s.stock=p.stock AND s.date=? AND s.account_id=p.account_id "
                    "WHERE p.account_id=? AND p.stop_price IS NOT NULL ORDER BY p.stock",
                    (last_snap, aid),
                ):
                    d = dict(r)
                    d["distance_pct"] = (
                        round((r["last"] / r["stop_price"] - 1) * 100, 2)
                        if r["last"]
                        else None
                    )
                    stops.append(d)
            latest = None
            curve = []
            if marks:
                l = marks[-1]
                latest = {
                    "date": l["date"],
                    "total": l["total"],
                    "cash": l["cash"],
                    "market_value": l["market_value"],
                    "pos_pct": l["pos_pct"],
                    "pending": l["pending"],
                    "verify_ok": bool(l["verify_ok"]),
                    "cum_pct": round((l["total"] / base - 1) * 100, 2),
                }
                curve = [
                    {
                        "date": m["date"],
                        "total": m["total"],
                        "cum_pct": round((m["total"] / base - 1) * 100, 2),
                    }
                    for m in marks
                ]
            out["accounts"].append(
                {
                    "account_id": aid,
                    "latest": latest,
                    "curve": curve,
                    "positions": [dict(r) for r in positions],
                    "stops": stops,
                    "pending_orders": _pending_health(
                        [dict(r) for r in pending], last_snap or latest["date"]
                    ),
                }
            )
        return out
    finally:
        con.close()


def us_status() -> dict:
    """US 独立账本聚合投影（K-STOCK-045e G3）：us_% 账本各一份迷你 status。"""
    return _market_status("USD", "us_")


def hk_status() -> dict:
    """HK 独立账本聚合投影（H 批 hk_qingping 灰位）：hk_% 账本，HKD 原币口径。

    与 us_status 同构（_market_status 共用体）；无 hk_% 账本时 ok=True 且
    accounts=[]（前端灰态，不算错误）。"""
    return _market_status("HKD", "hk_")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        print(
            json.dumps(history(sys.argv[1]), ensure_ascii=False, indent=2, default=str)
        )
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=2, default=str))
