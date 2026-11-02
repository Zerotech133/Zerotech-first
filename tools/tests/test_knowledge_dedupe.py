# -*- coding: utf-8 -*-
"""知识单元重复合并测试（task-138，W0-2）。

覆盖：
  1) 归一化 key：前 14 个 CJK 字符、忽略标点；
  2) 元数据识别：时长/字数/无语音/共 N 条 → boilerplate，普通 claim 否；
  3) 分组与 evidence 合并：同 key 组保留首条 + merged_count/merged_unit_ids/evidence_list（去重）；
  4) dry-run 不写：输入文件 sha 不变、报告 dry_run=true；
  5) apply：备份到指定目录 + 写回去重结果 + 二次 apply 幂等（无重复→不写/无新备份）；
  6) 真实项目 dry-run 报告：3813 → 3406（-407）、dry_run、输入未改。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "knowledge_dedupe.py"
REPORT_JSON = REPO / "research" / "reports" / "knowledge_dedupe_20261103.json"
REPORT_MD = REPO / "research" / "reports" / "knowledge_dedupe_20261103.md"
REAL_INPUT = Path(r"C:\项目\知识库\30_知识单元\视频号学习_8博主_units_draft.jsonl")

_spec = importlib.util.spec_from_file_location("kd_test", SCRIPT)
kd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = kd
_spec.loader.exec_module(kd)  # type: ignore[union-attr]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def unit(uid: str, claim: str, file: str = "a.md", line: int = 1) -> dict:
    return {"unit_id": uid, "project_id": "p", "claim": claim,
            "evidence": {"file": file, "line": line}, "status": "draft"}


def write_jsonl(path: Path, units: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(u, ensure_ascii=False) for u in units) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1) 归一化 key / 2) 元数据识别
# ---------------------------------------------------------------------------
def test_normalize_key_and_boilerplate():
    k = kd.normalize_key("视频主要讲了MACD背离的判断方法，十分实用")
    assert len(k) == 14 and all("\u4e00" <= ch <= "\u9fff" for ch in k)
    assert kd.normalize_key("abc") == ""
    assert kd.is_boilerplate("- 时长：1:16")
    assert kd.is_boilerplate("- 字数：405")
    assert kd.is_boilerplate("_（无语音 / 转写为空）_")
    assert kd.is_boilerplate("> 共 23 条 · 由 faster-whisper 转写")
    assert not kd.is_boilerplate("视频主要讲了MACD背离的判断方法")


# ---------------------------------------------------------------------------
# 3) 分组 + evidence 合并
# ---------------------------------------------------------------------------
def test_grouping_and_evidence_merge():
    units = [
        unit("u1", "视频主要讲了MACD背离的判断方法，十分实用。", "a.md", 1),
        unit("u2", "视频主要讲了MACD背离的判断方法，十分实用。", "a.md", 1),
        unit("u3", "视频主要讲了MACD背离的判断方法，十分实用。", "b.md", 9),
        unit("u4", "完全不同的另一段知识内容，涉及均线系统。", "c.md", 2),
    ]
    res = kd.dedupe(units, skip_boilerplate=True)
    assert res["input"] == 4 and res["output"] == 2
    groups = res["dup_groups"]
    assert len(groups) == 1 and groups[0]["size"] == 3
    merged = [u for u in res["units"] if u.get("merged_count")][0]
    assert merged["unit_id"] == "u1"
    assert merged["merged_count"] == 3
    assert merged["merged_unit_ids"] == ["u1", "u2", "u3"]
    assert merged["evidence_list"] == [{"file": "a.md", "line": 1}, {"file": "b.md", "line": 9}]
    assert merged["evidence"] == {"file": "a.md", "line": 1}, "首条 evidence 保留"


# ---------------------------------------------------------------------------
# 4) dry-run 不写
# ---------------------------------------------------------------------------
def test_dryrun_no_write(tmp_path):
    inp = tmp_path / "proj_units_draft.jsonl"
    write_jsonl(inp, [unit("u1", "第一段知识内容讲的是趋势跟随策略。"),
                      unit("u2", "第一段知识内容讲的是趋势跟随策略。"),
                      unit("u3", "第二段知识内容讲的是仓位管理。")])
    before = sha(inp)
    payload = kd.build(input_path=inp, apply=False, out_dir=tmp_path)
    assert payload["dry_run"] is True and payload["applied"] is False
    assert payload["stats"]["duplicate_rows"] == 1
    assert sha(inp) == before, "dry-run 不得写输入文件"
    assert (tmp_path / "knowledge_dedupe_20261103.json").is_file()


# ---------------------------------------------------------------------------
# 5) apply 备份 + 幂等
# ---------------------------------------------------------------------------
def test_apply_backup_and_idempotent(tmp_path):
    inp = tmp_path / "proj_units_draft.jsonl"
    write_jsonl(inp, [unit("u1", "第一段知识内容讲的是趋势跟随策略。"),
                      unit("u2", "第一段知识内容讲的是趋势跟随策略。")])
    backups = tmp_path / "backups"
    payload = kd.build(input_path=inp, apply=True, out_dir=tmp_path, backup_dir=backups)
    assert payload["applied"] is True and payload["backup"]
    bak = Path(payload["backup"])
    assert bak.is_file() and bak.parent == backups
    after_lines = [l for l in inp.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(after_lines) == 1, "应写回去重后 1 条"
    first_after = sha(inp)
    # 二次 apply：无重复 → 不写、无新备份（幂等）
    payload2 = kd.build(input_path=inp, apply=True, out_dir=tmp_path, backup_dir=backups)
    assert payload2["stats"]["duplicate_rows"] == 0
    assert payload2["idempotent_noop"] is True and payload2["backup"] is None
    assert sha(inp) == first_after
    assert len(list(backups.glob("*.v1bak"))) == 1, "幂等运行不应新增备份"


# ---------------------------------------------------------------------------
# 6) 真实项目 dry-run 报告
# ---------------------------------------------------------------------------
def test_real_project_report():
    assert REPORT_JSON.is_file() and REPORT_MD.is_file()
    d = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    s = d["stats"]
    assert d["dry_run"] is True and d["applied"] is False
    assert d["project"] == "视频号学习_8博主"
    assert s["input"] == 3813
    assert s["output"] == 3813 - s["duplicate_rows"]
    assert s["duplicate_rows"] == 407 and s["output"] == 3406
    assert s["dup_groups"] == 73 and s["boilerplate"] == 1925
    assert d["source"]["sha256"] == sha(REAL_INPUT), "输入文件未被修改（dry-run）"
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("知识单元重复 cohort 合并报告", "重复组样例", "说明"):
        assert marker in md, marker
