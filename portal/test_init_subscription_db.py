# -*- coding: utf-8 -*-
"""订阅账本离线初始化工具单测（飞书中台 P3.8-lite · M-C / WO-FS-113）。

零碰生产：所有用例通过 env ``SUBSCRIPTION_DB`` 把 ``subscription_store._db_path()``
锚到 ``tmp_path``（沿 test_subscription_store.py / test_reconcile_subscription.py
判例），并额外加一条 autouse 兜底 fixture——即便某个用例忘了设 env，也落不到
``portal/work/subscription.sqlite3``。

覆盖：--check 在库不存在时报 ok=false 且不建文件／--init 空目录建 5 表 + 种子／
重复 --init 幂等（created=false、表与种子不变、数据不丢）／--init 后 --check 通过／
缺一表时 --init 补齐且不重建其他表（行数据仍在）／损坏文件时 --init 拒绝修复
（字节不变）／退出码三态 0/1/2／--json 键集齐备／ensure_schema 的 created 语义／
人读摘要区分「首次未初始化」与「结构缺失」／已齐备时 --init 为 no-op（文件字节与
mtime 均不变）／子进程真实退出码。
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "portal"), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import subscription_store as ss  # noqa: E402
import init_subscription_db as mod  # noqa: E402

TOOL = _ROOT / "tools" / "init_subscription_db.py"

EXPECTED_TABLES = {
    "subscriber",
    "plan",
    "subscription",
    "entitlement_log",
    "payment_order",
}
RESULT_KEYS = {"ok", "db_path", "mode", "created", "tables", "seeded_plans", "problems"}


@pytest.fixture(autouse=True)
def _never_touch_real_db(tmp_path, monkeypatch):
    """兜底防线：任何用例开始前就把库锚钉在 tmp，绝不落到真实库。"""
    monkeypatch.setenv("SUBSCRIPTION_DB", str(tmp_path / "_guard_never_used.sqlite3"))
    monkeypatch.delenv("SUBSCRIPTION_OWN_DOMAIN", raising=False)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """临时库路径（此时文件尚不存在）。用例结束后清理临时目录内残留。"""
    p = tmp_path / "ledger.sqlite3"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(p))
    yield p
    for suffix in ("", "-wal", "-shm", "-journal"):
        f = Path(str(p) + suffix)
        if f.exists():
            f.unlink()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def table_names(path):
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def plan_ids(path):
    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT plan_id FROM plan ORDER BY plan_id").fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def run_sql(path, statement, params=()):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def read_json(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# --check / --init 基本行为
# ---------------------------------------------------------------------------


def test_01_check_missing_reports_false_and_creates_nothing(ledger, capsys):
    """--check 在库不存在时 ok=false，且绝不创建文件（读路径不建库同判例）。"""
    assert not ledger.exists()
    rc = mod.main(["--check", "--json"])
    out = read_json(capsys)

    assert rc == 1
    assert out["ok"] is False
    assert out["mode"] == "check"
    assert out["created"] is False
    assert out["tables"] == []
    assert out["seeded_plans"] == 0
    assert "db_missing" in out["problems"]
    assert not ledger.exists()  # 关键断言：不创建任何东西
    assert ledger.parent.exists()


def test_02_init_creates_five_tables_and_seeds(ledger, capsys):
    """--init 在空目录建出 5 表 + 两条种子档位，created=true。"""
    rc = mod.main(["--init", "--json"])
    out = read_json(capsys)

    assert rc == 0
    assert out["ok"] is True
    assert out["mode"] == "init"
    assert out["created"] is True
    assert set(out["tables"]) == EXPECTED_TABLES
    assert out["seeded_plans"] == len(ss.SEED_PLANS) == 2
    assert out["problems"] == []
    assert ledger.exists()
    assert table_names(ledger) == EXPECTED_TABLES
    assert plan_ids(ledger) == ["basic_monthly", "internal"]


def test_03_init_twice_is_idempotent(ledger, capsys):
    """重复 --init：created=false，表与种子数量不变，已有业务数据不丢。"""
    assert mod.main(["--init", "--json"]) == 0
    read_json(capsys)
    run_sql(
        ledger,
        "INSERT INTO subscriber(open_id, display_name, created_at) VALUES (?,?,?)",
        ("ou_keep", "留存用户", "2026-10-20 10:00:00"),
    )

    assert mod.main(["--init", "--json"]) == 0
    out = read_json(capsys)

    assert out["ok"] is True
    assert out["created"] is False
    assert set(out["tables"]) == EXPECTED_TABLES
    assert out["seeded_plans"] == 2
    assert out["problems"] == []
    assert table_names(ledger) == EXPECTED_TABLES
    assert plan_ids(ledger) == ["basic_monthly", "internal"]
    conn = sqlite3.connect(str(ledger))
    try:
        kept = conn.execute(
            "SELECT display_name FROM subscriber WHERE open_id='ou_keep'"
        ).fetchall()
    finally:
        conn.close()
    assert kept == [("留存用户",)]  # 幂等不等于重建：数据仍在


def test_04_check_passes_after_init(ledger, capsys):
    """--init 之后 --check 通过（ok=true，退出码 0）。"""
    assert mod.main(["--init"]) == 0
    capsys.readouterr()

    rc = mod.main(["--check", "--json"])
    out = read_json(capsys)

    assert rc == 0
    assert out["ok"] is True
    assert out["mode"] == "check"
    assert out["problems"] == []
    assert set(out["tables"]) == EXPECTED_TABLES


def test_05_init_restores_dropped_table_without_rebuild(ledger, capsys):
    """库存在但缺一表：--init 只补该表，其他表与行数据原样保留。"""
    ss._connect(create=True).close()
    run_sql(
        ledger,
        "INSERT INTO subscriber(open_id, display_name, created_at) VALUES (?,?,?)",
        ("ou_keep", "留存用户", "2026-10-20 10:00:00"),
    )
    run_sql(ledger, "DROP TABLE payment_order")
    assert "payment_order" not in table_names(ledger)

    rc = mod.main(["--init", "--json"])
    out = read_json(capsys)

    assert rc == 0
    assert out["ok"] is True
    assert out["created"] is False  # 库文件本来就存在
    assert set(out["tables"]) == EXPECTED_TABLES
    assert table_names(ledger) == EXPECTED_TABLES
    conn = sqlite3.connect(str(ledger))
    try:
        kept = conn.execute(
            "SELECT display_name FROM subscriber WHERE open_id='ou_keep'"
        ).fetchall()
        order_rows = conn.execute("SELECT COUNT(*) FROM payment_order").fetchone()[0]
    finally:
        conn.close()
    assert kept == [("留存用户",)]  # 未重建其他表
    assert order_rows == 0  # 补出来的是空表，不是凭空造数据


# ---------------------------------------------------------------------------
# 损坏文件：拒绝自动修复
# ---------------------------------------------------------------------------


def test_06_init_refuses_corrupt_file(ledger, capsys):
    """损坏库：--init 回 db_corrupt、退出码 1，且一个字节都不改。"""
    garbage = b"not a sqlite database, deliberately corrupted\n0123456789abcdef" * 40
    ledger.write_bytes(garbage)
    before_hash = hashlib.sha256(ledger.read_bytes()).hexdigest()
    before_size = ledger.stat().st_size

    rc = mod.main(["--init", "--json"])
    out = read_json(capsys)

    assert rc == 1
    assert out["ok"] is False
    assert out["created"] is False
    assert "db_corrupt" in out["problems"]
    assert ledger.read_bytes() == garbage  # 未覆盖、未重建
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before_hash
    assert ledger.stat().st_size == before_size

    # --check 同样判 corrupt，且不改文件
    rc = mod.main(["--check", "--json"])
    out = read_json(capsys)
    assert rc == 1
    assert "db_corrupt" in out["problems"]
    assert ledger.read_bytes() == garbage


# ---------------------------------------------------------------------------
# 退出码 / JSON 结构 / ensure_schema 契约
# ---------------------------------------------------------------------------


def test_07_exit_codes_0_1_2(ledger, capsys, monkeypatch):
    """退出码三态：ok → 0；结构缺失/损坏 → 1；工具自身失败 → 2。"""
    assert mod.main(["--check"]) == 1  # 库不存在
    capsys.readouterr()
    assert mod.main(["--init"]) == 0  # 初始化成功
    capsys.readouterr()

    def boom(*_a, **_kw):
        raise RuntimeError("探测内部错误（注入）")

    monkeypatch.setattr(mod, "probe", boom)
    assert mod.main(["--check"]) == 2  # 工具自身执行失败
    out = capsys.readouterr().out
    assert "RuntimeError" in out


def test_08_json_key_set_exact(ledger, capsys):
    """--json 输出键集与契约严格一致（7 键，不多不少）。"""
    mod.main(["--init", "--json"])
    out = read_json(capsys)
    assert set(out.keys()) == RESULT_KEYS
    assert isinstance(out["ok"], bool)
    assert isinstance(out["db_path"], str)
    assert isinstance(out["mode"], str)
    assert isinstance(out["created"], bool)
    assert isinstance(out["tables"], list)
    assert isinstance(out["seeded_plans"], int)
    assert isinstance(out["problems"], list)


def test_09_ensure_schema_created_semantics(ledger):
    """ensure_schema：首建 created=true，二次 created=false，结构/种子稳定。"""
    assert not ledger.exists()
    first = ss.ensure_schema()
    assert set(first.keys()) == RESULT_KEYS - {"mode"}
    assert first["ok"] is True
    assert first["created"] is True
    assert first["db_path"] == str(ledger)
    assert set(first["tables"]) == EXPECTED_TABLES
    assert first["seeded_plans"] == 2
    assert first["problems"] == []
    assert ledger.exists()

    second = ss.ensure_schema()
    assert second["ok"] is True
    assert second["created"] is False
    assert set(second["tables"]) == EXPECTED_TABLES
    assert second["seeded_plans"] == 2
    assert plan_ids(ledger) == ["basic_monthly", "internal"]


def test_10_human_output_distinguishes_missing_vs_incomplete(ledger, capsys):
    """人读摘要必须区分「库不存在（首次未初始化）」与「结构缺失（异常）」。"""
    assert mod.main(["--check"]) == 1
    missing_txt = capsys.readouterr().out
    assert "库不存在" in missing_txt
    assert "从未初始化" in missing_txt
    assert "结构缺失（异常" not in missing_txt
    assert not ledger.exists()

    ss._connect(create=True).close()
    run_sql(ledger, "DROP TABLE entitlement_log")
    assert mod.main(["--check"]) == 1
    incomplete_txt = capsys.readouterr().out
    assert "结构缺失" in incomplete_txt
    assert "异常" in incomplete_txt
    assert "从未初始化过" not in incomplete_txt
    assert "missing_table:entitlement_log" in incomplete_txt
    assert missing_txt != incomplete_txt


# ---------------------------------------------------------------------------
# no-op 铁律（已齐备时一个字节都不动）
# ---------------------------------------------------------------------------


def test_11_init_on_ready_db_is_pure_noop(ledger, capsys):
    """库已齐备时 --init 是 no-op：文件字节与 mtime 均不变。"""
    assert mod.main(["--init"]) == 0
    capsys.readouterr()
    before_bytes = ledger.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()
    before_mtime = ledger.stat().st_mtime_ns

    rc = mod.main(["--init", "--json"])
    out = read_json(capsys)

    assert rc == 0
    assert out["created"] is False
    assert out["problems"] == []
    assert ledger.read_bytes() == before_bytes
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == before_hash
    assert ledger.stat().st_mtime_ns == before_mtime


# ---------------------------------------------------------------------------
# 子进程真实退出码（证明 sys.exit(main()) 接线正确）
# ---------------------------------------------------------------------------


def test_12_subprocess_real_exit_codes(tmp_path):
    """子进程跑真实 CLI：库不存在 --check → 1；--init → 0；再 --check → 0。"""
    p = tmp_path / "subproc.sqlite3"
    full_env = dict(os.environ)
    full_env["SUBSCRIPTION_DB"] = str(p)

    def run(*args):
        return subprocess.run(
            [sys.executable, str(TOOL), *args],
            capture_output=True,
            text=True,
            env=full_env,
            timeout=120,
        )

    r = run("--check", "--json")
    assert r.returncode == 1, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["ok"] is False
    assert not p.exists()

    r = run("--init", "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["ok"] is True
    assert p.exists()

    r = run("--check", "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["ok"] is True
