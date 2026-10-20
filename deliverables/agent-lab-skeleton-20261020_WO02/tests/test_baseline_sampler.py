"""工位BC测试：只读基线采样器（tools/baseline_sampler.py）。

采样器是 threshold_calibration 的"上游进料口"：它要是能造数、能写源、
能把无关列采进基线，标定结果就是假的。故本文件只守四件事：

1. 采出来的形状必须与 threshold_calibration.read_baseline 期望逐字段一致
   （ts 为 float、metrics 为 dict），且**真能被那个工具读进去**（真实接入性，
   不只测自己）；
2. 白名单外字段不采（防止无关列污染基线）；
3. 不造数：非数值跳过并计入 gaps、缺失指标不补 0、采不到就不写输出文件、
   **metrics 为空的行绝不写进输出**（标定器的 sample_count 取行数，空行等于
   虚增样本量）；坏行默认即失败，只有显式 --tolerate-bad-lines 才跳过且留痕；
4. sqlite 只读可验证：采样前后源库 sha256 不变、不产生 -journal/-wal 边车
   文件、连接串确实是 mode=ro 且 uri=True。

本文件内所有指标值均为**模拟数据（sim）**，仅用于验证工具行为，
不代表任何真实生产基线，不得据此投用阈值。

零真实网络；全部产物落 tmp_path。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import tools.baseline_sampler as baseline_sampler
from tools.baseline_sampler import EXIT_INPUT_ERROR, EXIT_INSUFFICIENT, EXIT_OK, main
from tools.threshold_calibration import calibrate, read_baseline

METRIC = "task_backlog_total"
METRIC2 = "risk_alert_rate_per_hour"


# ---------------- 构造工具（模拟数据） ----------------

def write_jsonl(path: Path, rows: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                            for r in rows), encoding="utf-8")
    return path


def sim_rows(n: int = 3, span_hours: float = 1.0) -> list:
    """模拟 jsonl 源：指标在顶层，附带一个不应被采的无关数值列。"""
    total = span_hours * 3600
    return [{
        "ts": 1_750_000_000.0 + total * i / max(n - 1, 1),
        "task_backlog_total": 10 + i,
        "risk_alert_rate_per_hour": 0.5,
        "internal_retry_count": 999,      # 白名单外，必须不进输出
    } for i in range(n)]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_sqlite(path: Path, rows: list) -> Path:
    """建一个真实 sqlite（默认 journal 模式），写入后立即关连接。

    关连接是关键：只有关掉写连接，文件 sha256 才是稳定判据（WAL/journal 残留
    会干扰对比）。本库用默认 delete 模式，不产生 -wal。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE metrics (ts REAL, task_backlog_total INTEGER, "
            "risk_alert_rate_per_hour REAL, internal_retry_count INTEGER)")
        conn.executemany(
            "INSERT INTO metrics VALUES (?, ?, ?, ?)",
            [(r["ts"], r["task_backlog_total"], r["risk_alert_rate_per_hour"],
              r["internal_retry_count"]) for r in rows])
        conn.commit()
    finally:
        conn.close()
    return path


# ---------------- jsonl 源 ----------------

def test_jsonl_sampling_shape_matches_calibration_contract(tmp_path):
    """输出每行可 json.load，ts 是 float、metrics 是 dict（与 read_baseline 对齐）。"""
    source = write_jsonl(tmp_path / "src.jsonl", sim_rows(3))
    out = tmp_path / "out" / "baseline.jsonl"

    code = main(["--source", str(source), "--out", str(out), "--metric", METRIC])

    assert code == EXIT_OK
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        row = json.loads(line)
        assert isinstance(row["ts"], float)
        assert isinstance(row["metrics"], dict)
        assert set(row["metrics"]) == {METRIC}
        assert isinstance(row["metrics"][METRIC], float)


def test_jsonl_whitelist_excludes_unlisted_numeric_fields(tmp_path):
    """白名单外字段（internal_retry_count）不得被采进基线。"""
    source = write_jsonl(tmp_path / "src.jsonl", sim_rows(2))
    out = tmp_path / "baseline.jsonl"

    main(["--source", str(source), "--out", str(out), "--metric", METRIC])

    rows = read_baseline(out)
    assert [set(r["metrics"]) for r in rows] == [{METRIC}, {METRIC}]
    assert all("internal_retry_count" not in r["metrics"] for r in rows)


def test_jsonl_non_numeric_values_skipped_and_counted(tmp_path, capsys):
    """字符串/bool/null 指标值：跳过并计入 gaps，绝不强转成数值。"""
    rows = sim_rows(3)
    rows[0]["task_backlog_total"] = "12"        # 字符串数字也不强转
    rows[1]["task_backlog_total"] = True        # bool 不是数值
    rows[2]["task_backlog_total"] = None        # null 不补 0
    source = write_jsonl(tmp_path / "src.jsonl", rows)
    out = tmp_path / "baseline.jsonl"

    code = main(["--source", str(source), "--out", str(out), "--metric", METRIC])

    # 一条可用样本都没有 -> 采集不足，不写输出（不拿空文件冒充基线）
    assert code == EXIT_INSUFFICIENT
    assert not out.exists()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "skipped_non_numeric: 3" in payload["gaps"]
    assert any("no_metric_sampled" in g for g in payload["gaps"])


def test_jsonl_non_numeric_values_gap_counts_via_api(tmp_path):
    """同一场景走 API：skipped_non_numeric 计数如实，指标未被强转。"""
    rows = sim_rows(3)
    rows[0]["task_backlog_total"] = "12"
    rows[1]["task_backlog_total"] = True
    rows[2]["task_backlog_total"] = None
    rows.append({"ts": 1_750_000_000.0 + 10, "task_backlog_total": 7})
    source = write_jsonl(tmp_path / "src.jsonl", rows)

    payload = baseline_sampler.sample(
        source, [METRIC], kind=baseline_sampler.SOURCE_JSONL,
        out=tmp_path / "o.jsonl")

    assert payload["ok"] is True
    assert payload["samples_written"] == 1
    assert "skipped_non_numeric: 3" in payload["gaps"]
    assert payload["records"][0]["metrics"] == {METRIC: 7.0}


def test_jsonl_missing_metric_not_zero_filled(tmp_path):
    """某行缺某指标：该行不输出这个键（不补 0），并留下 metric_not_sampled。"""
    rows = [
        {"ts": 1_750_000_000.0, METRIC: 5},
        {"ts": 1_750_000_060.0, METRIC: 6},
    ]
    source = write_jsonl(tmp_path / "src.jsonl", rows)
    out = tmp_path / "baseline.jsonl"

    payload = baseline_sampler.sample(
        source, [METRIC, METRIC2], kind=baseline_sampler.SOURCE_JSONL, out=out)
    payload.pop("records")

    assert payload["ok"] is True                     # 有一个指标采到即成功
    assert f"metric_not_sampled: {METRIC2}" in payload["gaps"]
    main(["--source", str(source), "--out", str(out),
          "--metric", METRIC, "--metric", METRIC2])
    for row in read_baseline(out):
        assert METRIC2 not in row["metrics"]         # 不补 0、不填 None
        assert set(row["metrics"]) == {METRIC}


def test_jsonl_missing_ts_column_fails(tmp_path):
    """时间戳列缺失：无法定位时间轴 -> 采集不足，退出码 2，不写输出。"""
    source = write_jsonl(tmp_path / "src.jsonl",
                         [{"when": 1.0, METRIC: 3}, {"when": 2.0, METRIC: 4}])
    out = tmp_path / "baseline.jsonl"

    code = main(["--source", str(source), "--out", str(out), "--metric", METRIC])

    assert code == EXIT_INSUFFICIENT
    assert not out.exists()


def test_jsonl_nested_metrics_object_supported(tmp_path):
    """指标可嵌在行内 metrics 对象下（真实落盘常见形态），仍按白名单取值。"""
    source = write_jsonl(tmp_path / "src.jsonl", [
        {"ts": 1_750_000_000.0, "metrics": {METRIC: 3, "unlisted": 1}},
        {"ts": 1_750_000_060.0, "metrics": {METRIC: 4, "unlisted": 2}},
    ])
    out = tmp_path / "baseline.jsonl"

    assert main(["--source", str(source), "--out", str(out),
                 "--metric", METRIC]) == EXIT_OK
    rows = read_baseline(out)
    assert [r["metrics"] for r in rows] == [{METRIC: 3.0}, {METRIC: 4.0}]


def test_jsonl_bad_line_fails_by_default(tmp_path, capsys):
    """坏行默认即失败：不产出输出文件、退出码 2、gaps 给出行号。

    口径与下游 read_baseline 一致——同一个坏行不能在采样器里被放过、
    又在标定器里炸掉。默认跳过等于 fail-open 悄悄缩小样本。
    """
    source = tmp_path / "src.jsonl"
    source.write_text(
        json.dumps({"ts": 1_750_000_000.0, METRIC: 5}) + "\n"
        + "{坏行}\n"
        + json.dumps({"ts": 1_750_000_060.0, METRIC: 6}) + "\n",
        encoding="utf-8")
    out = tmp_path / "baseline.jsonl"

    code = main(["--source", str(source), "--out", str(out), "--metric", METRIC])

    assert code == EXIT_INSUFFICIENT
    assert not out.exists()                     # 失败不留半成品基线
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert any(g.startswith("bad_json_lines: 1") for g in payload["gaps"])
    assert any("行号 2" in g for g in payload["gaps"])


def test_jsonl_bad_line_tolerated_only_with_explicit_flag(tmp_path, capsys):
    """显式 --tolerate-bad-lines：跳过并继续，但必须在 gaps 与 stderr 双留痕。"""
    source = tmp_path / "src.jsonl"
    source.write_text(
        json.dumps({"ts": 1_750_000_000.0, METRIC: 5}) + "\n"
        + "not-a-json-line\n"
        + json.dumps({"ts": 1_750_000_060.0, METRIC: 6}) + "\n",
        encoding="utf-8")
    out = tmp_path / "baseline.jsonl"

    code = main(["--source", str(source), "--out", str(out), "--metric", METRIC,
                 "--tolerate-bad-lines"])

    assert code == EXIT_OK
    rows = read_baseline(out)
    assert [r["metrics"][METRIC] for r in rows] == [5.0, 6.0]
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert any(g.startswith("bad_json_lines: 1") for g in payload["gaps"])
    assert any("已按 --tolerate-bad-lines 跳过" in g for g in payload["gaps"])
    assert "bad_json_lines: 1" in captured.err     # 留痕到 stderr


def test_rows_with_empty_metrics_are_not_written(tmp_path):
    """无可用指标的行必须跳过并计数，绝不写 metrics 空行去虚增样本量。

    threshold_calibration.calibrate 的 sample_count 取**行数**，写空 metrics
    行等于凭空多送样本（伪造样本量），故这里连"空 dict 行"都不允许出现。
    """
    source = write_jsonl(tmp_path / "src.jsonl", [
        {"ts": 1_750_000_000.0, "other_field": 1},        # 无白名单指标
        {"ts": 1_750_000_060.0, METRIC: 7},
    ])
    out = tmp_path / "baseline.jsonl"

    payload = baseline_sampler.sample(
        source, [METRIC], kind=baseline_sampler.SOURCE_JSONL, out=out)
    records = payload.pop("records")

    assert payload["ok"] is True
    assert payload["samples_written"] == 1
    assert "rows_without_whitelisted_metric: 1" in payload["gaps"]
    assert len(records) == 1 and records[0]["metrics"] == {METRIC: 7.0}
    assert all(r["metrics"] for r in records)          # 无空 metrics 行


# ---------------- sqlite 源（只读） ----------------

def test_sqlite_readonly_source_untouched(tmp_path):
    """只读判据（可复现）：采样前后源库 sha256 一致 + 无 -journal/-wal 残留。

    选 sha256 而非 mtime：mtime 会因只读打开被系统更新（atime/mtime 粒度），
    不可作判据；sha256 直指文件内容，任何页面写回都会变。
    WAL 干扰说明：本测试库用默认 journal 模式且写连接已关闭，故无 -wal；
    若真实源库是 WAL，只读打开失败会报 sqlite_error（不回退可写模式），
    不会为了"采到数"去动源库。
    """
    source = make_sqlite(tmp_path / "db" / "metrics.sqlite", sim_rows(3))
    before = sha256_of(source)
    before_files = sorted(p.name for p in source.parent.iterdir())
    out = tmp_path / "baseline.jsonl"

    code = main(["--source", str(source), "--table", "metrics",
                 "--out", str(out), "--metric", METRIC])

    assert code == EXIT_OK
    assert sha256_of(source) == before
    assert sorted(p.name for p in source.parent.iterdir()) == before_files


def test_sqlite_connection_is_readonly_uri(tmp_path, monkeypatch):
    """连接串必须是 uri=True 的 mode=ro（对实现方式的直接断言，防被改成可写）。"""
    source = make_sqlite(tmp_path / "metrics.sqlite", sim_rows(2))
    seen: list = []
    real_connect = sqlite3.connect

    def spy(database, *args, **kwargs):
        seen.append((database, kwargs.get("uri")))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(baseline_sampler.sqlite3, "connect", spy)

    payload = baseline_sampler.sample(
        source, [METRIC], kind=baseline_sampler.SOURCE_SQLITE,
        out=tmp_path / "o.jsonl", table="metrics")
    payload.pop("records")

    assert payload["ok"] is True
    assert seen, "采样器未建立 sqlite 连接"
    for dsn, uri in seen:
        assert uri is True
        assert "mode=ro" in dsn


def test_sqlite_wide_table_values(tmp_path):
    """宽表：每指标一列，值原样采出（float/int 均转 float）。"""
    source = make_sqlite(tmp_path / "metrics.sqlite", sim_rows(3))
    out = tmp_path / "baseline.jsonl"

    code = main(["--source", str(source), "--table", "metrics", "--out", str(out),
                 "--metric", METRIC, "--metric", METRIC2])

    assert code == EXIT_OK
    rows = read_baseline(out)
    assert len(rows) == 3
    assert [r["metrics"][METRIC] for r in rows] == [10.0, 11.0, 12.0]
    assert all(r["metrics"][METRIC2] == 0.5 for r in rows)


def test_sqlite_missing_table_fails(tmp_path):
    """表不存在：ok=False + 退出码 2，gaps 点名缺哪张表。"""
    source = make_sqlite(tmp_path / "metrics.sqlite", sim_rows(2))
    out = tmp_path / "baseline.jsonl"

    code = main(["--source", str(source), "--table", "nope",
                 "--out", str(out), "--metric", METRIC])

    assert code == EXIT_INSUFFICIENT
    assert not out.exists()
    payload = baseline_sampler.sample(
        source, [METRIC], kind=baseline_sampler.SOURCE_SQLITE,
        out=out, table="nope")
    payload.pop("records")
    assert payload["ok"] is False
    assert "table_not_found: nope" in payload["gaps"]


def test_sqlite_missing_metric_column_fails(tmp_path):
    """白名单指标在表里没有对应列：如实报缺口，不猜列、不降级全采。"""
    source = make_sqlite(tmp_path / "metrics.sqlite", sim_rows(2))
    payload = baseline_sampler.sample(
        source, [METRIC, "nope_metric"], kind=baseline_sampler.SOURCE_SQLITE,
        out=tmp_path / "o.jsonl", table="metrics")
    payload.pop("records")

    assert payload["ok"] is False
    assert "metric_col_missing: nope_metric" in payload["gaps"]


def test_sqlite_long_table_with_value_col(tmp_path):
    """长表（一行一指标）：--value-col + --metric-col，白名单外指标名不采。"""
    path = tmp_path / "long.sqlite"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE samples (ts REAL, name TEXT, value REAL)")
        conn.executemany("INSERT INTO samples VALUES (?, ?, ?)", [
            (1_750_000_000.0, METRIC, 5.0),
            (1_750_000_060.0, METRIC, 6.0),
            (1_750_000_120.0, "other_metric", 999.0),   # 白名单外，不采
        ])
        conn.commit()
    finally:
        conn.close()
    out = tmp_path / "baseline.jsonl"

    code = main(["--source", str(path), "--table", "samples",
                 "--value-col", "value", "--metric-col", "name",
                 "--out", str(out), "--metric", METRIC])

    assert code == EXIT_OK
    rows = read_baseline(out)
    assert [r["metrics"] for r in rows] == [{METRIC: 5.0}, {METRIC: 6.0}]


# ---------------- 真实性接入：喂给 threshold_calibration ----------------

def test_output_is_consumable_by_threshold_calibration(tmp_path):
    """真实接入性：采样产物直接被 read_baseline 读入，且 calibrate 能算出阈值。

    只测自己不算数——这一条断言"进料口格式与标定工具逐字段对得上"，
    否则采样器写得再漂亮，标定端仍然读不进、永远出不了数。
    """
    source = write_jsonl(tmp_path / "src.jsonl", sim_rows(n=120, span_hours=48.0))
    out = tmp_path / "baseline.jsonl"

    assert main(["--source", str(source), "--out", str(out),
                 "--metric", METRIC]) == EXIT_OK

    rows = read_baseline(out)                       # 不抛 BaselineError
    assert len(rows) == 120
    result = calibrate(rows, min_samples=60, min_span_hours=24.0, safety=1.5)
    assert result["ok"] is True
    assert result["sample_count"] == 120
    assert result["span_hours"] == pytest.approx(48.0, abs=0.01)
    assert result["thresholds"][METRIC]["calibrated"] is True


# ---------------- 失败路径与 CLI 用法 ----------------

def test_missing_source_returns_code_2_and_writes_nothing(tmp_path):
    """源不存在：ok=False + 退出码 2（采集不足），不创建输出文件。"""
    out = tmp_path / "baseline.jsonl"
    code = main(["--source", str(tmp_path / "nope.jsonl"), "--out", str(out),
                 "--metric", METRIC])
    assert code == EXIT_INSUFFICIENT
    assert not out.exists()


def test_cli_requires_explicit_metric_whitelist(tmp_path, capsys):
    """不给 --metric：退出码 1（输入错误），不做"全字段都采"的默认行为。"""
    source = write_jsonl(tmp_path / "src.jsonl", sim_rows(2))
    code = main(["--source", str(source), "--out", str(tmp_path / "o.jsonl")])
    assert code == EXIT_INPUT_ERROR
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "metric" in payload["error"]


def test_cli_rejects_out_same_as_source(tmp_path, capsys):
    """--out 与 --source 同一路径：拒绝（本工具只读源，不得覆盖源）。"""
    source = write_jsonl(tmp_path / "src.jsonl", sim_rows(2))
    before = source.read_text(encoding="utf-8")
    code = main(["--source", str(source), "--out", str(source),
                 "--metric", METRIC])
    assert code == EXIT_INPUT_ERROR
    assert source.read_text(encoding="utf-8") == before
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is False


def test_cli_rejects_value_col_without_table(tmp_path):
    """jsonl 源给 --value-col：语义不明，直接判用法错误（退出码 1）。"""
    source = write_jsonl(tmp_path / "src.jsonl", sim_rows(2))
    code = main(["--source", str(source), "--out", str(tmp_path / "o.jsonl"),
                 "--value-col", "value", "--metric", METRIC])
    assert code == EXIT_INPUT_ERROR
