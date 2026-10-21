# -*- coding: utf-8 -*-
"""N7 冻结时点对齐自动化的离线对抗测试（WO-QT-20261020-056 机制复用）。

先红后绿：跨日反例断言先行——先确认对未加守护的版本转红，再加守护转绿。
全程 tmp 目录合成行，不触真实数据文件。
契约：签发/消费业务日必须与全部源行 detect_date（YYYY-MM-DD）严格一致；
不一致 → REJECTED + 非零退出 + 不写出任何可被误读为成功的 manifest。
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from strategy_layer.freeze_aligned_pool import main
from strategy_layer.r0_m3_drill import synthetic_drill_rows

QUANT_ROOT = Path(__file__).resolve().parents[2]
_DAY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


def _rows(day: str) -> list[dict]:
    return synthetic_drill_rows(day)


def _write_jsonl(path: Path, rows: list[dict]) -> bytes:
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n"
                  for row in rows).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _run(tmp_path, raw_or_rows, business_day, name="src/pool.jsonl"):
    src = tmp_path / name
    if isinstance(raw_or_rows, list):
        _write_jsonl(src, raw_or_rows)
    else:
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(raw_or_rows)
    work = tmp_path / "work"
    code = main(["--source", str(src), "--business-day", business_day,
                 "--work-dir", str(work)])
    return src, work, code


def test_aligned_freeze_happy_path(tmp_path, capsys):
    """正常路径：manifest 含四要素，事件数与源行数一致，副本字节同源。"""
    src, work, code = _run(tmp_path, _rows("20261020"), "20261020")
    assert code == 0
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["freeze_basis"] == "point_in_time_freeze_of_live_pool_file"
    assert manifest["business_date"] == "20261020"
    assert _DAY_RE.fullmatch(manifest["observed_date"])
    assert manifest["event_count"] == 2 == len(
        src.read_text(encoding="utf-8").strip().split("\n"))
    assert manifest["source_sha256"] == __import__("hashlib").sha256(
        src.read_bytes()).hexdigest()
    assert (work / "source_batch.jsonl").read_bytes() == src.read_bytes()
    assert manifest["source_path"] == str(work / "source_batch.jsonl")


def test_cross_day_rejected_without_manifest(tmp_path, capsys):
    """跨日反例：business_day ≠ 源行 detect_date → REJECTED + 非零退出
    + 不写出任何 manifest（不得被误读为成功）。"""
    src, work, code = _run(tmp_path, _rows("20261020"), "20261019")
    assert code != 0
    assert not (work / "manifest.json").exists()
    err = capsys.readouterr().err
    assert "REJECTED" in err
    assert "1" in err and "2026-10-20" in err and "2026-10-19" in err


def test_partial_cross_day_reports_first_bad_row(tmp_path, capsys):
    """混合行：首个不一致行被指明（全部源行口径，非仅首行）。"""
    rows = _rows("20261020") + _rows("20261019")
    _, work, code = _run(tmp_path, rows, "20261020")
    assert code != 0
    assert not (work / "manifest.json").exists()
    err = capsys.readouterr().err
    assert "REJECTED" in err
    assert "第 3 行" in err and "2026-10-19" in err


def test_empty_source_alignment_vacuously_ok(tmp_path):
    """空源：无行可不一致（空虚真），冻结照走，event_count=0 如实记录。"""
    _, work, code = _run(tmp_path, b"", "20261020")
    assert code == 0
    manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["event_count"] == 0
    assert manifest["freeze_basis"] == "point_in_time_freeze_of_live_pool_file"


def test_invalid_business_day_rejected(tmp_path, capsys):
    """非法业务日格式 → REJECTED + 非零退出 + 无 manifest。"""
    _, work, code = _run(tmp_path, _rows("20261020"), "2026-10-20")
    assert code != 0
    assert not (work / "manifest.json").exists()
    assert "REJECTED" in capsys.readouterr().err


def test_malformed_json_row_rejected(tmp_path, capsys):
    """坏 JSON 行 → REJECTED + 非零退出 + 无 manifest（与验签层同口径）。"""
    rows = _rows("20261020")
    raw = ("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
           + "{not json}\n").encode("utf-8")
    _, work, code = _run(tmp_path, raw, "20261020")
    assert code != 0
    assert not (work / "manifest.json").exists()
    assert "REJECTED" in capsys.readouterr().err


def test_source_file_untouched(tmp_path):
    """源文件只读：冻结前后字节与 mtime 均不变。"""
    src = tmp_path / "src" / "pool.jsonl"
    raw = _write_jsonl(src, _rows("20261020"))
    before = (raw, src.stat().st_mtime)
    code = main(["--source", str(src), "--business-day", "20261020",
                 "--work-dir", str(tmp_path / "work")])
    assert code == 0
    assert (src.read_bytes(), src.stat().st_mtime) == before


def test_cli_subprocess_exit_codes(tmp_path):
    """真实 CLI 退出码：对齐 → 0；跨日 → 非零（subprocess 实证）。"""
    src = tmp_path / "src" / "pool.jsonl"
    _write_jsonl(src, _rows("20261020"))
    env = {**os.environ, "PYTHONPATH": str(QUANT_ROOT)}

    def run_cli(day, work):
        return subprocess.run(
            [sys.executable, "-m", "strategy_layer.freeze_aligned_pool",
             "--source", str(src), "--business-day", day,
             "--work-dir", str(work)],
            env=env, capture_output=True, text=True, cwd=str(tmp_path))

    ok = run_cli("20261020", tmp_path / "w_ok")
    assert ok.returncode == 0, ok.stderr
    manifest = json.loads(
        (tmp_path / "w_ok" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["event_count"] == 2

    bad = run_cli("20261019", tmp_path / "w_bad")
    assert bad.returncode != 0
    assert "REJECTED" in bad.stderr
    assert not (tmp_path / "w_bad" / "manifest.json").exists()
