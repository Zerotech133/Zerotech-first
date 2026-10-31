# -*- coding: utf-8 -*-
"""evidence_chain_audit：P0 evidence 链式审计器离线单测（不依赖生产 evidence）。

覆盖：canonical 哈希/HMAC、链式 ledger（genesis/prev_hash/head）、坏文件、run_id 重复、
signature 缺失/被改、无 key unknown、时间倒序/mtime 回拨/缺口、内嵌链字段不符、
baseline 篡改/删行/中间插入/新增追加/不可读、auto 基线、CLI 退出码与 JSON/Markdown 渲染。
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "evidence_chain_audit.py"
KEY = b"k" * 32


def _load():
    name = "evidence_chain_audit_under_test"
    spec = importlib.util.spec_from_file_location(name, TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # 兼容 dataclass/PEP 563 的模块注册约定
    spec.loader.exec_module(mod)
    return mod


def _sign(record, key=KEY):
    body = {k: v for k, v in record.items() if k != "signature"}
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _record(run_id="run-0001", kind="orchestrate", task="T-EV",
            created="2026-10-31T10:00:00+08:00", finalized=None, extra=None):
    record = {"schema": "legion-evidence/v1", "run_id": run_id, "kind": kind,
              "task": task, "agent": "m1-kpi", "created_at": created,
              "events": [{"at": created, "event": "hermes_plan", "payload": {}}]}
    if finalized:
        record["finalized_at"] = finalized
    record.update(extra or {})
    return record


def _setup(tmp_path, key=KEY):
    directory = tmp_path / "evidence"
    directory.mkdir()
    key_path = tmp_path / "cap.key"
    key_path.write_bytes(key)
    return directory, key_path


def _write(directory, name, record, key=KEY):
    record = dict(record)
    if key:
        record["signature"] = _sign(record, key)
    path = directory / name
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def _audit(mod, directory, key_path, **kwargs):
    kwargs.setdefault("baseline", "none")
    kwargs.setdefault("repo", directory.parent)
    args = {"directory": directory, "key_path": key_path}
    args.update(kwargs)
    return mod.collect(**args)


def _baseline_from(report):
    return {"schema": report["schema"], "dir": report["dir"],
            "chain": report["chain"], "entries": report["entries"]}


def _write_baseline(path, report):
    path.write_text(json.dumps(_baseline_from(report), ensure_ascii=False),
                    encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# canonical 哈希 / signature
# ---------------------------------------------------------------------------
def test_canonical_hash_ignores_signature_only(tmp_path):
    mod = _load()
    record = _record()
    payload = mod.canonical_payload(record)
    first = mod.record_hash(record)
    signed = dict(record, signature="deadbeef")
    assert mod.canonical_payload(signed) == payload
    assert mod.record_hash(signed) == first
    changed = dict(record, task="T-OTHER")
    assert mod.record_hash(changed) != first


def test_verify_signature_states(tmp_path):
    mod = _load()
    record = _record()
    signed = dict(record, signature=_sign(record))
    assert mod.verify_signature(signed, KEY) == "true"
    assert mod.verify_signature(dict(record, signature="bad"), KEY) == "false"
    assert mod.verify_signature(record, KEY) == "absent"
    assert mod.verify_signature(signed, b"") == "unknown"


# ---------------------------------------------------------------------------
# 基础扫描 / 链
# ---------------------------------------------------------------------------
def test_valid_chain_pass(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    _write(directory, "run-0001.json", _record("run-0001"))
    _write(directory, "run-0002.json", _record(
        "run-0002", created="2026-10-31T11:00:00+08:00",
        finalized="2026-10-31T11:05:00+08:00"))
    report = _audit(mod, directory, key_path,
                    now=_ts("2026-10-31T12:00:00+08:00"))
    assert report["verdict"] == "pass"
    assert report["counts"]["records"] == 2
    assert report["counts"]["signature_verified"] == 2
    assert report["chain"]["length"] == 2
    assert report["chain"]["self_consistent"] is True
    assert report["chain"]["anchored"] is False
    assert report["entries"][0]["prev_hash"] == mod.GENESIS_HASH
    assert report["entries"][1]["prev_hash"] == report["entries"][0]["record_hash"]
    assert report["chain"]["head_hash"] == report["entries"][-1]["record_hash"]
    assert "无基线" in " ".join(report["notes"])


def _ts(text):
    import datetime as dt
    return dt.datetime.fromisoformat(text)


def test_sorted_by_created_at_not_filename(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    _write(directory, "zzz.json", _record(
        "run-late", created="2026-10-31T12:00:00+08:00"))
    _write(directory, "aaa.json", _record(
        "run-early", created="2026-10-31T09:00:00+08:00"))
    report = _audit(mod, directory, key_path)
    assert [entry["run_id"] for entry in report["entries"]] == ["run-early", "run-late"]


def test_empty_dir_unknown(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    report = _audit(mod, directory, key_path)
    assert report["verdict"] == "unknown"


def test_missing_dir_unknown(tmp_path):
    mod = _load()
    report = mod.collect(directory=tmp_path / "nope", baseline="none")
    assert report["verdict"] == "unknown"


# ---------------------------------------------------------------------------
# 坏文件 / run_id / signature
# ---------------------------------------------------------------------------
def test_bad_json_file_fails(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    _write(directory, "good.json", _record("run-good"))
    (directory / "bad.json").write_text("{not-json", encoding="utf-8")
    report = _audit(mod, directory, key_path)
    assert report["verdict"] == "fail"
    assert report["counts"]["bad_files"] == 1


def test_non_object_file_fails(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    (directory / "list.json").write_text("[]", encoding="utf-8")
    report = _audit(mod, directory, key_path)
    assert report["verdict"] == "fail"
    assert report["counts"]["bad_files"] == 1


def test_missing_run_id_fails(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    record = _record("run-x")
    record.pop("run_id")
    (directory / "run-x.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8")
    report = _audit(mod, directory, key_path)
    assert report["verdict"] == "fail"
    assert report["counts"]["bad_files"] == 1


def test_duplicate_run_id_fails(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    _write(directory, "run-dup-a.json", _record("run-dup"))
    _write(directory, "run-dup-b.json", _record("run-dup",
                                                created="2026-10-31T11:00:00+08:00"))
    report = _audit(mod, directory, key_path)
    assert report["verdict"] == "fail"
    assert report["counts"]["duplicate_run_ids"] == 1


def test_missing_signature_fails(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    _write(directory, "unsigned.json", _record("run-unsigned"), key=None)
    report = _audit(mod, directory, key_path)
    assert report["verdict"] == "fail"
    assert report["counts"]["unsigned"] == 1
    assert report["issues"]["signature_missing"]


def test_tampered_record_signature_fails(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    path = _write(directory, "run-tamper.json", _record("run-tamper"))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["task"] = "T-HACKED"  # 签名后改内容
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    report = _audit(mod, directory, key_path)
    assert report["verdict"] == "fail"
    assert report["counts"]["signature_failed"] == 1


def test_no_key_signature_unknown_pass(tmp_path):
    mod = _load()
    directory, _key_path = _setup(tmp_path)
    _write(directory, "run-0001.json", _record("run-0001"))
    report = mod.collect(directory=directory, key_path=tmp_path / "missing.key",
                         baseline="none", repo=tmp_path)
    assert report["verdict"] == "pass"
    assert report["counts"]["signature_unknown"] == 1
    assert report["key_available"] is False


# ---------------------------------------------------------------------------
# 时间异常 / 缺口 / stem
# ---------------------------------------------------------------------------
def test_created_after_finalized_fails(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    _write(directory, "run-time.json", _record(
        "run-time", created="2026-10-31T12:00:00+08:00",
        finalized="2026-10-31T11:00:00+08:00"))
    report = _audit(mod, directory, key_path)
    assert report["verdict"] == "fail"
    assert report["counts"]["time_anomalies"] == 1


def test_mtime_before_declared_fails(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    path = _write(directory, "run-backdated.json", _record(
        "run-backdated", created="2026-10-31T12:00:00+08:00"))
    declared = _ts("2026-10-31T12:00:00+08:00").timestamp()
    os.utime(path, (declared - 3600, declared - 3600))
    report = _audit(mod, directory, key_path)
    assert report["verdict"] == "fail"
    assert report["counts"]["time_anomalies"] == 1


def test_time_gap_warns_only(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    _write(directory, "run-a.json", _record("run-a", created="2026-10-31T10:00:00+08:00"))
    _write(directory, "run-b.json", _record("run-b", created="2026-10-31T12:00:00+08:00"))
    report = _audit(mod, directory, key_path, gap_hours=1)
    assert report["verdict"] == "pass"
    assert report["counts"]["gaps"] == 1


def test_file_stem_mismatch_warns_only(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    _write(directory, "renamed-file.json", _record("run-original"))
    report = _audit(mod, directory, key_path)
    assert report["verdict"] == "pass"
    assert report["issues"]["file_stem_mismatch"]


def test_stored_prev_hash_mismatch_fails(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    _write(directory, "run-chain.json", _record("run-chain", extra={"prev_hash": "f" * 64}))
    report = _audit(mod, directory, key_path)
    assert report["verdict"] == "fail"
    assert report["counts"]["chain_breaks"] >= 1


# ---------------------------------------------------------------------------
# baseline：篡改/删行/插入/追加/不可读
# ---------------------------------------------------------------------------
def _three(tmp_path):
    directory, key_path = _setup(tmp_path)
    _write(directory, "run-1.json", _record("run-1", created="2026-10-31T09:00:00+08:00"))
    _write(directory, "run-2.json", _record("run-2", created="2026-10-31T10:00:00+08:00"))
    _write(directory, "run-3.json", _record("run-3", created="2026-10-31T11:00:00+08:00"))
    return directory, key_path


def test_baseline_append_pass(tmp_path):
    mod = _load()
    directory, key_path = _three(tmp_path)
    first = _audit(mod, directory, key_path)
    baseline = _write_baseline(tmp_path / "baseline.json", first)
    _write(directory, "run-4.json", _record("run-4", created="2026-10-31T12:00:00+08:00"))
    report = _audit(mod, directory, key_path, baseline=str(baseline))
    assert report["verdict"] == "pass"
    assert report["chain"]["anchored"] is True
    assert report["baseline"]["appended"] == 1
    assert report["baseline"]["chain_ok"] is True


def test_baseline_modified_fails(tmp_path):
    mod = _load()
    directory, key_path = _three(tmp_path)
    first = _audit(mod, directory, key_path)
    baseline = _write_baseline(tmp_path / "baseline.json", first)
    _write(directory, "run-1.json", _record(
        "run-1", created="2026-10-31T09:00:00+08:00", task="T-CHANGED"))
    report = _audit(mod, directory, key_path, baseline=str(baseline))
    assert report["verdict"] == "fail"
    assert report["baseline"]["modified"] == ["run-1.json"]


def test_baseline_deleted_fails(tmp_path):
    mod = _load()
    directory, key_path = _three(tmp_path)
    first = _audit(mod, directory, key_path)
    baseline = _write_baseline(tmp_path / "baseline.json", first)
    (directory / "run-2.json").unlink()
    report = _audit(mod, directory, key_path, baseline=str(baseline))
    assert report["verdict"] == "fail"
    assert report["baseline"]["missing"] == ["run-2.json"]


def test_baseline_inserted_middle_fails(tmp_path):
    mod = _load()
    directory, key_path = _three(tmp_path)
    first = _audit(mod, directory, key_path)
    baseline = _write_baseline(tmp_path / "baseline.json", first)
    _write(directory, "run-insert.json", _record(
        "run-insert", created="2026-10-31T09:30:00+08:00"))
    report = _audit(mod, directory, key_path, baseline=str(baseline))
    assert report["verdict"] == "fail"
    assert report["baseline"]["chain_ok"] is False
    assert any(break_info["kind"] in ("reordered_or_inserted", "head_discontinuity")
               for break_info in report["baseline"]["chain_breaks"])


def test_baseline_unreadable_unknown(tmp_path):
    mod = _load()
    directory, key_path = _three(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    report = _audit(mod, directory, key_path, baseline=str(bad))
    assert report["verdict"] == "unknown"


def test_auto_baseline_discovered(tmp_path):
    mod = _load()
    directory, key_path = _three(tmp_path)
    first = _audit(mod, directory, key_path)
    deliverables = tmp_path / "deliverables"
    deliverables.mkdir()
    _write_baseline(deliverables / "evidence_chain_20261031.json", first)
    _write(directory, "run-4.json", _record("run-4", created="2026-10-31T12:00:00+08:00"))
    report = mod.collect(directory=directory, key_path=key_path,
                         baseline="auto", repo=tmp_path)
    assert report["baseline"]["source"].startswith("auto:")
    assert report["baseline"]["appended"] == 1
    assert report["verdict"] == "pass"


# ---------------------------------------------------------------------------
# 渲染 / CLI / 落盘
# ---------------------------------------------------------------------------
def test_report_json_serializable(tmp_path):
    mod = _load()
    directory, key_path = _three(tmp_path)
    report = _audit(mod, directory, key_path)
    json.dumps(report, ensure_ascii=False)


def test_md_cell_escapes_pipe():
    mod = _load()
    assert mod._md_cell("a|b") == "a\\|b"


def test_render_markdown_sections(tmp_path):
    mod = _load()
    directory, key_path = _three(tmp_path)
    text = mod.render_markdown(_audit(mod, directory, key_path))
    assert "# evidence 链式审计 · 只读" in text
    assert "## 链头" in text
    assert "## 问题明细" in text
    assert "## 未决口径" in text
    assert "## 只读声明" in text


def test_main_exit_codes(tmp_path, capsys):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    _write(directory, "run-ok.json", _record("run-ok"))
    assert mod.main(["--json", "--dir", str(directory), "--key", str(key_path),
                     "--baseline", "none"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "pass"
    (directory / "bad.json").write_text("[", encoding="utf-8")
    assert mod.main(["--json", "--dir", str(directory), "--key", str(key_path),
                     "--baseline", "none"]) == 1
    assert json.loads(capsys.readouterr().out)["verdict"] == "fail"
    empty = tmp_path / "empty"
    empty.mkdir()
    assert mod.main(["--json", "--dir", str(empty), "--key", str(key_path),
                     "--baseline", "none"]) == 2
    assert json.loads(capsys.readouterr().out)["verdict"] == "unknown"


def test_main_out_writes_ledger_pair(tmp_path, capsys):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    _write(directory, "run-ok.json", _record("run-ok"))
    base = tmp_path / "deliverables" / "evidence_chain_20261031"
    assert mod.main(["--json", "--dir", str(directory), "--key", str(key_path),
                     "--baseline", "none", "--out", str(base)]) == 0
    capsys.readouterr()
    ledger = base.with_name(base.name + ".json")
    markdown = base.with_name(base.name + ".md")
    assert ledger.is_file() and markdown.is_file()
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["schema"] == mod.SCHEMA
    assert len(data["entries"]) == 1
    # 用刚写出的 ledger 当 baseline 复跑 → 锚定通过
    assert mod.main(["--json", "--dir", str(directory), "--key", str(key_path),
                     "--baseline", str(ledger)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["baseline"]["appended"] == 0
    assert payload["baseline"]["chain_ok"] is True


def test_max_samples_truncates_issues(tmp_path):
    mod = _load()
    directory, key_path = _setup(tmp_path)
    for index in range(3):
        (directory / ("bad-%d.json" % index)).write_text("{", encoding="utf-8")
    report = _audit(mod, directory, key_path, max_samples=2)
    assert report["counts"]["bad_files"] == 3
    assert len(report["issues"]["bad_files"]) == 2
