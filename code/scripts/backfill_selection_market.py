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
import argparse

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


def _csv_values(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _source_markets(source_strategies: str | None) -> list[str]:
    """从池行的 source_strategies 推断市场，避免裸 US/HK 代码被当成 CN。"""
    markets = {strategy_market(sid) for sid in _csv_values(source_strategies)}
    markets.discard(None)
    return sorted(market for market in markets if market in MARKETS)


def _pick_markets(c, pool_date, code: str) -> list[str]:
    """只查同一池日的 pick，不能拿全历史同代码市场来改历史池行。"""
    rows = c.execute(sa.text(
        "SELECT DISTINCT market FROM v2_selection_pick "
        "WHERE report_date=:d AND code=:c AND market IN ('CN','HK','US','CRYPTO')"),
        {"d": pool_date, "c": code}).all()
    return sorted(row[0] for row in rows if row[0] in MARKETS)


def _target_market(current: str, source_markets: list[str],
                   pick_markets: list[str]) -> str | None:
    """按池行自身的来源/同日 pick 选目标市场；不猜、不跨日聚合。"""
    if len(source_markets) == 1:
        return source_markets[0]
    if len(pick_markets) == 1:
        return pick_markets[0]
    return current if current in MARKETS else None


def _merge_csv(*values: str | None) -> str:
    return ",".join(sorted({item for value in values for item in _csv_values(value)}))


def _merge_pool_row(c, old: dict, target: dict) -> None:
    """同一池日/市场/代码已存在时，合并来源并删除旧错市场行，避免撞唯一键。"""
    c.execute(sa.text("""
        UPDATE v2_selection_pool
        SET source_strategies=:src, signal_types=:sigs, name=:name
        WHERE id=:i
    """), {
        "src": _merge_csv(old["source_strategies"], target["source_strategies"]),
        "sigs": _merge_csv(old["signal_types"], target["signal_types"]),
        "name": target["name"] or old["name"],
        "i": target["id"],
    })
    c.execute(sa.text("DELETE FROM v2_selection_pool WHERE id=:i"), {"i": old["id"]})


def _backfill_pool(c) -> tuple[int, int, int, int, int]:
    """回填池市场；返回 (总行数, 改市场, 合并, 无法判定, 不变)。"""
    pool = c.execute(sa.text(
        "SELECT id, pool_date, market, code, name, source_strategies, "
        "signal_types FROM v2_selection_pool ORDER BY id")).mappings().all()
    relabeled = merged = ambiguous = unchanged = 0
    for row in pool:
        target = _target_market(
            row["market"],
            _source_markets(row["source_strategies"]),
            _pick_markets(c, row["pool_date"], row["code"]),
        )
        if target is None:
            ambiguous += 1
            continue
        if target == row["market"]:
            unchanged += 1
            continue
        existing = c.execute(sa.text(
            "SELECT id, source_strategies, signal_types, name "
            "FROM v2_selection_pool "
            "WHERE pool_date=:d AND market=:m AND code=:c"),
            {"d": row["pool_date"], "m": target, "c": row["code"]}).mappings().first()
        if existing:
            _merge_pool_row(c, dict(row), dict(existing))
            merged += 1
        else:
            c.execute(sa.text(
                "UPDATE v2_selection_pool SET market=:m WHERE id=:i"),
                {"m": target, "i": row["id"]})
            relabeled += 1
    return len(pool), relabeled, merged, ambiguous, unchanged


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="回填 selection 表的 market 列")
    parser.add_argument("--dry-run", action="store_true",
                        help="在事务内执行后回滚，只打印将发生的变更")
    args, _ = parser.parse_known_args(argv)

    e = sa.create_engine(
        get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    with e.connect() as c:
        trans = c.begin()
        try:
            n_picks = _backfill_picks(c)
            n_reports = _split_reports(c)
            n_pool, relabeled, merged, ambiguous, unchanged = _backfill_pool(c)
            multi = c.execute(sa.text(
                "SELECT count(*) FROM v2_selection_report WHERE market='MULTI'")).scalar()
            print("backfilled picks=%d reports=%d pool=%d "
                  "relabeled=%d merged=%d ambiguous=%d unchanged=%d multi=%d"
                  % (n_picks, n_reports, n_pool, relabeled, merged,
                     ambiguous, unchanged, multi))
            if args.dry_run:
                trans.rollback()
                print("dry_run=1 rollback=1")
            else:
                trans.commit()
        except Exception:
            trans.rollback()
            raise


if __name__ == "__main__":
    main()
