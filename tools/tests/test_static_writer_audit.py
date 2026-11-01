# -*- coding: utf-8 -*-
"""tools/tests/test_static_writer_audit.py — 静态产物写入脚本审计（wave26 支线 CR）。

覆盖（任务验收 ≥3）：
  1. 扫描：合成工作区中识别写盘脚本 / 只读引用；写入目标与页面消费正确；
  2. 评级：人工资产 + 无备份 + 无原子写 → high；有备份+原子写 → 非 high；只读 → low；
     参数默认值/变量别名（`def save(base=BASE)`）也能识别写入目标；
  3. 报告 schema/MD：summary/writers/targets/high_risk 齐全、高风险带最小修复建议、
     JSON 可序列化、MD 含高风险清单与修复章节；
  4. CLI：--root 合成工作区 → 写出 JSON/MD 且返回 0。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_static_writer_audit.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import static_writer_audit as swa  # noqa: E402

BAD_WRITER = '''# -*- coding: utf-8 -*-
from pathlib import Path
PORTAL = Path(__file__).resolve().parents[1] / "portal"
OUT = PORTAL / "static" / "kb_learning_cards.json"

def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("{}", encoding="utf-8")

if __name__ == "__main__":
    main()
'''

GOOD_WRITER = '''# -*- coding: utf-8 -*-
import json, os, shutil
from pathlib import Path
PORTAL = Path(__file__).resolve().parents[1] / "portal"
OUT = PORTAL / "static" / "kb_learning_cards.json"

def main():
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    shutil.copy2(OUT, OUT.with_suffix(".bak.json"))
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, OUT)
'''

ALIAS_WRITER = '''# -*- coding: utf-8 -*-
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "portal" / "static"
BASE = STATIC / "kb_learning_cards.json"

def save(base: Path = BASE) -> None:
    base.write_text("{}", encoding="utf-8")

if __name__ == "__main__":
    save()
'''

READER = '''# -*- coding: utf-8 -*-
import json
from pathlib import Path
STATIC = Path(__file__).resolve().parents[1] / "portal" / "static"
data = json.loads((STATIC / "kb_learning_cards.json").read_text(encoding="utf-8"))
'''

HELPER_WRITER = '''# -*- coding: utf-8 -*-
import json, os, shutil
from pathlib import Path
PORTAL = Path(__file__).resolve().parents[1] / "portal"
OUT = PORTAL / "static" / "kb_learning_cards.json"

def safe_write_json(path, payload, merge_fn=None):
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if path.exists():
        shutil.copy2(path, path.with_suffix(".bak.json"))
    tmp = path.with_suffix(".tmp")
    merged = merge_fn(old, payload) if merge_fn else payload
    tmp.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)

def _merge_cards(old, new):
    return dict(old, **new)

def save():
    safe_write_json(OUT, {"a": 1}, merge_fn=_merge_cards)
'''


def _make_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "tools").mkdir(parents=True)
    (root / "portal" / "static").mkdir(parents=True)
    (root / "portal" / "static" / "kb_learning_cards.json").write_text("{}", encoding="utf-8")
    (root / "portal" / "index.html").write_text(
        '<script>fetch("/static/kb_learning_cards.json")</script>', encoding="utf-8")
    (root / "tools" / "bad_writer.py").write_text(BAD_WRITER, encoding="utf-8")
    (root / "tools" / "good_writer.py").write_text(GOOD_WRITER, encoding="utf-8")
    (root / "tools" / "alias_writer.py").write_text(ALIAS_WRITER, encoding="utf-8")
    (root / "tools" / "helper_writer.py").write_text(HELPER_WRITER, encoding="utf-8")
    (root / "tools" / "reader.py").write_text(READER, encoding="utf-8")
    return root


def _scan_ws(tmp_path: Path) -> list[dict]:
    root = _make_workspace(tmp_path)
    return swa.scan(root, static_dir=root / "portal" / "static", scan_dirs=("tools", "portal"))


# ------------------------------------------------------------------ 1. 扫描
def test_scan_detects_writers_and_read_only(tmp_path):
    records = _scan_ws(tmp_path)
    by_script = {r["script"].replace("\\", "/"): r for r in records}
    writers = {k for k, r in by_script.items() if not r["read_only"]}
    assert "tools/bad_writer.py" in writers
    assert "tools/good_writer.py" in writers
    assert "tools/alias_writer.py" in writers, "参数默认值别名应被识别"
    reader = next(r for k, r in by_script.items() if k == "tools/reader.py")
    assert reader["read_only"] is True and reader["risk"] == "low"
    bad = by_script["tools/bad_writer.py"]
    assert "kb_learning_cards.json" in bad["targets"]
    assert any("portal/index.html" in c.replace("\\", "/") for c in bad["consumers"])


# ------------------------------------------------------------------ 2. 评级
def test_risk_grading_high_vs_protected(tmp_path):
    by_script = {r["script"].replace("\\", "/"): r for r in _scan_ws(tmp_path)}
    bad = by_script["tools/bad_writer.py"]
    good = by_script["tools/good_writer.py"]
    alias = by_script["tools/alias_writer.py"]
    # 人工资产 + 无备份 + 无原子写 → high
    assert bad["risk"] == "high"
    assert bad["flags"]["backup"] is False and bad["flags"]["atomic_write"] is False
    assert bad["fix_suggestions"], "高风险应给出最小修复建议"
    # 有备份+原子写+合并：不再 high
    assert good["risk"] != "high"
    assert good["flags"]["backup"] is True and good["flags"]["atomic_write"] is True
    # 别名识别：仍是人工资产（无备份/原子写 → high）
    assert alias["risk"] == "high" and alias["targets"] == ["kb_learning_cards.json"]


# ------------------------------------------------------------------ 3. 报告 schema/MD
def test_report_schema_and_md(tmp_path):
    root = _make_workspace(tmp_path)
    records = swa.scan(root, static_dir=root / "portal" / "static")
    rep = swa.build_report(records, root)
    for key in ("schema_version", "scope", "policy", "summary", "writers",
                "read_only_references", "targets", "high_risk"):
        assert key in rep, f"报告缺字段 {key}"
    assert rep["summary"]["n_writers"] >= 3
    assert rep["summary"]["high"] >= 1
    assert rep["high_risk"] and rep["high_risk"][0]["minimal_fixes"]
    assert any(t["target"] == "kb_learning_cards.json" for t in rep["targets"])
    json.dumps(rep, ensure_ascii=False)
    md = swa.render_md(rep)
    for needle in ("高风险清单", "最小修复建议", "全部写盘脚本清单", "口径与局限"):
        assert needle in md, f"MD 缺章节：{needle}"
    assert "kb_learning_cards.json" in md


# ------------------------------------------------------------------ 4. CLI
def test_cli_writes_reports(tmp_path):
    root = _make_workspace(tmp_path)
    out_json, out_md = tmp_path / "audit.json", tmp_path / "audit.md"
    rc = swa.main(["audit", "--root", str(root), "--json", str(out_json), "--md", str(out_md)])
    assert rc == 0
    rep = json.loads(out_json.read_text(encoding="utf-8"))
    assert rep["summary"]["n_writers"] >= 3
    assert out_md.exists() and "高风险清单" in out_md.read_text(encoding="utf-8")


# ------------------------------------------------------------------ 5. v2 辅助函数识别
def test_helper_writer_detection_and_downgrade(tmp_path, monkeypatch):
    root = _make_workspace(tmp_path)
    records = swa.scan(root, static_dir=root / "portal" / "static")
    by = {r["script"].replace("\\", "/"): r for r in records}
    helper = by["tools/helper_writer.py"]
    assert helper["helper_based"] is True
    assert "safe_write_json" in helper["helper_names"]
    assert helper["targets"] == ["kb_learning_cards.json"]
    assert helper["protection"] == {"backup": True, "merge_existing": True,
                                    "atomic_write": True}
    assert helper["risk"] == "low"
    assert any(c["helper"] == "safe_write_json" for c in helper["helper_calls"])
    # v1 → v2 复审：伪造 v1 报告把该脚本判为 high → 应出现 downgrade
    fake_v1 = tmp_path / "v1.json"
    fake_script = str(Path("tools") / "helper_writer.py")   # 与 scan 输出同平台分隔符
    fake_v1.write_text(json.dumps({
        "writers": [{"script": fake_script, "risk": "high"}],
        "summary": {"n_writers": 1, "high": 1, "medium": 0, "low": 0}}, ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(swa, "V1_JSON", fake_v1)
    rep = swa.build_report(records, root)
    rv = rep["review_v1_v2"]
    assert rv["v1_available"] is True
    assert any(c["script"] == fake_script and c["v1_risk"] == "high"
               and c["v2_risk"] == "low" for c in rv["downgraded"])
    md = swa.render_md(rep)
    assert "v1 → v2 复审对比" in md and "降级确认" in md


# ------------------------------------------------------------------ 6. 真实仓库复审
def test_real_repo_high_risk_downgraded():
    records = swa.scan()
    by = {r["script"].replace("\\", "/"): r for r in records}
    for script in ("tools/kb_corpus/morning_brief_push.py",
                   "tools/research/panel_export.py"):
        rec = by.get(script)
        assert rec is not None, f"未识别 helper 写入：{script}"
        assert rec["helper_based"] is True
        assert rec["risk"] == "low", (script, rec["risk"], rec["risk_reasons"])
    learn = by["tools/kb_corpus/learning_cards.py"]
    assert learn["risk"] == "medium", learn["risk_reasons"]   # 有合并、缺备份/原子写
    rep = swa.build_report(records)
    assert rep["summary"]["high"] == 0
    rv = rep["review_v1_v2"]
    downgraded = {(c["script"].replace("\\", "/"), c["v1_risk"], c["v2_risk"])
                  for c in rv["downgraded"]}
    assert ("tools/kb_corpus/morning_brief_push.py", "high", "low") in downgraded
    assert ("tools/research/panel_export.py", "high", "low") in downgraded
    assert ("tools/kb_corpus/learning_cards.py", "high", "medium") in downgraded
    assert rv["v2_high_scripts"] == []
