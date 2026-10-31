# -*- coding: utf-8 -*-
"""strategy_health_weekly CLI 单测（WO-HEALTH-WEEKLY-GEN-20261031）。

覆盖验收口径：
1) 默认 PG 路径（mock ``_load_rows_pg``，**不连真实 PG**）→ 生成周报 → 原子写
   latest.json（JSON 合法、generator/TID/source 元数据、period 计数、无 .tmp 残留）；
2) ``--rows-file`` 注入（PG mock 置为抛错证明未连库）、rows 文件坏/缺 → rc=2；
3) ``--dry-run`` 只打印 JSON 不落盘；``--out`` 指向 tmp（不写仓库/不改生产产物）；
4) PG 不可用 → 显式 rc=1、stdout JSON ok=false、不落盘、不造数；
   无 rows → 显式 rc=3 + insufficient_data 报告、不落盘、不覆盖既有 latest.json；
5) 原子写助手：父目录自动建、覆盖写、无 tmp 残留；坏参数/未知参数 rc=2；
6) 只读：源码只用 load_rows_pg（SELECT only），无 INSERT/UPDATE/DELETE/DROP。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import strategy_health_weekly as sgw  # noqa: E402


# ── 合成 rows（与 strategy_health 行契约同构；窗口 20261024~20261030） ──
def _row(day, symbol="600000.SH", sid="kg_boost", ret_t1=1.0, ret_t5=2.0):
    return {
        "trade_date": day,
        "symbol": symbol,
        "action": "buy",
        "score": 88.0,
        "confidence": 0.8,
        "contributions": {sid: {"score": 50.0, "weight": 0.5, "action": "buy"}},
        "divergence": False,
        "rationale": "",
        "ret_t1": ret_t1,
        "ret_t5": ret_t5,
    }


def _rows(days=7, per_day=3, sid="kg_boost"):
    rows = []
    for offset in range(days):
        day = "202610%02d" % (24 + offset)  # 20261024..20261030
        for index in range(per_day):
            rows.append(
                _row(day, symbol="60000%d.SH" % index, sid=sid)
            )
    return rows


def _patch_pg(monkeypatch, rows, seen=None):
    def fake_load(limit):
        if seen is not None:
            seen.append(limit)
        return rows

    monkeypatch.setattr(sgw, "_load_rows_pg", fake_load)


def _write_rows_file(path, rows):
    path.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
    return path


def _no_tmp_files(directory):
    return [item.name for item in directory.iterdir() if item.name.endswith(".tmp")] == []


# ── 1. PG 路径 → 原子写 latest.json ──────────────────────────────────
class TestGenerateFromPg:
    def test_pg_rows_written_atomic(self, monkeypatch, tmp_path, capsys):
        rows = _rows()
        seen = []
        _patch_pg(monkeypatch, rows, seen=seen)
        out = tmp_path / "automation_runs" / "strategy_health" / "latest.json"

        rc = sgw.run(days=7, limit=123, out=str(out))

        assert rc == sgw.EXIT_OK == 0
        assert out.is_file()
        assert seen == [123]
        assert _no_tmp_files(out.parent)
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["schema"] == "strategy_health_weekly/v1"
        assert doc["status"] == "OK"
        assert doc["readonly"] is True
        assert doc["generator"] == sgw.GENERATOR
        assert doc["generator_wo"] == sgw.TID
        assert doc["source"]["kind"] == "pg"
        assert doc["source"]["limit"] == 123
        assert doc["period"]["rows"] == len(rows)
        assert doc["period"]["start"] == "20261024"
        assert doc["period"]["end"] == "20261030"
        assert doc["period"]["trading_days"] == 7
        assert doc["asof"] == "20261030"
        stdout = capsys.readouterr().out
        assert "[strategy_health_weekly]" in stdout
        assert "status=OK" in stdout
        assert str(out) in stdout

    def test_default_limit_used(self, monkeypatch, tmp_path):
        seen = []
        _patch_pg(monkeypatch, _rows(), seen=seen)
        out = tmp_path / "latest.json"
        assert sgw.run(out=str(out)) == 0
        assert seen == [sgw.DEFAULT_LIMIT]
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["source"]["limit"] == sgw.DEFAULT_LIMIT


# ── 2. rows-file 注入（不连 PG） ─────────────────────────────────────
class TestRowsFileSource:
    def test_rows_file_does_not_touch_pg(self, monkeypatch, tmp_path):
        def forbidden(_limit):
            raise AssertionError("--rows-file 模式不得连 PG")

        monkeypatch.setattr(sgw, "_load_rows_pg", forbidden)
        rows_file = _write_rows_file(tmp_path / "rows.json", _rows())
        out = tmp_path / "weekly.json"

        rc = sgw.run(rows_file=str(rows_file), out=str(out))

        assert rc == 0
        assert out.is_file()
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["source"]["kind"] == "rows_file"
        assert doc["source"]["path"] == str(rows_file.resolve())
        assert doc["period"]["rows"] == 21

    def test_missing_rows_file_rc2(self, tmp_path, capsys):
        out = tmp_path / "weekly.json"
        rc = sgw.run(rows_file=str(tmp_path / "missing.json"), out=str(out))
        assert rc == sgw.EXIT_USAGE == 2
        assert not out.exists()
        assert "输入非法" in capsys.readouterr().err

    def test_bad_rows_file_rc2(self, tmp_path, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text("{not-json", encoding="utf-8")
        out = tmp_path / "weekly.json"
        assert sgw.run(rows_file=str(bad), out=str(out)) == 2
        assert not out.exists()
        assert "输入非法" in capsys.readouterr().err


# ── 3. dry-run 不落盘 ────────────────────────────────────────────────
class TestDryRun:
    def test_dry_run_prints_json_without_write(self, monkeypatch, tmp_path, capsys):
        _patch_pg(monkeypatch, _rows())
        out = tmp_path / "latest.json"

        assert sgw.run(out=str(out), dry_run=True) == 0

        assert not out.exists()
        assert not (tmp_path / "latest.json.tmp").exists()
        captured = capsys.readouterr()
        doc = json.loads(captured.out)
        assert doc["status"] == "OK"
        assert doc["period"]["rows"] == 21
        assert "dry-run" in captured.err


# ── 4. PG 不可用 / 无 rows：显式错误码，不造数、不落盘 ────────────────
class TestFailureModes:
    def test_pg_unavailable_rc1(self, monkeypatch, tmp_path, capsys):
        def broken(_limit):
            raise RuntimeError("XMAP_PG_PASSWORD 未配置（env 或 .env.local）")

        monkeypatch.setattr(sgw, "_load_rows_pg", broken)
        out = tmp_path / "latest.json"

        rc = sgw.run(out=str(out))

        assert rc == sgw.EXIT_PG_UNAVAILABLE == 1
        assert not out.exists()
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["ok"] is False
        assert payload["status"] == "pg_unavailable"
        assert payload["readonly"] is True
        assert "PG" in captured.err

    def test_no_rows_rc3_insufficient_without_write(self, monkeypatch, tmp_path, capsys):
        _patch_pg(monkeypatch, [])
        out = tmp_path / "latest.json"

        rc = sgw.run(out=str(out))

        assert rc == sgw.EXIT_NO_ROWS == 3
        assert not out.exists()
        doc = json.loads(capsys.readouterr().out)
        assert doc["status"] == "insufficient_data"
        assert doc["period"]["rows"] == 0
        assert doc["readonly"] is True
        assert doc["generator"] == sgw.GENERATOR

    def test_no_rows_does_not_overwrite_existing_latest(self, monkeypatch, tmp_path):
        out = tmp_path / "latest.json"
        out.write_text('{"status": "OK", "period": {"rows": 42}}', encoding="utf-8")
        before = out.read_bytes()
        _patch_pg(monkeypatch, [])
        assert sgw.run(out=str(out)) == 3
        assert out.read_bytes() == before  # 不覆盖既有 latest.json


# ── 5. 原子写助手 / 参数校验 / CLI 解析 ──────────────────────────────
class TestAtomicWriteAndCli:
    def test_atomic_write_creates_parents_and_no_tmp(self, tmp_path):
        target = tmp_path / "deep" / "dir" / "weekly.json"
        path = sgw.write_report_atomic({"status": "OK", "rows": 1}, str(target))
        assert path == str(target.resolve())
        assert target.is_file()
        assert json.loads(target.read_text(encoding="utf-8"))["rows"] == 1
        assert _no_tmp_files(target.parent)

    def test_atomic_write_overwrites_previous(self, tmp_path):
        target = tmp_path / "latest.json"
        target.write_text('{"old": true}', encoding="utf-8")
        sgw.write_report_atomic({"new": True}, str(target))
        assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
        assert _no_tmp_files(tmp_path)

    def test_invalid_days_and_limit_rc2(self, tmp_path, capsys):
        out = tmp_path / "x.json"
        assert sgw.run(days=0, out=str(out)) == 2
        assert sgw.run(limit=0, out=str(out)) == 2
        assert sgw.run(days="7", out=str(out)) == 2  # type: ignore[arg-type]
        assert sgw.run(days=True, out=str(out)) == 2  # bool 不算正整数
        assert not out.exists()
        assert "--days" in capsys.readouterr().err

    def test_main_help(self, capsys):
        assert sgw.main(["-h"]) == 0
        assert "用法" in capsys.readouterr().out

    def test_main_unknown_flag_rc2(self, capsys):
        assert sgw.main(["--nope"]) == 2
        assert "未知参数" in capsys.readouterr().err

    def test_main_missing_value_rc2(self, capsys):
        assert sgw.main(["--days"]) == 2
        assert "缺值" in capsys.readouterr().err

    def test_main_bad_int_rc2(self, capsys):
        assert sgw.main(["--days", "abc"]) == 2
        assert "--days" in capsys.readouterr().err
        assert sgw.main(["--limit", "abc"]) == 2

    def test_main_dry_run_with_args(self, monkeypatch, capsys):
        seen = []
        _patch_pg(monkeypatch, _rows(), seen=seen)
        rc = sgw.main(["--days", "7", "--limit", "100", "--dry-run"])
        assert rc == 0
        assert seen == [100]
        doc = json.loads(capsys.readouterr().out)
        assert doc["period"]["days_requested"] == 7
        assert doc["source"]["limit"] == 100

    def test_main_success_with_out(self, monkeypatch, tmp_path):
        _patch_pg(monkeypatch, _rows())
        out = tmp_path / "latest.json"
        assert sgw.main(["--out", str(out), "--limit", "500"]) == 0
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["source"]["limit"] == 500
        assert doc["status"] == "OK"

    def test_default_constants(self):
        assert sgw.DEFAULT_DAYS == 7
        assert sgw.DEFAULT_LIMIT > 60  # 默认行数上限须覆盖 7 天窗口
        assert sgw.DEFAULT_OUT.endswith(
            "automation_runs" + os.sep + "strategy_health" + os.sep + "latest.json"
        )

    def test_source_is_pg_read_only(self):
        source = Path(sgw.__file__).read_text(encoding="utf-8")
        assert "load_rows_pg" in source  # 只读加载器
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER "):
            assert verb not in source, "CLI 不得含 PG 写语句: %s" % verb
