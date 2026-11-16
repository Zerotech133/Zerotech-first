# -*- coding: utf-8 -*-
"""按池对齐工具测试（2026-11-16 WO-POOL-ALIGN）。

全部在 tmp 内构造账本，不碰生产；默认路径的写回用 --apply 才发生。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "pool_align_t", REPO / "tools" / "acad1_pool_align.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pa = _load()
DATE = "2026-11-16"
TAG = "wp-acad1-20261116-"


def _seed(tmp_path, in_pool=("600000.SH",), decided=("600000.SH", "600001.SH"),
          plan_status=None):
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    dec = [{"code": c, "source": "acad1_pool_bridge",
            "evidence": [{"kind": "acad1_pool", "value": DATE + "/x"}]} for c in decided]
    (d / "d1_decisions.jsonl").write_text(
        "\n".join(json.dumps(x) for x in dec) + "\n", encoding="utf-8")
    plans = []
    for c in decided:
        bare = c.split(".")[0]
        for kind in ("stop", "take"):
            st = (plan_status or {}).get((bare, kind), "armed")
            plans.append({"plan_id": "%s%s-%s" % (TAG, bare, kind), "status": st})
    (d / "watch_plans.jsonl").write_text(
        "\n".join(json.dumps(x) for x in plans) + "\n", encoding="utf-8")
    return d, set(in_pool)


def test_dry_run_lists_only_out_of_pool(tmp_path, monkeypatch, capsys):
    data, pool = _seed(tmp_path)
    monkeypatch.setattr(pa, "DATA", data)
    monkeypatch.setattr(pa, "pool_cn", lambda day, timeout=20: (pool, "ok"))
    rc = pa.main(["--pool-date", DATE])
    out = capsys.readouterr().out
    assert rc == 0 and "dry-run" in out
    assert "002223" not in out and TAG + "600001-stop" in out
    assert TAG + "600000-stop" not in out          # 池内标的不动
    # 未写回
    rows = [json.loads(l) for l in (data / "watch_plans.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(r["status"] == "armed" for r in rows)


def test_apply_cancels_only_armed_out_of_pool(tmp_path, monkeypatch, capsys):
    data, pool = _seed(tmp_path, plan_status={("600001", "stop"): "triggered"})
    monkeypatch.setattr(pa, "DATA", data)
    monkeypatch.setattr(pa, "pool_cn", lambda day, timeout=20: (pool, "ok"))
    assert pa.main(["--pool-date", DATE, "--apply"]) == 0
    rows = {r["plan_id"]: r for r in
            (json.loads(l) for l in (data / "watch_plans.jsonl").read_text(encoding="utf-8").splitlines())}
    assert rows[TAG + "600001-take"]["status"] == "cancelled"
    assert "cancelled_reason" in rows[TAG + "600001-take"]
    assert rows[TAG + "600001-stop"]["status"] == "triggered"   # 已触发不撤
    assert rows[TAG + "600000-stop"]["status"] == "armed"       # 池内不撤
    assert list(data.glob("watch_plans.jsonl.bak_*")), "必须留备份"


def test_refused_when_pool_degraded(tmp_path, monkeypatch, capsys):
    data, _ = _seed(tmp_path)
    monkeypatch.setattr(pa, "DATA", data)
    monkeypatch.setattr(pa, "pool_cn", lambda day, timeout=20: (set(), "pool_degraded:shadow"))
    assert pa.main(["--pool-date", DATE, "--apply"]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_refused_when_pool_date_mismatch(tmp_path, monkeypatch, capsys):
    data, _ = _seed(tmp_path)
    monkeypatch.setattr(pa, "DATA", data)
    monkeypatch.setattr(pa, "pool_cn", lambda day, timeout=20: (set(), "pool_date_mismatch:2026-11-15!=2026-11-16"))
    assert pa.main(["--pool-date", DATE]) == 2
    assert "pool_date_mismatch" in capsys.readouterr().out


def test_noop_when_fully_aligned(tmp_path, monkeypatch, capsys):
    data, pool = _seed(tmp_path, in_pool=("600000.SH", "600001.SH"))
    monkeypatch.setattr(pa, "DATA", data)
    monkeypatch.setattr(pa, "pool_cn", lambda day, timeout=20: (pool, "ok"))
    assert pa.main(["--pool-date", DATE]) == 0
    assert "ALIGNED" in capsys.readouterr().out
