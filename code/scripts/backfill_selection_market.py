# -*- coding: utf-8 -*-
"""回填 selection report/pick/pool 的 market 列（P2-2：按市场独立，不写 MULTI）。

每日 run_daily_research_ops 在 adapt/seed 之后调用本脚本：
1) 按代码/策略前缀回填 v2_selection_pick.market；
2) v2_selection_report.market 按所属 pick 的市场回填；一条日报跨市场时拆成
   多条按市场日报（pick 关联保留），绝不再写 market='MULTI'（库层 0045 也有
   CHECK 兜底）；
3) v2_selection_pool.market 按代码回填。

可重复执行（幂等）。
"""
import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

MARKETS = ("CN", "HK", "US", "CRYPTO")


def strategy_market(strategy_id: str) -> str | None:
    s = (strategy_id or "").strip().lower()
    for prefix, market in (("v1-us", "US"), ("v1-hk", "HK"),
                           ("v1-crypto", "CRYPTO"), ("v1-cn", "CN")):
        if s.startswith(prefix):
            return market
    return None


def infer_market(code: str, strategy_id: str = "") -> str | None:
    """按代码前缀/后缀 → 策略前缀推断市场；判断不了返回 None。

    注意：平台适配器的 HK/US 选股代码是裸代码（如 '00005'、'AAPL'），
    必须靠 strategy_id 前缀（v1-hk-*/v1-us-*）判定，不能落到 CN。
    """
    c = (code or "").lower()
    if c.startswith("rt_hk") or c.endswith((".hk", ".hkg")):
        return "HK"
    if c.startswith("gb_") or c.endswith((".us", ".nasdaq", ".nyse")):
        return "US"
    if c.startswith(("sh", "sz", "bj")) or c.endswith((".sh", ".sz", ".bj")):
        return "CN"
    if "/" in c and c.split("/")[-1] in ("usdt", "usdc", "btc", "eth"):
        return "CRYPTO"
    return strategy_market(strategy_id)


def market_of(code: str, strategy_id: str = "") -> str:
    return infer_market(code, strategy_id) or "CN"


def _backfill_picks(c) -> int:
    picks = c.execute(sa.text(
        "SELECT id, code, strategy_id, market FROM v2_selection_pick")).all()
    for pid, code, sid, current in picks:
        inferred = infer_market(code, sid)
        # 推断不出时保留已有合法市场，避免把 HK/US 裸代码误降级成 CN。
        target = inferred or (current if current in MARKETS else "CN")
        if target != current:
            c.execute(sa.text("UPDATE v2_selection_pick SET market=:m WHERE id=:i"),
                      {"m": target, "i": pid})
    return len(picks)


def _merge_into_report(c, rid: int, target_id: int) -> None:
    """把 rid 日报的 pick 合并进 target 日报（同 report_id+code 唯一，去重后删 rid）。"""
    c.execute(sa.text(
        "UPDATE v2_selection_pick SET report_id=:t WHERE report_id=:r "
        "AND code NOT IN (SELECT code FROM v2_selection_pick WHERE report_id=:t)"),
        {"t": target_id, "r": rid})
    c.execute(sa.text("DELETE FROM v2_selection_pick WHERE report_id=:r"), {"r": rid})
    c.execute(sa.text("DELETE FROM v2_selection_report WHERE id=:r"), {"r": rid})


def _new_report_like(c, report, market: str) -> int:
    c.execute(sa.text("""
        INSERT INTO v2_selection_report
          (report_date, strategy_id, market, analyst, market_view, yesterday_review,
           lessons, status, submitted_at)
        VALUES (:d, :s, :m, :a, :mv, :yr, :ls, :st, :sat)
    """), {"d": report["report_date"], "s": report["strategy_id"], "m": market,
             "a": report["analyst"], "mv": report["market_view"],
             "yr": report["yesterday_review"], "ls": report["lessons"],
             "st": report["status"], "sat": report["submitted_at"]})
    return c.execute(sa.text(
        "SELECT id FROM v2_selection_report WHERE report_date=:d "
        "AND strategy_id=:s AND market=:m"),
        {"d": report["report_date"], "s": report["strategy_id"], "m": market}).scalar()


def _split_reports(c) -> int:
    """按 pick 市场拆分跨市场日报；返回拆分后的日报总数。"""
    reports = c.execute(sa.text(
        "SELECT id, report_date, strategy_id, market, analyst, market_view, "
        "yesterday_review, lessons, status, submitted_at "
        "FROM v2_selection_report ORDER BY id")).mappings().all()
    for report in reports:
        rid = report["id"]
        picks = c.execute(sa.text(
            "SELECT id, market FROM v2_selection_pick WHERE report_id=:r ORDER BY id"),
            {"r": rid}).mappings().all()
        if not picks:
            target = report["market"] if report["market"] in MARKETS else (
                strategy_market(report["strategy_id"]) or "CN")
            if report["market"] != target:
                c.execute(sa.text("UPDATE v2_selection_report SET market=:m WHERE id=:i"),
                          {"m": target, "i": rid})
            continue
        markets = sorted({p["market"] if p["market"] in MARKETS
                          else strategy_market(report["strategy_id"]) or "CN"
                          for p in picks})
        if len(markets) == 1:
            target = markets[0]
            if report["market"] != target:
                existing = c.execute(sa.text(
                    "SELECT id FROM v2_selection_report WHERE report_date=:d "
                    "AND strategy_id=:s AND market=:m AND id<>:i"),
                    {"d": report["report_date"], "s": report["strategy_id"],
                     "m": target, "i": rid}).scalar()
                if existing:
                    _merge_into_report(c, rid, existing)
                else:
                    c.execute(sa.text(
                        "UPDATE v2_selection_report SET market=:m WHERE id=:i"),
                        {"m": target, "i": rid})
            continue
        # 跨市场：保留一行（优先保留它原来标的市场），其余市场各建一条日报
        keeper = report["market"] if report["market"] in markets else markets[0]
        c.execute(sa.text("UPDATE v2_selection_report SET market=:m WHERE id=:i"),
                  {"m": keeper, "i": rid})
        for market in markets:
            if market == keeper:
                continue
            existing = c.execute(sa.text(
                "SELECT id FROM v2_selection_report WHERE report_date=:d "
                "AND strategy_id=:s AND market=:m"),
                {"d": report["report_date"], "s": report["strategy_id"],
                 "m": market}).scalar()
            target_id = existing or _new_report_like(c, report, market)
            for pick in picks:
                if (pick["market"] if pick["market"] in MARKETS else None) != market:
                    continue
                c.execute(sa.text(
                    "UPDATE v2_selection_pick SET report_id=:t WHERE id=:i"),
                    {"t": target_id, "i": pick["id"]})
    return c.execute(sa.text("SELECT count(*) FROM v2_selection_report")).scalar()


def _pool_market(c, code: str) -> str:
    """pool 无 strategy_id 列：优先按同代码 pick 的实际市场取，裸代码才回退推断。"""
    markets = sorted({row[0] for row in c.execute(sa.text(
        "SELECT DISTINCT market FROM v2_selection_pick WHERE code=:c"),
        {"c": code}).all() if row[0] in MARKETS})
    if len(markets) == 1:
        return markets[0]
    return market_of(code)


def _backfill_pool(c) -> int:
    pool = c.execute(sa.text("SELECT id, code FROM v2_selection_pool")).all()
    for pid, code in pool:
        target = _pool_market(c, code)
        c.execute(sa.text("UPDATE v2_selection_pool SET market=:m WHERE id=:i"),
                  {"m": target, "i": pid})
    return len(pool)


def main():
    e = sa.create_engine(
        get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.begin() as c:
        n_picks = _backfill_picks(c)
        n_reports = _split_reports(c)
        n_pool = _backfill_pool(c)
        multi = c.execute(sa.text(
            "SELECT count(*) FROM v2_selection_report WHERE market='MULTI'")).scalar()
        print("backfilled picks=%d reports=%d pool=%d multi=%d"
              % (n_picks, n_reports, n_pool, multi))


if __name__ == "__main__":
    main()
