# -*- coding: utf-8 -*-
"""审计 A 修复回归测试（K-QUANT-20261026-AUDITFIX）。

覆盖：M1 注释模板不计经验 / M2 主工作树根解析 / M3 inject --dry-run 零写入与幂等 /
M4 zhuge 失败 rc=1 + upstream_generated_at / M5 a0 degraded 不假成功 + 非 dict 输入不崩。
"""
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WT = HERE.parent  # linked worktree root (or main repo)


def _load(rel, name):
    path = (WT / rel) if rel.startswith("agent_platform") else (HERE / rel)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------- M2: 主工作树根解析 ----------
def test_repo_root_resolves_linked_worktree(tmp_path):
    main = tmp_path / "main"
    (main / ".git").mkdir(parents=True)
    (main / ".git" / "worktrees" / "WT1").mkdir(parents=True)
    wt = tmp_path / "WT1"
    (wt / "tools").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: %s/.git/worktrees/WT1\n" % main.as_posix(),
                             encoding="utf-8")
    mod = _load("_repo_paths.py", "auditfix_repo_paths")
    assert mod.repo_root(wt / "tools" / "x.py") == main.resolve()


def test_repo_root_normal_checkout(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "tools").mkdir(parents=True)
    mod = _load("_repo_paths.py", "auditfix_repo_paths2")
    assert mod.repo_root(repo / "tools" / "x.py") == repo.resolve()


# ---------- M1: 注释模板不计经验 ----------
def test_evidence_packet_ignores_html_comment_template(tmp_path):
    M = _load("seat_evidence_packet.py", "auditfix_evidence")
    status = tmp_path / "STATUS.md"
    status.write_text("# S\n健康卡 20绿\n", encoding="utf-8")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps({"T1": {"status": "done"}}), encoding="utf-8")
    exp = tmp_path / "EXPERIENCE.md"
    exp.write_text(
        "<!-- 条目模板：\n## EXP-SUPERVISION-001\n- **cue**: 什么情境触发\n"
        "- **lesson**: 教训本体\n- **evidence**: 依据\n- **confidence**: 0/1\n-->\n\n"
        "## EXP-SUPERVISION-002\n- **cue**: 真实情境\n- **lesson**: 真实教训\n"
        "- **evidence**: Z4 卡 2\n- **confidence**: 0.9\n", encoding="utf-8")
    packet = M.build(status, tasks, exp)
    ids = [e["id"] for e in packet["experience"]["entries"]]
    assert ids == ["EXP-SUPERVISION-002"], ids
    assert packet["experience"]["total"] == 1


# ---------- M4: zhuge 失败 rc / 上游时间 ----------
def test_zhuge_failure_rc_and_upstream_time(tmp_path, monkeypatch, capsys):
    M = _load("zhuge_brief_snapshot.py", "auditfix_zhuge")
    monkeypatch.setattr(M, "fetch", lambda url, timeout=30: {"ok": False, "error": "boom"})
    rc = M.main(["--out", str(tmp_path), "--quiet"])
    assert rc == 1
    snap = json.loads(next(tmp_path.glob("zhuge_brief_*.json")).read_text(encoding="utf-8"))
    assert snap["ok"] is False and snap["error"] == "boom"

    payload = {"business_day": "2026-10-26", "generated_at": "2026-10-26T19:00:00+08:00",
               "suggestions": []}
    monkeypatch.setattr(M, "fetch", lambda url, timeout=30: {"ok": True, "http": 200,
                                                             "payload": payload})
    rc2 = M.main(["--out", str(tmp_path), "--quiet"])
    assert rc2 == 0
    snap2 = json.loads(sorted(tmp_path.glob("zhuge_brief_*.json"))[-1]
                       .read_text(encoding="utf-8"))
    assert snap2["upstream_generated_at"] == "2026-10-26T19:00:00+08:00"


# ---------- M3: inject dry-run 零写入 + apply 幂等 ----------
def test_inject_dry_run_zero_write_and_apply(tmp_path, monkeypatch, capsys):
    M = _load("seat_capability_inject.py", "auditfix_inject")
    homes = {"tianfeng": str(tmp_path / "tianfeng"),
             "zhaoyun": str(tmp_path / "zhaoyun"),
             "zhuge": str(tmp_path / "zhuge")}
    monkeypatch.setattr(M, "HOMES", homes)
    M.inject(True)
    assert not any(Path(h).exists() for h in homes.values()), "dry-run 写了目录"

    M.inject(False)
    for home in homes.values():
        h = Path(home)
        assert (h / "skills" / "CAPABILITIES.json").is_file()
        assert (h / "memories" / "WORKING_MEMORY.md").is_file()
    cards = list((Path(homes["tianfeng"]) / "skills").glob("*/SKILL.md"))
    assert len(cards) >= 15
    content = {p.parent.name: p.read_text(encoding="utf-8") for p in cards}

    M.inject(False)  # 幂等：不覆盖既有技能卡内容
    for p in cards:
        assert p.read_text(encoding="utf-8") == content[p.parent.name]


# ---------- M5 / L1 / L2: a0 degraded 不假成功 ----------
class FakeLLM:
    def __init__(self, text="判读", status="ok", fail=False):
        self.text, self.status, self.fail = text, status, fail

    def chat(self, system, user):
        if self.fail:
            raise RuntimeError("429")
        return self.text, self.status


PACKET = {
    "schema": "seat_evidence_packet/v1", "generated_at": "2026-10-26T17:55:00+0800",
    "sources": [{"path": "s", "sha256": "x", "exists": True}],
    "tickets": {"total": 3, "counts": {"done": 3}, "doing_review": []},
    "experience": {"total": 1, "entries": [
        {"id": "EXP-SUPERVISION-001", "cue": "c", "lesson": "l",
         "evidence": "e", "confidence": "0.9"}]},
    "health": {"tail": ["- ok"]},
}


def test_a0_degraded_not_ok(tmp_path):
    M = _load("agent_platform/a0_evidence_judge.py", "auditfix_a0")
    res = M.judge(PACKET, live=True, out_dir=tmp_path,
                  llm=FakeLLM(status="degraded"))
    assert res["ok"] is False and res.get("degraded") is True
    assert res["error"] == "llm_degraded"


def test_a0_non_dict_packet_guard():
    M = _load("agent_platform/a0_evidence_judge.py", "auditfix_a0b")
    res = M.judge(["not", "dict"], live=True, llm=FakeLLM())
    assert res["ok"] is False and res["error"] == "packet_not_dict"


