# -*- coding: utf-8 -*-
"""人工资产写入加固测试（wave29 CY）：learning_cards / kb_units_snapshot。

覆盖：
  1) merge_cards 语义保留：现有卡全量保留、生成卡按 concept 归一化去重追加；
  2) learning_cards 原子写+备份+合并：main() 写入保留旧卡、二次运行产生备份；
  3) learning_cards 无源数据时行为不变（rc=1 "no units"）；
  4) kb_units_snapshot 写盘+备份+schema：main() 写合法快照、二次运行备份；
  5) kb_units_snapshot 校验失败不覆盖：空快照拒绝覆盖非空旧快照（rc=1，旧文件字节不变）；
  6) validate_units_payload 纯函数：合法通过 / total 不一致 / 缺字段 / 空覆盖均拦截。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
KB = REPO / "tools" / "kb_corpus"
sys.path.insert(0, str(KB))
import safe_json as sj  # noqa: E402


def load_script(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def make_source(tmp_path: Path, book: str = "rp_test", n: int = 2) -> Path:
    root = tmp_path / "src"
    d = root / book
    d.mkdir(parents=True, exist_ok=True)
    lines = []
    for i in range(n):
        lines.append(json.dumps({
            "book": book, "chapter": "第1章", "page_start": i + 1, "page_end": i + 2,
            "concept": f"测试概念{i}", "claim": f"测试概念{i}的核心结论与数据支撑，用于人工资产回归测试。",
            "evidence": f"测试概念{i}的原文证据段落，含数据与结论。",
        }, ensure_ascii=False))
    (d / "knowledge_units.jsonl").write_text("\n".join(lines), encoding="utf-8")
    return root


@pytest.fixture()
def backup_dir(tmp_path, monkeypatch):
    bdir = tmp_path / "backups"
    monkeypatch.setattr(sj, "DEFAULT_BACKUP_DIR", bdir)
    return bdir


# ---------------------------------------------------------------------------
# 1) merge_cards 语义
# ---------------------------------------------------------------------------
def test_learning_cards_merge_semantics():
    mod = load_script("tools/kb_corpus/learning_cards.py", "reg_learning_cards_merge")
    existing = [{"q": "旧卡Q", "a": "旧卡A", "concept": "人工保留卡", "topic": "其他"}]
    generated = [{"q": "新卡Q1", "a": "新卡A1", "concept": "机器生成卡1", "topic": "AI/科技"},
                 {"q": "新卡Q2", "a": "新卡A2", "concept": "机器生成卡2", "topic": "AI/科技"}]
    merged = mod.merge_cards(existing, generated)
    assert merged[0]["concept"] == "人工保留卡", "现有卡必须全量保留且在前"
    assert len(merged) == 3
    # 同名（归一化）生成卡不重复追加
    dup = [{"q": "dup", "a": "dup", "concept": "人工 保留卡"}]
    assert len(mod.merge_cards(existing, dup)) == 1


# ---------------------------------------------------------------------------
# 2) learning_cards 原子写 + 备份 + 保留旧卡
# ---------------------------------------------------------------------------
def test_learning_cards_write_and_backup(tmp_path, monkeypatch, backup_dir):
    mod = load_script("tools/kb_corpus/learning_cards.py", "reg_learning_cards_main")
    src = make_source(tmp_path, n=2)
    out_json = tmp_path / "kb_learning_cards.json"
    old_card = {"q": "什么是「人工保留卡」？", "a": "人工答案", "concept": "人工保留卡",
                "source": "《人工》 p1-2", "topic": "其他", "evidence": "人工证据"}
    out_json.write_text(json.dumps({"generated_at": "old", "total_units": 10,
                                    "cards": [old_card], "daily": None, "topics": {"其他": 1},
                                    "file": "old.md"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "INGESTS", [src])
    monkeypatch.setattr(mod, "CARDS_DIR", tmp_path / "cards_md")
    monkeypatch.setattr(mod, "OUT_JSON", out_json)
    assert mod.main(["--n", "2", "--seed", "1"]) == 0
    data1 = load(out_json)
    concepts = [c.get("concept") for c in data1["cards"]]
    assert "人工保留卡" in concepts, "旧卡必须保留"
    assert len(data1["cards"]) == 3, "旧卡 + 2 张生成卡"
    assert data1["total_units"] == 2
    # 二次运行 → 备份上一版 + 仍保留旧卡
    assert mod.main(["--n", "2", "--seed", "2"]) == 0
    baks = list(backup_dir.glob("kb_learning_cards.*.bak.json"))
    # 首次运行备份了预置旧文件；二次运行再备份第一版 → 共 2 份（时间戳唯一）
    assert len(baks) == 2 and len({b.name for b in baks}) == 2, "每次覆盖写都应备份上一版"
    assert any(load(b) == data1 for b in baks), "应存在内容=写前版本的备份"
    assert "人工保留卡" in [c.get("concept") for c in load(out_json)["cards"]]


# ---------------------------------------------------------------------------
# 3) learning_cards CLI 不变（无数据 → rc=1 + 提示）
# ---------------------------------------------------------------------------
def test_learning_cards_cli_no_units(tmp_path, monkeypatch, capsys):
    mod = load_script("tools/kb_corpus/learning_cards.py", "reg_learning_cards_empty")
    monkeypatch.setattr(mod, "INGESTS", [tmp_path / "no_source"])
    monkeypatch.setattr(mod, "OUT_JSON", tmp_path / "should_not_exist.json")
    assert mod.main([]) == 1
    out = capsys.readouterr().out
    assert "no units" in out
    assert not (tmp_path / "should_not_exist.json").exists()


# ---------------------------------------------------------------------------
# 4) kb_units_snapshot 写盘 + 备份 + schema
# ---------------------------------------------------------------------------
def test_kb_units_snapshot_write_and_backup(tmp_path, monkeypatch, backup_dir):
    mod = load_script("tools/kb_corpus/kb_units_snapshot.py", "reg_snapshot_main")
    src = make_source(tmp_path, book="rp_snap", n=2)
    monkeypatch.setattr(mod, "ROOTS", [("研报", src)])
    monkeypatch.setattr(mod, "STATIC", tmp_path)
    assert mod.main() == 0
    out = tmp_path / "kb_units_all.json"
    data1 = load(out)
    assert data1["total"] == 2 and len(data1["units"]) == 2
    assert all(u.get("source") == "研报" and u.get("claim") for u in data1["units"])
    # 二次运行 → 备份
    assert mod.main() == 0
    baks = list(backup_dir.glob("kb_units_all.*.bak.json"))
    assert len(baks) == 1
    assert load(baks[0]) == data1


# ---------------------------------------------------------------------------
# 5) kb_units_snapshot 校验失败不覆盖（空快照保护）
# ---------------------------------------------------------------------------
def test_kb_units_snapshot_refuses_empty_overwrite(tmp_path, monkeypatch, backup_dir):
    mod = load_script("tools/kb_corpus/kb_units_snapshot.py", "reg_snapshot_empty")
    monkeypatch.setattr(mod, "STATIC", tmp_path)
    out = tmp_path / "kb_units_all.json"
    existing = {"generated_at": "old", "total": 1,
                "units": [{"source": "书籍", "book": "b", "chapter": "c", "p1": 1, "p2": 2,
                           "concept": "c", "claim": "人工核对后的 claim", "evidence": "e"}]}
    out.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
    before = out.read_bytes()
    monkeypatch.setattr(mod, "ROOTS", [])  # 源缺失 → 空快照
    rc = mod.main()
    assert rc == 1, "空快照不得覆盖非空人工资产"
    assert out.read_bytes() == before


# ---------------------------------------------------------------------------
# 6) validate_units_payload 纯函数
# ---------------------------------------------------------------------------
def test_validate_units_payload_rules():
    mod = load_script("tools/kb_corpus/kb_units_snapshot.py", "reg_snapshot_validate")
    good = {"total": 1, "units": [{"source": "研报", "book": "b", "claim": "有 claim"}]}
    assert mod.validate_units_payload(good) is None
    with pytest.raises(ValueError):
        mod.validate_units_payload({"total": 2, "units": [{"source": "研报", "book": "b", "claim": "x"}]})
    with pytest.raises(ValueError):
        mod.validate_units_payload({"total": 1, "units": [{"source": "研报", "book": "b"}]})
    with pytest.raises(ValueError):
        mod.validate_units_payload({"total": 0, "units": []}, {"total": 1, "units": [{"claim": "x"}]})
