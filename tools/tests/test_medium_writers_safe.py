# -*- coding: utf-8 -*-
"""tools/tests/test_medium_writers_safe.py — 中风险生成类脚本补原子写（wave29 支线 DA）。

覆盖（任务验收 ≥5）：
  1. safe_json 工具：validator 失败 → 目标不覆盖、tmp 清理（通用保护语义）；
  2. card_revision_apply.build(apply=True)：cards 落盘合法、手工备份存在、无 tmp 残留；
  3. kb_qa_bank_v2.write_outputs：门户 JSON 合法 + 二次写入生成时间戳备份 + tmp 清理；
  4. kb_qa_bank_v3.write_outputs：同上；
  5. kb_qa_expand.run_pipeline(dry_run)：门户 JSON 合法、无 tmp 残留、返回结构不变；
  6. h18_watch_push.refresh：写 h18_watch.json 合法 + 二次刷新生成备份 + tmp 清理；
  7. 复审：真实仓库扫描中这 5 个脚本风险已降级（非中/高）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_medium_writers_safe.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KBC = ROOT / "tools" / "kb_corpus"
sys.path.insert(0, str(KBC))

import safe_json as sj  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _no_tmp(path: Path) -> bool:
    return not list(path.parent.glob(f".{path.name}.*.tmp"))


# ------------------------------------------------------------------ 1. safe_json 校验失败
def test_safe_json_validator_failure_keeps_target(tmp_path, monkeypatch):
    monkeypatch.setattr(sj, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    target = tmp_path / "x.json"
    _write(target, {"old": True})
    before = target.read_bytes()
    try:
        sj.backup_then_atomic_write(target, {"new": True}, validator=lambda p: ["bad"])
        assert False, "应抛 SafeJsonError"
    except sj.SafeJsonError:
        pass
    assert target.read_bytes() == before
    assert _no_tmp(target)


# ------------------------------------------------------------------ 2. card_revision_apply
def test_card_revision_apply_atomic(tmp_path, monkeypatch):
    mod = _load("cra_safe", KBC / "card_revision_apply.py")
    cards = tmp_path / "cards.json"
    revisions = tmp_path / "revisions.json"
    backup = tmp_path / "cards.bak.json"
    out_dir = tmp_path / "reports"
    _write(cards, {"schema_version": 1, "cards": [{"concept": "A", "q": "q", "a": "a"}],
                   "keep_top": 1})
    _write(revisions, {})
    monkeypatch.setattr(mod, "refresh_digest", lambda runner=None: {"ok": True, "refreshed": False})
    monkeypatch.setattr(mod, "validate_result", lambda *a, **k: [])   # 本用例只验写盘链路
    rep = mod.build(revisions_path=revisions, cards_path=cards, backup_path=backup,
                    out_dir=out_dir, apply=True, generated_at="2026-11-01T00:00:00+08:00")
    assert rep["ok"] is True
    assert backup.exists() and json.loads(backup.read_text(encoding="utf-8"))["cards"]
    written = json.loads(cards.read_text(encoding="utf-8"))
    assert written.get("keep_top") == 1 and written["cards"]
    assert _no_tmp(cards)
    assert (out_dir / "card_revision_apply_20261101.json").exists()


# ------------------------------------------------------------------ 3/4. bank v2 / v3
def _bank_case(tmp_path, monkeypatch, module_name: str, file_name: str):
    mod = _load(module_name, KBC / file_name)
    orig_write = mod.backup_then_atomic_write
    monkeypatch.setattr(
        mod, "backup_then_atomic_write",
        lambda path, payload, **kw: orig_write(path, payload,
                                               backup_dir=tmp_path / "backups", **kw))
    monkeypatch.setattr(mod, "render_markdown", lambda payload: "# md")
    portal = tmp_path / "portal.json"
    payload = {"schema_version": 3, "kind": "kb_qa_questions", "questions": [{"id": "Q1"}]}
    mod.write_outputs(payload, tmp_path / "out.json", tmp_path / "out.md", portal)
    assert json.loads(portal.read_text(encoding="utf-8"))["questions"][0]["id"] == "Q1"
    assert _no_tmp(portal)
    # 二次写入 → 时间戳备份
    payload["questions"].append({"id": "Q2"})
    mod.write_outputs(payload, tmp_path / "out.json", tmp_path / "out.md", portal)
    backups = list((tmp_path / "backups").glob("portal.*.bak.json"))
    assert backups, "二次写入应生成写前备份"
    assert len(json.loads(backups[0].read_text(encoding="utf-8"))["questions"]) == 1
    assert len(json.loads(portal.read_text(encoding="utf-8"))["questions"]) == 2


def test_bank_v2_atomic(tmp_path, monkeypatch):
    _bank_case(tmp_path, monkeypatch, "kbq_v2_safe", "kb_qa_bank_v2.py")


def test_bank_v3_atomic(tmp_path, monkeypatch):
    _bank_case(tmp_path, monkeypatch, "kbq_v3_safe", "kb_qa_bank_v3.py")


# ------------------------------------------------------------------ 5. kb_qa_expand
def test_kb_qa_expand_portal_atomic(tmp_path, monkeypatch):
    mod = _load("kb_expand_safe", KBC / "kb_qa_expand.py")
    monkeypatch.setattr(sj, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    ctx = {
        "schema_version": 1, "kind": "kb_qa_questions", "generated_at": "2026-11-01T00:00:00+08:00",
        "questions": [{"id": "Q1", "question": "q1", "theme": "主题", "source_unit_id": "u1"}],
    }
    original = {"schema_version": 1, "kind": "kb_qa_samples", "samples": [{"id": "s1"}]}
    questions_path = tmp_path / "questions.json"
    portal = tmp_path / "samples.json"
    _write(questions_path, ctx)
    _write(portal, original)
    res = mod.run_pipeline(questions_path=questions_path, limit=1, dry_run=True,
                           out_path=tmp_path / "expanded.json",
                           backup_path=tmp_path / "backup_expanded.json",
                           portal_path=portal)
    assert res["portal_path"] == str(portal)
    written = json.loads(portal.read_text(encoding="utf-8"))
    assert written["samples"]  # 原样本保留（合并）
    assert _no_tmp(portal)


# ------------------------------------------------------------------ 6. h18_watch_push
def test_h18_watch_push_refresh_atomic(tmp_path, monkeypatch):
    mod = _load("h18_push_safe", ROOT / "tools" / "research" / "h18_watch_push.py")
    static = tmp_path / "static"
    static.mkdir()
    watch = static / "h18_watch.json"
    _write(static / "selection_daily.json", {"date": "20261101", "forecast_watch": {
        "items": [{"ts_code": "A.SZ", "ann_date": "20261030", "delta_pp": 1.0}]}})
    monkeypatch.setattr(mod, "STATIC", static)
    monkeypatch.setattr(mod, "WATCH", watch)
    orig_write = mod.backup_then_atomic_write
    monkeypatch.setattr(
        mod, "backup_then_atomic_write",
        lambda path, payload, **kw: orig_write(path, payload,
                                               backup_dir=tmp_path / "backups", **kw))
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)
    mod.refresh()
    first = json.loads(watch.read_text(encoding="utf-8"))
    assert first["items"][0]["ts_code"] == "A.SZ"
    assert _no_tmp(watch)
    mod.refresh()                       # 二次刷新 → 备份
    assert list((tmp_path / "backups").glob("h18_watch.*.bak.json"))
    assert json.loads(watch.read_text(encoding="utf-8"))["date"] == "20261101"


# ------------------------------------------------------------------ 7. 复审降级
def test_audit_risk_downgraded():
    sys.path.insert(0, str(KBC))
    import static_writer_audit as swa
    records = swa.scan()
    by = {r["script"].replace("\\", "/"): r for r in records}
    for script in ("tools/kb_corpus/card_revision_apply.py",
                   "tools/kb_corpus/kb_qa_bank_v2.py",
                   "tools/kb_corpus/kb_qa_bank_v3.py",
                   "tools/kb_corpus/kb_qa_expand.py",
                   "tools/research/h18_watch_push.py"):
        rec = by.get(script)
        assert rec is not None, f"审计未识别：{script}"
        assert rec["risk"] in ("low",), (script, rec["risk"], rec["risk_reasons"])
