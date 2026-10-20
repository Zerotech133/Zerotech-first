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
  python k_stock/paper/trainee.py grad-check                 # 毕业验收判定（建议面，不自动改档案）
  python k_stock/paper/trainee.py hire chaser --source qd    # 雇佣（仅 live；A3 配置面）
  python k_stock/paper/trainee.py fire chaser --source qd    # 解雇（注册表行 status=ended）
  python k_stock/paper/trainee.py hires                      # 雇佣关系列表
  python k_stock/paper/trainee.py exec --source qd           # 按雇佣路由落单（07:05 automation 用）

automation 建议（训练期）：07:05 主账本闭环（settle→exec→check-stops→mark）后追加
  trainee.py routine    # 子账本同节奏：撮昨日 pending → 挂/查止损 → 快照
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
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
    prof = json.loads(p.read_text(encoding="utf-8"))
    return _apply_tier(prof)


def _load_tiers() -> dict:
    """经验等级协议（K-STOCK-101）：tiers.json 缺失/损坏时静默返回空表
    （档案无 tier 字段则零影响，向后兼容）。"""
    tp = TRAINEE_DIR / "tiers.json"
    if not tp.exists():
        tp = Path(__file__).resolve().parent / "tiers.json"
    try:
        return json.loads(tp.read_text(encoding="utf-8")).get("tiers", {})
    except Exception:
        return {}


def _apply_tier(prof: dict) -> dict:
    """tier 缺省补齐 + tier 缺省参数合并（档案显式 params 优先——
    老田拍板过的止损等不被协议覆盖）。llm_binding 只挂字段不动路由
    （M2 网关侧接线前是纯元数据）。"""
    tiers = _load_tiers()
    tid = prof.get("tier") or "T2"
    t = tiers.get(tid)
    if not t:
        prof.setdefault("tier", "T2")
        return prof
    prof["tier"] = tid
    prof.setdefault("llm_binding", t.get("llm_binding"))
    merged = dict(t.get("params") or {})
    merged.update(prof.get("params") or {})   # 显式优先
    prof["params"] = merged
    prof.setdefault("tier_label", t.get("label"))
    prof.setdefault("daily_cost_cap", t.get("daily_cost_cap"))
    return prof


def list_profiles() -> list[dict]:
    out = []
    for p in sorted(TRAINEE_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


# status 口径（P-5 止损档案化新增）：training/live=训练链；design=仅重放预筛；
# ops=运营账本档案（main/us_qingping 等）——只参与止损档案化与 board 展示，
# 不进 routine/settle/exec/毕业链（运营账本自有闭环，勿双跑）。
OPS_STATUS = "ops"


def stop_dist_for(account: str) -> float | None:
    """账户止损距离档案化读取（K-STOCK-077 P-5）：trainees/<account>.json
    params.stop_dist_pct。档案缺失/未配 → None（调用方自行回退或跳过）。
    替代 cli.py 硬编码 US_STOP_DIST——改档案 JSON 即生效，零代码变更。"""
    p = TRAINEE_DIR / f"{account}.json"
    if not p.exists():
        return None
    try:
        prof = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    dist = prof.get("params", {}).get("stop_dist_pct")
    return float(dist) if dist else None


# K-STOCK-414：ai_team 通道排除面补全（qd/qpzm/qpzmus/qpzmhk 通道产物
# + pf 组合并集 + adapter 适配器产物——字典序 poc_pf_* 恒大于日期戳文件名，
# 混入即喂错信号源；poc_qpzmhk_ 为 H-D2 HK 通道，同款排除）
_NON_AI_PREFIXES = ("poc_qd_", "poc_qpzm_", "poc_qpzmus_", "poc_qpzmhk_",
                    "poc_pf_", "poc_adapter_")


def _newest_by_mtime(paths) -> Path:
    """按文件 mtime 取最新（替代字典序：前缀字母恒大于日期戳的排序陷阱）。

    K-STOCK-414 补（WO-QT-20261020-017）：mtime 相等时必须有确定性破平——
    glob 顺序是文件系统返回序（NTFS 非字典序），max() 遇并列返回**首个**，
    于是「同刻落盘的多个 run」选中哪个纯看磁盘布局，实测 k_stock/tests 全量跑
    间歇性把 ai_team 最新 run 选成较旧的 poc_20260101_a.json（单跑也偶发）。
    破平键取文件名（日期戳升序 = 新旧顺序，且前缀已在 _NON_AI_PREFIXES /
    调用方 prefix 过滤掉，不会回退到 414 要防的字典序陷阱）；mtime 用整数
    纳秒比较，避开 float 秒的小数位精度损失。
    """
    return max(paths, key=lambda p: (p.stat().st_mtime_ns, p.name))


def _newest_ai_run() -> Path:
    runs = [x for x in DATA_DIR.glob("poc_*.json")
            if not x.name.startswith(_NON_AI_PREFIXES)]
    if not runs:
        raise Reject("k_stock/data/ 下无 poc_*.json（AI 通道）")
    return _newest_by_mtime(runs)


# ---------------- 雇佣交互（A3/K-STOCK-042：配置面，门户永不直接下单） ----------------

HIRES_PATH = HERE / "hires.json"
STRAT_OF_SOURCE = {"qd": "double_knife", "qpzm": "qingping", "ai_team": "ai_team"}


def load_hires() -> dict:
    """雇佣注册表；缺失/损坏 fail-closed 返回空表（路由零雇佣，不炸 07:05 主闭环）。"""
    try:
        d = json.loads(HIRES_PATH.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("hires"), list):
            return d
    except Exception:
        pass
    return {"hires": []}


def _save_hires(d: dict) -> None:
    """原子写（临时文件+os.replace）：写一半崩溃不会产生半截注册表——
    load_hires 的 fail-closed 是最后防线，不该被常态化触发。"""
    tmp = HIRES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(HIRES_PATH))


def hire(trader: str, source: str, note: str = "") -> dict:
    """雇佣 = 策略源路由到 live 交易员档案（只改注册表，次日主闭环生效）。"""
    if source not in STRAT_OF_SOURCE:
        raise Reject(f"未知策略源: {source}（可选 {'/'.join(STRAT_OF_SOURCE)}）")
    prof = load_profile(trader)
    if prof.get("status") != "live":
        raise Reject(f"{trader} 状态 {prof.get('status')} ≠ live，不可雇佣")
    d = load_hires()
    for h in d["hires"]:
        if (h.get("trader") == trader and h.get("source") == source
                and h.get("status") == "active"):
            raise Reject(f"{trader} 已被 {source} 雇佣（先 fire 再操作）")
    rec = {"trader": trader, "source": source, "status": "active",
           "hired_at": datetime.now().isoformat(timespec="seconds"), "note": note}
    d["hires"].append(rec)
    _save_hires(d)
    return rec


def fire(trader: str, source: str) -> dict:
    d = load_hires()
    for h in d["hires"]:
        if (h.get("trader") == trader and h.get("source") == source
                and h.get("status") == "active"):
            h["status"] = "ended"
            h["fired_at"] = datetime.now().isoformat(timespec="seconds")
            _save_hires(d)
            return h
    raise Reject(f"无 active 雇佣: {trader}×{source}")


def hires_list() -> list[dict]:
    """注册表 + 档案名册合并视图（前端/审计直接用；悬空雇佣如实保留）。"""
    profs = {p["id"]: p for p in list_profiles()}
    return [{**h, "trader_name": profs.get(h.get("trader"), {}).get("name"),
             "trader_status": profs.get(h.get("trader"), {}).get("status")}
            for h in load_hires().get("hires", [])]


def _newest_run_for(source: str) -> Path:
    """按策略源取最新产物（qd=poc_qd_* / qpzm=poc_qpzm_* / ai_team=其余 poc_*）。"""
    prefix = {"qd": "poc_qd_", "qpzm": "poc_qpzm_"}.get(source)
    if prefix is None:
        runs = [x for x in DATA_DIR.glob("poc_*.json")
                if not x.name.startswith(_NON_AI_PREFIXES)]
    else:
        runs = list(DATA_DIR.glob(prefix + "*.json"))
    if not runs:
        raise Reject(f"k_stock/data/ 下无 {source} 源产物（{prefix or 'poc_*'}）")
    return _newest_by_mtime(runs)  # K-STOCK-414：mtime 取最新，替代字典序


def _hired_profiles(source: str) -> list[dict]:
    """active 雇佣 → 交易员档案（仅 training/live；悬空雇佣跳过不炸）。
    口径决策（审查 2026-10-09 显性化）：雇佣时校验 live，但路由时 training
    也放行——覆盖「live 期回撤失控降回 training 复训」场景：复训期继续接
    信号才有净值数据可验收，断供会让复训永远无法毕业。design 始终不喂。"""
    out = []
    for h in load_hires().get("hires", []):
        if h.get("source") != source or h.get("status") != "active":
            continue
        try:
            prof = load_profile(h.get("trader", ""))
        except Reject:
            continue
        if prof.get("status") in ("training", "live"):
            out.append(prof)
    return out


def _engine_for(profile: dict, market, db: Path | None = None) -> PaperEngine:
    """按档案开子账本引擎：account=id，风控参数用档案覆盖默认值。"""
    pr = profile.get("params", {})
    cfg = {"initial_cash": float(profile.get("initial_cash", 1_000_000.0))}
    for k in ("max_pos_pct", "max_total_pct", "commission_rate", "min_commission", "stamp_tax"):
        if k in pr:
            cfg[k] = pr[k]
    return PaperEngine(db or DEFAULT_DB, market, config=cfg, account=profile["id"])


# ---------------- 训练动作 ----------------

def _tier_gate(prof: dict, eng: PaperEngine) -> str | None:
    """K-STOCK-101 T4/T5 纪律门槛（077w）：任一触发返回拒绝原因，None=放行。
    dd_circuit_pct：账本净值自峰值回撤超阈 → 熔断停开新仓（已有持仓/止损不受影响）。
    judge_min_grade：最近一次 judge 评审等级低于要求（昨日评审，exec 在 routine 尾
    judge 之前跑）→ 暂停加仓。评审文件缺失=无记录，不拦（首跑宽限）。"""
    pr = prof.get("params", {})
    dd = pr.get("dd_circuit_pct")
    if dd:
        rows = eng.con.execute(
            "SELECT total FROM equity_marks WHERE account_id=? ORDER BY date",
            (prof["id"],)).fetchall()
        if len(rows) >= 2:
            peak = max(r["total"] for r in rows)
            if peak > 0:
                drawdown = (peak - rows[-1]["total"]) / peak * 100.0
                if drawdown > float(dd):
                    return (f"回撤熔断：峰值回撤 {drawdown:.2f}% > "
                            f"dd_circuit_pct {dd}%（停开新仓）")
    jmin = pr.get("judge_min_grade")
    if jmin:
        order = {"A": 4, "B": 3, "C": 2, "D": 1}
        try:
            jdir = DATA_DIR / "judge_reviews"
            files = sorted(jdir.glob("*.json"))
            if files:
                jd = json.loads(files[-1].read_text(encoding="utf-8"))
                grade = jd.get("grade")
                if grade and order.get(grade, 0) < order.get(jmin, 0):
                    return (f"judge 门槛：最近评审 {jd.get('date')} 为 {grade} 级 "
                            f"< 要求 {jmin} 级（暂停加仓）")
        except Exception:
            pass  # 门槛判定失败不拦（与 judge 失败不阻塞闭环同口径）
    return None


def exec_for(run_path: str | None = None, who: list[str] | None = None,
             dry_run: bool = False, db: Path | None = None,
             source: str | None = None, force_trading: bool = False) -> dict:
    """同一条 picks run 落到若干训练子账本（conf_floor 在引擎端过滤 BUY）。
    source 雇佣路由（A3）：传入时只喂 hires.json active 交易员，run 取该源最新
    产物，strategy 按源标注（qd=double_knife / qpzm=qingping / ai_team=ai_team）。
    force_trading：跳过交易日守卫（调试用；automation 勿加，077q）。"""
    if source and who:
        raise Reject("--source 与 --who 互斥：雇佣路由按注册表定账本，"
                     "不接受手工指定（调试请二选一）")
    if source:
        profiles = _hired_profiles(source)
        if not profiles:
            raise Reject(f"策略源 {source} 无可用雇佣（active 且 training/live）")
        # K-STOCK-044 M3 口径（保守观察）：US 信号只进 us_qingping 独立账本
        # （qpzm_adapter._run_us），训练账本暂不喂 US——_newest_run_for("qpzm")
        # 只认 poc_qpzm_*（CN），poc_qpzmus_* 已从 AI 通道排除。独立账本跑通后再议。
        strategy = STRAT_OF_SOURCE.get(source, "ai_team")
        run = Path(run_path) if run_path else _newest_run_for(source)
    else:
        strategy = "ai_team"
        run = Path(run_path) if run_path else _newest_ai_run()
        profiles = list_profiles() if not who else [load_profile(t) for t in who]
        profiles = [p for p in profiles if p.get("status") in ("training", "live")]
        if not profiles:
            raise Reject("无可训练交易员（status=training/live）")
    if not run.exists() and not run.is_absolute():
        run = DATA_DIR / run   # 相对路径按 k_stock/data/ 解析（与 CLI cwd 无关）
    market = ParquetMarket()   # 行情湖只读，多账本共享一份
    if not force_trading:
        from trade_guard import ensure_trading_day   # 077q 交易日守卫
        ensure_trading_day(market, market_label="CN")
    out = {"run": run.name, "source": source, "strategy": strategy,
           "trainees": [], "dry_run": dry_run}
    for prof in profiles:
        eng = _engine_for(prof, market, db)
        min_conf = prof.get("params", {}).get("conf_floor")
        gate = _tier_gate(prof, eng)   # 077w：T4/T5 纪律门槛（回撤熔断/judge 评审级）
        if gate and not dry_run:
            out["trainees"].append({
                "id": prof["id"], "name": prof["name"],
                "pending": 0, "skipped": 1,
                "detail": {"orders": [], "skipped": [gate]},
                "tier_gate": gate,
            })
            continue
        r = eng.exec_run(run, dry_run=dry_run, strategy=strategy,
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
    """按档案 stop_dist_pct 给子账本持仓挂初始止损（成本锚定；已有止损不覆盖）。
    P-5 起 ops 运营账本（main/us_qingping）同样按档案参与挂止损——机制统一，
    main 是否挂由档案 stop_dist_pct 决定（null=未拍板不挂）。"""
    profiles = list_profiles() if not who else [load_profile(t) for t in who]
    profiles = [p for p in profiles
                if p.get("status") in ("training", "live", OPS_STATUS)]
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
        # K-STOCK-122 M7：手机端止盈线检查（exit_rules.json，异常不阻塞闭环）
        tp_triggered = 0
        try:
            from exit_rules import check_tps as _check_tps
            tp = _check_tps(aid, eng=eng, dry_run=dry_run)
            tp_triggered = len([t for t in tp["triggered"]
                                if t.get("status") == "pending"])
        except Exception as e:  # noqa: BLE001
            out.setdefault("tp_error", []).append({"account": aid,
                                                   "error": repr(e)})
        mk = None if dry_run else eng.mark(account_id=aid)
        out["trainees"].append({
            "id": aid, "name": prof["name"],
            "settled": len(st["filled"]), "rejected": len(st["rejected"]),
            "stops_set": stops_set,
            "stop_triggered": len([t for t in ck["triggered"] if t.get("status")]),
            "tp_triggered": tp_triggered,
            "mark": mk,
        })
    # K-STOCK-122 M7：ops 运营账本（main 等）不在上方训练循环内，
    # 手机端止盈线若设在这些账本也须检查（异常不阻塞）。
    if not dry_run:
        try:
            from exit_rules import _load as _er_load, check_tps as _check_tps
            tp_accounts = sorted({r["account"] for r in _er_load().get("rules", [])
                                  if r.get("kind") == "tp"
                                  and r["account"] not in
                                  {p["id"] for p in profiles}})
            for tp_aid in tp_accounts:
                tp = _check_tps(tp_aid)
                out.setdefault("ops_tp", []).append(
                    {"account": tp_aid, "triggered": len(
                        [t for t in tp["triggered"]
                         if t.get("status") == "pending"])})
        except Exception as e:  # noqa: BLE001
            out["ops_tp_error"] = repr(e)
    # 077w：端3 judge 日评审（轻量规则版，llm=False；异常不阻塞闭环）
    if not dry_run:
        try:
            from judge import review as _judge_review
            out["judge"] = _judge_review()
        except Exception as e:  # noqa: BLE001
            out["judge"] = {"ok": False, "error": repr(e)}
        # K-STOCK-122 M5：投研经理复盘会（judge 升级，产出会议纪要 JSON；
        # 异常不阻塞闭环）
        try:
            from judge import meeting as _judge_meeting
            out["meeting"] = _judge_meeting()
        except Exception as e:  # noqa: BLE001
            out["meeting"] = {"ok": False, "error": repr(e)}
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
    # 训练账本（ops 且 id=main 跳过：上面基准行已含，防重复行）
    for prof in list_profiles():
        if prof["id"] == "main":
            continue
        eng = _engine_for(prof, market, con_db)
        rows.append({"id": prof["id"], "name": prof["name"],
                     "style": prof.get("style"), "status": prof.get("status"),
                     **_stats(eng, prof["id"])})
    return rows


# ---------------- 毕业验收判定（K-STOCK-043：建议面） ----------------

def graduation_check(db: Path | None = None) -> dict:
    """毕业验收：board 四判据 vs 档案 graduation 线逐条核对。

    建议面哲学（与 hire 同款）：判定自动化、变更留人——达标只输出
    「建议改 live」，档案 status 变更仍由老板复核后手改 JSON。
    四判据 = 实习天数 / 最大回撤 / 对账通过 / 相对基准超额 > 0。"""
    rows = {r["id"]: r for r in scoreboard(db)}
    main = rows.get("main", {})
    out = []
    for prof in list_profiles():
        if prof.get("status") == OPS_STATUS:
            continue  # 运营账本不参与毕业验收（无 graduation 段，避免噪声行）
        r = rows.get(prof["id"], {})
        grad = prof.get("graduation", {})
        base = {"id": prof["id"], "name": prof.get("name"),
                "status": prof.get("status")}
        if not r.get("days"):
            out.append({**base, "eligible": False,
                        "reason": "无净值快照（未开账或未跑 routine）"})
            continue
        min_days = int(grad.get("min_days", 20))
        dd_line = -abs(float(grad.get("max_drawdown_pct", 15)))
        checks = {
            "days": {"need": f">= {min_days}", "have": r["days"],
                     "pass": r["days"] >= min_days},
            "max_dd": {"need": f">= {dd_line:g}%", "have": r["max_dd_pct"],
                       "pass": r["max_dd_pct"] >= dd_line},
            "verify": {"need": "100%", "have": "100%" if r["verify_ok"] else "异常",
                       "pass": bool(r["verify_ok"])},
        }
        bench = grad.get("bench", "main")
        excess = round(r["cum_pct"] - main.get("cum_pct", 0.0), 2)
        checks["excess"] = {"need": f">0 vs {bench}", "have": excess,
                            "pass": excess > 0}
        ok = all(c["pass"] for c in checks.values())
        out.append({**base, "checks": checks, "eligible": ok,
                    "suggest": ("达标 → 建议老板复核后改档案 status=live"
                                if ok else "未达标 → 继续 training")})
    return {"ok": True, "rows": out}


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
    p.add_argument("--source", default=None,
                   help="雇佣路由源（qd/qpzm/ai_team）：只喂 hires.json active 交易员")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force-trading", dest="force_trading", action="store_true",
                   help="跳过交易日守卫（调试用；automation 勿加）")
    p.set_defaults(fn=_cmd_exec)

    p = sub.add_parser("routine", help="每日训练例程：settle→auto-stops→check-stops→mark")
    p.add_argument("--who", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=_cmd_routine)

    p = sub.add_parser("board", help="成绩单（vs main 基准）")
    p.add_argument("--json", action="store_true", help="单行 JSON 输出（portal 子进程用）")
    p.set_defaults(fn=_cmd_board)

    p = sub.add_parser("grad-check", help="毕业验收判定（建议面，不自动改档案）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_grad_check)

    p = sub.add_parser("hire", help="雇佣：策略源路由到 live 交易员（仅改注册表）")
    p.add_argument("trader")
    p.add_argument("--source", required=True, help="qd / qpzm / ai_team")
    p.add_argument("--note", default="")
    p.add_argument("--json", action="store_true", help="单行 JSON 输出（portal 子进程用）")
    p.set_defaults(fn=_cmd_hire)

    p = sub.add_parser("fire", help="解雇：注册表行 status=ended")
    p.add_argument("trader")
    p.add_argument("--source", required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_fire)

    p = sub.add_parser("hires", help="雇佣关系列表")
    p.set_defaults(fn=_cmd_hires)

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
        sd = pr.get("stop_dist_pct")
        cf = pr.get("conf_floor")
        print(f"  [{p.get('status','?'):>8}] {p['id']:<8} {p['name']}  {p.get('style') or ''}"
              f"  单票≤{(pr.get('max_pos_pct') or 0):.0%}"
              f" 总仓≤{(pr.get('max_total_pct') or 0):.0%}"
              f" 止损{('-' + format(sd, '.0%')) if sd else '未配'}"
              f" conf≥{'-' if cf is None else format(cf, '.2f')}")
    return 0


def _cmd_exec(args) -> int:
    who = args.who.split(",") if args.who else None
    r = exec_for(args.run, who, args.dry_run, args.db_path, source=args.source,
                 force_trading=getattr(args, "force_trading", False))
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


def _cmd_grad_check(args) -> int:
    r = graduation_check(args.db_path)
    if getattr(args, "json", False):
        print(json.dumps(r, ensure_ascii=False))
        return 0
    print("毕业验收判定（建议面 · 变更由老板复核后手改档案 JSON）:")
    hit = False
    for row in r["rows"]:
        if row.get("reason"):
            print(f"  {row['name']}({row['id']}): {row['reason']}")
            continue
        marks = " ".join(
            f"{k}={'✓' if c['pass'] else '✗'}({c['have']}/{c['need']})"
            for k, c in row["checks"].items())
        print(f"  {row['name']}({row['id']}) [{row['status']}]: {marks}"
              f" → {row['suggest']}")
        hit = hit or row["eligible"]
    if not any(row.get("eligible") for row in r["rows"]):
        print("  （当前无达标交易员——毕业线见 trainees/*.json graduation 段）")
    return 0


def _cmd_hire(args) -> int:
    try:
        rec = hire(args.trader, args.source, args.note)
    except Reject as e:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 2
        print(f"❌ {e}")
        return 1
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, "hire": rec}, ensure_ascii=False))
        return 0
    print(f"✅ 已雇佣 {rec['trader']} × {rec['source']}"
          f"（注册表 {HIRES_PATH.name} · 次日 07:05 主闭环生效）")
    return 0


def _cmd_fire(args) -> int:
    try:
        rec = fire(args.trader, args.source)
    except Reject as e:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
            return 2
        print(f"❌ {e}")
        return 1
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, "fired": rec}, ensure_ascii=False))
        return 0
    print(f"✅ 已解雇 {args.trader} × {args.source}")
    return 0


def _cmd_hires(args) -> int:
    rows = hires_list()
    if not rows:
        print("（无雇佣记录 · hire <trader> --source <qd/qpzm/ai_team> 开始雇佣）")
        return 0
    for h in rows:
        line = (f"  [{h.get('status', '?'):>6}] {h.get('trader_name') or h['trader']}"
                f" × {h['source']}  雇于 {h.get('hired_at', '?')}")
        if h.get("fired_at"):
            line += f"  解于 {h['fired_at']}"
        if h.get("note"):
            line += f"  # {h['note']}"
        print(line)
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
