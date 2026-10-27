# -*- coding: utf-8 -*-
"""baseline_manifest 测试（WO-LCS1-A4A6-20261027）。"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "quant_backtest" / "baseline_manifest.py"


def _run(*args):
    return subprocess.run([sys.executable, "-X", "utf8", str(TOOL), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=60)


def test_file_manifest_and_verify(tmp_path):
    a = tmp_path / "summary.json"
    a.write_text('{"ok": true}\n', encoding="utf-8")
    b = tmp_path / "metrics.csv"
    b.write_text("factor,ic\nMOM5,0.01\n", encoding="utf-8")
    out = tmp_path / "MANIFEST.json"
    r = _run("--out", str(out), str(a), str(b))
    assert r.returncode == 0, r.stdout + r.stderr
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["schema"] == "baseline_manifest/v1" and len(m["entries"]) == 2
    assert all(e["sha256"] for e in m["entries"])
    v = _run("--verify", str(out))
    assert v.returncode == 0, v.stdout + v.stderr
    assert json.loads(v.stdout)["ok"] is True


def test_tamper_detected(tmp_path):
    a = tmp_path / "x.txt"
    a.write_text("v1", encoding="utf-8")
    out = tmp_path / "M.json"
    assert _run("--out", str(out), str(a)).returncode == 0
    a.write_text("v2", encoding="utf-8")
    v = _run("--verify", str(out))
    assert v.returncode == 1
    assert "sha256_mismatch" in v.stdout


def test_dir_entry(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    (d / "a.txt").write_text("a", encoding="utf-8")
    (d / "b.txt").write_text("bb", encoding="utf-8")
    out = tmp_path / "M.json"
    assert _run("--out", str(out), str(d)).returncode == 0
    m = json.loads(out.read_text(encoding="utf-8"))
    e = m["entries"][0]
    assert e["type"] == "dir" and e["files"] == 2 and e["mode"] == "content"
    assert _run("--verify", str(out)).returncode == 0
