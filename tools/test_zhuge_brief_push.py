# -*- coding: utf-8 -*-
"""zhuge_brief_push dry-run 契约：默认不发送、只校验快照。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "tools" / "zhuge_brief_push.py"


def _load():
    spec = importlib.util.spec_from_file_location("_zhuge_push_test", str(MOD))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _snap(tmp_path: Path) -> Path:
    p = tmp_path / "zhuge_brief_20261029-085000.md"
    p.write_text("# 诸葛亮证据快照（双锚取数，2026-10-29T08:50:00+0800）\n\n- ok=True\n", encoding="utf-8")
    return p


def test_latest_snapshot(tmp_path):
    mod = _load()
    p1 = tmp_path / "zhuge_brief_20261029-085000.md"
    p1.write_text("# 诸葛亮证据快照\n", encoding="utf-8")
    p2 = tmp_path / "zhuge_brief_20261029-175000.md"
    p2.write_text("# 诸葛亮证据快照\n", encoding="utf-8")
    assert mod.latest_snapshot(tmp_path) == p2


def test_dry_run_no_send(tmp_path, capsys):
    mod = _load()
    p = _snap(tmp_path)
    rc = mod.main(["--snapshot", str(p)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["mode"] == "dry-run" and out["sent"] is False
    assert out["chars"] > 0


def test_reject_bad_snapshot(tmp_path):
    mod = _load()
    p = tmp_path / "bad.md"
    p.write_text("not a zhuge snapshot", encoding="utf-8")
    rc = mod.main(["--snapshot", str(p)])
    assert rc == 2
