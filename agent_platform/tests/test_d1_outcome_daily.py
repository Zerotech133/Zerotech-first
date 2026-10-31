# -*- coding: utf-8 -*-
"""D1 outcome 回填 + 校准日常 测试（WO-D1-OUTCOME-DAILY-20261031，离线）。

覆盖：
  · `--since` 只回填该日（含）之后缺失的行，输出 new/dates；10-30 在下一交易日
    （本例 2026-11-02）收盘价可算时可回填；
  · 重复跑幂等（不重复追加、第二次 new=0、文件字节不变）；
  · outcome 行缺 decision_id/model 时从 shadow_ledger 同 run_id 补（补不到落
    null，不伪造；已有值不动；补完再跑不再改）；
  · dry-run（write=False）报告「本可新增/本可补行数」但零写盘；
  · `tools/d1_calibration.py --outcomes` 可指向隔离文件（日常/测试共用）；
  · `tools/d1_outcome_daily.py` 报告字段 n/Brier/ECE/coverage + 幂等 +
    --no-fetch 离线预演 + 不自动交易声明。

隔离纪律：全部用 pytest tmp_path 夹具；价格用假 K 线 fetcher（不联网）；
不触碰生产 sidecar。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
PLATFORM = WT / "agent_platform"
TOOLS = WT / "tools"

# outcome writer：正常 sys.path 导入（d1_outcome_daily 内部也 import 同一模块，
# 保证测试 monkeypatch 的 _default_price_fetcher 对日常工具同样生效）。
sys.path.insert(0, str(PLATFORM))
import decision_outcomes as dout  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "d1_outcome_daily", str(TOOLS / "d1_outcome_daily.py"))
daily = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(daily)

# 假 K 线：10-30 的下一交易日 = 11-02（周末跳过；覆盖「11-02 收盘后可回填 10-30」）
BARS = [
    ["2026-10-27", "1", "100", "1", "1", "1"],
    ["2026-10-28", "1", "102", "1", "1", "1"],
    ["2026-10-29", "1", "104", "1", "1", "1"],
    ["2026-10-30", "1", "106", "1", "1", "1"],
    ["2026-11-02", "1", "108", "1", "1", "1"],
]


def _fetcher(symbol, n=60):
    return BARS


def _ledger_row(**kw):
    row = {"status": "done", "date": "2026-10-30", "symbol": "600519",
           "run_id": "r-1030", "price_at_decision": "100",
           "recommendation": "BUY", "confidence": 0.8, "latency_s": 10.0,
           "parse_ok": True, "model": "glm/glm-5.3", "fallback": False}
    row.update(kw)
    return row


def _write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                            for r in rows), encoding="utf-8")
    return path


def _read_jsonl(path):
    return dout._load_jsonl(str(path))


# ---------------------------------------------------------------------------
# --since / 幂等
# ---------------------------------------------------------------------------

def test_since_only_backfills_rows_on_or_after_date(tmp_path, monkeypatch):
    monkeypatch.setattr(dout, "_default_price_fetcher", _fetcher)
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [
        _ledger_row(run_id="r-1029", date="2026-10-29"),
        _ledger_row(run_id="r-1030", date="2026-10-30"),
    ])
    out = tmp_path / "out.jsonl"

    rep = dout.run_backfill(ledger, out, since="2026-10-30", write=True)

    assert rep["new"] == 1
    assert rep["dates"] == {"2026-10-30": 1}
    assert rep["written"] is True
    rows = _read_jsonl(out)
    assert len(rows) == 1 and rows[0]["run_id"] == "r-1030"
    # 10-30 裁决的 T+1 = 11-02（周末跳过）收盘价可算 → 回填成立
    assert rows[0]["next_trade_date"] == "2026-11-02"
    assert rows[0]["decision_id"] == dout.decision_id(
        "600519", "2026-10-30", "r-1030")

    # since 放宽到 10-29：只补 10-29 那行（10-30 已存在，不重复）
    rep2 = dout.run_backfill(ledger, out, since="2026-10-29", write=True)
    assert rep2["new"] == 1 and rep2["dates"] == {"2026-10-29": 1}
    assert len(_read_jsonl(out)) == 2


def test_since_invalid_format_rejected(tmp_path):
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [])
    out = tmp_path / "out.jsonl"
    try:
        dout.main(["--ledger", str(ledger), "--out", str(out),
                   "--since", "2026/10/30", "--no-write"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - 防御：不允许静默通过
        raise AssertionError("非法 --since 应被 argparse 拒绝（exit 2）")


def test_repeated_run_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(dout, "_default_price_fetcher", _fetcher)
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row()])
    out = tmp_path / "out.jsonl"

    first = dout.run_backfill(ledger, out, since="2026-10-30", write=True)
    assert first["new"] == 1 and first["written"] is True
    before = out.read_bytes()

    second = dout.run_backfill(ledger, out, since="2026-10-30", write=True)
    assert second["new"] == 0 and second["written"] is False
    assert out.read_bytes() == before  # 幂等：不重写、不重复追加
    assert len(_read_jsonl(out)) == 1


# ---------------------------------------------------------------------------
# 缺 decision_id/model → 从 shadow_ledger 按 run_id 补；缺就 null，不伪造
# ---------------------------------------------------------------------------

def test_repair_fills_missing_decision_id_and_model_from_ledger(tmp_path):
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [
        _ledger_row(run_id="r-old", date="2026-10-27"),           # 无 decision_id 字段
        dict(_ledger_row(run_id="r-custom", date="2026-10-27"),
             decision_id="D1-20261027-customid"),                  # 账本自带 decision_id
    ])
    out = _write_jsonl(tmp_path / "out.jsonl", [
        {"ts": 1.0, "run_id": "r-old", "date": "2026-10-27",
         "symbol": "600519", "recommendation": "BUY", "confidence": 0.8,
         "hit": 1, "model": None},
        {"ts": 2.0, "run_id": "r-custom", "date": "2026-10-27",
         "symbol": "600519", "recommendation": "WATCH", "confidence": 0.5,
         "hit": None},
        {"ts": 3.0, "run_id": "r-unknown", "date": "2026-10-27",
         "symbol": "600519", "recommendation": "WATCH", "confidence": 0.5,
         "hit": None},
    ])

    rep = dout.run_backfill(ledger, out, since="2026-10-30", write=True)

    assert rep["new"] == 0                 # 目标 run_id 都在 sidecar：不重复写
    assert rep["repaired"] == 2            # r-old、r-custom 各补缺字段
    assert rep["repaired_fields"]["decision_id"] == 2
    assert rep["repaired_fields"]["model"] == 2
    rows = {r["run_id"]: r for r in _read_jsonl(out)}
    assert rows["r-old"]["decision_id"] == dout.decision_id(
        "600519", "2026-10-27", "r-old")
    assert rows["r-old"]["model"] == "glm/glm-5.3"
    assert rows["r-custom"]["decision_id"] == "D1-20261027-customid"
    assert rows["r-custom"]["model"] == "glm/glm-5.3"
    # 账本里没有 run_id → decision_id/model 都落 null，绝不伪造
    assert "decision_id" in rows["r-unknown"]
    assert rows["r-unknown"]["decision_id"] is None
    assert rows["r-unknown"]["model"] is None

    # 补完再跑：不再报 repaired、不再写盘（幂等）
    again = dout.run_backfill(ledger, out, since="2026-10-30", write=True)
    assert again["repaired"] == 0 and again["written"] is False


def test_existing_values_are_not_overwritten(tmp_path):
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [
        _ledger_row(run_id="r-keep", date="2026-10-27"),
    ])
    out = _write_jsonl(tmp_path / "out.jsonl", [
        {"run_id": "r-keep", "date": "2026-10-27", "symbol": "600519",
         "decision_id": "keep-me", "model": "keep-model"},
    ])
    rep = dout.run_backfill(ledger, out, since="2026-10-30", write=True)
    assert rep["repaired"] == 0
    row = _read_jsonl(out)[0]
    assert row["decision_id"] == "keep-me" and row["model"] == "keep-model"


def test_dry_run_reports_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(dout, "_default_price_fetcher", _fetcher)
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row()])
    out = tmp_path / "out.jsonl"

    rep = dout.run_backfill(ledger, out, since="2026-10-30", write=False)
    assert rep["new"] == 1 and rep["written"] is False
    assert not out.exists(), "dry-run 不得创建 sidecar"

    # 已有 legacy 行：dry-run 报告「本可补」但文件零改动
    legacy = {"run_id": "r-old", "date": "2026-10-27", "symbol": "600519"}
    _write_jsonl(out, [legacy])
    ledger2 = _write_jsonl(tmp_path / "ledger2.jsonl", [
        _ledger_row(run_id="r-old", date="2026-10-27"),
    ])
    before = out.read_bytes()
    rep2 = dout.run_backfill(ledger2, out, since="2026-10-30", write=False)
    assert rep2["new"] == 0 and rep2["repaired"] >= 1
    assert rep2["written"] is False
    assert out.read_bytes() == before


# ---------------------------------------------------------------------------
# tools/d1_calibration.py --outcomes（隔离文件）
# ---------------------------------------------------------------------------

def _run_calibration(path):
    cmd = [sys.executable, "-X", "utf8", str(TOOLS / "d1_calibration.py"),
           "--outcomes", str(path), "--json"]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=120)
    return res.returncode, json.loads(res.stdout)


def test_calibration_reads_isolated_outcomes_file(tmp_path):
    path = _write_jsonl(tmp_path / "o.jsonl", [
        {"recommendation": "BUY", "hit": 1, "confidence": 0.8},
        {"recommendation": "SELL", "hit": 0, "confidence": 0.6},
        {"recommendation": "WATCH", "hit": None, "confidence": 0.5},
    ])
    rc, rep = _run_calibration(path)
    assert rc == 0
    assert rep["n_directional"] == 2
    assert rep["brier"] == round(((0.8 - 1) ** 2 + (0.6 - 0) ** 2) / 2, 4)
    assert rep["ece"] is not None
    assert rep["can_expand"] is False      # n=2 < 30


# ---------------------------------------------------------------------------
# tools/d1_outcome_daily.py：字段 / 幂等 / 离线预演 / 不交易
# ---------------------------------------------------------------------------

def test_daily_report_fields_and_idempotent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dout, "_default_price_fetcher", _fetcher)
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row()])
    out = tmp_path / "out.jsonl"
    reports = tmp_path / "reports"

    rc = daily.main(["--ledger", str(ledger), "--out", str(out),
                     "--report-dir", str(reports),
                     "--since", "2026-10-30", "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "d1_outcome_daily/v1"
    assert report["date"] == str(daily.date.today())
    assert report["since"] == "2026-10-30"
    assert report["outcome_backfill"]["new"] == 1
    assert report["outcome_backfill"]["dates"] == {"2026-10-30": 1}
    assert report["n"] == 1
    assert report["brier"] == round((0.8 - 1) ** 2, 4)
    assert report["ece"] is not None
    assert report["coverage"] == 1.0
    assert report["trading"] == {"auto_trade": False, "orders_placed": 0,
                                 "trading_permitted": "unchanged"}
    report_path = Path(report["report_path"])
    assert report_path.exists()
    assert json.loads(report_path.read_text(encoding="utf-8"))["n"] == 1
    assert len(_read_jsonl(out)) == 1

    # 同日重跑：幂等（sidecar 不重复写；报告同路径覆盖）
    rc2 = daily.main(["--ledger", str(ledger), "--out", str(out),
                      "--report-dir", str(reports),
                      "--since", "2026-10-30", "--json"])
    assert rc2 == 0
    report2 = json.loads(capsys.readouterr().out)
    assert report2["outcome_backfill"]["new"] == 0
    assert Path(report2["report_path"]) == report_path
    assert len(_read_jsonl(out)) == 1


def test_daily_no_fetch_is_offline_and_writes_nothing_new(tmp_path, monkeypatch):
    # 即便默认 fetcher 是网络链路，--no-fetch 也必须不取价、不新增
    def _boom(*a, **k):  # pragma: no cover - 被调用即失败
        raise AssertionError("--no-fetch 模式不得调用取价")

    monkeypatch.setattr(dout, "_default_price_fetcher", _boom)
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row()])
    out = tmp_path / "out.jsonl"
    reports = tmp_path / "reports"

    report, path = daily.run_daily(ledger=ledger, outcomes=out,
                                   report_dir=reports, since="2026-10-30",
                                   no_fetch=True)

    assert report["outcome_backfill"]["new"] == 0
    assert report["network"] is False
    assert any("--no-fetch" in w for w in report["warnings"])
    assert not out.exists() and path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["n"] == 0


def test_daily_no_write_leaves_sidecar_untouched(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(dout, "_default_price_fetcher", _fetcher)
    ledger = _write_jsonl(tmp_path / "ledger.jsonl", [_ledger_row()])
    out = tmp_path / "out.jsonl"
    reports = tmp_path / "reports"

    rc = daily.main(["--ledger", str(ledger), "--out", str(out),
                     "--report-dir", str(reports), "--since", "2026-10-30",
                     "--no-write", "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["outcome_backfill"]["written"] is False
    assert not out.exists()
    assert not reports.exists(), "--no-write 不得创建报告目录/文件"
