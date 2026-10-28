# -*- coding: utf-8 -*-
"""周一(2026-10-13)撮合验收只读验收器（K-STOCK-069）。

五判据逐条检查 paper.db（mode=ro，零写入），输出逐条 PASS/FAIL＋明细，
供盘后一条命令验收；提前跑（盘前/周末）应全部 FAIL——即探测器自证有效。

判据对应 docs/ops/2026-10-13_周一撮合验收清单.md §2：
  C1 CN 批次(plan_date=--plan-day, 默认 20261012) filled >= 4（main/chaser，
     fill 字段齐全；191 口径 B：≥ 判据，残留 pending 降观察项；
     209 v1.1：批次口径——成交日允许跨验收日，CN 10-12 成交 US 10-13 成交）
  C2 US 批次 6 笔 pending→filled（us_qingping，硬等式——US 无超额单源）
  C3 持仓生成后止损自动挂出（US 无持仓=SKIP，192）
  C4 positions_snapshot 出现验收日(--date)快照行（首战）
  C5 幂等：同日同 ref 至多一条 filled（无重复成交）
口径版本化：docs/ops/acceptance_criteria_v1.md（v1.1，改口径必升版本）

用法：
  python acceptance_check.py --date 20261013          # 周一盘后验收
  python acceptance_check.py --date 20261013 --json   # 机器可读输出
  python acceptance_check.py --daily [--json]         # 每日三查（断档+消费链活性），
                                                      # 退出码：P0→2 / P1→1 / 全绿→0

每日三查（K-PLAN-V1 批0 任务0.2，其中产物查为既有实现不在此重复）：
  查一 账本断档（P0）：在役账本 equity_marks 最新 date 距今超过 3 个交易日
       （按各自市场日历：A股 ParquetMarket / US UsMarket / HK HkMarket；
       零 mark 账本=灰位期不告警；日历源不可用降级 P2 不误报）。
  查二 消费链活性（P1）：rps_daily_batches.jsonl 最近 5 批中含买入 pick 的批次，
       其 ts 日期（北京时间）当日 orders.created_at 无同日记录=买而不落；
       连续 ≥2 批如此 → 消费链停摆指纹（10-13 断档 7 天事故防再发）。
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "paper.db"
CN_ACCOUNTS = ("main", "chaser")
US_ACCOUNTS = ("us_qingping",)
HK_ACCOUNTS = ("hk_qingping",)  # H-D2 P-6：HK 影子账本对账可见（灰位期无期望笔数判据）
CN_EXPECT = 4  # CN filled 期望笔数
US_EXPECT = 6  # US filled 期望笔数

# ---- 每日三查常量（K-PLAN-V1-0.2） ----
# 在役账本（taishan 为 A股人民币侧现役第三账本）；hawk_test 等测试账本不在役
ACTIVE_ACCOUNTS = ("main", "chaser", "taishan", "us_qingping", "hk_qingping")
LEDGER_STALE_GAP = 3  # 最新 mark 距今超过 N 个交易日 → P0
CHAIN_WINDOW = 5  # 消费链活性回看批数
CHAIN_MIN_RUN = 2  # 连续 ≥N 批买而不落 → P1
BATCHES_FILE = (
    Path(__file__).resolve().parent.parent.parent
    / "quant_backtest"
    / "reports"
    / "rps_daily_batches.jsonl"
)


def ro_connect():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def check_c1_cn_filled(cur, day, plan_day):
    # 口径 v1.1（K-STOCK-209 修订，老田 10-12 拍板 B 语义不变）：验收主语=
    # 「20261012 落单批次转 filled」——批次按 plan_date=plan_day 圈定，成交日
    # 允许跨验收日（实况：CN 10-12 成交、US 10-13 成交；v1 的 fill_date=验收日
    # 过滤在跨日场景会把已成交批次误判 FAIL）。批次隔离天然免疫历史/未来
    # 批次混入（201 防混入诉求由此继承，不再依赖 fill_date==day）。
    # filled >= CN_EXPECT 即 PASS（191 拍板 B）；批次内残留 pending 降观察项。
    rows = cur.execute(
        "SELECT id, account_id, stock, qty, status, fill_date, filled_price,"
        " filled_qty, fee, plan_date, reason FROM orders"
        " WHERE account_id IN (?,?) AND plan_date=?",
        CN_ACCOUNTS + (plan_day,),
    ).fetchall()
    filled = [
        r
        for r in rows
        if r["status"] == "filled" and r["filled_price"] and r["filled_qty"]
    ]
    pending = [r for r in rows if r["status"] == "pending"]
    rejected = [r for r in rows if r["status"] == "rejected"]
    ok = len(filled) >= CN_EXPECT
    detail = {
        "expected_min": CN_EXPECT,
        "filled": len(filled),
        "batch": plan_day,
        "pending_watch": len(pending),
        "rejected": [
            {"id": r["id"], "stock": r["stock"], "why": (r["reason"] or "")[:80]}
            for r in rejected
        ],
        "fills": [
            {
                k: r[k]
                for k in (
                    "id",
                    "account_id",
                    "stock",
                    "qty",
                    "fill_date",
                    "filled_price",
                    "filled_qty",
                    "fee",
                )
            }
            for r in filled
        ],
    }
    return ok, detail


def check_c2_us_filled(cur, day, plan_day):
    # 口径 v1.1：同 C1 批次口径（plan_date=plan_day 圈定 US 6 笔），硬等式不变
    rows = cur.execute(
        "SELECT id, stock, qty, status, fill_date, filled_price, filled_qty,"
        " plan_date, reason FROM orders"
        " WHERE account_id=? AND plan_date=?",
        (US_ACCOUNTS[0], plan_day),
    ).fetchall()
    filled = [
        r
        for r in rows
        if r["status"] == "filled" and r["filled_price"] and r["filled_qty"]
    ]
    pending = [r for r in rows if r["status"] == "pending"]
    rejected = [r for r in rows if r["status"] == "rejected"]
    ok = len(filled) == US_EXPECT and not pending
    detail = {
        "expected": US_EXPECT,
        "filled": len(filled),
        "batch": plan_day,
        "pending_left": len(pending),
        "rejected": [
            {"id": r["id"], "stock": r["stock"], "why": (r["reason"] or "")[:80]}
            for r in rejected
        ],
        "fills": [
            {k: r[k] for k in ("id", "stock", "qty", "fill_date", "filled_price")}
            for r in filled
        ],
    }
    return ok, detail


def check_c3_stops(cur, day):
    """止损自动挂实证=positions.stop_price 非空（挂出待触发），
    而非 order_kind='stop' 触发单（收盘跌破后才生成，成交当日必空——首版口径坑）。
    分层断言（K-STOCK-069c 口径）：
      核心=US 6 只持仓 stop_price 全非空（cli G2 8% 成本锚定承诺）；
      chaser=trainee routine auto-stops（0.08）一并断言；
      main 无自动止损机制（设计现状，是否补默认档待老田拍板）——只展示不并判据。"""
    n_pos = cur.execute("SELECT count(*) FROM positions").fetchone()[0]
    if n_pos == 0:
        return None, {"skip": "positions 空（未撮合，判据未激活）"}
    rows = cur.execute("SELECT account_id, stock, stop_price FROM positions").fetchall()
    by_acct = {}
    for r in rows:
        a = by_acct.setdefault(r["account_id"], {"n": 0, "with_stop": 0, "no_stop": []})
        a["n"] += 1
        if r["stop_price"] is not None:
            a["with_stop"] += 1
        else:
            a["no_stop"].append(r["stock"])
    us = by_acct.get(US_ACCOUNTS[0], {"n": 0, "with_stop": 0})
    chaser = by_acct.get("chaser", {"n": 0, "with_stop": 0})
    if us["n"] == 0:
        # K-STOCK-192: US 无持仓=判据未激活（US 未成交无从挂止损），SKIP 而非
        # FAIL——否则 C2 FAIL 时 C3 必然陪绑 FAIL（伴生噪声污染归因）；
        # FAIL 收敛到「有持仓但无止损=真缺口」。G2 接线已核实（cmd_settle US 段
        # 成交后 _stop_dist_of 自动挂，us_qingping=0.08 档案承载）。
        return None, {
            "skip": "US 无持仓（未成交，判据未激活；成交后 G2 自动挂 0.08）",
            "by_account": by_acct,
        }
    core_ok = us["with_stop"] == us["n"]
    chaser_ok = chaser["n"] == 0 or chaser["with_stop"] == chaser["n"]
    ok = core_ok and chaser_ok
    return ok, {
        "by_account": by_acct,
        "core": "US+chaser 止损全挂" if ok else "US/chaser 有止损缺口",
        "main_note": "main 无自动止损机制（设计现状，待拍板是否补默认档）"
        if "main" in by_acct and by_acct["main"]["no_stop"]
        else None,
    }


def check_c4_snapshot(cur, day):
    # K-STOCK-189: 表内 date 为连字符形态（2026-10-12），判据入参为 YYYYMMDD
    # ——两形态都查，防日期形态不匹配恒 FAIL
    day_hyphen = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else day
    rows = cur.execute(
        "SELECT account_id, stock, qty, market_value FROM positions_snapshot"
        " WHERE date IN (?, ?)",
        (day, day_hyphen),
    ).fetchall()
    rows = [dict(r) for r in rows]
    ok = len(rows) > 0
    return ok, {
        "n_rows": len(rows),
        "by_account": {
            a: sum(1 for r in rows if r["account_id"] == a)
            for a in CN_ACCOUNTS + US_ACCOUNTS + HK_ACCOUNTS
        },
    }


def check_c5_idempotent(cur, day):
    # K-STOCK-189: 空 ref（""）非 order_id 标识，剔除分组（原判据仅排 NULL，
    #   两条手工单空 ref 被误判同组重复）
    dup = cur.execute(
        "SELECT ref, count(*) c FROM orders WHERE status='filled' AND fill_date=?"
        " AND ref IS NOT NULL AND ref != '' GROUP BY ref HAVING c > 1",
        (day,),
    ).fetchall()
    dup = [dict(r) for r in dup]
    ok = not dup
    return ok, {
        "duplicate_refs": dup,
        "note": "同一 order_id(ref) 只允许一条 filled（同日）",
    }


# ======================================================================
# 每日三查（K-PLAN-V1 批0 任务0.2）：查一 账本断档 / 查二 消费链活性
# 全部只读（mode=ro），输出结构化 findings：
#   {severity: P0/P1/P2, check: str, account: str, detail: dict}
# ======================================================================


def _norm_day(d) -> str:
    """日期归一为 YYYYMMDD（equity_marks 表存 2026-10-15 连字符形态）。"""
    return str(d).replace("-", "")


def fetch_last_marks(cur) -> dict:
    """各账本 equity_marks 最新 date（原样连字符形态）。"""
    rows = cur.execute(
        "SELECT account_id, MAX(date) AS last_day FROM equity_marks GROUP BY account_id"
    ).fetchall()
    return {r["account_id"]: r["last_day"] for r in rows}


def fetch_order_dates(cur) -> set:
    """orders.created_at 的北京时间日期集合（YYYY-MM-DD，批次 ts 同形态可直接比对）。"""
    rows = cur.execute(
        "SELECT DISTINCT substr(created_at, 1, 10) AS d FROM orders"
        " WHERE created_at IS NOT NULL"
    ).fetchall()
    return {r["d"] for r in rows}


def _calendar_for(account: str):
    """在役账本 → 各自市场交易日历（YYYYMMDD 升序）。日历源不可用返回 None（降级）。

    账本前缀路由：hk_* → HkMarket（聚合日历 market_days.csv）、
    us_* → UsMarket（SPY CSV）、其余 → ParquetMarket（A股特征湖）。
    惰性 import + 双路径兜底（脚本直跑 sys.path=k_stock/paper；包内走 k_stock.paper.*），
    pandas/requests 缺失或湖文件缺失均降级，不在验收器内崩。"""
    try:
        if account.startswith("hk_"):
            try:
                from k_stock.paper.hk_market import HkMarket
            except ImportError:
                from hk_market import HkMarket
            days = HkMarket().trading_days()
        elif account.startswith("us_"):
            try:
                from k_stock.paper.us_market import UsMarket
            except ImportError:
                from us_market import UsMarket
            days = UsMarket().trading_days()
        else:
            try:
                from k_stock.paper.market import ParquetMarket
            except ImportError:
                from market import ParquetMarket
            days = ParquetMarket().trading_days()
        return [_norm_day(d) for d in days]
    except Exception as e:  # noqa: BLE001
        return e


def check_ledger_freshness(
    marks_by_account: dict,
    today: str,
    calendar_fn=None,
    max_gap: int = LEDGER_STALE_GAP,
):
    """查一 账本断档：最新 mark 距今 > max_gap 个交易日 → P0。

    marks_by_account: {account_id: 最新 mark date（任意连字符/紧凑形态）}
    today: YYYYMMDD；calendar_fn(account) → list[YYYYMMDD] 或 Exception（降级）
    零 mark = 灰位期（P2 流水不告警）。"""
    calendar_fn = calendar_fn or _calendar_for
    findings = []
    for acct in ACTIVE_ACCOUNTS:
        last = marks_by_account.get(acct)
        if not last:
            findings.append(
                {
                    "severity": "P2",
                    "check": "ledger_freshness",
                    "account": acct,
                    "detail": {"skip": "零 mark（灰位期，不告警）"},
                }
            )
            continue
        try:
            days = calendar_fn(acct)
        except Exception as e:  # noqa: BLE001
            days = e
        if isinstance(days, Exception) or days is None:
            why = str(days) if isinstance(days, Exception) else "calendar_fn 返回 None"
            findings.append(
                {
                    "severity": "P2",
                    "check": "ledger_freshness",
                    "account": acct,
                    "detail": {"degrade": f"日历源不可用，降级不误报: {why}"},
                }
            )
            continue
        last_n = _norm_day(last)
        future = [d for d in days if last_n < d <= today]
        gap = len(future)
        if gap > max_gap:
            findings.append(
                {
                    "severity": "P0",
                    "check": "ledger_freshness",
                    "account": acct,
                    "detail": {
                        "last_mark": last,
                        "gap_trading_days": gap,
                        "max_gap": max_gap,
                        "missing_days": future[-8:],
                    },
                }
            )
        elif gap == max_gap:
            findings.append(
                {
                    "severity": "P2",
                    "check": "ledger_freshness",
                    "account": acct,
                    "detail": {
                        "last_mark": last,
                        "gap_trading_days": gap,
                        "watch": f"距今已达 {gap} 个交易日（再断一日即 P0）",
                    },
                }
            )
        else:
            findings.append(
                {
                    "severity": "P2",
                    "check": "ledger_freshness",
                    "account": acct,
                    "detail": {"last_mark": last, "gap_trading_days": gap, "ok": True},
                }
            )
    return findings


def check_consumer_chain(batches: list, order_dates: set, min_run: int = CHAIN_MIN_RUN):
    """查二 消费链活性：买入批 ts 日期（北京）orders 无同日落单=买而不落，
    连续 ≥min_run 批 → P1（消费链停摆指纹）。无买入批不欠落单，链条重置。"""
    unhealthy, run, max_run = [], 0, 0
    buy_batches = 0
    for b in batches:
        picks = b.get("picks") or []
        n_buys = sum(1 for p in picks if str(p.get("action", "")).startswith("买入"))
        if n_buys == 0:
            run = 0
            continue
        buy_batches += 1
        ts_day = str(b.get("ts", ""))[:10]
        has_orders = ts_day in order_dates
        unhealthy.append(
            {"ts": b.get("ts"), "buys": n_buys, "orders_same_day": has_orders}
        )
        if has_orders:
            run = 0
        else:
            run += 1
            max_run = max(max_run, run)
    detail = {
        "window_batches": len(batches),
        "buy_batches": buy_batches,
        "max_stall_run": max_run,
        "min_run": min_run,
        "unhealthy": unhealthy,
    }
    if max_run >= min_run:
        return [
            {
                "severity": "P1",
                "check": "consumer_chain",
                "account": "*",
                "detail": {
                    **detail,
                    "verdict": f"连续 {max_run} 批买而不落（消费链停摆指纹）",
                },
            }
        ]
    return [
        {
            "severity": "P2",
            "check": "consumer_chain",
            "account": "*",
            "detail": {**detail, "verdict": "健康（买入批均有同日落单）", "ok": True},
        }
    ]


def read_batches(path: Path, window: int = CHAIN_WINDOW):
    """读批次 jsonl 尾部 window 批（时间升序）。文件缺失/不可读返回 None。"""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        return None
    out = []
    for ln in lines[-window:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            out.append({"ts": "", "picks": [], "bad_line": True})
    return out


def run_daily_checks(
    con=None,
    today: str | None = None,
    calendar_fn=None,
    batches_path: Path | None = None,
    window: int = CHAIN_WINDOW,
) -> tuple:
    """每日三查编排（断档+消费链活性）。返回 (findings, exit_code)。
    con 不传则按 DB 只读打开；today 默认本机日期（北京口径）。"""
    import datetime as _dt

    today = today or _dt.date.today().strftime("%Y%m%d")
    own_con = con is None
    if own_con:
        con = ro_connect()
    try:
        cur = con.cursor()
        marks = fetch_last_marks(cur)
        order_dates = fetch_order_dates(cur)
    finally:
        if own_con:
            con.close()
    findings = check_ledger_freshness(marks, today, calendar_fn=calendar_fn)
    batches = read_batches(batches_path or BATCHES_FILE, window)
    if batches is None:
        findings.append(
            {
                "severity": "P1",
                "check": "consumer_chain",
                "account": "*",
                "detail": {
                    "degrade": False,
                    "verdict": "批次产物文件缺失/不可读（零产物）",
                    "path": str(batches_path or BATCHES_FILE),
                },
            }
        )
    else:
        findings += check_consumer_chain(batches, order_dates)
    has_p0 = any(f["severity"] == "P0" for f in findings)
    has_p1 = any(f["severity"] == "P1" for f in findings)
    return findings, (2 if has_p0 else 1 if has_p1 else 0)


def _run_daily_cli(json_mode: bool) -> int:
    """--daily 入口：三查（断档+消费链活性），结构化输出 + 分级退出码。"""
    if not DB.exists():
        print(f"[exit 2] paper.db 不存在: {DB}")
        return 2
    findings, code = run_daily_checks()
    if json_mode:
        print(
            json.dumps(
                {"mode": "daily", "exit_code": code, "findings": findings},
                ensure_ascii=False,
                indent=1,
            )
        )
        return code
    print("=" * 62)
    print(f"每日三查 {__import__('datetime').date.today()}（只读，paper.db 未动）")
    for f in findings:
        print(
            f"  [{f['severity']}] {f['check']:17s} {f['account']:12s}"
            f"  {json.dumps(f['detail'], ensure_ascii=False)[:110]}"
        )
    print("=" * 62)
    print(f"总判 exit={code}（2=P0 数据断线 1=P1 跑批/消费链异常 0=全绿）")
    return code


def main(argv=None):
    ap = argparse.ArgumentParser(description="周一撮合验收只读验收器")
    ap.add_argument(
        "--date", default="20261013", help="验收日 YYYYMMDD（管 C4/C5 及总判语境）"
    )
    ap.add_argument(
        "--plan-day",
        default="20261012",
        help="落单批次日 YYYYMMDD（管 C1/C2 批次圈定，v1.1 口径）",
    )
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument(
        "--daily",
        action="store_true",
        help="每日三查模式（账本断档 P0 + 消费链活性 P1），退出码 P0→2 / P1→1 / 全绿→0",
    )
    args = ap.parse_args(argv)

    if args.daily:
        return _run_daily_cli(args.json)

    if not DB.exists():
        print(f"[exit 2] paper.db 不存在: {DB}")
        return 2
    con = ro_connect()
    cur = con.cursor()
    results = {
        "date": args.date,
        "plan_day": args.plan_day,
        "db": str(DB),
        "C1_cn_filled": check_c1_cn_filled(cur, args.date, args.plan_day),
        "C2_us_filled": check_c2_us_filled(cur, args.date, args.plan_day),
        "C3_stops_auto": check_c3_stops(cur, args.date),
        "C4_snapshot": check_c4_snapshot(cur, args.date),
        "C5_idempotent": check_c5_idempotent(cur, args.date),
    }
    con.close()

    def tag(v):
        return {True: "PASS", False: "FAIL", None: "SKIP"}[v]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        print("=" * 62)
        print(f"撮合验收 {args.date}（只读，paper.db 未动）")
        for k in (
            "C1_cn_filled",
            "C2_us_filled",
            "C3_stops_auto",
            "C4_snapshot",
            "C5_idempotent",
        ):
            ok, det = results[k]
            print(f"  {k:16s}: {tag(ok)}  {json.dumps(det, ensure_ascii=False)[:120]}")
        print("=" * 62)
    verdicts = [
        results[k][0]
        for k in (
            "C1_cn_filled",
            "C2_us_filled",
            "C3_stops_auto",
            "C4_snapshot",
            "C5_idempotent",
        )
    ]
    hard = [v for v in verdicts if v is not None]
    if not hard:
        print("总判：SKIP（判据未激活——尚未撮合）")
        return 0
    all_pass = all(hard)
    print(
        f"总判：{'PASS 五判据全过' if all_pass else 'FAIL（见上，未过项需排查撮合链）'}"
    )
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
