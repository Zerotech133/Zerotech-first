# -*- coding: utf-8 -*-
"""WO-QT-20261020-020 验收测试：后验收益自动回填接线。

验收口径（工单 + 契约）：
  1. 配对规则**以快照为锚**：快照.orders_json[i].ref → orders.ref 精确配对；
     配不上即显式 unlinked，**禁按日期就近配对**（前视污染）；
  2. 单位口径：unit 必填（缺 → 抛错，-016/-018 同口径）；三种口径同一算式；
  3. 方向：BUY 与 SELL 代入**同一算式**，SELL 收益符号正确（卖出价<成本→负）；
  4. 不造数：无结算 → 显式 pending（**不填 0**）；部分可配 → 部分回填 + 剩余
     明示；快照内部分腿不可算 → 整体不出数（不混算）；
  5. 歧义：一条快照对多条候选落单 → `ambiguous` + 候选全列出 + **不写账**，
     **禁静默择一**；
  6. 只读与幂等：快照库/订单库 mode=ro 零写入（无 -wal/-journal 生成）；回填账
     append-only，幂等键 snapshot_id|kind，重复回填**零新增且不改既有行**；
  7. 前视防护：喂入 asof 早于成交日 → 抛错；
  8. 消费闭环：产出**原样**喂进 -013 / -016 / -018 现有接口（不新增第二套口径）。

未接线路径照契约：真实结算后验的**估值价来源**属调用方喂入（本单只给口径与
接线，行情湖取价未接线）；跨库/跨进程与批量调度属后续工单。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "paper"))
sys.path.insert(0, str(HERE / "tests"))

import baseline as bl                                       # noqa: E402
import combo_factory                                       # noqa: E402
import combo_registry                                      # noqa: E402
import combo_review                                        # noqa: E402
import decision_snapshot                                    # noqa: E402
import posterior_link as pl                                # noqa: E402
import topic_miner                                         # noqa: E402

COMBO = "combo_base_a"
BR = f"{COMBO}@follow_research"               # -008/-011 隔离账口径 combo@branch
STOCK = "000002.SZ"
OTHER = "600519.SH"


# ---------- fixture：快照库 / 订单库 / 回填账（三账分离） ----------

_ORDERS_DDL = """
CREATE TABLE orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, source TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '',
  stock TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL, plan_date TEXT,
  price_base TEXT NOT NULL, manual_price REAL, status TEXT NOT NULL,
  fill_date TEXT, filled_price REAL, filled_qty INTEGER, fee REAL, reason TEXT,
  conf REAL, strategy TEXT NOT NULL DEFAULT 'manual',
  account_id TEXT NOT NULL DEFAULT 'main', order_kind TEXT NOT NULL DEFAULT 'market',
  trace_id TEXT, fact_hash TEXT, decision_id TEXT, idempotency_key TEXT,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_orders_ref ON orders(ref) WHERE ref != '';
"""


@pytest.fixture()
def env(tmp_path):
    """隔离环境：全部 tmp 路径（零污染仓内 data/paper.db）。"""
    e = {"tmp_path": tmp_path, "snap_db": tmp_path / "snap.db",
         "orders_db": tmp_path / "orders.db", "ledger_db": tmp_path / "posterior.db",
         "seq": 0}
    con = sqlite3.connect(e["orders_db"])
    con.executescript(_ORDERS_DDL)
    con.commit()
    con.close()
    return e


def _ref(run_name, stock, account_id="main"):
    """engine 幂等键口径（engine._ref_of：子账本带 @acct 段）。"""
    return f"{run_name}#{stock}" if account_id == "main" else \
        f"{run_name}@{account_id}#{stock}"


def _snapshot(env, *, plans, run_name=None, account_id=BR, combo_id=COMBO,
              strategy="qingping", context=None):
    """用 -004 正规入口 capture 一份快照（走其校验口径，不手搓表）。"""
    env["seq"] += 1
    run_name = run_name or f"run_{env['seq']}"
    run = env["tmp_path"] / f"{run_name}.json"
    run.write_text(json.dumps([{"stock": p.get("stock")} for p in plans],
                              ensure_ascii=False), encoding="utf-8")
    con = sqlite3.connect(env["snap_db"])
    try:
        decision_snapshot.ensure_table(con)
        snap = decision_snapshot.capture(
            con, run_path=run, run_name=run_name, out={"orders": plans},
            account_id=account_id, strategy=strategy,
            combo={"combo_id": combo_id},
            context=context if context is not None else
            {"signal": {"signal_id": f"sig-{run_name}", "stock": STOCK}})
        con.commit()
    finally:
        con.close()
    return snap


def _order(env, *, ref, stock=STOCK, side="BUY", qty=1000, status="filled",
           fill_date="2026-10-21", filled_price=10.0, fee=5.0,
           account_id=BR, strategy="qingping", reason=None):
    """直接写一条 orders 行（只经 sqlite 连接，不经 engine 写路径——被测模块只读它）。"""
    con = sqlite3.connect(env["orders_db"])
    try:
        cur = con.execute(
            "INSERT INTO orders(ts,source,ref,stock,side,qty,plan_date,price_base,"
            "status,fill_date,filled_price,filled_qty,fee,reason,strategy,account_id,"
            "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("2026-10-20T09:00:00", "ai", ref, stock, side, qty, fill_date, "open",
             status, fill_date if status == "filled" else None,
             filled_price if status == "filled" else None,
             qty if status == "filled" else None,
             fee if status == "filled" else None, reason, strategy, account_id,
             "2026-10-20T09:00:00"))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _drop_index(env):
    """去掉 ref 唯一索引，构造「一条 ref 对多条落单」的歧义现场。"""
    con = sqlite3.connect(env["orders_db"])
    con.execute("DROP INDEX ux_orders_ref")
    con.commit()
    con.close()


# ---------- 1. BUY 收益配对 ----------

def test_buy_pct_pairing(env):
    """BUY 腿：entry=成交价 10.0，exit=显式估值价 11.0 → +10%（不猜价、不按日期配）。"""
    snap = _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                                  "ref": _ref("run_buy", STOCK, BR)}],
                     run_name="run_buy")
    _order(env, ref=_ref("run_buy", STOCK, BR), fill_date="2026-10-21",
           filled_price=10.0, qty=1000)

    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT,
                           marks={STOCK: {"price": 11.0, "asof": "2026-10-22"}})
    assert r["summary"]["linked"] == 1
    assert r["summary"]["unlinked"] == 0 and r["summary"]["pending"] == 0
    got = r["posterior"]["snapshots"][snap["snapshot_id"]]
    assert got["value"] == pytest.approx(0.1)          # (11-10)/10
    assert got["unit"] == "pct"
    assert "run_buy" in got["source"]
    leg = r["linked"][0]["legs"][0]
    assert leg["entry"] == 10.0 and leg["exit"] == 11.0
    assert leg["entry_source"].startswith("settle_fill:")
    assert leg["exit_source"] == "explicit_mark@2026-10-22"


def test_unit_amount_and_price_diff_same_formula(env):
    """同一现场换口径：price_diff=+1.0；CNY=(1.0)×1000 股=1000.0。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                           "ref": _ref("run_u", STOCK, BR)}], run_name="run_u")
    _order(env, ref=_ref("run_u", STOCK, BR), filled_price=10.0, qty=1000)
    kw = dict(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
              marks={STOCK: 11.0})
    assert pl.link_posteriors(unit=pl.RET_PRICE_DIFF, **kw)["posterior"]["snapshots"]
    d = list(pl.link_posteriors(unit=pl.RET_PRICE_DIFF, **kw)
             ["posterior"]["snapshots"].values())[0]
    a = list(pl.link_posteriors(unit=pl.RET_AMOUNT, **kw)
             ["posterior"]["snapshots"].values())[0]
    assert d["value"] == pytest.approx(1.0)
    assert a["value"] == pytest.approx(1000.0)
    assert d["unit"] == "price_diff" and a["unit"] == "CNY"


# ---------- 2. SELL 方向收益符号正确（同一算式，不只算 BUY） ----------

def test_sell_sign_correct_with_explicit_cost_basis(env):
    """SELL 腿：entry=成本基准 10.0，exit=成交价 9.0 → -10%（亏钱即负）。"""
    snap = _snapshot(env, plans=[{"stock": STOCK, "side": "SELL", "qty": 1000,
                                  "ref": _ref("run_sell", STOCK, BR)}],
                     run_name="run_sell")
    _order(env, ref=_ref("run_sell", STOCK, BR), side="SELL", qty=1000,
           fill_date="2026-10-25", filled_price=9.0)

    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, cost_basis={STOCK: 10.0})
    v = r["posterior"]["snapshots"][snap["snapshot_id"]]["value"]
    assert v == pytest.approx(-0.1)          # (9-10)/10，符号天然正确
    leg = r["linked"][0]["legs"][0]
    assert leg["entry"] == 10.0 and leg["exit"] == 9.0   # entry=基准/exit=成交


def test_sell_profit_sign_positive(env):
    """SELL 盈利方向：成本 9.0、卖出 10.0 → +11.11%（SELL 不因方向被压成负）。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "SELL", "qty": 500,
                           "ref": _ref("run_s2", STOCK, BR)}], run_name="run_s2")
    _order(env, ref=_ref("run_s2", STOCK, BR), side="SELL", qty=500,
           fill_date="2026-10-25", filled_price=10.0)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, cost_basis={STOCK: 9.0})
    v = list(r["posterior"]["snapshots"].values())[0]["value"]
    assert v == pytest.approx(1.0 / 9.0)


def test_sell_cost_basis_from_unique_earlier_buy(env):
    """未喂 cost_basis：从 orders 取**唯一一笔**严格更早的 BUY 成交当基准。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "SELL", "qty": 1000,
                           "ref": _ref("run_s3", STOCK, BR)}], run_name="run_s3")
    _order(env, ref="bridge_buy#1", side="BUY", qty=1000,
           fill_date="2026-10-21", filled_price=8.0)
    _order(env, ref=_ref("run_s3", STOCK, BR), side="SELL", qty=1000,
           fill_date="2026-10-25", filled_price=9.0)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT)
    v = list(r["posterior"]["snapshots"].values())[0]["value"]
    assert v == pytest.approx(0.125)         # (9-8)/8
    assert r["linked"][0]["legs"][0]["entry_source"].startswith("settle_fill:")


# ---------- 3. 无结算 → 显式 pending（绝不填 0） ----------

def test_pending_order_no_settlement(env):
    """落单仍 pending（未成交）→ pending_settle，绝不产出 0。"""
    snap = _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                                  "ref": _ref("run_p", STOCK, BR)}], run_name="run_p")
    _order(env, ref=_ref("run_p", STOCK, BR), status="pending",
           fill_date="2026-10-21", filled_price=None, fee=None)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 11.0})
    assert r["summary"]["pending"] == 1 and r["summary"]["linked"] == 0
    assert snap["snapshot_id"] not in r["posterior"]["snapshots"]
    item = r["pending"][0]
    assert item["legs"][0]["reason_code"] == "not_settled"
    assert "不填 0" in item["legs"][0]["reason"]


def test_pending_buy_without_mark_price(env):
    """BUY 已成交但未喂估值价（未平仓/未估值）→ pending，**不猜价**。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                           "ref": _ref("run_pm", STOCK, BR)}], run_name="run_pm")
    _order(env, ref=_ref("run_pm", STOCK, BR), filled_price=10.0, qty=1000)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT)          # 不喂 marks
    assert r["summary"]["pending"] == 1 and r["summary"]["linked"] == 0
    assert r["pending"][0]["legs"][0]["reason_code"] == "mark_price_absent"


def test_pending_sell_without_cost_basis(env):
    """SELL 已成交但无任何成本基准来源 → pending，不猜基准价。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "SELL", "qty": 1000,
                           "ref": _ref("run_ps", STOCK, BR)}], run_name="run_ps")
    _order(env, ref=_ref("run_ps", STOCK, BR), side="SELL", qty=1000,
           fill_date="2026-10-25", filled_price=9.0)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT)
    assert r["summary"]["pending"] == 1
    assert r["pending"][0]["legs"][0]["reason_code"] == "cost_basis_absent"


# ---------- 4. 部分可配 → 部分回填 + 剩余明示 ----------

def test_partial_batch_backfills_linked_and_lists_rest(env):
    """批次内 3 张快照：2 可配 1 不可配 → 前两张照常回填，第三张显式 pending。"""
    s1 = _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                                "ref": _ref("r1", STOCK, BR)}], run_name="r1")
    s2 = _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                                "ref": _ref("r2", STOCK, BR)}], run_name="r2")
    s3 = _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                                "ref": _ref("r3", STOCK, BR)}], run_name="r3")
    _order(env, ref=_ref("r1", STOCK, BR), filled_price=10.0)
    _order(env, ref=_ref("r2", STOCK, BR), filled_price=10.0)
    _order(env, ref=_ref("r3", STOCK, BR), status="pending", filled_price=None)

    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 12.0})
    assert r["summary"]["linked"] == 2 and r["summary"]["pending"] == 1
    sn = r["posterior"]["snapshots"]
    assert sn[s1["snapshot_id"]]["value"] == pytest.approx(0.2)
    assert sn[s2["snapshot_id"]]["value"] == pytest.approx(0.2)
    assert s3["snapshot_id"] not in sn                  # 剩余显式 pending，不填 0
    assert r["pending"][0]["snapshot_id"] == s3["snapshot_id"]


def test_partial_legs_snapshot_not_silently_summed(env):
    """快照内一腿可算一腿 pending → 该快照**整体不出数**（部分收益≠整体收益）。"""
    snap = _snapshot(env, plans=[
        {"stock": STOCK, "side": "BUY", "qty": 1000, "ref": _ref("m1", STOCK, BR)},
        {"stock": OTHER, "side": "BUY", "qty": 1000, "ref": _ref("m1", OTHER, BR)}],
        run_name="m1")
    _order(env, ref=_ref("m1", STOCK, BR), stock=STOCK, filled_price=10.0)
    _order(env, ref=_ref("m1", OTHER, BR), stock=OTHER, status="pending",
           filled_price=None)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 12.0, OTHER: 99.0})
    assert snap["snapshot_id"] not in r["posterior"]["snapshots"]
    assert r["summary"]["pending"] == 1
    assert r["pending"][0]["reason_code"] == "legs_partial"
    assert r["pending"][0]["legs_linked"] == 1 and r["pending"][0]["legs_pending"] == 1


# ---------- 5. 配对歧义 → 显式候选 + 禁静默择一 ----------

def test_ambiguous_ref_multi_hit_lists_candidates(env):
    """一条快照对多条落单（ref 唯一索引缺失）→ ambiguous + 候选全列 + 不写账。"""
    snap = _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                                  "ref": _ref("amb", STOCK, BR)}], run_name="amb")
    _drop_index(env)
    _order(env, ref=_ref("amb", STOCK, BR), filled_price=10.0)
    _order(env, ref=_ref("amb", STOCK, BR), filled_price=99.0)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 12.0},
                           write_ledger=True, ledger_db=env["ledger_db"])
    assert r["summary"]["ambiguous"] == 1 and r["summary"]["linked"] == 0
    assert snap["snapshot_id"] not in r["posterior"]["snapshots"]
    leg = r["ambiguous"][0]["legs"][0]
    assert leg["reason_code"] == "ref_multi_hit" and len(leg["candidates"]) == 2
    assert {c["filled_price"] for c in leg["candidates"]} == {10.0, 99.0}
    assert "禁静默择一" in leg["reason"]
    assert r["ledger_write"]["rows_inserted"] == 0      # 歧义不写账


def test_ambiguous_cost_basis_multi_candidate(env):
    """SELL 成本基准有多笔更早 BUY 候选 → ambiguous（即使按 FIFO 也不静默择一）。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "SELL", "qty": 1000,
                           "ref": _ref("amb2", STOCK, BR)}], run_name="amb2")
    _order(env, ref="b1", side="BUY", qty=500, fill_date="2026-10-18",
           filled_price=8.0)
    _order(env, ref="b2", side="BUY", qty=500, fill_date="2026-10-19",
           filled_price=7.0)
    _order(env, ref=_ref("amb2", STOCK, BR), side="SELL", qty=1000,
           fill_date="2026-10-25", filled_price=9.0)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT)
    assert r["summary"]["ambiguous"] == 1
    leg = r["ambiguous"][0]["legs"][0]
    assert leg["reason_code"] == "cost_basis_multi_candidate"
    assert len(leg["candidates"]) == 2
    # 显式喂 cost_basis 可消歧（唯一出口，不靠猜）
    r2 = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                            unit=pl.RET_PCT, cost_basis={STOCK: 8.0})
    assert r2["summary"]["linked"] == 1


# ---------- 6. 缺 unit 抛错 / 前视防护 ----------

def test_missing_unit_raises(env):
    """unit 缺省 → 抛错（不声明单位即不造数，-016/-018 同口径）。"""
    with pytest.raises(pl.PosteriorLinkError) as ei:
        pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"])
    assert ei.value.reason_code == "unit_missing"
    with pytest.raises(pl.PosteriorLinkError) as ei2:
        pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit="   ")
    assert ei2.value.reason_code == "unit_missing"
    with pytest.raises(pl.PosteriorLinkError) as ei3:
        pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit="美元")
    assert ei3.value.reason_code == "unit_unknown"      # 不自动猜/不自动换算


def test_lookahead_mark_price_raises(env):
    """喂入估值价 asof 早于成交日 → 抛错（前视价格污染）。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                           "ref": _ref("la", STOCK, BR)}], run_name="la")
    _order(env, ref=_ref("la", STOCK, BR), fill_date="2026-10-21",
           filled_price=10.0)
    with pytest.raises(pl.PosteriorLinkError) as ei:
        pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT,
                           marks={STOCK: {"price": 9.0, "asof": "2026-10-19"}})
    assert ei.value.reason_code == "lookahead_price"


def test_closed_on_failure_missing_db(tmp_path):
    """快照库/订单库缺失或无表 → 显式抛错（closed on failure，不静默返回空）。"""
    with pytest.raises(pl.PosteriorLinkError) as ei:
        pl.link_posteriors(snapshot_db=tmp_path / "nope.db",
                           orders_db=tmp_path / "nope2.db", unit=pl.RET_PCT)
    assert ei.value.reason_code == "db_missing"


# ---------- 7. 配对不按日期就近（前视污染防护） ----------

def test_no_date_nearest_pairing(env):
    """构造两个候选落单：一个 ref 真身、另一个只有「日期上更近」→ 只能配真身。"""
    snap = _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                                  "ref": _ref("nd", STOCK, BR)}], run_name="nd")
    # 日期上更近、成交更「赚」的另一张票的落单（无 ref 关联）
    _order(env, ref="other_run#600519.SH", stock=OTHER, qty=5000,
           fill_date="2026-10-22", filled_price=5.0)
    _order(env, ref=_ref("nd", STOCK, BR), fill_date="2026-10-21",
           filled_price=10.0, qty=1000)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 11.0})
    assert r["summary"]["linked"] == 1 and r["summary"]["unlinked"] == 0
    leg = r["linked"][0]["legs"][0]
    assert leg["order_id"] is not None and leg["ref"] == _ref("nd", STOCK, BR)
    assert leg["entry"] == 10.0               # 只认同 ref 的那笔，别的票不掺和
    assert r["posterior"]["snapshots"][snap["snapshot_id"]]["value"] == pytest.approx(0.1)


def test_unlinked_when_ref_absent_even_dates_match(env):
    """orders 里有同票同期成交但 ref 对不上 → 显式 unlinked（**绝不**就近配对）。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                           "ref": _ref("miss", STOCK, BR)}], run_name="miss")
    _order(env, ref="unrelated#1", stock=STOCK, qty=1000,
           fill_date="2026-10-21", filled_price=10.0)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 11.0})
    assert r["summary"]["linked"] == 0 and r["summary"]["unlinked"] == 1
    assert r["unlinked"][0]["legs"][0]["reason_code"] == "ref_not_found"
    assert "不按日期就近配对" in r["unlinked"][0]["legs"][0]["reason"]


def test_account_mismatch_not_paired(env):
    """ref 命中但 account_id 与快照不符 → 串账拒配（子账本隔离）。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                           "ref": _ref("x", STOCK, BR)}], run_name="x")
    _order(env, ref=_ref("x", STOCK, BR), account_id="main", filled_price=10.0)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 11.0})
    assert r["summary"]["unlinked"] == 1
    assert r["unlinked"][0]["legs"][0]["reason_code"] == "account_mismatch"


# ---------- 8. 只读零写入 ----------

def test_readonly_zero_write(env):
    """快照库/订单库只读：跑完无 -wal/-shm/-journal 新增，快照表行数零变化。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                           "ref": _ref("ro", STOCK, BR)}], run_name="ro")
    _order(env, ref=_ref("ro", STOCK, BR), filled_price=10.0)

    def snap_fingerprint(p: Path):
        return (p.stat().st_size, p.read_bytes())

    before = {p: snap_fingerprint(p) for p in (env["snap_db"], env["orders_db"])}
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 11.0},
                           write_ledger=True, ledger_db=env["ledger_db"])
    assert r["summary"]["linked"] == 1
    for p, fp in before.items():                       # 字节级零变化
        assert snap_fingerprint(p) == fp
    # 未生成 WAL/回滚日志文件（只读连接绝不开写事务）
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(str(env["snap_db"]) + suffix).exists()
        assert not Path(str(env["orders_db"]) + suffix).exists()


# ---------- 9. 幂等：重复回填零新增、不改既有行 ----------

def _ledger_rows(path: Path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM posterior_ledger ORDER BY id").fetchall()]
    finally:
        con.close()


def test_ledger_idempotent_no_duplicate_no_overwrite(env):
    """重复回填：行数零新增，且既有行（值/单位/时间戳）一字不改。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                           "ref": _ref("id", STOCK, BR)}], run_name="id")
    _order(env, ref=_ref("id", STOCK, BR), filled_price=10.0)
    kw = dict(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
              unit=pl.RET_PCT, marks={STOCK: 11.0},
              write_ledger=True, ledger_db=env["ledger_db"])
    r1 = pl.link_posteriors(**kw)
    assert r1["ledger_write"]["rows_inserted"] == 1
    rows1 = _ledger_rows(env["ledger_db"])
    assert len(rows1) == 1 and rows1[0]["unit"] == "pct"

    r2 = pl.link_posteriors(**kw)
    assert r2["ledger_write"]["rows_inserted"] == 0     # 幂等：零新增
    rows2 = _ledger_rows(env["ledger_db"])
    assert rows2 == rows1                               # 既有行未被改写

    # 同 snapshot 换 kind → 新行（幂等键第二段生效），换 unit 不覆盖旧行
    r3 = pl.link_posteriors(**{**kw, "kind": "return_v2"})
    assert r3["ledger_write"]["rows_inserted"] == 1
    assert len(_ledger_rows(env["ledger_db"])) == 2


def test_ledger_not_written_by_default(env):
    """默认 write_ledger=False = 纯预演，不建账不写行。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                           "ref": _ref("dry", STOCK, BR)}], run_name="dry")
    _order(env, ref=_ref("dry", STOCK, BR), filled_price=10.0)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 11.0})
    assert r["linked"] and r["ledger_write"] is None
    assert not env["ledger_db"].exists()


def test_write_ledger_requires_ledger_db(env):
    _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                           "ref": _ref("n1", STOCK, BR)}], run_name="n1")
    _order(env, ref=_ref("n1", STOCK, BR), filled_price=10.0)
    with pytest.raises(pl.PosteriorLinkError) as ei:
        pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 11.0}, write_ledger=True)
    assert ei.value.reason_code == "ledger_db_missing"


# ---------- 10. 三处消费闭环（-013 / -016 / -018，原样喂） ----------

def _populate(env, n=3, price=(10.0, 12.0), combo_id=COMBO, account_id=BR):
    """铺 n 张可配快照（BUY 腿，全部成交），供三处消费闭环测试。"""
    ids = []
    for i in range(n):
        run = f"loop_{i}"
        snap = _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                                      "ref": _ref(run, STOCK, account_id)}],
                         run_name=run, account_id=account_id, combo_id=combo_id)
        _order(env, ref=_ref(run, STOCK, account_id), filled_price=price[0], qty=1000,
               account_id=account_id)
        ids.append(snap["snapshot_id"])
    return ids


def test_consumer_closure_016_topic_miner(env):
    """产出原样喂 -016 mine_topics：≥2 可用样本即成对出议题（此前永远无样本可提）。"""
    _populate(env, n=3)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 12.0})
    out = topic_miner.mine_topics(snapshot_db=env["snap_db"],
                                  posterior=pl.to_topic_posterior(r),
                                  persist=False)
    assert out["suppressed"]["posterior_pending"] == []
    pairs = [t for t in out["topics"]
             if t["source_type"] == topic_miner.SRC_MOST_PROFITABLE]
    assert pairs, "有 3 个已回填样本仍无议题 → 闭环未生效"
    assert pairs[0]["evidence"]["posterior"]["unit"] == "pct"
    assert pairs[0]["evidence"]["posterior"]["value"] == pytest.approx(0.2)
    assert pairs[0]["meeting_initiated"] is False


def test_consumer_closure_018_baseline(env):
    """产出原样喂 -018 build_baseline：样本从 pending_backfill → available（n 计）。"""
    ids = _populate(env, n=2)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 12.0})
    b = bl.build_baseline(BR, dimension=bl.DIM_BRANCH, snapshot_db=env["snap_db"],
                          posterior=pl.to_topic_posterior(r))
    assert b["samples_pending_backfill"] == 0
    assert {s["snapshot_id"] for s in b["sample_returns"]} == set(ids)
    assert b["metrics"]["n"] == 2
    assert b["status"] == bl.STATUS_INSUFFICIENT   # n=2 < 30：门槛不足但**已非**待回填
    assert b["metrics"]["hit_rate"] is None and b["metrics"]["avg_return"] is None


def test_consumer_closure_013_combo_review(env, tmp_path):
    """产出经 to_revenue_payload → -013 build_review：ROI 不再永久 pending_backfill。"""
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(combo_registry.load_registry(), ensure_ascii=False),
                   encoding="utf-8")
    subs = tmp_path / "subs.jsonl"
    combo_factory.create_combo(_combo_registration(tmp_path), registry_path=reg,
                               subscription_log=subs)
    for i, px in enumerate((10.0, 12.0, 14.0)):
        run = f"roi_{i}"
        _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                               "ref": _ref(run, STOCK, BR)}], run_name=run)
        _order(env, ref=_ref(run, STOCK, BR), filled_price=10.0, qty=1000)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_AMOUNT, marks={STOCK: 11.0})
    payload = pl.to_revenue_payload(r, combo_id=COMBO)
    assert payload["unit"] == "CNY"
    assert payload["branches"][BR] == pytest.approx(3000.0)     # 3 腿 ×(11-10)×1000

    rev = combo_review.build_review(COMBO, registry_path=reg,
                                    ledger=tmp_path / "budget.jsonl",
                                    receipts=tmp_path / "receipts.jsonl",
                                    posterior=payload)
    assert rev["roi"]["status"] == combo_review.ROI_COMPUTED
    assert rev["roi"]["revenue"] == pytest.approx(3000.0)
    assert rev["roi"]["value"] is not None          # 收益 − 算力成本，不再 pending


def test_to_revenue_payload_rejects_non_amount_unit(env):
    """pct 口径不可直接喂 -013（与 CNY 成本不可相减）→ 显式抛错，不静默混算。"""
    _snapshot(env, plans=[{"stock": STOCK, "side": "BUY", "qty": 1000,
                           "ref": _ref("u1", STOCK, BR)}], run_name="u1")
    _order(env, ref=_ref("u1", STOCK, BR), filled_price=10.0)
    r = pl.link_posteriors(snapshot_db=env["snap_db"], orders_db=env["orders_db"],
                           unit=pl.RET_PCT, marks={STOCK: 11.0})
    with pytest.raises(pl.PosteriorLinkError) as ei:
        pl.to_revenue_payload(r, unit=pl.RET_PCT)
    assert ei.value.reason_code == "unit_mismatch"


def _combo_registration(tmp_path, combo_id=COMBO, budget=100.0):
    reg = {
        "factory_schema_version": 1, "combo_id": combo_id,
        "name": "后验回填接线测试组合",
        "quad": {"pick_source": {"id": "src_review", "version": "v1"},
                 "persona": {"id": "persona_review", "version": "v1",
                             "params": {"risk_appetite": "balanced",
                                        "conf_floor": 0.55,
                                        "max_pos_pct": 0.25,
                                        "max_total_pct": 0.80}},
                 "watch_style": {"id": "watch_review", "version": "v1"},
                 "trader": {"id": "trader_review", "version": "v1"}},
        "budget": {"monthly_llm_cost_budget": budget},
    }
    p = tmp_path / f"reg_{combo_id}.json"
    p.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
    return p
