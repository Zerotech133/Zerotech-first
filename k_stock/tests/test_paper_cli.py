# -*- coding: utf-8 -*-
"""paper.cli._newest_run 回归测试（WO-QT-20261020-049）。

核心缺陷（-046 取证实锤）：原实现按文件名字典序取尾，poc_pf_*/poc_adapter_*
前缀首字母恒大于日期戳开头，旧产物会压过新产物被当"最新"——默认 exec 落到
全陈旧跳过。修复后委托 trainee._newest_ai_run（mtime_ns + 文件名确定性破平，
排除面含 pf_/adapter_ 两族），本文件锁死该口径不回退。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import pytest

PAPER_DIR = Path(__file__).resolve().parent.parent / "paper"
sys.path.insert(0, str(PAPER_DIR))

import cli as paper_cli  # noqa: E402
import trainee  # noqa: E402
from engine import Reject  # noqa: E402


def _mk_ai_dir(tmp_path: Path, names: list[str]) -> Path:
    """造决策产物目录：每个文件落盘并给错开的 mtime（按列表序递增 1s）。"""
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    for i, name in enumerate(names):
        p = d / name
        p.write_text("{}", encoding="utf-8")
        os.utime(p, ns=(1_700_000_000_000_000_000 + i * 1_000_000_000,) * 2)
    return d


def test_newest_run_beats_lexicographic_trap(tmp_path, monkeypatch):
    """字典序陷阱回归：pf_/adapter_ 产物字典序 > 日期戳，但 mtime 更旧——
    修复前 sorted()[-1] 选中 poc_pf_002_20261015（10-15 旧产物）；
    修复后必须选 mtime 最新的 AI 产物 poc_20261020_211855。"""
    d = _mk_ai_dir(
        tmp_path,
        [
            "poc_20261008_151924.json",
            "poc_adapter_chanlun_20261013.json",
            "poc_pf_002_20261015.json",
            "poc_20261020_211855.json",  # 列表最后落盘 → mtime 最新
        ],
    )
    monkeypatch.setattr(trainee, "DATA_DIR", d)
    assert paper_cli._newest_run().name == "poc_20261020_211855.json"


def test_newest_run_excludes_non_ai_channels(tmp_path, monkeypatch):
    """他源通道排除面（qd/qpzm/qpzmus/qpzmhk/pf/adapter）：即便 mtime 最新
    也不得入选——07:05 automation 不吃他源信号（策略串味防护，K-STOCK-414）。"""
    d = _mk_ai_dir(
        tmp_path,
        [
            "poc_20261020_211855.json",
            "poc_qd_20261021_090000.json",  # 双刀通道，mtime 更新
            "poc_qpzm_20261021_090100.json",
            "poc_qpzmus_20261021_090200.json",
            "poc_qpzmhk_20261021_090300.json",
            "poc_pf_003_20261021_090400.json",
            "poc_adapter_x_20261021_090500.json",  # 全场 mtime 最新，仍须排除
        ],
    )
    monkeypatch.setattr(trainee, "DATA_DIR", d)
    assert paper_cli._newest_run().name == "poc_20261020_211855.json"


def test_newest_run_mtime_tie_breaks_deterministically(tmp_path, monkeypatch):
    """mtime 并列时按文件名破平（日期戳升序=新旧序），与 trainee 侧 -017 同款：
    glob 返回序不确定，max 首个会间歇选旧——本用例反复跑必须恒选 b。"""
    d = tmp_path / "data"
    d.mkdir()
    for name in ("poc_20260101_a.json", "poc_20260102_b.json"):
        p = d / name
        p.write_text("{}", encoding="utf-8")
        os.utime(p, ns=(1_700_000_000_000_000_000,) * 2)  # 同一刻落盘
    monkeypatch.setattr(trainee, "DATA_DIR", d)
    got = {paper_cli._newest_run().name for _ in range(5)}
    assert got == {"poc_20260102_b.json"}, f"破平不确定: {got}"


def test_newest_run_empty_dir_rejects(tmp_path, monkeypatch):
    """目录无任何 AI 产物 → Reject（closed on failure，不静默返回 None）。"""
    d = tmp_path / "data"
    d.mkdir()
    (d / "poc_pf_009_20261015.json").write_text("{}", encoding="utf-8")  # 仅他源
    monkeypatch.setattr(trainee, "DATA_DIR", d)
    with pytest.raises(Reject):
        paper_cli._newest_run()


def _accounts_db(tmp_path, ids):
    db = tmp_path / "accounts.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE account (account_id TEXT PRIMARY KEY)")
    con.executemany("INSERT INTO account VALUES (?)", [(aid,) for aid in ids])
    con.commit()
    con.close()
    return db


def test_mark_selection_reads_only_existing_accounts(tmp_path):
    db = _accounts_db(tmp_path, ["main", "combo_x@follow_research", "us_qingping"])
    assert paper_cli._mark_accounts(db) == [
        "combo_x@follow_research",
        "main",
        "us_qingping",
    ]
    assert paper_cli._mark_accounts(db, ["main", "main"]) == ["main"]
    with pytest.raises(Reject, match="不会自动创建"):
        paper_cli._mark_accounts(db, ["unknown"])
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM account").fetchone()[0] == 3


def test_mark_selection_missing_db_does_not_create(tmp_path):
    db = tmp_path / "absent.db"
    with pytest.raises(Reject, match="账本不存在"):
        paper_cli._mark_accounts(db)
    assert not db.exists()


def test_mark_targets_exclusive_and_default_unchanged(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(paper_cli, "cmd_mark", lambda args: called.append(args) or 0)
    db = tmp_path / "unused.db"
    assert paper_cli.main(["--db", str(db), "mark"]) == 0
    assert not called[0].all_accounts and called[0].mark_accounts is None
    assert (
        paper_cli.main(
            [
                "--db",
                str(db),
                "mark",
                "--mark-account",
                "main",
                "--mark-account",
                "main",
            ]
        )
        == 0
    )
    assert called[1].mark_accounts == ["main", "main"]
    with pytest.raises(SystemExit):
        paper_cli.main(["mark", "--all-accounts", "--mark-account", "main"])


def test_mark_default_still_covers_main_and_us_only(monkeypatch, tmp_path):
    db = _accounts_db(
        tmp_path, ["main", "us_qingping", "hk_qingping", "combo_x@follow_research"]
    )
    selected = []
    monkeypatch.setattr(
        paper_cli,
        "_mark_selected",
        lambda args, path, accounts: selected.append((path, accounts)) or 0,
    )
    assert paper_cli.main(["--db", str(db), "mark"]) == 0
    assert selected == [(db, ["main", "us_qingping"])]


def test_mark_default_us_failure_is_nonzero_and_no_false_us_row(tmp_path, monkeypatch):
    db = tmp_path / "paper.db"
    e = paper_cli.PaperEngine(db, paper_cli.ParquetMarket(), account="main")
    e.ensure_account("main")
    e.ensure_account("us_qingping")
    e.con.close()
    original = paper_cli._check_mark_ready

    def check(engine, aid, now=None):
        if aid == "us_qingping":
            raise Reject("US 行情缺失")
        return original(engine, aid, now)

    monkeypatch.setattr(paper_cli, "_check_mark_ready", check)
    assert paper_cli.main(["--db", str(db), "mark"]) == 1
    with sqlite3.connect(db) as con:
        assert [r[0] for r in con.execute("SELECT account_id FROM equity_marks")] == [
            "main"
        ]
    assert not Path(str(db) + ".bak").exists()


def test_mark_default_rejects_missing_main_without_opening(tmp_path):
    db = _accounts_db(tmp_path, ["us_qingping"])
    assert paper_cli.main(["--db", str(db), "mark"]) == 2
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM account").fetchone()[0] == 1


def test_explicit_global_account_routes_once_and_blocks_conflict(tmp_path, monkeypatch):
    db = _accounts_db(tmp_path, ["main", "us_qingping", "hk_qingping"])
    selected = []
    monkeypatch.setattr(
        paper_cli,
        "_mark_selected",
        lambda args, path, accounts: selected.append((path, accounts)) or 0,
    )
    monkeypatch.setattr(
        paper_cli,
        "_engine",
        lambda args: pytest.fail("explicit account must not run legacy CN engine"),
    )
    monkeypatch.setattr(
        paper_cli,
        "_us_pass",
        lambda *args, **kwargs: pytest.fail("must not run US second pass"),
    )
    assert paper_cli.main(["--db", str(db), "mark", "--account", "us_qingping"]) == 0
    assert paper_cli.main(["--db", str(db), "--account", "hk_qingping", "mark"]) == 0
    assert selected == [(db, ["us_qingping"]), (db, ["hk_qingping"])]
    assert (
        paper_cli.main(["--db", str(db), "mark", "--account", "main", "--all-accounts"])
        == 2
    )
    assert (
        paper_cli.main(
            [
                "--db",
                str(db),
                "mark",
                "--account",
                "main",
                "--mark-account",
                "us_qingping",
            ]
        )
        == 2
    )
    with pytest.raises(Reject, match="不会自动创建"):
        paper_cli.cmd_mark(
            SimpleNamespace(
                db=str(db),
                account="ghost",
                account_explicit=True,
                all_accounts=False,
                mark_accounts=None,
            )
        )
    with pytest.raises(SystemExit, match="需要有效账户"):
        paper_cli.main(["mark", "--account", "--all-accounts"])
    with pytest.raises(SystemExit, match="需要有效账户"):
        paper_cli.main(["mark", "--account"])


def test_mark_selected_routes_markets_and_backs_up(tmp_path, monkeypatch):
    db = _accounts_db(tmp_path, ["main", "us_qingping", "hk_qingping"])
    seen = []

    class MarketA:
        pass

    class MarketUS:
        pass

    class MarketHK:
        pass

    class Engine:
        def __init__(self, db_path, market, account):
            seen.append((account, type(market).__name__))
            self.market = market
            self.con = SimpleNamespace(close=lambda: None)

        def mark(self, day=None):
            return {
                "verify_ok": True,
                "date": day,
                "total": 100.0,
                "pos_pct": 0.0,
                "pending": 0,
            }

    import market
    import us_market
    import hk_market

    monkeypatch.setattr(market, "ParquetMarket", MarketA)
    monkeypatch.setattr(us_market, "UsMarket", MarketUS)
    monkeypatch.setattr(hk_market, "HkMarket", MarketHK)
    monkeypatch.setattr(paper_cli, "PaperEngine", Engine)
    monkeypatch.setattr(
        paper_cli, "_check_mark_ready", lambda e, aid, now=None: ("20261021", {})
    )
    args = SimpleNamespace()
    assert (
        paper_cli._mark_selected(args, db, ["main", "us_qingping", "hk_qingping"]) == 0
    )
    assert seen == [
        ("main", "MarketA"),
        ("us_qingping", "MarketUS"),
        ("hk_qingping", "MarketHK"),
    ]
    with sqlite3.connect(str(db) + ".bak") as bak:
        assert bak.execute("SELECT count(*) FROM account").fetchone()[0] == 3


def test_mark_all_real_engine_cash_only_accounts(tmp_path):
    db = tmp_path / "paper.db"
    engine = paper_cli.PaperEngine(db, paper_cli.ParquetMarket(), account="main")
    engine.ensure_account("main")
    engine.ensure_account("combo_test@follow_research")
    engine.con.close()
    accounts = paper_cli._mark_accounts(db)
    assert accounts == ["combo_test@follow_research", "main"]
    assert paper_cli._mark_selected(SimpleNamespace(), db, accounts) == 0
    with sqlite3.connect(db) as con:
        rows = con.execute(
            "SELECT account_id,total,verify_ok FROM equity_marks ORDER BY account_id"
        ).fetchall()
    assert rows == [("combo_test@follow_research", 1000000, 1), ("main", 1000000, 1)]
    assert paper_cli._mark_selected(SimpleNamespace(), db, accounts) == 0
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM equity_marks").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM account").fetchone()[0] == 2


def test_mark_rejects_bad_ledger_before_writing(tmp_path):
    db = tmp_path / "paper.db"
    e = paper_cli.PaperEngine(db, paper_cli.ParquetMarket(), account="main")
    e.ensure_account("main")
    e.con.execute("UPDATE account SET cash=cash+1 WHERE account_id='main'")
    e.con.commit()
    e.con.close()
    assert paper_cli._mark_selected(SimpleNamespace(), db, ["main"]) == 1
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM equity_marks").fetchone()[0] == 0


def test_mark_default_rejects_bad_ledger_before_writing(tmp_path):
    db = tmp_path / "paper.db"
    e = paper_cli.PaperEngine(db, paper_cli.ParquetMarket(), account="main")
    e.ensure_account("main")
    e.con.execute("UPDATE account SET cash=cash+1 WHERE account_id='main'")
    e.con.commit()
    e.con.close()
    assert paper_cli.main(["--db", str(db), "mark"]) == 1
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM equity_marks").fetchone()[0] == 0


def test_mark_rejects_missing_bar_before_writing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        paper_cli, "_expected_mark_day", lambda aid, now=None: "20261020"
    )
    db = tmp_path / "paper.db"
    e = paper_cli.PaperEngine(db, paper_cli.ParquetMarket(), account="main")
    e.ensure_account("main")
    e.con.close()

    class FakeMarket:
        def trading_days(self):
            return ["20261020"]

        def last_bar(self, symbol):
            return None

    class StubEngine:
        def __init__(self, path, market, account):
            self.con = SimpleNamespace(close=lambda: None)
            self.market = FakeMarket()

        def verify(self, aid):
            return {"ok": True}

        def positions(self, aid):
            return [{"stock": "600519.SH"}]

        def mark(self):
            raise AssertionError("missing bar must not write")

    monkeypatch.setattr(paper_cli, "PaperEngine", StubEngine)
    assert paper_cli._mark_selected(SimpleNamespace(), db, ["main"]) == 1
    assert "缺有效收盘价" in capsys.readouterr().err
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM equity_marks").fetchone()[0] == 0


def test_mark_rejects_stale_positive_bar_before_writing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        paper_cli, "_expected_mark_day", lambda aid, now=None: "20261020"
    )
    db = tmp_path / "paper.db"
    e = paper_cli.PaperEngine(db, paper_cli.ParquetMarket(), account="main")
    e.ensure_account("main")
    e.con.close()

    class FakeMarket:
        def trading_days(self):
            return ["20261019", "20261020"]

        def last_bar(self, symbol):
            return {"trade_date": "20261019", "close": 100.0}

    class StubEngine:
        def __init__(self, path, market, account):
            self.con = SimpleNamespace(close=lambda: None)
            self.market = FakeMarket()

        def verify(self, aid):
            return {"ok": True}

        def positions(self, aid):
            return [{"stock": "600519.SH"}]

        def mark(self):
            raise AssertionError("stale positive bar must not write")

    monkeypatch.setattr(paper_cli, "PaperEngine", StubEngine)
    assert paper_cli._mark_selected(SimpleNamespace(), db, ["main"]) == 1
    assert "行情日期滞后" in capsys.readouterr().err
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM equity_marks").fetchone()[0] == 0


@pytest.mark.parametrize(
    "days,bar_day,allowed",
    [
        (["20261019", "20261020"], "20261020", True),
        (["20261019", "20261020"], "20261021", False),
        ([], "20261020", False),
        (["2026-10-20"], "20261020", False),
    ],
)
def test_mark_ready_requires_calendar_date_for_positions(
    days, bar_day, allowed, monkeypatch
):
    monkeypatch.setattr(
        paper_cli, "_expected_mark_day", lambda aid, now=None: "20261020"
    )

    class Market:
        def trading_days(self):
            return days

        def last_bar(self, stock):
            return {"trade_date": bar_day, "close": 20.0}

    engine = SimpleNamespace(
        market=Market(),
        verify=lambda aid: {"ok": True},
        positions=lambda aid: [{"stock": "600519.SH"}],
    )
    if allowed:
        paper_cli._check_mark_ready(engine, "main")
    else:
        with pytest.raises(Reject, match="日历|行情日期"):
            paper_cli._check_mark_ready(engine, "main")


def test_market_valuation_day_cross_timezone_and_holidays():
    bj = ZoneInfo("Asia/Shanghai")

    def at(iso):
        return datetime.fromisoformat(iso).replace(tzinfo=bj)

    assert (
        paper_cli._expected_mark_day("us_qingping", at("2026-03-09T04:05:00"))
        == "20260306"
    )
    assert (
        paper_cli._expected_mark_day("us_qingping", at("2026-03-10T04:05:00"))
        == "20260306"
    )
    assert (
        paper_cli._expected_mark_day("us_qingping", at("2026-03-10T04:11:00"))
        == "20260309"
    )
    assert (
        paper_cli._expected_mark_day("us_qingping", at("2026-11-02T05:05:00"))
        == "20261030"
    )
    assert (
        paper_cli._expected_mark_day("us_qingping", at("2026-11-03T05:11:00"))
        == "20261102"
    )
    assert (
        paper_cli._expected_mark_day("us_qingping", at("2026-11-27T07:05:00"))
        == "20261125"
    )
    assert (
        paper_cli._expected_mark_day("us_qingping", at("2026-11-28T02:11:00"))
        == "20261127"
    )
    with pytest.raises(Reject, match="未覆盖"):
        paper_cli._expected_mark_day("main", at("2026-01-02T20:00:00"))
    assert paper_cli._expected_mark_day("main", at("2026-10-07T20:00:00")) == "20260930"
    assert (
        paper_cli._expected_mark_day("hk_qingping", at("2026-10-07T20:00:00"))
        == "20261007"
    )
    assert (
        paper_cli._expected_mark_day("hk_qingping", at("2026-02-16T12:21:00"))
        == "20260216"
    )
    assert (
        paper_cli._expected_mark_day("hk_qingping", at("2026-02-17T12:21:00"))
        == "20260216"
    )
    with pytest.raises(Reject, match="未覆盖"):
        paper_cli._expected_mark_day("main", at("2027-01-04T07:05:00"))
    with pytest.raises(Reject, match="带时区"):
        paper_cli._expected_mark_day("main", datetime(2026, 10, 21, 7, 5))


def test_mark_uses_checked_price_when_market_changes(tmp_path, monkeypatch):
    seen = []

    class Market:
        def __init__(self):
            self.calls = 0

        def trading_days(self):
            return ["20261020"]

        def last_bar(self, stock):
            self.calls += 1
            return {
                "trade_date": "20261020" if self.calls == 1 else "20261019",
                "close": 20.0 if self.calls == 1 else 1.0,
            }

    market = Market()

    class Engine:
        def __init__(self, path, market_arg, account):
            self.market = market
            self.con = SimpleNamespace(close=lambda: None)

        def verify(self, aid):
            return {"ok": True}

        def positions(self, aid):
            return [{"stock": "600519.SH"}]

        def mark(self, day=None):
            bar = self.market.last_bar("600519.SH")
            seen.append(bar)
            return {
                "verify_ok": True,
                "date": "2026-10-21",
                "total": bar["close"],
                "pos_pct": 1.0,
                "pending": 0,
            }

    monkeypatch.setattr(paper_cli, "PaperEngine", Engine)
    monkeypatch.setattr(
        paper_cli, "_expected_mark_day", lambda aid, now=None: "20261020"
    )
    accounts_db = _accounts_db(tmp_path, ["main"])
    assert paper_cli._mark_selected(SimpleNamespace(), accounts_db, ["main"]) == 0
    assert market.calls == 1
    assert seen == [{"trade_date": "20261020", "close": 20.0}]


def test_mark_rejects_calendar_stale_as_a_whole(monkeypatch):
    monkeypatch.setattr(
        paper_cli, "_expected_mark_day", lambda aid, now=None: "20261021"
    )

    class Market:
        def trading_days(self):
            return ["20261019", "20261020"]

        def last_bar(self, stock):
            return {"trade_date": "20261020", "close": 20.0}

    engine = SimpleNamespace(
        market=Market(),
        verify=lambda aid: {"ok": True},
        positions=lambda aid: [{"stock": "600519.SH"}],
    )
    with pytest.raises(Reject, match="行情湖日历最新.*市场目标日"):
        paper_cli._check_mark_ready(engine, "main")


def test_mark_selected_empty_fails_without_backup(tmp_path):
    db = _accounts_db(tmp_path, [])
    assert paper_cli._mark_selected(SimpleNamespace(), db, []) == 1
    assert not Path(str(db) + ".bak").exists()


def test_mark_selected_partial_failure_is_nonzero(tmp_path, monkeypatch):
    db = _accounts_db(tmp_path, ["main", "us_qingping"])
    seen = []

    class Engine:
        def __init__(self, db_path, market, account):
            self.account = account
            self.market = market
            self.con = SimpleNamespace(close=lambda: None)

        def mark(self, day=None):
            seen.append(self.account)
            if self.account == "us_qingping":
                raise RuntimeError("missing bar")
            return {
                "verify_ok": True,
                "date": "2026-10-21",
                "total": 100,
                "pos_pct": 0,
                "pending": 0,
            }

    monkeypatch.setattr(paper_cli, "PaperEngine", Engine)
    monkeypatch.setattr(
        paper_cli, "_check_mark_ready", lambda e, aid, now=None: ("20261021", {})
    )
    assert paper_cli._mark_selected(SimpleNamespace(), db, ["main", "us_qingping"]) == 1
    assert seen == ["main", "us_qingping"]
    assert not Path(str(db) + ".bak").exists()
