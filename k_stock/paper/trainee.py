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


# ---------------- 风险指标（board 与 replay 共用） ----------------

_ANNUAL = 252 ** 0.5


def _risk_metrics(curve: list[dict]) -> dict:
    """日净值序列 → 年化 Sharpe / Sortino（rf=0，全样本下行差）。
    样本 <3 或波动为 0 → None（如实缺省，不硬凑数字）。"""
    tots = [float(c["total"]) for c in curve if c.get("total")]
    if len(tots) < 3:
        return {"sharpe": None, "sortino": None}
    rets = [tots[i] / tots[i - 1] - 1 for i in range(1, len(tots)) if tots[i - 1] > 0]
    if len(rets) < 2:
        return {"sharpe": None, "sortino": None}
    mean = sum(rets) / len(rets)
    sd = (sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5
    down = [r for r in rets if r < 0]
    dsd = (sum(r * r for r in down) / len(rets)) ** 0.5 if down else 0.0
    return {"sharpe": round(mean / sd * _ANNUAL, 2) if sd > 1e-12 else None,
            "sortino": round(mean / dsd * _ANNUAL, 2) if dsd > 1e-12 else None}


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
                "verify_ok": bool(v["ok"]),
                **_risk_metrics(curve)}

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


# ---------------- A2 历史重放预筛（v0.4） ----------------

def replay(run_path: str | None = None, days: int = 20, start: str | None = None,
           end: str | None = None, who: list[str] | None = None,
           market=None, profiles: list[dict] | None = None) -> dict:
    """历史重放预筛（训练三级火箭的「预筛」环节）：
    把「start 日拿到这份 picks 并按档案执行到 end」的全过程在**临时库**重放一遍，
    快速淘汰先天不合格的档案参数，再进真实模拟盘实习——省实习时间。

    实现要点：
    - 引擎零改动：AsOfMarket 视图逐日推进（view=T），T+1 冻结 / 风控 / 止损 /
      对账全部停在视角日，不偷看窗口外数据；
    - picks 副本把每条 asof 改写为窗口起点前一交易日（决策时点=窗口开盘前），
      exec_run 既有语义原样复用（plan_date=起点当日开盘撮合）；
    - 逐日例程 = settle → 按档案挂初始止损 → check_stops → mark(day=T)，
      与 trainee routine 同节奏；
    - 输出各档案 days/cum/max_dd/sharpe/sortino/止损触发次数/对账 + 毕业先行判定。
    临时库用 tempfile 自动清理，真库零触碰。
    """
    import shutil
    import tempfile
    from market import AsOfMarket

    run = Path(run_path) if run_path else _newest_ai_run()
    if not run.exists() and not run.is_absolute():
        run = DATA_DIR / run
    if not run.exists():
        raise Reject(f"picks 文件不存在: {run}")
    profiles = ([p for p in list_profiles() if p.get("status") in ("training", "live", "design")]
                if profiles is None else profiles)
    if who:
        profiles = [p for p in profiles if p["id"] in set(who)]
    if not profiles:
        raise Reject("无可重放交易员")

    mkt = market or ParquetMarket()
    cal = [d for d in mkt.trading_days()]
    end_day = str(end).replace("-", "") if end else cal[-1]
    if end_day not in cal:                      # end 纠偏到 ≤end 的最后一个交易日
        le = [d for d in cal if d <= end_day]
        if not le:
            raise Reject(f"窗口终点 {end_day} 早于行情湖首日")
        end_day = le[-1]
    if start:
        start_day = str(start).replace("-", "")
        ge = [d for d in cal if d >= start_day and d <= end_day]
        if not ge:
            raise Reject(f"窗口起点 {start_day} 在窗口内无交易日")
        start_day = ge[0]
    else:                                       # 缺省：end 往前推 days 个交易日
        idx = cal.index(end_day)
        start_day = cal[max(0, idx - max(1, int(days)) + 1)]
    prev_day_i = cal.index(start_day)
    if prev_day_i == 0:
        raise Reject(f"起点 {start_day} 是行情湖首日，无前置决策日")
    prev_day = cal[prev_day_i - 1]              # 决策时点参考价=起点前收
    window = [d for d in cal if start_day <= d <= end_day]

    # picks 副本：asof 改写为窗口起点盘前（exec 语义原样复用：盘前决策 → 当日开盘撮合；
    # 参考价 = 起点前收，由 exec 时 view=prev_day 的 AsOfMarket 视图保证）
    payload = json.loads(run.read_text(encoding="utf-8"))
    for it in payload:
        it["asof"] = f"{start_day[:4]}-{start_day[4:6]}-{start_day[6:]} 08:30:00"
    tmpdir = Path(tempfile.mkdtemp(prefix="trainee_replay_"))
    try:
        rw_run = tmpdir / run.name
        rw_run.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        rows = []
        for prof in profiles:
            aid = prof["id"]
            asof_mkt = AsOfMarket(mkt, view=prev_day, end=end_day)
            eng = _engine_for(prof, asof_mkt, db=tmpdir / f"{aid}.db")
            ex = eng.exec_run(rw_run, strategy="ai_team", account_id=aid,
                              min_conf=prof.get("params", {}).get("conf_floor"))
            placed = len([o for o in ex["orders"] if o.get("status") == "pending"])
            stop_hits = 0
            for t in window:
                asof_mkt.view = t                       # 视角推进（盘后语义）
                eng.settle(account_id=aid)
                dist = prof.get("params", {}).get("stop_dist_pct")
                if dist:
                    for pos in eng.positions(aid):
                        if pos.get("stop_price") is None:
                            eng.set_stop(pos["stock"],
                                         round(pos["avg_cost"] * (1 - dist), 2),
                                         account_id=aid)
                ck = eng.check_stops(account_id=aid)
                stop_hits += len([x for x in ck["triggered"] if x.get("qty")])
                eng.mark(day=f"{t[:4]}-{t[4:6]}-{t[6:]}", account_id=aid)
            curve = eng.curve(aid)
            st = _replay_stats(eng, aid, curve, placed, stop_hits, prof)
            rows.append(st)
        report = {"ok": True, "run": run.name, "window": {"start": start_day, "end": end_day,
                                                          "days": len(window)},
                  "rows": rows}
        return report
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _replay_stats(eng: PaperEngine, aid: str, curve: list[dict],
                  placed: int, stop_hits: int, prof: dict) -> dict:
    """重放结果统计 + 毕业先行判定（provisional：窗口短于毕业线天数时只预判不定案）。"""
    grad = prof.get("graduation", {})
    min_days = int(grad.get("min_days", 20))
    max_dd_line = float(grad.get("max_drawdown_pct", 15))
    if not curve:
        return {"id": aid, "name": prof.get("name"), "status": prof.get("status"),
                "days": 0, "placed": placed, "verdict": "fail（未形成任何净值快照）"}
    base = curve[0]["total"] or 1.0
    peak, max_dd = curve[0]["total"], 0.0
    for pt in curve:
        peak = max(peak, pt["total"])
        if peak > 0:
            max_dd = min(max_dd, pt["total"] / peak - 1)
    v = eng.verify(aid)
    st = {"id": aid, "name": prof.get("name"), "status": prof.get("status"),
          "days": len(curve), "total": curve[-1]["total"],
          "cum_pct": round((curve[-1]["total"] / base - 1) * 100, 2),
          "max_dd_pct": round(max_dd * 100, 2),
          "verify_ok": bool(v["ok"]), "placed": placed, "stop_hits": stop_hits,
          **_risk_metrics(curve)}
    reasons = []
    if not placed and not stop_hits:
        # 零落单不是「通过」：conf 门槛×信号质量不匹配或行情缺失，预筛必须暴露出来
        st["verdict"] = ("inert（零落单：conf 门槛 "
                         f"{prof.get('params', {}).get('conf_floor', '-')} 高于信号质量，"
                         "档案在当前策略源下永不交易）")
        return st
    if not st["verify_ok"]:
        reasons.append("对账异常")
    if st["max_dd_pct"] < -abs(max_dd_line):
        reasons.append(f"回撤 {st['max_dd_pct']}% 破线 -{abs(max_dd_line):g}%")
    if reasons:
        st["verdict"] = "fail（" + "；".join(reasons) + "）"
    elif st["days"] < min_days:
        st["verdict"] = f"provisional（重放 {st['days']} 天 < 毕业线 {min_days} 天，预判通过）"
    else:
        st["verdict"] = "pass（预筛通过，进模拟盘实习）"
    return st


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

    p = sub.add_parser("board", help="成绩单（vs main 基准）")
    p.add_argument("--json", action="store_true", help="单行 JSON 输出（portal 子进程用）")
    p.set_defaults(fn=_cmd_board)

    p = sub.add_parser("replay", help="历史重放预筛（临时库，不碰真账本）")
    p.add_argument("--run", default=None)
    p.add_argument("--days", type=int, default=20, help="回看交易日数（缺省 20）")
    p.add_argument("--start", default=None, help="窗口起点 YYYYMMDD（缺省按 --days 推）")
    p.add_argument("--end", default=None, help="窗口终点 YYYYMMDD（缺省湖最新）")
    p.add_argument("--who", default=None)
    p.add_argument("--save", action="store_true", help="报告落 k_stock/data/replay_report_*.json")
    p.set_defaults(fn=_cmd_replay)

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
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, "rows": rows}, ensure_ascii=False))
        return 0
    print(f"{'账本':<10}{'状态':>8}{'天数':>6}{'总资产':>14}{'累计':>9}{'最大回撤':>9}"
          f"{'Sharpe':>8}{'Sortino':>9}  对账")
    for r in rows:
        if not r.get("days"):
            print(f"{r['name']:<10}{r['status']:>8}{0:>6}  （尚无快照）")
            continue
        ok = "OK" if r["verify_ok"] else "❌"
        sh = "-" if r.get("sharpe") is None else f"{r['sharpe']:+.2f}"
        so = "-" if r.get("sortino") is None else f"{r['sortino']:+.2f}"
        print(f"{r['name']:<10}{r['status']:>8}{r['days']:>6}{r['total']:>14,.2f}"
              f"{r['cum_pct']:>+8.2f}%{r['max_dd_pct']:>8.2f}%{sh:>8}{so:>9}  {ok}")
    return 0


def _cmd_replay(args) -> int:
    import datetime as _dt
    who = args.who.split(",") if args.who else None
    r = replay(args.run, days=args.days, start=args.start, end=args.end, who=who)
    w = r["window"]
    print(f"历史重放预筛 {r['run']}  窗口 {w['start']}→{w['end']}（{w['days']} 个交易日）:")
    print(f"{'账本':<10}{'落单':>5}{'止损':>5}{'天数':>6}{'累计':>9}{'最大回撤':>9}"
          f"{'Sharpe':>8}{'Sortino':>9}  判定")
    for s in r["rows"]:
        sh = "-" if s.get("sharpe") is None else f"{s['sharpe']:+.2f}"
        so = "-" if s.get("sortino") is None else f"{s['sortino']:+.2f}"
        if not s.get("days"):
            print(f"{s['name']:<10}{s.get('placed', 0):>5}{0:>5}{0:>6}  "
                  f"{s.get('verdict', '')}")
            continue
        print(f"{s['name']:<10}{s.get('placed', 0):>5}{s.get('stop_hits', 0):>5}"
              f"{s['days']:>6}{s['cum_pct']:>+8.2f}%{s['max_dd_pct']:>8.2f}%"
              f"{sh:>8}{so:>9}  {s['verdict']}")
    if getattr(args, "save", False):
        out = DATA_DIR / ("replay_report_"
                          + _dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
        out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"报告已落 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
