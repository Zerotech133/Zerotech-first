# -*- coding: utf-8 -*-
"""tools/tests/test_static_writers_safe.py — 高风险静态写入脚本安全化（wave27 支线 CT）。

覆盖（任务验收 ≥6）：
  1. morning_brief：items 按 (ts_code, ann_date) 合并——新数据优先、旧独有项保留、旧顶层未知字段保留；
  2. morning_brief：safe_write 生成写前备份（research/reports/backups）、原子写（.tmp→replace、tmp 清理）、
     sha/条数报告字段齐全，旧数据不丢；
  3. morning_brief：合并后校验失败 → 抛 SafeWriteError，目标文件保持原样（不覆盖、无 .tmp 残留）；
  4. panel_export：reports/candidates/rejected/page_issues/review.items 按 report_id 合并，
     旧独有研报追加保留、未知顶层字段保留、summary 计数按合并后重算；
  5. panel_export：export_panel 二次导出生成备份、历史研报保留、原子写落盘、返回值含合成后 summary；
  6. panel_export：校验失败（无效 v3 输入）→ PanelExportError，已有目标文件不被覆盖；
  7. 两脚本 CLI/接口不变：panel_export main export 返回 0；morning_brief main dry-run 返回 0。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_static_writers_safe.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mbp = _load("mbp_safe_under_test", ROOT / "tools" / "kb_corpus" / "morning_brief_push.py")
pe = _load("pe_safe_under_test", ROOT / "tools" / "research" / "panel_export.py")


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
def _v3(report_ids: list[str]) -> dict:
    panel = []
    for rid in report_ids:
        panel.append({
            "report_id": rid, "title": f"T{rid}", "date": "2026-11-01", "pages": 10,
            "candidate": True,
            "models": [{"model": "m1", "role_kind": "primary",
                        "roles": [{"role": "fundamental", "verdict": "pass",
                                   "page_check": {"valid_ratio": 1.0}}]}],
            "cross_model": {"role_agreement_rate": 1.0, "verdict_agreement": True,
                            "key_facts_overlap": {"overlap_ratio": 1.0}},
            "page_validation": {"issue_count": 0, "legacy_union_issue_count": 0,
                                "per_model": {}},
            "candidate_decision": {"conditions": {}, "reasons": []},
        })
    return {"generated_at": "2026-11-01T10:00:00+08:00", "schema_version": 3,
            "panel": panel,
            "candidates": [{"report_id": rid, "title": f"T{rid}"} for rid in report_ids],
            "review_checklist": [{"report_id": rid, "title": f"T{rid}", "facts": [],
                                  "opinions": [], "page_issue_count": 0} for rid in report_ids],
            "models": {"primary": "m1", "cross": "m2"},
            "conclusion": {"verdict": "pass", "passed": 1, "total": 1},
            "page_validation_summary": {"invalid_facts": 0}}


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. morning 合并语义
# ---------------------------------------------------------------------------
def test_morning_merge_preserves_old_and_unknown(tmp_path):
    old = {"generated_at": "2026-10-31", "date": "20261031",
           "unknown_top": {"keep": True},
           "items": [{"ts_code": "A.SZ", "ann_date": "20261030", "delta_pp": 1.0},
                     {"ts_code": "B.SZ", "ann_date": "20261029", "delta_pp": 2.0}]}
    new = {"generated_at": "2026-11-01", "date": "20261101",
           "items": [{"ts_code": "A.SZ", "ann_date": "20261030", "delta_pp": 9.9},
                     {"ts_code": "C.SZ", "ann_date": "20261101", "delta_pp": 3.0}]}
    merged = mbp.merge_watch_payload(old, new)
    keys = [(i["ts_code"], i["ann_date"]) for i in merged["items"]]
    assert keys == [("A.SZ", "20261030"), ("C.SZ", "20261101"), ("B.SZ", "20261029")]
    assert merged["items"][0]["delta_pp"] == 9.9      # 同主键：新数据优先
    assert merged["unknown_top"] == {"keep": True}     # 旧未知字段保留
    assert merged["date"] == "20261101"


# ---------------------------------------------------------------------------
# 2. morning safe_write：备份 + 原子写 + sha/条数
# ---------------------------------------------------------------------------
def test_morning_safe_write_backup_atomic_and_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(mbp, "BACKUP_DIR", tmp_path / "backups")
    target = tmp_path / "h18_watch.json"
    _write_json(target, {"items": [{"ts_code": "A.SZ", "ann_date": "20261030"}],
                         "keep_me": 1})
    old_bytes = target.read_bytes()
    report = mbp.safe_write_json(
        target, {"items": [{"ts_code": "B.SZ", "ann_date": "20261101"}]},
        merge_fn=mbp.merge_watch_payload, counts_fn=mbp._watch_counts)
    assert report["atomic"] is True and report["tmp_removed"] is True
    assert len(report["sha_before"]) == 64 and len(report["sha_after"]) == 64
    assert report["counts_after"] == {"items": 2, "date": None}
    backups = list((tmp_path / "backups").glob("h18_watch.json.*.bak.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == old_bytes
    data = json.loads(target.read_text(encoding="utf-8"))
    assert [i["ts_code"] for i in data["items"]] == ["B.SZ", "A.SZ"]
    assert data["keep_me"] == 1
    assert not (tmp_path / "h18_watch.json.tmp").exists()


# ---------------------------------------------------------------------------
# 3. morning 校验失败不覆盖
# ---------------------------------------------------------------------------
def test_morning_validation_failure_does_not_overwrite(tmp_path):
    target = tmp_path / "h18_watch.json"
    _write_json(target, {"items": [], "keep": "old"})
    before = target.read_bytes()

    def bad_validator(_payload):
        return ["模拟校验失败"]

    try:
        mbp.safe_write_json(target, {"items": [{"ts_code": "X"}]},
                            merge_fn=mbp.merge_watch_payload,
                            validator=bad_validator, counts_fn=mbp._watch_counts,
                            backup_dir=tmp_path / "backups")
        assert False, "应抛 SafeWriteError"
    except mbp.SafeWriteError:
        pass
    assert target.read_bytes() == before
    assert not (tmp_path / "h18_watch.json.tmp").exists()
    assert not list((tmp_path / "backups").glob("*")) if (tmp_path / "backups").exists() else True


# ---------------------------------------------------------------------------
# 4. panel 合并语义
# ---------------------------------------------------------------------------
def test_panel_merge_preserves_history_and_unknown():
    old = {"generated_at": "old", "unknown_top": "keep",
           "reports": [{"report_id": "R1", "title": "old R1"},
                       {"report_id": "R2", "title": "historical R2"}],
           "candidates": [{"report_id": "R1"}],
           "rejected": [{"report_id": "R2"}],
           "page_issues": [{"report_id": "R2", "model": "m1", "role": "r",
                            "fact": "f", "page": 99}],
           "review": {"items": [{"report_id": "R2"}], "note": "old note"},
           "summary": {"reports": 2, "candidates": 1, "rejected": 1, "extra": 7}}
    new = {"generated_at": "new",
           "reports": [{"report_id": "R1", "title": "new R1"},
                       {"report_id": "R3", "title": "new R3"}],
           "candidates": [{"report_id": "R1"}],
           "rejected": [], "page_issues": [],
           "review": {"items": [{"report_id": "R3"}], "note": "new note"},
           "summary": {"reports": 2, "candidates": 1, "rejected": 0}}
    merged = pe.merge_panel_payload(old, new)
    ids = [r["report_id"] for r in merged["reports"]]
    assert ids == ["R1", "R3", "R2"]                    # 新顺序 + 旧独有追加
    assert merged["reports"][0]["title"] == "new R1"    # 同主键新优先
    assert merged["unknown_top"] == "keep"              # 旧未知顶层字段保留
    assert merged["review"]["note"] == "new note"       # 已知字段新优先
    assert merged["review"]["items"][-1]["report_id"] == "R2"   # 旧复核项保留
    assert merged["summary"]["reports"] == 3            # 计数按合并后重算
    assert merged["summary"]["candidates"] == 1
    assert merged["summary"]["extra"] == 7              # 旧 summary 未知字段保留


# ---------------------------------------------------------------------------
# 5. panel export：备份 + 原子写 + 历史保留
# ---------------------------------------------------------------------------
def test_panel_export_backup_atomic_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "BACKUP_DIR", tmp_path / "backups")
    in1, in2 = tmp_path / "v3_1.json", tmp_path / "v3_2.json"
    out = tmp_path / "report_panel.json"
    _write_json(in1, _v3(["R1"]))
    _write_json(in2, _v3(["R1", "R3"]))
    first = pe.export_panel(in1, out, check=True)
    assert [r["report_id"] for r in first["reports"]] == ["R1"]
    old_bytes = out.read_bytes()
    second = pe.export_panel(in2, out, check=True)
    ids = [r["report_id"] for r in second["reports"]]
    assert ids == ["R1", "R3"]                     # 旧 R1 保留（新数据覆盖）+ 新 R3
    assert second["summary"]["reports"] == 2
    backups = list((tmp_path / "backups").glob("report_panel.json.*.bak.json"))
    assert len(backups) >= 1 and old_bytes in [b.read_bytes() for b in backups]
    assert not (tmp_path / "report_panel.json.tmp").exists()


# ---------------------------------------------------------------------------
# 6. panel 校验失败不覆盖
# ---------------------------------------------------------------------------
def test_panel_validation_failure_keeps_target(tmp_path, monkeypatch):
    monkeypatch.setattr(pe, "BACKUP_DIR", tmp_path / "backups")
    out = tmp_path / "report_panel.json"
    _write_json(out, {"reports": [{"report_id": "OLD"}], "sentinel": True})
    before = out.read_bytes()
    bad_in = tmp_path / "bad_v3.json"
    _write_json(bad_in, {"schema_version": 3})       # 无 panel → reports 为空 → 校验失败
    try:
        pe.export_panel(bad_in, out, check=True)
        assert False, "应抛 PanelExportError"
    except pe.PanelExportError:
        pass
    assert out.read_bytes() == before


# ---------------------------------------------------------------------------
# 7. CLI/接口不变
# ---------------------------------------------------------------------------
def test_cli_interfaces_unchanged(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pe, "BACKUP_DIR", tmp_path / "backups")
    v3 = tmp_path / "v3.json"
    out = tmp_path / "panel.json"
    _write_json(v3, _v3(["R1"]))
    rc = pe.main(["export", "--input", str(v3), "--out", str(out)])
    assert rc == 0, capsys.readouterr().err
    assert json.loads(out.read_text(encoding="utf-8"))["reports"][0]["report_id"] == "R1"
    capsys.readouterr()                              # 丢弃 panel_export 的 stdout，避免混入后续 JSON
    # morning_brief：dry-run 接口不变（不发送、不进 refresh）
    cards = tmp_path / "cards.json"
    watch = tmp_path / "watch.json"
    _write_json(cards, {"cards": [{"q": "Q", "a": "A", "topic": "t", "source": "s"}]})
    _write_json(watch, {"date": "20261101", "items": [{"ts_code": "A", "ann_date": "20261101"}]})
    monkeypatch.setattr(mbp, "CARDS", cards)
    monkeypatch.setattr(mbp, "WATCH", watch)
    monkeypatch.setattr(mbp, "H14", tmp_path / "h14_missing.json")
    assert mbp.main([]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["mode"] == "dry-run" and printed["sent"] is False
