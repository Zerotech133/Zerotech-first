"""A4 汇总与 tmp_path 文件层测试；不执行真实 inspect 或报告写入。"""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def audit_module(monkeypatch):
    stub = types.ModuleType("strategy_layer.a4_readonly_probe")
    stub.inspect = lambda *args, **kwargs: pytest.fail("不应调用真实 inspect")
    monkeypatch.setitem(sys.modules, stub.__name__, stub)
    path = Path(__file__).resolve().parents[1] / "a4_backfill_audit_probe.py"
    spec = importlib.util.spec_from_file_location("a4_backfill_totals_readonly_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def summarize_totals(audit_module):
    return audit_module.summarize_totals


def event(code, count=1, dt="2026-09-01"):
    return {"code": code, "tag": "2买", "dt": dt, "count": count}


def day(added=(), removed=(), *, archive_exists=True, rec_status="COMPARED",
        pool_only=0, diff_stocks=0):
    added = list(added)
    removed = list(removed)
    layer = {"rows_delta_current_minus_meta_n": None}
    if archive_exists:
        layer.update({
            "added_events_vs_archive": added,
            "added_event_total": sum(e["count"] for e in added),
            "removed_events_vs_archive": removed,
            "removed_event_total": sum(e["count"] for e in removed),
            "added_event_dt_month_distribution": {
                m: sum(e["count"] for e in added if e["dt"].startswith(m))
                for m in sorted({e["dt"][:7] for e in added})},
            "added_events_all_before_archive_day": True if added else None,
        })
    rec = {"status": rec_status}
    if rec_status == "COMPARED":
        rec.update({"pool_only_event_total": pool_only,
                    "archive_only_event_total": 0,
                    "difference_stock_count": diff_stocks,
                    "pool_only_dt_month_distribution": {"2026-09": pool_only} if pool_only else {},
                    "pool_only_dt_all_before_archive_day": True if pool_only else None})
    return {"archive_exists": archive_exists, "file_layer": layer, "reconciliation": rec}


def test_invalid_pool_line_is_not_silently_skipped(audit_module, monkeypatch, tmp_path):
    pool = tmp_path / "pool.jsonl"
    pool.write_text('{"detect_date":"2026-10-13","code":"A.SZ","type":"2买","dt":"2026-09-01"}\n{bad json}\n', encoding="utf-8")
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool])
    with pytest.raises(ValueError, match="pool.jsonl:2: invalid JSON"):
        audit_module.load_current_cn_batch("2026-10-13")


@pytest.mark.parametrize("row", [
    [], None, 42, "text",
    {"detect_date": "2026-10-13", "code": "", "type": "2买", "dt": "2026-09-01"},
    {"detect_date": "2026-10-13", "code": "A.SZ", "type": "", "dt": "2026-09-01"},
    {"detect_date": "2026-10-13", "code": "A.SZ", "type": "2买", "dt": ""},
    {"detect_date": "2026-02-30", "code": "A.SZ", "type": "2买", "dt": "2026-09-01"},
    {"detect_date": "2026-10-XX", "code": "A.SZ", "type": "2买", "dt": "2026-09-01"},
    {"detect_date": "20261013", "code": "A.SZ", "type": "2买", "dt": "2026-09-01"},
    {"detect_date": None, "code": "A.SZ", "type": "2买", "dt": "2026-09-01"},
    {"detect_date": "2026-10-13", "code": None, "type": "2买", "dt": "2026-09-01"},
    {"detect_date": "2026-10-13", "code": "   ", "type": "2买", "dt": "2026-09-01"},
])
def test_invalid_pool_record_prevents_false_comparison(audit_module, monkeypatch, tmp_path, row):
    pool = tmp_path / "pool.jsonl"
    pool.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    archive = tmp_path / "todays_20261013.json"
    archive.write_text(json.dumps({"stocks": [], "strategies": {"chanlun": {"n": 0}}}),
                       encoding="utf-8")
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool])
    monkeypatch.setattr(audit_module, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(audit_module, "inspect", lambda *args, **kwargs: {})
    result = audit_module.audit_date("20261013")
    assert result["file_layer"]["status"] == "NOT_COMPARED"
    assert "pool.jsonl:1" in result["file_layer"]["error"]
    assert audit_module.summarize_totals({"20261013": result})["status"] == "NOT_COMPARED"


@pytest.mark.parametrize("day_iso", ["2026-02-30", "20261013", "2026-10-XX"])
def test_invalid_target_date_is_rejected(audit_module, monkeypatch, tmp_path, day_iso):
    pool = tmp_path / "pool.jsonl"
    pool.write_text('', encoding="utf-8")
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool])
    with pytest.raises(ValueError, match="invalid target detect_date"):
        audit_module.load_current_cn_batch(day_iso)


def test_empty_pool_file_list_is_not_zero(audit_module, monkeypatch, tmp_path):
    archive = tmp_path / "todays_20261013.json"
    archive.write_text(json.dumps({"stocks": [], "strategies": {"chanlun": {"n": 0}}}),
                       encoding="utf-8")
    monkeypatch.setattr(audit_module, "POOL_FILES", [])
    monkeypatch.setattr(audit_module, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(audit_module, "inspect", lambda *args, **kwargs: {})
    with pytest.raises(ValueError, match="POOL_FILES is empty"):
        audit_module.load_current_cn_batch("2026-10-13")
    result = audit_module.audit_date("20261013")
    assert result["file_layer"]["status"] == "NOT_COMPARED"
    assert audit_module.summarize_totals({"20261013": result})["file_layer_added_event_total"] is None


def test_corrupt_archive_file_layer_is_not_compared(audit_module, monkeypatch, tmp_path):
    archive = tmp_path / "todays_20261013.json"
    archive.write_text("{bad json}", encoding="utf-8")
    monkeypatch.setattr(audit_module, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(audit_module, "POOL_FILES", [])
    def rejected_inspect(*args, **kwargs):
        raise ValueError("invalid archive")
    monkeypatch.setattr(audit_module, "inspect", rejected_inspect)
    result = audit_module.audit_date("20261013")
    assert result["file_layer"]["status"] == "NOT_COMPARED"
    assert "added_event_total" not in result["file_layer"]
    assert audit_module.summarize_totals({"20261013": result})["status"] == "NOT_COMPARED"


@pytest.mark.parametrize("deep_file", ["archive", "pool"])
def test_deep_file_json_is_not_compared(audit_module, monkeypatch, tmp_path, deep_file):
    archive = tmp_path / "todays_20261013.json"
    pool = tmp_path / "pool.jsonl"
    archive.write_text('{"stocks":[],"strategies":{"chanlun":{"n":0}}}', encoding="utf-8")
    pool.write_text("", encoding="utf-8")
    depth = sys.getrecursionlimit() * 5
    nested_json = "[" * depth + "0" + "]" * depth
    (archive if deep_file == "archive" else pool).write_text(nested_json, encoding="utf-8")
    monkeypatch.setattr(audit_module, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool])
    monkeypatch.setattr(audit_module, "inspect", lambda *args, **kwargs: {})
    result = audit_module.audit_date("20261013")
    assert result["file_layer"]["status"] == "NOT_COMPARED"
    assert "RecursionError" in result["file_layer"]["error"]
    assert "added_event_total" not in result["file_layer"]
    assert audit_module.summarize_totals({"20261013": result})["status"] == "NOT_COMPARED"


def test_missing_expected_pool_is_not_zero(audit_module, monkeypatch, tmp_path):
    present = tmp_path / "present.jsonl"
    present.write_text('', encoding="utf-8")
    missing = tmp_path / "missing.jsonl"
    archive = tmp_path / "todays_20261013.json"
    archive.write_text(json.dumps({"stocks": [], "strategies": {"chanlun": {"n": 0}}}),
                       encoding="utf-8")
    monkeypatch.setattr(audit_module, "POOL_FILES", [present, missing])
    monkeypatch.setattr(audit_module, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(audit_module, "inspect", lambda *args, **kwargs: {})
    with pytest.raises(FileNotFoundError, match="missing.jsonl"):
        audit_module.load_current_cn_batch("2026-10-13")
    result = audit_module.audit_date("20261013")
    assert result["file_layer"]["status"] == "NOT_COMPARED"
    assert "missing.jsonl" in result["file_layer"]["error"]
    assert "current_batch_rows" not in result["file_layer"]
    totals = audit_module.summarize_totals({"20261013": result})
    assert totals["status"] == "NOT_COMPARED"
    assert totals["file_layer_added_event_total"] is None
    assert totals["days_with_backfill"] is None


@pytest.mark.parametrize("doc", [
    [],
    {"strategies": {}, "stocks": {}},
    {"strategies": {}, "stocks": [None]},
    {"strategies": {}, "stocks": [{"code": "A.SZ", "sources": {"s": "chanlun"}}]},
    {"strategies": {}, "stocks": [{"code": "A.SZ", "sources": [None]}]},
    {"strategies": {}, "stocks": [{"code": "A.SZ", "sources": [{"s": "chanlun", "tag": "2买", "dt": None}]}]},
    {"strategies": {}, "stocks": [{"code": "A.SZ", "sources": [{"s": "chanlun", "tag": "2买", "dt": "2026-02-30"}]}]},
    {"strategies": {}, "stocks": [{"code": "A.SZ", "sources": [{"s": "chanlun", "tag": [], "dt": "2026-09-01"}]}]},
    {"strategies": {}, "stocks": [{"code": None, "sources": [{"s": "chanlun", "tag": "2买", "dt": "2026-09-01"}]}]},
    {"strategies": {}, "stocks": [{"code": "", "sources": [{"s": "chanlun", "tag": "2买", "dt": "2026-09-01"}]}]},
    {"strategies": {}, "stocks": [{"code": "A.SZ", "sources": [{"s": "chanlun", "tag": "", "dt": "2026-09-01"}]}]},
    {"strategies": {}, "stocks": [{"code": "A.SZ", "sources": [{"s": "chanlun", "tag": "2买", "dt": ""}]}]},
    {"strategies": {}, "stocks": [{"code": "A.SZ", "sources": None}]},
    {"strategies": {}, "stocks": [{"code": "A.SZ"}]},
    {"strategies": {}, "stocks": [{"code": "A.SZ", "sources": [{"s": "", "tag": "2买", "dt": "2026-09-01"}]}]},
    {"strategies": {}, "stocks": [{"code": "A.SZ", "sources": [{"tag": "2买", "dt": "2026-09-01"}]}]},
    {"strategies": {}},
])
def test_bad_archive_records_are_not_compared(audit_module, monkeypatch, tmp_path, doc):
    archive = tmp_path / "todays_20261013.json"
    archive.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    pool = tmp_path / "pool.jsonl"
    pool.write_text('', encoding="utf-8")
    monkeypatch.setattr(audit_module, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool])
    monkeypatch.setattr(audit_module, "inspect", lambda *args, **kwargs: {})
    result = audit_module.audit_date("20261013")
    assert result["file_layer"]["status"] == "NOT_COMPARED"
    assert "added_event_total" not in result["file_layer"]
    totals = audit_module.summarize_totals({"20261013": result})
    assert totals["status"] == "NOT_COMPARED"
    assert totals["file_layer_added_event_total"] is None
    assert totals["file_layer_removed_event_total"] is None


def test_bad_date_in_matching_pool_and_archive_event_is_not_compared(
        audit_module, monkeypatch, tmp_path):
    pool = tmp_path / "pool.jsonl"
    pool.write_text(json.dumps({"detect_date": "2026-10-13", "code": "A.SZ",
                                "type": "2买", "dt": "2026-02-30"}) + "\n", encoding="utf-8")
    archive = tmp_path / "todays_20261013.json"
    archive.write_text(json.dumps({"stocks": [{"code": "A.SZ", "sources": [
        {"s": "chanlun", "tag": "2买", "dt": "2026-02-30"}]}]}), encoding="utf-8")
    monkeypatch.setattr(audit_module, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool])
    monkeypatch.setattr(audit_module, "inspect", lambda *args, **kwargs: {})
    result = audit_module.audit_date("20261013")
    assert result["file_layer"]["status"] == "NOT_COMPARED"
    assert "added_event_total" not in result["file_layer"]
    assert audit_module.summarize_totals({"20261013": result})["status"] == "NOT_COMPARED"


def test_valid_other_date_and_non_cn_rows_keep_cn_batch_scope(audit_module, monkeypatch, tmp_path):
    pool = tmp_path / "pool.jsonl"
    pool.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in [
        {"detect_date": "2026-10-12", "code": "A.SZ", "type": "2买", "dt": "2026-09-01"},
        {"detect_date": "2026-10-13", "code": "B.HK", "type": "2买", "dt": "2026-09-01"},
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool])
    batch = audit_module.load_current_cn_batch("2026-10-13")
    assert batch["rows"] == 0
    assert batch["per_file"]["pool.jsonl"]["rows"] == 0


def test_valid_file_layer_still_compares(audit_module, monkeypatch, tmp_path):
    pool = tmp_path / "pool.jsonl"
    pool.write_text(json.dumps({"detect_date": "2026-10-13", "code": "A.SZ",
                                "type": "2买", "dt": "2026-09-01"}) + "\n", encoding="utf-8")
    archive = tmp_path / "todays_20261013.json"
    archive.write_text(json.dumps({"strategies": {"chanlun": {"n": 0}},
                                   "stocks": []}), encoding="utf-8")
    monkeypatch.setattr(audit_module, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool])
    monkeypatch.setattr(audit_module, "inspect", lambda *args, **kwargs: {})
    result = audit_module.audit_date("20261013")
    assert result["file_layer"]["added_event_total"] == 1
    assert result["file_layer"]["current_batch_rows"] == 1
    assert audit_module.summarize_totals({"20261013": result})["status"] == "COMPARED"


def test_pool_changes_after_first_read_is_not_compared(audit_module, monkeypatch, tmp_path):
    pool = tmp_path / "pool.jsonl"
    pool.write_text('{"detect_date":"2026-10-13","code":"A.SZ","type":"2买","dt":"2026-09-01"}\n',
                    encoding="utf-8")
    archive = tmp_path / "todays_20261013.json"
    archive.write_text('{"stocks":[],"strategies":{"chanlun":{"n":0}}}', encoding="utf-8")
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool])
    monkeypatch.setattr(audit_module, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(audit_module, "inspect", lambda *args, **kwargs: {})
    original = audit_module.load_archive_chanlun

    def changed_between_reads(path, snapshots=None):
        pool.write_text('', encoding="utf-8")
        return original(path, snapshots)

    monkeypatch.setattr(audit_module, "load_archive_chanlun", changed_between_reads)
    result = audit_module.audit_date("20261013")
    assert result["file_layer"]["status"] == "NOT_COMPARED"
    assert "file changed after read" in result["file_layer"]["error"]
    assert audit_module.summarize_totals({"20261013": result})["status"] == "NOT_COMPARED"


def test_second_read_with_different_content_is_not_compared(audit_module, monkeypatch, tmp_path):
    pool = tmp_path / "pool.jsonl"
    pool.write_text('{"detect_date":"2026-10-13","code":"A.SZ","type":"2买","dt":"2026-09-01"}\n',
                    encoding="utf-8")
    archive = tmp_path / "todays_20261013.json"
    archive.write_text('{"stocks":[],"strategies":{"chanlun":{"n":0}}}', encoding="utf-8")
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool])
    monkeypatch.setattr(audit_module, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(audit_module, "inspect", lambda *args, **kwargs: {})
    original = Path.read_bytes
    reads = 0

    def changed_second_read(path):
        nonlocal reads
        data = original(path)
        if path == pool:
            reads += 1
            if reads == 2:
                return data.replace(b"A.SZ", b"B.SZ")
        return data

    monkeypatch.setattr(Path, "read_bytes", changed_second_read)
    result = audit_module.audit_date("20261013")
    assert result["file_layer"]["status"] == "NOT_COMPARED"
    assert "file content changed after read" in result["file_layer"]["error"]
    assert audit_module.summarize_totals({"20261013": result})["file_layer_added_event_total"] is None


def test_truncated_read_is_not_compared(audit_module, monkeypatch, tmp_path):
    pool = tmp_path / "pool.jsonl"
    pool.write_text('{}\n', encoding="utf-8")
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool])
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path)[:1] if path == pool
                        else original(path))
    with pytest.raises(ValueError, match="file changed during read"):
        audit_module.load_current_cn_batch("2026-10-13")


def test_duplicate_pool_path_is_not_double_counted(audit_module, monkeypatch, tmp_path):
    pool = tmp_path / "pool.jsonl"
    pool.write_text('{"detect_date":"2026-10-13","code":"A.SZ","type":"2买","dt":"2026-09-01"}\n',
                    encoding="utf-8")
    archive = tmp_path / "todays_20261013.json"
    archive.write_text('{"stocks":[],"strategies":{"chanlun":{"n":0}}}', encoding="utf-8")
    monkeypatch.setattr(audit_module, "POOL_FILES", [pool, pool])
    monkeypatch.setattr(audit_module, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(audit_module, "inspect", lambda *args, **kwargs: {})
    result = audit_module.audit_date("20261013")
    assert result["file_layer"]["status"] == "NOT_COMPARED"
    assert "duplicate pool file" in result["file_layer"]["error"]
    assert audit_module.summarize_totals({"20261013": result})["status"] == "NOT_COMPARED"


def test_whole_new_symbols_count_in_full_file_total(summarize_totals):
    intersection = [event(f"OLD{i}.SZ") for i in range(4)]
    whole_new = [event(f"NEW{i}.SZ") for i in range(6)] + [event("NEW0.SZ")]
    totals = summarize_totals({"20261013": day(intersection + whole_new,
                                                   pool_only=4, diff_stocks=4)})
    assert totals["status"] == "COMPARED"
    assert totals["file_layer_added_event_total"] == 11  # 交集 4 + 整票新增 7
    assert totals["pool_only_event_total"] == 4  # legacy 仅交集
    assert totals["difference_stock_count_total"] == 4
    assert totals["affected_symbols"] == sorted({e["code"] for e in intersection + whole_new})
    assert totals["affected_symbol_count"] == 10
    assert totals["days_with_backfill"] == 1
    assert totals["days_with_backfill_list"] == ["20261013"]
    assert totals["file_layer_added_event_dt_month_distribution_combined"] == {"2026-09": 11}


def test_zero_day_and_empty_input(summarize_totals):
    totals = summarize_totals({"20261009": day()})
    assert totals["status"] == "COMPARED"
    assert totals["days_with_backfill"] == 0
    assert totals["file_layer_added_event_total"] == 0
    assert totals["file_layer_removed_event_total"] == 0
    assert totals["affected_symbols"] == []
    assert totals["file_layer_added_events_all_before_archive_day_every_day"] is None
    empty = summarize_totals({})
    assert empty["status"] == "NOT_COMPARED"
    assert empty["days_with_backfill"] is None
    assert empty["file_layer_added_event_total"] is None
    assert empty["pool_only_event_total"] is None


def test_missing_archive_is_not_reported_green(summarize_totals):
    totals = summarize_totals({"20261009": day(),
                               "20261012": day(archive_exists=False,
                                               rec_status="NOT_COMPARED")})
    assert totals["status"] == "NOT_COMPARED"
    assert totals["file_layer_not_compared_days"] == ["20261012"]
    assert totals["reconciliation_not_compared_days"] == ["20261012"]
    assert totals["days_with_backfill"] is None
    assert totals["file_layer_added_event_total"] is None
    assert totals["file_layer_removed_event_total"] is None
    assert totals["affected_symbols"] is None
    assert totals["pool_only_event_total"] is None


def test_added_and_removed_coexist_without_netting(summarize_totals):
    totals = summarize_totals({"20261013": day([event("A.SZ", 2)],
                                                   [event("A.SZ"), event("B.SH", 3)])})
    assert totals["file_layer_added_event_total"] == 2
    assert totals["file_layer_removed_event_total"] == 4
    assert totals["affected_symbols"] == ["A.SZ", "B.SH"]
    assert totals["affected_symbol_count"] == 2
    assert totals["days_with_backfill_list"] == ["20261013"]
    assert totals["pool_only_event_total"] == 0


def test_file_layer_backfill_when_reconciliation_not_compared(summarize_totals):
    totals = summarize_totals({"20261013": day([event("NEW.SZ")],
                                                   rec_status="NOT_COMPARED")})
    assert totals["status"] == "COMPARED"
    assert totals["days_with_backfill"] == 1
    assert totals["file_layer_added_event_total"] == 1
    assert totals["reconciliation_not_compared_days"] == ["20261013"]
    assert totals["pool_only_event_total"] is None
