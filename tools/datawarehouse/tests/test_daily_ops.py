# -*- coding: utf-8 -*-
"""DATA-05/06 日常证据生成器契约测试（写 tmp 不污染主仓）。"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_quality_daily(tmp_path):
    out = tmp_path / "quality_daily_20990101.json"
    r = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools/datawarehouse/quality_daily.py"),
                        "--out", str(out), "--date", "20990101"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, r.stderr[-400:]
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["date"] == "20990101"
    assert "checks" in d and len(d["checks"]) >= 5
    assert "summary" in d


def test_etl_dryrun_daily(tmp_path):
    out = tmp_path / "etl_dryrun.jsonl"
    r = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools/datawarehouse/etl_dryrun_daily.py"),
                        "--out", str(out)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, r.stderr[-400:]
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert lines and all(x.get("dry_run_only") is True for x in lines)
    assert all(x.get("executed") is not True for x in lines)
