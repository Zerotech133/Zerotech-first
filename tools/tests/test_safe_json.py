# -*- coding: utf-8 -*-
"""safe_json 安全写入工具测试（wave28 CX · 中风险批次）。

覆盖：
  1) 新建写入（无备份）+ 返回报告 schema；
  2) 覆盖写入 → research/reports/backups 备份（内容/ sha 与写前一致）；
  3) 序列化失败（不可 JSON 化）→ 抛 SafeJsonError 且目标不被覆盖、不产生备份；
  4) 业务 validator 失败 → 抛错且目标不被覆盖；
  5) 父目录不存在自动创建 + tmp 清理 + 原子标记；
  6) 默认备份目录位于 research/reports/backups；
  7) 四个中风险脚本回归（monkeypatch 到 tmp 路径）：
     daily_briefing / kb_ingest_status / kb_reports_gov / panel_daily.append_status。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SAFE = REPO / "tools" / "kb_corpus" / "safe_json.py"

# 与 4 个脚本共享同一个 safe_json 模块实例（脚本内 from safe_json import 走 sys.modules 缓存）
sys.path.insert(0, str(SAFE.parent))
import safe_json as sj  # noqa: E402


def sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def load_script(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture()
def backup_dir(tmp_path, monkeypatch):
    bdir = tmp_path / "backups"
    monkeypatch.setattr(sj, "DEFAULT_BACKUP_DIR", bdir)
    return bdir


# ---------------------------------------------------------------------------
# 1) 新建写入
# ---------------------------------------------------------------------------
def test_write_new_no_backup(tmp_path, backup_dir):
    p = tmp_path / "new.json"
    payload = {"name": "零界", "items": [1, 2, 3]}
    report = sj.backup_then_atomic_write(p, payload)
    assert report["schema"] == "safe_write.v1" and report["ok"] is True
    assert report["backup"] is None and report["sha_before"] is None
    assert report["sha_after"] == sha(p)
    assert report["bytes"] == p.stat().st_size
    assert report["atomic"] is True and report["tmp_removed"] is True and report["validated"] is True
    assert load(p) == payload
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# 2) 覆盖写入 → 备份
# ---------------------------------------------------------------------------
def test_overwrite_backs_up_old(tmp_path, backup_dir):
    p = tmp_path / "kb.json"
    sj.backup_then_atomic_write(p, {"v": 1})
    old_sha = sha(p)
    report = sj.backup_then_atomic_write(p, {"v": 2})
    assert report["sha_before"] == old_sha
    assert report["sha_after"] == sha(p)
    assert load(p) == {"v": 2}
    assert report["backup"] and "path" in report["backup"]
    bak = Path(report["backup"]["path"])
    assert bak.is_file() and bak.name.endswith(".bak.json")
    assert load(bak) == {"v": 1}, "备份应为写前内容"
    assert report["backup"]["sha256"] == old_sha == sha(bak)
    assert bak.parent == backup_dir


# ---------------------------------------------------------------------------
# 3) 序列化失败不覆盖
# ---------------------------------------------------------------------------
def test_invalid_payload_does_not_overwrite(tmp_path, backup_dir):
    p = tmp_path / "keep.json"
    sj.backup_then_atomic_write(p, {"safe": True})
    before = p.read_bytes()
    with pytest.raises(sj.SafeJsonError):
        sj.backup_then_atomic_write(p, {"bad": object()})
    assert p.read_bytes() == before
    assert load(p) == {"safe": True}
    assert not backup_dir.exists() or not list(backup_dir.glob("*.bak.json")), "失败不应产生新备份"


# ---------------------------------------------------------------------------
# 4) validator 失败不覆盖
# ---------------------------------------------------------------------------
def test_validator_failure_does_not_overwrite(tmp_path, backup_dir):
    p = tmp_path / "valid.json"
    sj.backup_then_atomic_write(p, {"ok": 1})
    before = p.read_bytes()

    def reject(parsed):
        return "缺少必需字段" if "required" not in parsed else None

    with pytest.raises(sj.SafeJsonError):
        sj.backup_then_atomic_write(p, {"ok": 2}, validator=reject)
    assert p.read_bytes() == before
    assert load(p) == {"ok": 1}
    # 校验通过时正常写入
    rep = sj.backup_then_atomic_write(p, {"required": True}, validator=reject)
    assert rep["ok"] is True and load(p) == {"required": True}


# ---------------------------------------------------------------------------
# 5) 父目录创建 + tmp 清理
# ---------------------------------------------------------------------------
def test_missing_parent_dir_created(tmp_path):
    p = tmp_path / "a" / "b" / "c" / "out.json"
    report = sj.backup_then_atomic_write(p, {"deep": True})
    assert p.is_file() and report["ok"] is True
    assert load(p) == {"deep": True}
    assert not list((tmp_path / "a").rglob("*.tmp"))


# ---------------------------------------------------------------------------
# 6) 默认备份目录
# ---------------------------------------------------------------------------
def test_default_backup_dir_location():
    assert sj.DEFAULT_BACKUP_DIR.name == "backups"
    assert sj.DEFAULT_BACKUP_DIR.parent.name == "reports"
    assert sj.DEFAULT_BACKUP_DIR.parent.parent.name == "research"


# ---------------------------------------------------------------------------
# 7) 四脚本回归（tmp 路径，不改真实静态产物）
# ---------------------------------------------------------------------------
def test_regression_daily_briefing(tmp_path, monkeypatch):
    mod = load_script("tools/kb_corpus/daily_briefing.py", "reg_daily_briefing")
    monkeypatch.setattr(mod, "REPORTS", tmp_path / "no_reports")
    monkeypatch.setattr(mod, "OUT_DIR", tmp_path / "out_md")
    monkeypatch.setattr(mod, "OUT_JSON", tmp_path / "kb_daily_briefing.json")
    monkeypatch.setattr(sj, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    assert mod.main(["--top", "2"]) == 0
    payload = load(tmp_path / "kb_daily_briefing.json")
    assert "generated_at" in payload and payload["docs"] == []
    # 二次执行产生备份
    assert mod.main(["--top", "2"]) == 0
    baks = list((tmp_path / "backups").glob("kb_daily_briefing.*.bak.json"))
    assert len(baks) == 1, "第二次写应备份上一版"


def test_regression_kb_ingest_status(tmp_path, monkeypatch):
    mod = load_script("tools/kb_corpus/kb_ingest_status.py", "reg_kb_ingest")
    monkeypatch.setattr(mod, "INGESTS", [])
    monkeypatch.setattr(mod, "KB_DOCS", tmp_path / "no_kb_docs")
    monkeypatch.setattr(mod, "OUT", tmp_path / "kb_ingest.json")
    monkeypatch.setattr(sj, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    assert mod.main() == 0
    payload = load(tmp_path / "kb_ingest.json")
    for key in ("generated_at", "books", "totals", "verify", "pipeline", "rag", "thresholds"):
        assert key in payload, key
    assert payload["books"] == []
    assert mod.main() == 0
    assert list((tmp_path / "backups").glob("kb_ingest.*.bak.json"))


def test_regression_kb_reports_gov(tmp_path, monkeypatch):
    mod = load_script("tools/kb_corpus/kb_reports_gov.py", "reg_kb_reports_gov")
    monkeypatch.setattr(mod, "REPORTS", tmp_path / "no_reports")
    monkeypatch.setattr(mod, "KB_DOCS", tmp_path / "no_kb_docs")
    monkeypatch.setattr(mod, "OUT", tmp_path / "kb_reports_gov.json")
    monkeypatch.setattr(sj, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    assert mod.main() == 0
    payload = load(tmp_path / "kb_reports_gov.json")
    for key in ("generated_at", "docs", "pages", "units", "tables", "rag", "next_steps"):
        assert key in payload, key
    assert mod.main() == 0
    assert list((tmp_path / "backups").glob("kb_reports_gov.*.bak.json"))


def test_regression_panel_daily_append_status(tmp_path, monkeypatch):
    mod = load_script("tools/research/panel_daily.py", "reg_panel_daily")
    monkeypatch.setattr(sj, "DEFAULT_BACKUP_DIR", tmp_path / "backups")
    status = tmp_path / "report_panel_daily_status.json"
    mod.append_status({"run_at": "2026-11-01T16:00:00+08:00", "ok": True}, status_path=status)
    data1 = load(status)
    assert data1["runs"] and data1["latest"]["ok"] is True
    mod.append_status({"run_at": "2026-11-01T16:05:00+08:00", "ok": False}, status_path=status)
    data2 = load(status)
    assert len(data2["runs"]) == 2 and data2["latest"]["ok"] is False
    assert list((tmp_path / "backups").glob("report_panel_daily_status.*.bak.json")), "第二次 append 应备份"
