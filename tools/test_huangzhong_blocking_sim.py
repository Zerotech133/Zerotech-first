# -*- coding: utf-8 -*-
"""黄忠阻断分级模拟（P3-D · WO-HZ-BLOCKSIM-20261101）单测。

覆盖（tmp 夹具 + fake _run，不依赖真实 git/网络）：
  · 高风险无验收→模拟 block；高风险有新鲜 PASS→pass；高风险证据超龄→block；
  · 中风险无验收→block；低风险/无改动文件→不拦；
  · 误杀候选识别（严格 merge_sha 模拟 block，但 merge_range 实际存在新鲜 PASS）；
  · sim/actual 匹配口径可配（branch_tip / merge_range）、--rules 覆盖；
  · 坏规则 rc2（不静默回退）；坏验收单容错 + fail-visible（exit 1）；
  · git log 失败→rc2；--diff-merges 不可用→逐 merge diff 回退（fail-visible）；
  · 风险分类/解析纯函数；CLI --json/--days/--no-write、MD 落盘；只读不修改仓库文件。
"""
import importlib.util
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 1, 8, 0, tzinfo=TZ)


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_blocking_sim", _TOOLS / "huangzhong_blocking_sim.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._log = lambda *a, **k: None
    return mod


def _sha(n: int) -> str:
    return ("%08x" % n) + "0" * 32


def _write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def _sheet(work: Path, sha: str, ages=(1.0, 2.0), verdict="PASS",
           generated_at="2026-11-01T05:00:00+08:00") -> Path:
    baseline_age, redteam_age = ages if ages else (None, None)
    ev = {}
    if baseline_age is not None:
        ev = {"baseline": {"age_hours": baseline_age, "stale": False,
                           "generated_at": "2026-11-01T03:00:00+08:00"},
              "redteam": {"age_hours": redteam_age, "stale": False,
                          "generated_at": "2026-11-01T03:00:00+08:00"}}
    return _write_json(work / ("acceptance_%s.json" % sha[:8]),
                       {"ok": True, "verdict": verdict,
                        "generated_at": generated_at,
                        "mode": "observe", "blocking": False,
                        "max_evidence_age_hours": 48.0,
                        "commit": {"sha": sha}, "evidence": ev})


def _gitlog(merges: list[dict], with_files: bool = True) -> str:
    parts = []
    for m in merges:
        parents = " ".join(m.get("parents") or [])
        parts.append("@@%s\x1f%s\x1f%s\x1f%s"
                     % (m["sha"], parents,
                        m.get("date", "2026-11-01T07:00:00+08:00"),
                        m.get("subject", "merge task/X: subject")))
        parts.append("")
        if with_files:
            for f in m.get("files") or []:
                parts.append(f)
    return "\n".join(parts) + "\n"


def _fixture(tmp_path: Path, merges: list[dict], sheets: list | None = None,
             intro: dict | None = None):
    """返回 (root, work, fake_run, calls)。"""
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    work = root / "work" / "huangzhong"
    work.mkdir(parents=True, exist_ok=True)
    for sha, ages in (sheets or []):
        _sheet(work, sha, ages=ages)
    calls = []
    state = {"log_calls": 0}

    def fake(cmd, cwd=None, timeout=None):
        calls.append({"cmd": [str(c) for c in cmd], "cwd": str(cwd),
                      "timeout": timeout})
        joined = " ".join(str(c) for c in cmd)
        if "log" in cmd and "--merges" in cmd:
            state["log_calls"] += 1
            if "--diff-merges=first-parent" in cmd:
                # 首次：正常返回（含文件）
                return {"ok": True, "rc": 0, "stdout": _gitlog(merges),
                        "stderr": "", "error": None, "cmd": [], "cwd": None,
                        "elapsed_s": 0.0}
            # 回退路径：不带 name-only → 无文件
            return {"ok": True, "rc": 0,
                    "stdout": _gitlog(merges, with_files=False),
                    "stderr": "", "error": None, "cmd": [], "cwd": None,
                    "elapsed_s": 0.0}
        if "rev-list" in cmd:
            target = None
            for c in cmd:
                if str(c).endswith("..HEAD"):
                    target = str(c)[:-len("..HEAD")]
                    break
            intro_sha = (intro or {}).get((target or "")[:8])
            return {"ok": True, "rc": 0,
                    "stdout": (intro_sha + "\n") if intro_sha else "",
                    "stderr": "", "error": None, "cmd": [], "cwd": None,
                    "elapsed_s": 0.0}
        if "diff" in cmd and "--name-only" in cmd:
            # 逐 merge diff 回退：按父 sha 映射文件（测试中恒返回空）
            return {"ok": True, "rc": 0, "stdout": "",
                    "stderr": "", "error": None, "cmd": [], "cwd": None,
                    "elapsed_s": 0.0}
        return {"ok": True, "rc": 0, "stdout": "", "stderr": "", "error": None,
                "cmd": [], "cwd": None, "elapsed_s": 0.0}

    return root, work, fake, calls


def _run_sim(root, work, fake, **kw):
    mod = _load()
    mod._run = fake
    kwargs = {"days": 7, "write_report": False, "now": NOW}
    kwargs.update(kw)
    rep = mod.run(repo_root=root, work_dir=work, **kwargs)
    return mod, rep


def _merge(n: int, files: list, parents=None, subject=None) -> dict:
    sha = _sha(0xA000 + n)
    return {"sha": sha, "parents": parents or [_sha(0x1000 + n)],
            "date": "2026-11-01T0%d:00:00+08:00" % (n % 6),
            "subject": subject or ("merge task/W%d: subject" % n),
            "files": files}


# ---------------------------------------------------------------- 基础判定

def test_high_risk_no_acceptance_blocks(tmp_path):
    m = _merge(1, ["quant_backtest/data/x.sql"])
    root, work, fake, calls = _fixture(tmp_path, [m])
    _, rep = _run_sim(root, work, fake)
    assert rep["ok"] is True
    assert rep["summary"]["merges"] == 1
    assert rep["summary"]["risk_counts"]["high"] == 1
    result = rep["results"][0]
    assert result["risk"] == "high"
    assert result["sim"]["decision"] == "block"
    assert any("无 PASS 验收单" in x for x in result["sim"]["reasons"])
    assert result["false_kill_candidate"] is False
    assert rep["summary"]["sim_blocked"] == 1
    assert rep["summary"]["sim_block_rate"] == 1.0
    assert rep["summary"]["false_kill_candidates"] == 0
    assert rep["summary"]["false_kill_rate"] == 0.0   # 有拦截、0 误杀 → 0%
    assert rep["exit_code"] == 0


def test_high_risk_fresh_pass_passes(tmp_path):
    m = _merge(2, ["quant_backtest/data/y.sql"])
    root, work, fake, _ = _fixture(tmp_path, [m], sheets=[(m["sha"], (1.0, 2.0))])
    _, rep = _run_sim(root, work, fake)
    result = rep["results"][0]
    assert result["risk"] == "high"
    assert result["sim"]["decision"] == "pass"
    assert result["sim"]["has_pass_acceptance"] is True
    assert result["sim"]["has_fresh_evidence"] is True
    assert rep["summary"]["sim_blocked"] == 0
    assert rep["summary"]["actual_pass_merges"] == 1
    assert rep["summary"]["actual_fresh_pass_merges"] == 1


def test_high_risk_stale_evidence_blocks(tmp_path):
    m = _merge(3, ["quant_backtest/data/z.sql"])
    root, work, fake, _ = _fixture(tmp_path, [m],
                                   sheets=[(m["sha"], (60.0, 70.0))])
    _, rep = _run_sim(root, work, fake)
    result = rep["results"][0]
    assert result["risk"] == "high"
    assert result["sim"]["decision"] == "block"
    assert any("证据超龄" in x or "超龄" in x for x in result["sim"]["reasons"])
    assert result["sim"]["has_fresh_evidence"] is False
    # 实际验收 PASS 但新鲜度为 False → 不算误杀
    assert result["actual"]["has_pass_acceptance"] is True
    assert result["actual"]["has_fresh_evidence"] is False
    assert result["false_kill_candidate"] is False


def test_mid_risk_no_acceptance_blocks(tmp_path):
    m = _merge(4, ["tools/foo.py"])
    root, work, fake, _ = _fixture(tmp_path, [m])
    _, rep = _run_sim(root, work, fake)
    result = rep["results"][0]
    assert result["risk"] == "medium"
    assert result["sim"]["decision"] == "block"
    assert rep["summary"]["sim_blocked"] == 1


def test_low_risk_not_blocked(tmp_path):
    m = _merge(5, ["docs/plans/a.md"])
    root, work, fake, _ = _fixture(tmp_path, [m])
    _, rep = _run_sim(root, work, fake)
    result = rep["results"][0]
    assert result["risk"] == "low"
    assert result["sim"]["decision"] == "pass"
    assert rep["summary"]["sim_blocked"] == 0


def test_no_changed_files_not_blocked(tmp_path):
    m = _merge(6, [])
    root, work, fake, _ = _fixture(tmp_path, [m])
    _, rep = _run_sim(root, work, fake)
    result = rep["results"][0]
    assert result["risk"] == "none"
    assert result["sim"]["decision"] == "pass"
    assert any("无改动文件" in x for x in result["sim"]["reasons"])


# ------------------------------------------------------------ 误杀候选识别

def test_false_kill_candidate_identified(tmp_path):
    """严格 merge_sha 判 block，但区间口径存在新鲜 PASS → 误杀候选。"""
    branch_commit = _sha(0xB021)
    m = _merge(7, ["quant_backtest/data/w.sql"],
               parents=[_sha(0xB011), _sha(0xB012)])
    root, work, fake, _ = _fixture(
        tmp_path, [m], sheets=[(branch_commit, (1.0, 2.0))],
        intro={branch_commit[:8]: m["sha"]})
    _, rep = _run_sim(root, work, fake)
    result = rep["results"][0]
    assert result["risk"] == "high"
    assert result["sim"]["decision"] == "block"          # sim: merge_sha 无验收
    assert result["sim"]["match"] == "merge_sha"
    assert result["actual"]["match"] == "merge_range"
    assert result["actual"]["has_pass_acceptance"] is True
    assert result["actual"]["has_fresh_evidence"] is True
    assert result["false_kill_candidate"] is True
    assert any("误杀候选" in x for x in result["sim"]["reasons"])
    assert rep["summary"]["false_kill_candidates"] == 1
    assert rep["summary"]["false_kill_rate"] == 1.0


def test_branch_tip_match_passes(tmp_path):
    """--rules 指定 sim_match=branch_tip：分支 tip 有新鲜 PASS → 不拦。"""
    p1, p2 = _sha(0xC011), _sha(0xC012)
    m = _merge(8, ["quant_backtest/data/v.sql"], parents=[p1, p2])
    root, work, fake, _ = _fixture(tmp_path, [m],
                                   sheets=[(p2, (1.0, 2.0))])
    rules = {"version": "v1", "sim_match": "branch_tip",
             "actual_match": "branch_tip"}
    rules_path = _write_json(tmp_path / "rules.json", rules)
    _, rep = _run_sim(root, work, fake, rules_path=rules_path)
    result = rep["results"][0]
    assert result["sim"]["decision"] == "pass"
    assert result["sim"]["match"] == "branch_tip"
    assert rep["rules"]["source"] == "file"


def test_rules_override_can_disable_block(tmp_path):
    m = _merge(9, ["quant_backtest/data/u.sql"])
    root, work, fake, _ = _fixture(tmp_path, [m])
    rules = {"version": "v1",
             "rules": {"high": {"block_if_no_acceptance": False,
                                "block_if_evidence_stale": False}}}
    rules_path = _write_json(tmp_path / "rules_off.json", rules)
    _, rep = _run_sim(root, work, fake, rules_path=rules_path)
    assert rep["results"][0]["sim"]["decision"] == "pass"
    assert rep["summary"]["sim_blocked"] == 0
    assert rep["rules"]["source"] == "file"


def test_bad_rules_rc2(tmp_path, capsys):
    root, work, fake, _ = _fixture(tmp_path, [_merge(10, ["tools/a.py"])])
    bad = tmp_path / "bad_rules.json"
    bad.write_text("{broken", encoding="utf-8")
    mod = _load()
    mod._run = fake
    rc = mod.main(["--repo-root", str(root), "--work-dir", str(work),
                   "--rules", str(bad), "--json", "--no-write"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["ok"] is False
    assert "规则文件不可读" in data["error"]


def test_missing_rules_file_rc2(tmp_path, capsys):
    root, work, fake, _ = _fixture(tmp_path, [])
    mod = _load()
    mod._run = fake
    rc = mod.main(["--repo-root", str(root), "--work-dir", str(work),
                   "--rules", str(tmp_path / "nope.json"), "--json",
                   "--no-write"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert "不存在" in data["error"]


# ---------------------------------------------------------- 坏数据 fail-visible

def test_bad_acceptance_tolerated_exit1(tmp_path):
    m = _merge(11, ["tools/b.py"])
    root, work, fake, _ = _fixture(tmp_path, [m])
    (work / "acceptance_bad00000.json").write_text("{bad", encoding="utf-8")
    _, rep = _run_sim(root, work, fake)
    assert rep["ok"] is True
    assert rep["acceptance"]["invalid_count"] == 1
    assert any("验收单不可读" in w for w in rep["warnings"])
    assert rep["exit_code"] == 1        # 数据缺口 fail-visible


def test_git_log_failure_rc2(tmp_path, capsys):
    root, work, fake, _ = _fixture(tmp_path, [])
    mod = _load()

    def failing(cmd, cwd=None, timeout=None):
        return {"ok": True, "rc": 128, "stdout": "", "stderr": "fatal",
                "error": None, "cmd": [], "cwd": None, "elapsed_s": 0.0}

    mod._run = failing
    rc = mod.main(["--repo-root", str(root), "--work-dir", str(work),
                   "--json", "--no-write"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["ok"] is False
    assert "git log 取 merge 失败" in data["error"]


def test_diff_merges_unsupported_falls_back(tmp_path):
    """--diff-merges 不可用 → 回退逐 merge diff（fail-visible exit 1）。"""
    m = _merge(12, ["tools/c.py"])
    root, work, fake, calls = _fixture(tmp_path, [m])
    mod = _load()

    def fake2(cmd, cwd=None, timeout=None):
        calls.append({"cmd": [str(c) for c in cmd]})
        joined = " ".join(str(c) for c in cmd)
        if "log" in cmd and "--merges" in cmd:
            if "--diff-merges=first-parent" in cmd:
                return {"ok": True, "rc": 128, "stdout": "", "stderr": "unknown option",
                        "error": None, "cmd": [], "cwd": None, "elapsed_s": 0.0}
            return {"ok": True, "rc": 0,
                    "stdout": _gitlog([m], with_files=False), "stderr": "",
                    "error": None, "cmd": [], "cwd": None, "elapsed_s": 0.0}
        if "diff" in cmd and "--name-only" in cmd:
            return {"ok": True, "rc": 0, "stdout": "tools/c.py\n", "stderr": "",
                    "error": None, "cmd": [], "cwd": None, "elapsed_s": 0.0}
        if "rev-list" in cmd:
            return {"ok": True, "rc": 0, "stdout": "", "stderr": "",
                    "error": None, "cmd": [], "cwd": None, "elapsed_s": 0.0}
        return {"ok": True, "rc": 0, "stdout": "", "stderr": "", "error": None,
                "cmd": [], "cwd": None, "elapsed_s": 0.0}

    mod._run = fake2
    rep = mod.run(repo_root=root, work_dir=work, write_report=False, now=NOW)
    assert rep["ok"] is True
    assert rep["merges_input"]["fallback"] == "per-merge-diff"
    assert rep["results"][0]["risk"] == "medium"      # 从回退 diff 取到文件
    assert rep["exit_code"] == 1
    assert any("回退" in w for w in rep["warnings"])


def test_empty_window_data_insufficient(tmp_path):
    root, work, fake, _ = _fixture(tmp_path, [])
    _, rep = _run_sim(root, work, fake)
    s = rep["summary"]
    assert s["merges"] == 0
    assert s["sim_blocked"] == 0
    assert s["sim_block_rate"] is None
    assert s["false_kill_rate"] is None
    assert s["data_sufficient"] is False
    assert any("无 merge 数据" in x for x in s["notes"])


# ---------------------------------------------------------- CLI / 落盘 / 只读

def test_cli_json_and_days_window(tmp_path, capsys):
    m = _merge(13, ["quant_backtest/data/t.sql"])
    root, work, fake, calls = _fixture(tmp_path, [m])
    mod = _load()
    mod._run = fake
    rc = mod.main(["--repo-root", str(root), "--work-dir", str(work),
                   "--json", "--no-write", "--days", "3"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["ok"] is True
    assert data["summary"]["merges"] == 1
    assert data["results"][0]["sim"]["decision"] == "block"
    log_calls = [c for c in calls if "log" in c["cmd"]]
    assert log_calls and any("--since=3 days ago" in c["cmd"] for c in log_calls)


def test_cli_md_written(tmp_path, capsys):
    m = _merge(14, ["tools/d.py"])
    root, work, fake, _ = _fixture(tmp_path, [m])
    out = tmp_path / "out"
    mod = _load()
    mod._run = fake
    rc = mod.main(["--repo-root", str(root), "--work-dir", str(work),
                   "--json", "--out-dir", str(out)])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    md_path = Path(data["report_md"])
    assert md_path.exists()
    assert md_path.name.startswith("2026-11-01_黄忠阻断分级模拟_")
    md = md_path.read_text(encoding="utf-8")
    for token in ("阻断分级模拟报告", "汇总", "模拟拦截清单", "规则口径",
                  "只读模拟"):
        assert token in md


def test_readonly_no_writes(tmp_path):
    m = _merge(15, ["tools/e.py"])
    root, work, fake, _ = _fixture(tmp_path, [m])
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    _, rep = _run_sim(root, work, fake)
    after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    assert rep["ok"] is True
    assert before == after


# -------------------------------------------------------------- 纯函数

def test_risk_classification():
    mod = _load()
    assert mod.risk_level(["docs/plans/a.md"])[0] == "low"
    assert mod.risk_level(["tools/a.py"])[0] == "medium"
    assert mod.risk_level(["portal/a.py"])[0] == "medium"
    assert mod.risk_level(["quant_backtest/data/x.sql"])[0] == "high"
    assert mod.risk_level(["portal/security_gate.py"])[0] == "high"
    assert mod.risk_level([])[0] == "none"
    # 多文件取最高
    assert mod.risk_level(["docs/a.md", "tools/b.py"])[0] == "medium"


def test_parse_merge_log():
    mod = _load()
    merges = [
        {"sha": _sha(0xD001), "parents": [_sha(0xD011), _sha(0xD012)],
         "date": "2026-11-01T06:10:49+08:00", "subject": "merge task/X: 中文标题",
         "files": ["docs/审计/W2账户清零_v1.md", "tools/x.py"]},
        {"sha": _sha(0xD002), "parents": [_sha(0xD021)],
         "date": "2026-11-01T05:00:00+08:00", "subject": "merge task/Y",
         "files": []},
    ]
    parsed = mod._parse_merge_log(_gitlog(merges))
    assert len(parsed) == 2
    assert parsed[0]["sha"] == merges[0]["sha"]
    assert parsed[0]["parents"] == merges[0]["parents"]
    assert parsed[0]["subject"] == merges[0]["subject"]
    assert parsed[0]["files"] == merges[0]["files"]
    assert parsed[1]["files"] == []


def test_validate_rules_defaults_and_bad_values():
    mod = _load()
    good, errors = mod._validate_rules({"version": "v1"})
    assert errors == []
    assert good["sim_match"] == "merge_sha"
    assert good["actual_match"] == "merge_range"
    assert good["rules"]["high"]["block_if_no_acceptance"] is True
    _, errors = mod._validate_rules({"rules": "bad"})
    assert errors
    _, errors = mod._validate_rules({"sim_match": "nope"})
    assert any("sim_match" in e for e in errors)
