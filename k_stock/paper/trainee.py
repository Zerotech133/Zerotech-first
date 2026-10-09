# -*- coding: utf-8 -*-
"""交易员训练管线 v0.3（多账户模拟盘实习）。

设计（「交易员市场」的落地形态）：
  交易员 ≠ 策略源。策略源回答「买什么」（AI 值班/量子双刀/青萍 → picks JSON），
  交易员回答「怎么买、买多少、何时卖」（执行参数档案 + 独立子账本）。
  档案字段：风控参数（单票/总仓上限/止损距离/置信度门槛）+ 毕业标准（graduation）。

训练三级火箭：
  ① 档案定义   trainees/*.json：params（执行参数）+ graduation（验收线）
  ② 模拟盘实习 同一条 picks run 落多个子账本（ref 按 @账户 分段互不冲突），
               每日 routine：settle → auto-stops（按档案挂初始止损）→ check-stops → mark
  ③ 验收上岗   board 对比 main 基准：实习天数 / 最大回撤 / 对账通过率达标 → status=live

命令（系统 Python311；真实行情须 pandas）：
  python k_stock/paper/trainee.py list                       # 交易员名册
  python k_stock/paper/trainee.py exec --who taishan,chaser  # 最新 AI run 落训练账本
      [--run poc_x.json] [--dry-run]
  python k_stock/paper/trainee.py routine [--dry-run]        # 每日训练例程
  python k_stock/paper/trainee.py board                      # 成绩单（vs main 基准）

automation 建议（训练期）：07:05 主账本闭环（settle→exec→check-stops→mark）后追加
  trainee.py routine    # 子账本同节奏：撮昨日 pending → 挂/查止损 → 快照
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import PaperEngine, Reject          # noqa: E402
from market import ParquetMarket                # noqa: E402

TRAINEE_DIR = HERE / "trainees"
DATA_DIR = HERE.parent / "data"
DEFAULT_DB = DATA_DIR / "paper.db"


# ---------------- 档案 ----------------

def load_profile(tid: str) -> dict:
    p = TRAINEE_DIR / f"{tid}.json"
    if not p.exists():
        raise Reject(f"交易员档案不存在: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def list_profiles() -> list[dict]:
    out = []
    for p in sorted(TRAINEE_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def _newest_ai_run() -> Path:
    runs = sorted(x for x in DATA_DIR.glob("poc_*.json")
                  if not x.name.startswith("poc_qd_"))
    if not runs:
        raise Reject("k_stock/data/ 下无 poc_*.json（AI 通道）")
    return runs[-1]


def _engine_for(profile: dict, market, db: Path | None = None) -> PaperEngine:
    """按档案开子账本引擎：account=id，风控参数用档案覆盖默认值。"""
    pr = profile.get("params", {})
    cfg = {"initial_cash": float(profile.get("initial_cash", 1_000_000.0))}
    for k in ("max_pos_pct", "max_total_pct", "commission_rate", "min_commission", "stamp_tax"):
        if k in pr:
            cfg[k] = pr[k]
    return PaperEngine(db or DEFAULT_DB, market, config=cfg, account=profile["id"])


# ---------------- 训练动作 ----------------

def exec_for(run_path: str | None = None, who: list[str] | None = None,
             dry_run: bool = False, db: Path | None = None) -> dict:
    """同一条 picks run 落到若干训练子账本（conf_floor 在引擎端过滤 BUY）。"""
    run = Path(run_path) if run_path else _newest_ai_run()
    if not run.exists() and not run.is_absolute():
        run = DATA_DIR / run   # 相对路径按 k_stock/data/ 解析（与 CLI cwd 无关）
    profiles = list_profiles() if not who else [load_profile(t) for t in who]
    profiles = [p for p in profiles if p.get("status") in ("training", "live")]
    if not profiles:
        raise Reject("无可训练交易员（status=training/live）")
    market = ParquetMarket()   # 行情湖只读，多账本共享一份
    out = {"run": run.name, "trainees": [], "dry_run": dry_run}
    for prof in profiles:
        eng = _engine_for(prof, market, db)
        min_conf = prof.get("params", {}).get("conf_floor")
        r = eng.exec_run(run, dry_run=dry_run, strategy="ai_team",
                         account_id=prof["id"], min_conf=min_conf)
        out["trainees"].append({
            "id": prof["id"], "name": prof["name"],
            "pending": len([o for o in r["orders"] if o.get("status") == "pending"]),
            "skipped": len(r["skipped"]),
            "detail": r,
        })
    return out


def auto_stops(who: list[str] | None = None, dry_run: bool = False,
               db: Path | None = None) -> dict:
    """按档案 stop_dist_pct 给子账本持仓挂初始止损（成本锚定；已有止损不覆盖）。"""
    profiles = list_profiles() if not who else [load_profile(t) for t in who]
    profiles = [p for p in profiles if p.get("status") in ("training", "live")]
    market = ParquetMarket()
    out = {"set": [], "skip": [], "dry_run": dry_run}
    for prof in profiles:
        dist = prof.get("params", {}).get("stop_dist_pct")
        if not dist:
            continue
        eng = _engine_for(prof, market, db)
        for pos in eng.positions(prof["id"]):
            if pos.get("stop_price") is not None:
                out["skip"].append({"account": prof["id"], "stock": pos["stock"],
                                    "why": "已有止损"})
                continue
            stop = round(pos["avg_cost"] * (1 - dist), 2)
            if not dry_run:
                eng.set_stop(pos["stock"], stop, account_id=prof["id"])
            out["set"].append({"account": prof["id"], "stock": pos["stock"],
                               "stop_price": stop, "qty": pos["qty"]})
    return out


def routine(who: list[str] | None = None, dry_run: bool = False,
            db: Path | None = None) -> dict:
    """每日训练例程（automation 07:05 主闭环后调用）：
    settle（撮昨日 pending）→ auto-stops（新仓挂初始止损）→ check-stops（触发检查）→ mark。"""
    profiles = list_profiles() if not who else [load_profile(t) for t in who]
    profiles = [p for p in profiles if p.get("status") in ("training", "live")]
    market = ParquetMarket()
    out = {"trainees": [], "dry_run": dry_run}
    for prof in profiles:
        eng = _engine_for(prof, market, db)
        aid = prof["id"]
        st = eng.settle(dry_run=dry_run, account_id=aid)
        stops_set = []
        dist = prof.get("params", {}).get("stop_dist_pct")
        if dist:
            for pos in eng.positions(aid):
                if pos.get("stop_price") is None:
                    stop = round(pos["avg_cost"] * (1 - dist), 2)
                    if not dry_run:
                        eng.set_stop(pos["stock"], stop, account_id=aid)
                    stops_set.append({"stock": pos["stock"], "stop_price": stop})
        ck = eng.check_stops(dry_run=dry_run, account_id=aid)
        mk = None if dry_run else eng.mark(account_id=aid)
        out["trainees"].append({
            "id": aid, "name": prof["name"],
            "settled": len(st["filled"]), "rejected": len(st["rejected"]),
            "stops_set": stops_set,
            "stop_triggered": len([t for t in ck["triggered"] if t.get("status")]),
            "mark": mk,
        })
    return out


def scoreboard(db: Path | None = None) -> list[dict]:
    """成绩单：main 基准 + 各训练账本（累计/天数/最大回撤/对账）。"""
    con_db = db or DEFAULT_DB
    market = ParquetMarket()
    rows = []

    def _stats(eng: PaperEngine, aid: str) -> dict:
        curve = eng.curve(aid)
        if not curve:
            return {"days": 0}
        base = curve[0]["total"] or 1.0
        peak, max_dd = curve[0]["total"], 0.0
        for pt in curve:
            peak = max(peak, pt["total"])
            if peak > 0:
                max_dd = min(max_dd, pt["total"] / peak - 1)
        latest = curve[-1]
        v = eng.verify(aid)
        return {"days": len(curve), "total": latest["total"],
                "cum_pct": round((latest["total"] / base - 1) * 100, 2),
                "max_dd_pct": round(max_dd * 100, 2),
                "verify_ok": bool(v["ok"])}

    # main 基准
    main_eng = PaperEngine(con_db, market, account="main")
    rows.append({"id": "main", "name": "主交易台", "status": "live",
                 **_stats(main_eng, "main")})
    # 训练账本
    for prof in list_profiles():
        eng = _engine_for(prof, market, con_db)
        rows.append({"id": prof["id"], "name": prof["name"],
                     "style": prof.get("style"), "status": prof.get("status"),
                     **_stats(eng, prof["id"])})
    return rows


# ---------------- CLI ----------------

def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="trainee", description="交易员训练管线")
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="交易员名册").set_defaults(fn=_cmd_list)

    p = sub.add_parser("exec", help="picks run 落训练子账本")
    p.add_argument("--run", default=None)
    p.add_argument("--who", default=None, help="逗号分隔账本 ID（缺省全部 training/live）")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=_cmd_exec)

    p = sub.add_parser("routine", help="每日训练例程：settle→auto-stops→check-stops→mark")
    p.add_argument("--who", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=_cmd_routine)

    sub.add_parser("board", help="成绩单（vs main 基准）").set_defaults(fn=_cmd_board)

    args = ap.parse_args(argv)
    args.db_path = Path(args.db) if args.db else None
    return args.fn(args)


def _cmd_list(args) -> int:
    rows = list_profiles()
    print(f"交易员名册（{len(rows)}）:")
    for p in rows:
        pr = p.get("params", {})
        print(f"  [{p.get('status','?'):>8}] {p['id']:<8} {p['name']}  {p.get('style','')}"
              f"  单票≤{pr.get('max_pos_pct',0):.0%} 总仓≤{pr.get('max_total_pct',0):.0%}"
              f" 止损-{pr.get('stop_dist_pct',0):.0%} conf≥{pr.get('conf_floor',0):.2f}")
    return 0


def _cmd_exec(args) -> int:
    who = args.who.split(",") if args.who else None
    r = exec_for(args.run, who, args.dry_run, args.db_path)
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}训练落单 {r['run']}:")
    for t in r["trainees"]:
        print(f"  {t['name']}({t['id']}): pending {t['pending']} / 跳过 {t['skipped']}")
        for o in t["detail"]["orders"]:
            print(f"    [{o.get('status')}] {o['side']} {o['stock']} {o['qty']} 股"
                  f" @ {o['plan_date']} | {o['reason']}")
        for s in t["detail"]["skipped"]:
            print(f"    [skip] {s['stock']}: {s['why']}")
    return 0


def _cmd_routine(args) -> int:
    who = args.who.split(",") if args.who else None
    r = routine(who, args.dry_run, args.db_path)
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}训练例程（{len(r['trainees'])} 账本）:")
    for t in r["trainees"]:
        mk = t.get("mark") or {}
        print(f"  {t['name']}({t['id']}): 成交 {t['settled']} 搁置/拒绝 {t['rejected']}"
              f" 挂止损 {len(t['stops_set'])} 触发 {t['stop_triggered']}"
              + (f" 快照 {mk.get('total'):,.2f}" if mk else ""))
    return 0


def _cmd_board(args) -> int:
    rows = scoreboard(args.db_path)
    print(f"{'账本':<10}{'状态':>8}{'天数':>6}{'总资产':>14}{'累计':>9}{'最大回撤':>9}  对账")
    for r in rows:
        if not r.get("days"):
            print(f"{r['name']:<10}{r['status']:>8}{0:>6}  （尚无快照）")
            continue
        ok = "OK" if r["verify_ok"] else "❌"
        print(f"{r['name']:<10}{r['status']:>8}{r['days']:>6}{r['total']:>14,.2f}"
              f"{r['cum_pct']:>+8.2f}%{r['max_dd_pct']:>8.2f}%  {ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
