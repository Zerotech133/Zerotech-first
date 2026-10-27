# -*- coding: utf-8 -*-
"""§6.2 交付台账工具单测。"""
import csv
import importlib.util
import io
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("delivery_ledger", HERE / "delivery_ledger.py")
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def _write(path, rows):
    with io.open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=d.HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_init_and_exists(tmp_path):
    res = d.init_ledger(str(tmp_path), "2026-W45")
    assert res["ok"] and pathlib.Path(res["path"]).exists()
    res2 = d.init_ledger(str(tmp_path), "2026-W45")
    assert res2["ok"] is False


def test_check_good(tmp_path):
    p = tmp_path / "2026-W45.csv"
    _write(p, [{"week": "2026-W45", "date": "2026-11-02", "dept": "研究院",
                "seat": "ACAD1", "deliverable": "周报", "type": "report",
                "evidence": "deliverables/x.md", "acceptance": "A0",
                "status": "done", "note": ""}])
    r = d.check_ledger(str(p))
    assert r["ok"] is True and r["rows"] == 1


def test_check_missing_field(tmp_path):
    p = tmp_path / "bad.csv"
    _write(p, [{"week": "2026-W45", "date": "2026-11-02", "dept": "",
                "seat": "", "deliverable": "", "type": "report",
                "evidence": "", "acceptance": "", "status": "done", "note": ""}])
    r = d.check_ledger(str(p))
    assert r["ok"] is False and len(r["errors"]) >= 3


def test_summary(tmp_path):
    p = tmp_path / "s.csv"
    _write(p, [
        {"week": "W", "date": "D", "dept": "研究院", "seat": "ACAD1", "deliverable": "a",
         "type": "report", "evidence": "", "acceptance": "", "status": "done", "note": ""},
        {"week": "W", "date": "D", "dept": "研究院", "seat": "BACK1", "deliverable": "b",
         "type": "patrol", "evidence": "", "acceptance": "", "status": "done", "note": ""},
        {"week": "W", "date": "D", "dept": "情报部", "seat": "INTEL0", "deliverable": "c",
         "type": "patrol", "evidence": "", "acceptance": "", "status": "partial", "note": ""},
    ])
    s = d.summarize(str(p))
    assert s["rows"] == 3
    assert s["by_dept"]["研究院"] == 2
    assert s["by_status"]["done"] == 2
