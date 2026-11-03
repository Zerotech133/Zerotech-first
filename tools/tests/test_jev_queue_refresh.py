# -*- coding: utf-8 -*-
"""jev_queue_refresh 单测（WO-JEV-OPS-20261103）。

覆盖：dry-run 只读（hash 不变）/ 分层计数 / --apply 必须 --confirm /
一致性备份 / 单事务幂等（重复执行不重复入队）/ 坏行 fail-visible / 缺表缺列 fail-visible。
全部用 tmp_path 临时 sqlite，不碰生产 news.db。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_queue_refresh as qr  # noqa: E402

NEWS_DDL = """
CREATE TABLE news (
    id TEXT PRIMARY KEY,
    source TEXT,
    ts TEXT,
    title TEXT,
    content TEXT,
    hit INTEGER DEFAULT 0,
    pushed INTEGER DEFAULT 0,
    digested INTEGER DEFAULT 0,
    created_at TEXT,
    stocks TEXT,
    jev TEXT,
    model TEXT,
    judged_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
)
"""

QUEUE_DDL = """
CREATE TABLE jev_review_queue (
    id TEXT PRIMARY KEY,
    queued_at TEXT NOT NULL,
    conf REAL,
    bias TEXT,
    title TEXT,
    stocks TEXT,
    original_jev TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT,
    reviewed_at TEXT,
    decision TEXT,
    note TEXT,
    review_type TEXT NOT NULL DEFAULT ''
)
"""


def _jev(conf, impact="low", bias="bull"):
    return json.dumps({"bias": bias, "conf": conf, "impact": impact,
                       "scope": "cn", "reason": "test"},
                      ensure_ascii=False)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _insert(conn, table: str, values: dict) -> None:
    cols = [str(r[1]) for r in conn.execute('PRAGMA table_info("%s")' % table)]
    keys = [k for k in values if k in cols]
    conn.execute(
        "INSERT INTO %s (%s) VALUES (%s)"
        % (table, ", ".join(keys), ", ".join(["?"] * len(keys))),
        [values[k] for k in keys])


def _make_db(tmp_path: Path, rows: list[tuple], queued: list[tuple] | None = None,
             *, news: bool = True, queue: bool = True,
             drop_news_col: str = "", drop_queue_col: str = "") -> Path:
    """rows: (id, source, ts, title, stocks, jev, judged_at)；queued: id 列表。"""
    db = tmp_path / "news.db"
    conn = sqlite3.connect(db)
    try:
        if news:
            ddl = NEWS_DDL
            if drop_news_col:
                ddl = "\n".join(line for line in NEWS_DDL.splitlines()
                                if not line.strip().startswith(drop_news_col))
            conn.execute(ddl)
            fields = ("id", "source", "ts", "title", "stocks", "jev", "judged_at")
            for row in rows:
                _insert(conn, "news", dict(zip(fields, row)))
        if queue:
            ddl = QUEUE_DDL
            if drop_queue_col:
                ddl = "\n".join(line for line in QUEUE_DDL.splitlines()
                                if not line.strip().startswith(drop_queue_col))
            conn.execute(ddl)
            for nid in queued or []:
                _insert(conn, "jev_review_queue", {
                    "id": nid, "queued_at": "2026-10-30 16:21:47",
                    "conf": 0.2, "bias": "bull", "title": "old", "stocks": "[]",
                    "original_jev": _jev(0.2)})
        conn.commit()
    finally:
        conn.close()
    return db


def _default_rows():
    return [
        # id, source, ts, title, stocks, jev, judged_at
        ("h1", "sina", "2026-11-02 09:00", "high impact", "[]",
         _jev(0.9, "high"), "2026-11-02 10:00"),
        ("h2", "sina", "2026-11-02 08:00", "high impact low conf", "[]",
         _jev(0.4, "high"), "2026-11-02 10:01"),
        ("l1", "sina", "2026-11-01 08:00", "low conf", "[]",
         _jev(0.5, "medium"), "2026-11-01 09:00"),
        ("n1", "chinanews", "2026-10-20 08:00", "new source", "[]",
         _jev(0.8, "medium"), "2026-10-20 09:00"),
        ("old1", "eastmoney", "2026-01-05 08:00", "old", "[]",
         _jev(0.9, "low"), "2026-01-05 09:00"),
        ("s_old", "sina", "2025-12-01 08:00", "old sina", "[]",
         _jev(0.9, "low"), "2025-12-01 09:00"),
        ("jonly", "eastmoney", "2026-10-30 09:00", "judged only", "[]",
         None, "2026-10-30 09:00"),
        ("bad1", "eastmoney", "2026-11-01 09:00", "bad json", "[]",
         "Not-JSON{", "2026-11-01 09:00"),
        # 已在队样本（news 与 queue 双边都在，模拟真实库）
        ("q1", "sina", "2026-10-31 08:00", "queued", "[]",
         _jev(0.2, "medium"), "2026-10-31 09:00"),
    ]


def _run_json(capsys, argv):
    rc = qr.main(argv)
    out = capsys.readouterr()
    assert rc == 0, "rc=%s stderr=%s" % (rc, out.err)
    return json.loads(out.out)


# --------------------------------------------------------------------------- #
# dry-run / 分层
# --------------------------------------------------------------------------- #
def test_dry_run_layers_and_read_only(tmp_path, capsys):
    db = _make_db(tmp_path, _default_rows(), queued=["q1"])
    digest = _sha256(db)
    rep = _run_json(capsys, ["--db", str(db), "--json"])

    assert rep["mode"] == "dry-run"
    assert rep["read_only_scan"] is True
    # 已判读 = jev 非空 或 judged_at 非空（含已在队 q1）；候选 = 已判读 - 在队
    assert rep["universe"]["judged"] == 9
    assert rep["universe"]["queued"] == 1
    assert rep["universe"]["candidates"] == 8
    assert rep["queue_status"]["by_status"]["pending"] == 1
    # 分层 primary（互斥）：high=2 / low=1 / new=1 / unstratified=3
    # bad1 是坏行，从分层中剔除（仍计入 universe.candidates，fail-visible 计数）
    lp = rep["layer_primary"]
    assert lp["high_impact"] == 2
    assert lp["low_conf"] == 1
    assert lp["new_source"] == 1
    assert lp["unstratified"] == 3
    assert rep["layer_matched"]["low_conf"] == 2       # h2 同时命中 high+low
    assert rep["overlap"]["high_and_low"] == 1
    # 计划 = 全部有层候选（limit 默认 500），按 层→conf→ts 排序
    assert rep["planned"]["n"] == 4
    assert rep["planned"]["by_layer"] == {"high_impact": 2, "low_conf": 1,
                                         "new_source": 1}
    assert rep["planned"]["samples"][0]["id"] == "h2"   # 高影响 + 最低 conf 最先
    # 坏行 fail-visible
    assert rep["bad_rows"]["n"] == 1
    assert rep["bad_rows"]["invalid_jev"][0]["id"] == "bad1"
    # dry-run 只读：db 字节完全不变
    assert _sha256(db) == digest


def test_dry_run_human_text(tmp_path, capsys):
    db = _make_db(tmp_path, _default_rows(), queued=["q1"])
    digest = _sha256(db)
    rc = qr.main(["--db", str(db)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DRY-RUN" in out and "未写库" in out
    assert "未入队候选 8" in out
    assert "high_impact 2/2" in out
    assert _sha256(db) == digest


# --------------------------------------------------------------------------- #
# --apply 安全约束
# --------------------------------------------------------------------------- #
def test_apply_requires_confirm(tmp_path, capsys):
    db = _make_db(tmp_path, _default_rows(), queued=["q1"])
    digest = _sha256(db)
    rc = qr.main(["--db", str(db), "--apply"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "--confirm" in err
    assert _sha256(db) == digest              # 拒跑在写库之前


def test_apply_backup_transaction_and_idempotent(tmp_path, capsys):
    db = _make_db(tmp_path, _default_rows(), queued=["q1"])
    # 模拟生产库 WAL 模式：备份须能处理 WAL 源并落成自包含单文件
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()
    backup_dir = tmp_path / "backups"

    # 第一次：入队全部 4 条有层候选
    rep = _run_json(capsys, ["--db", str(db), "--apply", "--confirm",
                             "--backup-dir", str(backup_dir), "--json"])
    apply_info = rep["apply"]
    assert apply_info["performed"] is True
    assert apply_info["result"]["added"] == 4
    assert apply_info["result"]["after"] == 5      # 原 1 条 + 新 4 条
    backup = Path(apply_info["backup_path"])
    assert backup.is_file()
    assert apply_info["backup"]["integrity_check"] == "ok"
    assert apply_info["backup"]["self_contained"] is True
    assert not Path(str(backup) + "-wal").exists()
    assert apply_info["backup"]["queue_rows"] == 1  # 备份是写前快照
    # apply 回执（含本批 ids，回滚用）
    receipt = json.loads(Path(apply_info["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["planned"] == 4
    assert sorted(receipt["ids"]) == ["h1", "h2", "l1", "n1"]
    conn = sqlite3.connect(db)
    try:
        n = conn.execute("SELECT count(*) FROM jev_review_queue").fetchone()[0]
    finally:
        conn.close()
    assert n == 5

    # 第二次：同命令重复执行 —— 幂等，不重复入队、不再新建备份
    rep2 = _run_json(capsys, ["--db", str(db), "--apply", "--confirm",
                              "--backup-dir", str(backup_dir), "--json"])
    assert rep2["apply"]["performed"] is False
    assert rep2["apply"].get("result", {}).get("added", 0) == 0
    assert rep2["planned"]["n"] == 0
    conn = sqlite3.connect(db)
    try:
        n2 = conn.execute("SELECT count(*) FROM jev_review_queue").fetchone()[0]
    finally:
        conn.close()
    assert n2 == 5
    assert len(list(backup_dir.glob("*.bak"))) == 1
    assert len(list(backup_dir.glob("*.receipt.json"))) == 1

    # 直接重放同一批 planned：INSERT OR IGNORE 仍 0 新增（幂等键=id）
    rows = [dict(id="h2", conf=0.4, bias="bull", title="x", stocks="[]",
                 jev_raw=_jev(0.4, "high"))]
    again = qr.apply_planned(db, rows)
    assert again["added"] == 0


# --------------------------------------------------------------------------- #
# 坏行 / 缺表缺列 fail-visible
# --------------------------------------------------------------------------- #
def test_bad_rows_fail_on_bad_rc4(tmp_path, capsys):
    db = _make_db(tmp_path, _default_rows(), queued=["q1"])
    digest = _sha256(db)
    rc = qr.main(["--db", str(db), "--fail-on-bad"])
    out = capsys.readouterr()
    assert rc == 4
    assert "bad1" in out.err or "坏行" in out.err
    assert _sha256(db) == digest


def test_missing_news_table_fail_visible(tmp_path, capsys):
    db = _make_db(tmp_path, [], news=False, queue=True)
    rc = qr.main(["--db", str(db)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "缺表 news" in err


def test_missing_queue_table_fail_visible(tmp_path, capsys):
    db = _make_db(tmp_path, _default_rows(), news=True, queue=False)
    rc = qr.main(["--db", str(db)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "缺表 jev_review_queue" in err


def test_missing_column_fail_visible(tmp_path, capsys):
    # news 缺 judged_at 列 → fail-visible（不静默按 jev 单口径降级）
    db = _make_db(tmp_path, [], queued=[], news=True, queue=True,
                  drop_news_col="judged_at")
    rc = qr.main(["--db", str(db)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "缺列" in err and "judged_at" in err


def test_missing_queue_column_fail_visible(tmp_path, capsys):
    # jev_review_queue 缺 original_jev 列 → apply 无法安全写入，fail-visible
    db = _make_db(tmp_path, _default_rows(), queue=True,
                  drop_queue_col="original_jev")
    rc = qr.main(["--db", str(db)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "缺列" in err and "original_jev" in err


def test_db_not_found_fail_visible(tmp_path, capsys):
    rc = qr.main(["--db", str(tmp_path / "nope.db")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "DB 不存在" in err
