# -*- coding: utf-8 -*-
"""席位评测跑批器测试（WO-EVAL-SEATS-20261027）。"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "agent_platform" / "tools" / "seat_eval.py"
GOLDEN = REPO / "agent_platform" / "evals" / "seat_golden.json"


def _write_golden(path: Path) -> None:
    data = {
        "schema": "seat_golden/v1",
        "seats": {
            "T1": {
                "label": "测试席",
                "cases": [
                    {"id": "ok", "kind": "cmd", "args": ["-c", "print('hello')"],
                     "expect_rc": 0, "contains": ["hello"]},
                    {"id": "bad", "kind": "cmd", "args": ["-c", "print('x')"],
                     "expect_rc": 0, "contains": ["y"]},
                    {"id": "file", "kind": "file", "path": "README.md", "contains": "#"},
                ],
            },
            "T2": {
                "label": "测试席2",
                "cases": [
                    {"id": "ok2", "kind": "cmd", "args": ["-c", "print('z')"],
                     "expect_rc": 0, "contains": ["z"]},
                ],
            },
        },
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _run(root: Path, golden: Path, *args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), "--root", str(root),
         "--golden", str(golden), "--no-write", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def test_runner_scoring(tmp_path):
    golden = tmp_path / "golden.json"
    _write_golden(golden)
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    r = _run(tmp_path, golden, "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    rep = json.loads(r.stdout)
    assert rep["overall"] == {"passed": 3, "total": 4, "rate": 0.75}
    t1 = next(s for s in rep["seats"] if s["seat"] == "T1")
    assert t1["passed"] == 2 and t1["total"] == 3
    assert [c["id"] for c in t1["cases"] if not c["ok"]] == ["bad"]


def test_seat_filter(tmp_path):
    golden = tmp_path / "golden.json"
    _write_golden(golden)
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    r = _run(tmp_path, golden, "--seat", "T2", "--json")
    assert r.returncode == 0, r.stdout
    rep = json.loads(r.stdout)
    assert [s["seat"] for s in rep["seats"]] == ["T2"]
    assert rep["overall"]["passed"] == 1


def test_strict_exit_nonzero(tmp_path):
    golden = tmp_path / "golden.json"
    _write_golden(golden)
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    r = _run(tmp_path, golden, "--strict")
    assert r.returncode == 1


def test_actual_golden_shape():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert data["schema"] == "seat_golden/v1"
    seats = data["seats"]
    for seat in ("ACAD1", "STRAT1", "BACK1", "ARCH1"):
        assert seat in seats
        cases = seats[seat]["cases"]
        assert len(cases) >= 3, seat
        for c in cases:
            assert c["kind"] in ("cmd", "file"), c
            assert c.get("id")
