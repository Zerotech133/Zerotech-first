"""选股中台日报彻底按市场拆分，消灭 MULTI（0045）。

背景：0044 给 v2_selection_report / v2_selection_pick / v2_selection_pool 加了
market 列，但 v2_selection_report 的唯一键仍是 (report_date, strategy_id)。
sidecar 策略一次提交跨市场（CN/HK/US/CRYPTO）选股时，日报只能退化标
market='MULTI'，再被各市场页面重复展示，候选池溯源也无法按市场对齐。

本迁移（upgrade）：
1. 唯一约束从 (report_date, strategy_id) 改为
   (report_date, strategy_id, market)；约束名沿用 uq_v2_selection_report，
   以兼容现有 ON CONFLICT ON CONSTRAINT uq_v2_selection_report 写入方
   （scripts/adapt_platform_*.py、scripts/seed_selection_desk.py、API）；
2. 拆分历史 MULTI / 跨市场日报：按 v2_selection_pick.market 为每个市场复制
   一条日报，原 pick 重新挂到对应市场日报（保留 id 之外的落库字段），
   无 pick 的 MULTI 日报按 strategy_id 前缀回填市场，判定不了回退 CN；
3. 增加 CHECK (market <> 'MULTI')，库层杜绝再写出 MULTI。

回滚（downgrade）：
1. 删除 CHECK；
2. 同 (report_date, strategy_id) 的多市场日报合并回一条（market='MULTI'，
   pick 重新挂到保留行，同 code 冲突的 pick 丢弃）；
3. 唯一约束还原为 (report_date, strategy_id)。
手工回滚 SQL（PG，事务内执行）见本文件末尾注释。

注意：本迁移只处理 2026-11-02 试运行之前的历史数据；11-02 起由每日任务
（新写入方按市场写入）自然保持按市场独立，不需要重复拆分。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "v2_selection_report"
_PICK_TABLE = "v2_selection_pick"
_OLD_UNIQUE = "uq_v2_selection_report"
_NO_MULTI_CHECK = "ck_v2_selection_report_no_multi"
_NEW_COLUMNS = ["report_date", "strategy_id", "market"]
_OLD_COLUMNS = ["report_date", "strategy_id"]
_KNOWN_MARKETS = ("CN", "HK", "US", "CRYPTO")
_STRATEGY_MARKETS = (
    ("v1-us", "US"),
    ("v1-hk", "HK"),
    ("v1-crypto", "CRYPTO"),
    ("v1-cn", "CN"),
)


# --------------------------------------------------------------------------- #
# market 判定（纯函数，便于单测；migration 不能依赖运行期代码，故就地复制）
# --------------------------------------------------------------------------- #
def _market_from_code(code: str) -> str | None:
    c = (code or "").strip().lower()
    if not c:
        return None
    if c.startswith("rt_hk") or c.startswith("hk") or c.endswith((".hk", ".hkg")):
        return "HK"
    if c.startswith("gb_") or c.endswith((".us", ".nasdaq", ".nyse")):
        return "US"
    if c.startswith(("sh", "sz", "bj")) or c.endswith((".sh", ".sz", ".bj")):
        return "CN"
    if "/" in c and c.rsplit("/", 1)[-1] in ("usdt", "usdc", "btc", "eth"):
        return "CRYPTO"
    return None


def _market_from_strategy(strategy_id: str) -> str | None:
    s = (strategy_id or "").strip().lower()
    for prefix, market in _STRATEGY_MARKETS:
        if s.startswith(prefix):
            return market
    return None


def normalize_market(value: str | None, code: str = "", strategy_id: str = "") -> str:
    """把任意 market 写法归一为 CN/HK/US/CRYPTO；MULTI/空值按 code、策略前缀回退。"""
    m = (value or "").strip().upper()
    if m in _KNOWN_MARKETS:
        return m
    return (_market_from_code(code) or _market_from_strategy(strategy_id)
            or _market_from_code(strategy_id) or "CN")


# --------------------------------------------------------------------------- #
# DDL 辅助：PostgreSQL 直接 ALTER；SQLite（migration 单测）走 batch recreate
# --------------------------------------------------------------------------- #
def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _drop_old_unique() -> None:
    if _is_sqlite():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_constraint(_OLD_UNIQUE, type_="unique")
    else:
        op.drop_constraint(_OLD_UNIQUE, _TABLE, type_="unique")


def _create_new_unique() -> None:
    if _is_sqlite():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.create_unique_constraint(_OLD_UNIQUE, _NEW_COLUMNS)
    else:
        op.create_unique_constraint(_OLD_UNIQUE, _TABLE, _NEW_COLUMNS)


def _drop_new_unique() -> None:
    if _is_sqlite():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_constraint(_OLD_UNIQUE, type_="unique")
    else:
        op.drop_constraint(_OLD_UNIQUE, _TABLE, type_="unique")


def _create_old_unique() -> None:
    if _is_sqlite():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.create_unique_constraint(_OLD_UNIQUE, _OLD_COLUMNS)
    else:
        op.create_unique_constraint(_OLD_UNIQUE, _TABLE, _OLD_COLUMNS)


def _add_no_multi_check() -> None:
    if _is_sqlite():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.create_check_constraint(_NO_MULTI_CHECK, "market <> 'MULTI'")
    else:
        op.create_check_constraint(_NO_MULTI_CHECK, _TABLE, "market <> 'MULTI'")


def _drop_no_multi_check() -> None:
    if _is_sqlite():
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_constraint(_NO_MULTI_CHECK, type_="check")
    else:
        op.drop_constraint(_NO_MULTI_CHECK, _TABLE, type_="check")


# --------------------------------------------------------------------------- #
# 数据拆分 / 合并
# --------------------------------------------------------------------------- #
def _split_multi_reports() -> None:
    """把每条跨市场日报拆成按市场独立的多条；单市场日报顺带纠正 market。"""
    bind = op.get_bind()
    reports = bind.execute(sa.text(
        "SELECT id, report_date, strategy_id, analyst, market_view, "
        "yesterday_review, lessons, status, submitted_at, market "
        "FROM v2_selection_report ORDER BY id"
    )).mappings().all()

    for report in reports:
        rid = report["id"]
        sid = report["strategy_id"]
        picks = bind.execute(sa.text(
            "SELECT id, code, market FROM v2_selection_pick "
            "WHERE report_id=:rid ORDER BY id"
        ), {"rid": rid}).mappings().all()

        if not picks:
            target = normalize_market(report["market"], strategy_id=sid)
            if (report["market"] or "") != target:
                bind.execute(
                    sa.text("UPDATE v2_selection_report SET market=:m WHERE id=:i"),
                    {"m": target, "i": rid},
                )
            continue

        groups: dict[str, list[int]] = {}
        for pick in picks:
            market = normalize_market(pick["market"], code=pick["code"], strategy_id=sid)
            groups.setdefault(market, []).append(pick["id"])
            if (pick["market"] or "") != market:
                bind.execute(
                    sa.text("UPDATE v2_selection_pick SET market=:m WHERE id=:i"),
                    {"m": market, "i": pick["id"]},
                )

        if len(groups) == 1:
            target = next(iter(groups))
            if (report["market"] or "") != target:
                bind.execute(
                    sa.text("UPDATE v2_selection_report SET market=:m WHERE id=:i"),
                    {"m": target, "i": rid},
                )
            continue

        existing = normalize_market(report["market"], strategy_id=sid)
        keeper = existing if existing in groups else sorted(groups)[0]
        bind.execute(
            sa.text("UPDATE v2_selection_report SET market=:m WHERE id=:i"),
            {"m": keeper, "i": rid},
        )
        for market in sorted(groups):
            if market == keeper:
                continue
            bind.execute(sa.text(
                "INSERT INTO v2_selection_report "
                "  (report_date, strategy_id, analyst, market_view, yesterday_review, "
                "   lessons, status, submitted_at, market) "
                "VALUES (:d, :s, :a, :mv, :yr, :ls, :st, :sat, :m)"
            ), {
                "d": report["report_date"], "s": sid, "a": report["analyst"],
                "mv": report["market_view"], "yr": report["yesterday_review"],
                "ls": report["lessons"], "st": report["status"],
                "sat": report["submitted_at"], "m": market,
            })
            new_id = bind.execute(sa.text(
                "SELECT id FROM v2_selection_report "
                "WHERE report_date=:d AND strategy_id=:s AND market=:m"
            ), {"d": report["report_date"], "s": sid, "m": market}).scalar()
            # 只把该市场的 pick 挂到新日报；其余市场留在 keeper 行。
            ids = groups[market]
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                bind.execute(sa.text(
                    "UPDATE v2_selection_pick SET report_id=:new WHERE id IN :ids"
                ).bindparams(sa.bindparam("ids", expanding=True)),
                    {"new": new_id, "ids": chunk})


def _merge_reports_back_to_multi() -> None:
    """downgrade：同 (report_date, strategy_id) 的多市场日报合并回一条 MULTI。"""
    bind = op.get_bind()
    groups = bind.execute(sa.text(
        "SELECT report_date, strategy_id, min(id) AS keeper_id, count(*) AS n "
        "FROM v2_selection_report GROUP BY report_date, strategy_id HAVING count(*) > 1"
    )).mappings().all()
    for group in groups:
        keeper = group["keeper_id"]
        rows = bind.execute(sa.text(
            "SELECT id FROM v2_selection_report "
            "WHERE report_date=:d AND strategy_id=:s AND id<>:k"
        ), {"d": group["report_date"], "s": group["strategy_id"], "k": keeper}).scalars().all()
        for other in rows:
            # 保留行已存在的 code 不重复挂（v2_selection_pick 唯一键 report_id+code）。
            bind.execute(sa.text(
                "UPDATE v2_selection_pick SET report_id=:k WHERE report_id=:o "
                "AND code NOT IN (SELECT code FROM v2_selection_pick WHERE report_id=:k)"
            ), {"k": keeper, "o": other})
            bind.execute(
                sa.text("DELETE FROM v2_selection_pick WHERE report_id=:o"), {"o": other}
            )
        bind.execute(
            sa.text("DELETE FROM v2_selection_report "
                    "WHERE report_date=:d AND strategy_id=:s AND id<>:k"),
            {"d": group["report_date"], "s": group["strategy_id"], "k": keeper},
        )
        bind.execute(
            sa.text("UPDATE v2_selection_report SET market='MULTI' WHERE id=:k"),
            {"k": keeper},
        )


# --------------------------------------------------------------------------- #
# revisions
# --------------------------------------------------------------------------- #
def upgrade() -> None:
    # 1) 先放开旧唯一键（否则无法插入同 (report_date, strategy_id) 的其他市场行）。
    _drop_old_unique()
    # 2) 立即建立新唯一键（拆分期间也保证 (date, strategy, market) 唯一）。
    _create_new_unique()
    # 3) 存量的 MULTI / 跨市场日报按 pick.market 拆分。
    _split_multi_reports()
    # 4) 库层禁止再写 MULTI。
    _add_no_multi_check()


def downgrade() -> None:
    _drop_no_multi_check()
    _merge_reports_back_to_multi()
    _drop_new_unique()
    _create_old_unique()


# --------------------------------------------------------------------------- #
# 手工回滚 SQL（PostgreSQL，事务内执行；等价于 downgrade()，供 DBA 断链回滚）
# --------------------------------------------------------------------------- #
ROLLBACK_SQL = """
BEGIN;
ALTER TABLE v2_selection_report
  DROP CONSTRAINT IF EXISTS ck_v2_selection_report_no_multi;

-- 1) 记下需要合并的 (report_date, strategy_id)：保留最小 id 行
CREATE TEMP TABLE _p2_2_dup ON COMMIT DROP AS
  SELECT report_date, strategy_id, min(id) AS keeper_id
  FROM v2_selection_report
  GROUP BY report_date, strategy_id HAVING count(*) > 1;

-- 2) 先删掉与保留行同 code 的重复 pick（v2_selection_pick 唯一键 report_id+code）
DELETE FROM v2_selection_pick p
USING _p2_2_dup d, v2_selection_report r
WHERE p.report_id = r.id
  AND r.report_date = d.report_date AND r.strategy_id = d.strategy_id
  AND r.id <> d.keeper_id
  AND p.code IN (SELECT p2.code FROM v2_selection_pick p2
                 WHERE p2.report_id = d.keeper_id);

-- 3) 其余 pick 重新挂到保留行
UPDATE v2_selection_pick p SET report_id = d.keeper_id
FROM _p2_2_dup d, v2_selection_report r
WHERE p.report_id = r.id
  AND r.report_date = d.report_date AND r.strategy_id = d.strategy_id
  AND r.id <> d.keeper_id;

-- 4) 删掉多余日报，保留行标 MULTI
DELETE FROM v2_selection_report r USING _p2_2_dup d
WHERE r.report_date = d.report_date AND r.strategy_id = d.strategy_id
  AND r.id <> d.keeper_id;

UPDATE v2_selection_report r SET market = 'MULTI'
FROM _p2_2_dup d WHERE r.id = d.keeper_id;

-- 5) 唯一键还原
ALTER TABLE v2_selection_report DROP CONSTRAINT uq_v2_selection_report;
ALTER TABLE v2_selection_report
  ADD CONSTRAINT uq_v2_selection_report UNIQUE (report_date, strategy_id);
COMMIT;
"""
