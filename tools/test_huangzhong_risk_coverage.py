# -*- coding: utf-8 -*-
"""WO-HZ-RISKCOV-20261031 单测：黄忠风险加权覆盖率（fake _run + tmp 夹具，不碰真实数据）。

覆盖点：
  · 分类规则（data/security/code/docs/unknown、docs 优先、merge 取最高风险类别）；
  · 权重与公式（手工可算示例：Σcovered_weight/Σmerge_weight）+ --weights 覆盖；
  · strict vs 有效归属（chore 子提交沿 first-parent 归属最近 merge 祖先）；
  · 演练排除（台账文件名 / sha8 / drill 标记 / .md 标注）；
  · 无覆盖 → 0；缺 git → fail-visible rc2；坏验收单/空文件 merge 复核；
  · --target 投影（固定参数复算）、--json、--out-dir 落盘、窗口 --since 参数。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_MOD = None
NOW = "2026-10-31T15:00"
TARGET = "2026-11-04T10:00"

S_CODE = "1" * 40
S_CODE2 = "2" * 40
S_SEC = "3" * 40
S_DOCS = "4" * 40
S_DOCS2 = "5" * 40
S_DATA = "6" * 40
S_UNKNOWN = "7" * 40
S_CHILD = "8" * 40
S_DRILL_A = "9" * 40
S_DRILL_B = "a" * 40


def _load():
    global _MOD
    if _MOD is None:
        p = Path(__file__).resolve().parent / "huangzhong_risk_coverage.py"
        spec = importlib.util.spec_from_file_location("huangzhong_risk_coverage", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


# ------------------------------------------------------------------ fake git

class FakeGit:
    """synthetic git：log（批量 merges+name-only）、rev-list（父提交）、show（空文件复核）。"""

    def __init__(self, merges=None, parents=None, shows=None, fail_on=None):
        self.merges = list(merges or [])
        self.parents = dict(parents or {})
        self.shows = dict(shows or {})
        self.fail_on = set(fail_on or ())
        self.commands: list[list[str]] = []

    def __call__(self, args):
        self.commands.append(list(args))
        cmd = args[0] if args else ""
        if cmd in self.fail_on:
            return 1, "", "fake git failure"
        if cmd == "log":
            lines: list[str] = []
            for m in self.merges:
                lines.append("__MERGE__%s\t%s\t%s"
                             % (m["sha"], m.get("date", "2026-10-30T10:00:00+08:00"),
                                m.get("subject", "")))
                lines.extend(m.get("files") or [])
            return 0, "\n".join(lines), ""
        if cmd == "rev-list":
            shas = args[3:]
            return 0, "\n".join(" ".join([s] + self.parents.get(s, [])) for s in shas), ""
        if cmd == "show":
            return 0, "\n".join(self.shows.get(args[-1], [])), ""
        return 1, "", "unexpected fake cmd: %r" % (cmd,)


def _merge(sha, files, date="2026-10-30T10:00:00+08:00", subject=""):
    return {"sha": sha, "files": list(files), "date": date, "subject": subject}


def _make_repo(tmp_path, *, merges, parents=None, shows=None, sheets=None,
               drills=None, daily=None, events=None, extra_files=None,
               fail_on=None):
    """构造 tmp 仓库：<root>/work/huangzhong/ + <root>/portal/data/dev_events.jsonl。"""
    root = tmp_path / "repo"
    work = root / "work" / "huangzhong"
    work.mkdir(parents=True)
    (root / "portal" / "data").mkdir(parents=True)
    for s in sheets or []:
        data = {"ok": True, "commit": {"sha": s["sha"]},
                "verdict": s.get("verdict", "PASS"),
                "generated_at": s.get("generated_at", "2026-10-30T10:00:00+08:00")}
        data.update(s.get("extra") or {})
        p = work / ("acceptance_%s.json" % s["name"])
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        if s.get("md_drill"):
            p.with_suffix(".md").write_text(
                "> 受控拒收演练单（drill=true）\n# 黄忠验收单\n", encoding="utf-8")
    if drills:
        d = work / "drills"
        d.mkdir()
        (d / "drill_ledger_20261030.json").write_text(
            json.dumps({"schema": "huangzhong_drill_ledger/v1", "drills": drills},
                       ensure_ascii=False), encoding="utf-8")
    _write_jsonl(work / "daily.jsonl", daily or [])
    _write_jsonl(root / "portal" / "data" / "dev_events.jsonl", events or [])
    for rel, text in (extra_files or {}).items():
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    fake = FakeGit(merges, parents, shows, fail_on)
    return root, work, fake


def _write_jsonl(p: Path, rows) -> None:
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                 encoding="utf-8")


def _run_row(ts="2026-10-30T10:00:00+08:00", verdict="PASS"):
    return {"ts": ts, "commit": "x" * 12, "verdict": verdict, "blocking": False,
            "reasons": [], "baseline_failed": 0, "redteam_rate": 1.0, "report": "r.md"}


def _merge_event(ts="2026-10-30 10:00:00", kind="merge_done", task="WO-T"):
    return {"ts": ts, "day": ts[:10], "kind": kind, "task": task, "agent": "t",
            "detail": "merged"}


def _compute_std(root, work, fake, **kw):
    mod = _load()
    kw.setdefault("now", NOW)
    kw.setdefault("days", 7)
    return mod.compute(repo_root=root, work_dir=work, run_fn=fake, **kw)


# ------------------------------------------------------------------ 分类

def test_classification_rules():
    mod = _load()
    assert mod.classify_file("tools/x.py") == "code"
    assert mod.classify_file("portal/api_auth.py") == "security"
    assert mod.classify_file("agent_platform/secret_store.py") == "security"
    assert mod.classify_file("quant_backtest/data/a.parquet") == "data"
    assert mod.classify_file("db/migrations/001_add.sql") == "data"
    assert mod.classify_file("quant_backtest/data/report.md") == "docs"  # docs 优先
    assert mod.classify_file("docs/2026_plan.md") == "docs"
    assert mod.classify_file("docs/auth_design.md") == "docs"  # docs 前缀优先于 security 关键词
    assert mod.classify_file("README.md") == "docs"
    assert mod.classify_file("k_stock/x.py") == "unknown"
    assert mod.classify_file("config/app.yaml") == "unknown"
    assert mod.classify_file("") == "unknown"
    # merge 级：取最高风险类别
    assert mod.classify_merge(["docs/a.md", "tools/b.py"]) == "code"
    assert mod.classify_merge(["portal/api_auth.py", "tools/b.py"]) == "security"
    assert mod.classify_merge(["quant_backtest/data/a.csv", "portal/x.py"]) == "data"
    assert mod.classify_merge(["docs/a.md", "k_stock/z.py"]) == "docs"
    assert mod.classify_merge([]) == "unknown"


# ------------------------------------------------------------------ 权重与公式

def _weights_fixture(tmp_path):
    """7 个 merge（code×2 / security×1 / docs×2 / data×1 / unknown×1）；3 个 PASS 覆盖。"""
    merges = [
        _merge(S_CODE, ["tools/a.py"]),
        _merge(S_CODE2, ["portal/b.py"]),
        _merge(S_SEC, ["portal/api_auth.py"]),
        _merge(S_DOCS, ["docs/x.md"]),
        _merge(S_DOCS2, ["docs/y.md"]),
        _merge(S_DATA, ["quant_backtest/data/a.parquet"]),
        _merge(S_UNKNOWN, ["k_stock/z.py"]),
    ]
    sheets = [
        {"name": "m1", "sha": S_CODE},
        {"name": "m2", "sha": S_CODE2, "verdict": "REJECT"},  # 最新非 PASS → 不记覆盖
        {"name": "m3", "sha": S_SEC},
        {"name": "m4", "sha": S_DOCS},
    ]
    daily = [_run_row("2026-10-30T10:00:00+08:00"), _run_row("2026-10-30T11:00:00+08:00"),
             _run_row("2026-10-31T01:00:00+08:00"), _run_row("2026-10-31T02:00:00+08:00")]
    events = [_merge_event("2026-10-30 10:00:00"), _merge_event("2026-10-30 12:00:00"),
              _merge_event("2026-10-31 09:00:00")]
    return _make_repo(tmp_path, merges=merges, sheets=sheets, daily=daily, events=events)


def test_weights_and_formula_hand_computed(tmp_path):
    root, work, fake = _weights_fixture(tmp_path)
    rep = _compute_std(root, work, fake)
    s = rep["summary"]
    ty = rep["types"]
    # 已知 merge：code2 / security1 / docs2 / data1 = 6；unknown 1
    assert (ty["code"]["merges"], ty["security"]["merges"], ty["docs"]["merges"],
            ty["data"]["merges"], ty["unknown"]["merges"]) == (2, 1, 2, 1, 1)
    assert (ty["code"]["covered"], ty["security"]["covered"], ty["docs"]["covered"],
            ty["data"]["covered"], ty["unknown"]["covered"]) == (1, 1, 1, 0, 0)
    # 权重：code 2×1 + security 1×3 + docs 2×0.5 + data 1×3 = 9.0
    assert s["merge_weight"] == pytest.approx(9.0)
    # covered：code 1×1 + security 1×3 + docs 1×0.5 = 4.5
    assert s["covered_weight"] == pytest.approx(4.5)
    assert s["risk_weighted_coverage"] == pytest.approx(0.5)
    assert s["equal_coverage_known"] == pytest.approx(3 / 6, abs=1e-4)
    assert s["equal_coverage_all"] == pytest.approx(3 / 7, abs=1e-4)
    assert s["merges_total"] == 7 and s["merges_known"] == 6 and s["merges_unknown"] == 1
    # runs（窗口内验收单，按归属 merge 归型）：code 2（PASS+REJECT）、security 1、docs 1
    assert (ty["code"]["runs"], ty["security"]["runs"], ty["docs"]["runs"]) == (2, 1, 1)
    # 旧口径：runs 4 / merge_done 3 = 1.3333；窗口内 PASS 目标 3 个 / 3
    legacy = rep["legacy"]
    assert legacy["runs_window"] == 4 and legacy["merge_done_window"] == 3
    assert legacy["runs_merge_done"] == pytest.approx(4 / 3, abs=1e-4)
    assert legacy["unique_pass_targets_window"] == 3
    assert legacy["unique_commit_coverage"] == pytest.approx(1.0)
    assert rep["ok"] is True

    # --weights 覆盖：data=1,security=2,code=0.5,docs=0.25
    rep2 = _compute_std(root, work, fake, weights={"data": 1.0, "security": 2.0,
                                                   "code": 0.5, "docs": 0.25})
    s2 = rep2["summary"]
    assert s2["merge_weight"] == pytest.approx(2 * 0.5 + 1 * 2 + 2 * 0.25 + 1 * 1.0)
    assert s2["covered_weight"] == pytest.approx(1 * 0.5 + 1 * 2 + 1 * 0.25)
    assert s2["risk_weighted_coverage"] == pytest.approx(2.75 / 4.5, abs=1e-4)


# ------------------------------------------------------------------ strict vs 归属

def test_strict_vs_effective_attribution(tmp_path):
    merges = [
        _merge(S_CODE, ["tools/a.py"]),
        _merge(S_SEC, ["portal/api_auth.py"]),
        _merge(S_DOCS, ["docs/x.md"]),
    ]
    # 验收目标 S_CHILD 是 chore 子提交（父 = merge S_CODE）
    parents = {S_CHILD: [S_CODE]}
    sheets = [
        {"name": "chore_child", "sha": S_CHILD},   # 归属 S_CODE（1 跳）
        {"name": "exact_sec", "sha": S_SEC},       # strict 命中
    ]
    root, work, fake = _make_repo(tmp_path, merges=merges, parents=parents, sheets=sheets)
    rep = _compute_std(root, work, fake)
    s = rep["summary"]
    assert s["covered_merges"] == 2          # 有效归属：code + security
    assert s["covered_merges_strict"] == 1   # strict：仅 security
    assert s["risk_weighted_coverage"] == pytest.approx(
        (1.0 + 3.0) / (1.0 + 3.0 + 0.5), abs=1e-4)
    assert s["strict"]["risk_weighted_coverage"] == pytest.approx(3.0 / 4.5, abs=1e-4)
    assert s["equal_coverage_known"] == pytest.approx(2 / 3, abs=1e-4)
    a = s["attribution"]
    assert a["pass_targets"] == 2 and a["attributed"] == 2 and a["strict_count"] == 1
    assert a["unresolved"] == 0
    assert a["hops_hist"] == {"0": 1, "1": 1}
    # 归属查询走 rev-list --parents --no-walk
    assert any(c[0] == "rev-list" for c in fake.commands)


def test_attribution_unresolved_warning(tmp_path):
    merges = [_merge(S_CODE, ["tools/a.py"])]
    far = ["f" + "%02d" % i for i in range(6)]  # 40 位 sha：f00..f05
    far = [s * 20 for s in far]
    chain = {far[0]: [far[1]], far[1]: [far[2]], far[2]: [far[3]],
             far[3]: [far[4]], far[4]: [far[5]], far[5]: []}  # 6 跳无 merge 祖先
    sheets = [{"name": "far", "sha": far[0]}]
    root, work, fake = _make_repo(tmp_path, merges=merges, parents=chain, sheets=sheets)
    rep = _compute_std(root, work, fake, max_hops=5)
    assert rep["summary"]["attribution"]["unresolved"] == 1
    assert rep["summary"]["covered_merges"] == 0
    assert any("无法归属" in w for w in rep["warnings"])


# ------------------------------------------------------------------ 演练排除

def test_drill_exclusion(tmp_path):
    merges = [_merge(S_CODE, ["tools/a.py"]), _merge(S_SEC, ["portal/api_auth.py"])]
    drills = [
        {"drill_id": "D1", "target_commit": S_DRILL_A,
         "acceptance_report": "work/huangzhong/acceptance_drill_ledger.json"},
        {"drill_id": "D2", "target_commit": S_DRILL_B},  # sha8 排除
    ]
    sheets = [
        {"name": "ok", "sha": S_CODE},
        {"name": "drill_ledger", "sha": S_CODE,
         "generated_at": "2026-10-30T11:00:00+08:00"},          # 台账文件名排除
        {"name": "drill_flag", "sha": S_SEC, "extra": {"drill": True}},
        {"name": "drill_md", "sha": S_CODE, "md_drill": True},
        {"name": "drill_sha", "sha": S_DRILL_B},
    ]
    root, work, fake = _make_repo(tmp_path, merges=merges, sheets=sheets, drills=drills)
    rep = _compute_std(root, work, fake)
    acc = rep["inputs"]["acceptance"]
    assert set(acc["excluded_drills"]) == {
        "acceptance_drill_ledger.json", "acceptance_drill_flag.json",
        "acceptance_drill_md.json", "acceptance_drill_sha.json"}
    assert rep["inputs"]["drill_entries"] == 2
    # 覆盖只剩 ok（code 1）；security 未覆盖
    assert rep["summary"]["covered_merges"] == 1
    assert rep["types"]["code"]["covered"] == 1
    assert rep["types"]["security"]["covered"] == 0
    assert rep["summary"]["risk_weighted_coverage"] == pytest.approx(1.0 / 4.0)
    # 演练单不计入最新 PASS 目标
    assert rep["summary"]["attribution"]["pass_targets"] == 1


# ------------------------------------------------------------------ 0 覆盖 / 坏数据 / 空文件复核

def test_no_coverage_is_zero(tmp_path):
    merges = [_merge(S_CODE, ["tools/a.py"]), _merge(S_DOCS, ["docs/x.md"])]
    sheets = [{"name": "rej", "sha": S_CODE, "verdict": "REJECT"}]
    root, work, fake = _make_repo(tmp_path, merges=merges, sheets=sheets)
    rep = _compute_std(root, work, fake)
    assert rep["summary"]["covered_merges"] == 0
    assert rep["summary"]["risk_weighted_coverage"] == 0.0
    assert rep["summary"]["equal_coverage_known"] == 0.0
    assert rep["summary"]["threshold_pass"] is False
    assert any("最新结论均非 PASS" in w or "覆盖数 0" in w for w in rep["warnings"])


def test_bad_acceptance_and_empty_file_fallback(tmp_path):
    merges = [
        _merge(S_CODE, []),          # 批量无文件 → show 复核补充
        _merge(S_SEC, []),           # 仍为空（未在 shows）→ unknown
        _merge(S_DOCS, ["docs/x.md"]),
    ]
    shows = {S_CODE: ["tools/fallback.py"]}
    sheets = [
        {"name": "ok", "sha": S_DOCS},
        {"name": "no_sha", "sha": ""},
    ]
    extra = {
        "acceptance_badjson.json": "{broken",
        "acceptance_no_ts.json": json.dumps(
            {"ok": True, "commit": {"sha": "b" * 40}, "verdict": "PASS",
             "generated_at": ""}, ensure_ascii=False),
    }
    root, work, fake = _make_repo(tmp_path, merges=merges, shows=shows, sheets=sheets,
                                  extra_files=extra)
    rep = _compute_std(root, work, fake)
    inp = rep["inputs"]
    assert inp["fallback_used"] == 2
    assert inp["fallback_failed"] == 1
    assert inp["acceptance"]["invalid"] == 1
    assert inp["acceptance"]["missing_sha"] == 1
    assert inp["acceptance"]["missing_generated_at"] == 1
    # 批量空、show 有文件的 S_CODE 归 code；仍空的 S_SEC 归 unknown
    assert rep["types"]["code"]["merges"] == 1
    assert rep["types"]["unknown"]["merges"] == 1
    assert rep["types"]["docs"]["merges"] == 1
    assert any("unknown" in w for w in rep["warnings"])


# ------------------------------------------------------------------ 投影

def test_projection_formula(tmp_path):
    root, work, fake = _weights_fixture(tmp_path)
    rep = _compute_std(root, work, fake, target=TARGET)
    p = rep["projection"]
    # now=10-31T15:00 → target=11-04T10:00：剩余 3 天 19 小时 = 3.7916667 天
    remaining = (3 * 24 + 19) / 24.0
    assert p["past_target"] is False
    assert p["remaining_days"] == pytest.approx(remaining, abs=1e-4)
    factor = 1 + remaining / 7.0
    assert p["factor"] == pytest.approx(factor, abs=1e-4)
    assert p["projected_merges"] == pytest.approx(7 * factor, abs=0.06)
    assert p["projected_covered"] == pytest.approx(3 * factor, abs=0.06)
    # 平稳假设：比例不变
    assert p["projected_risk_weighted_coverage"] == pytest.approx(0.5, abs=1e-4)
    assert p["projected_equal_coverage"] == pytest.approx(3 / 7, abs=1e-4)
    assert p["types"]["code"]["projected_merges"] == pytest.approx(2 * factor, abs=0.06)


def test_projection_past_target(tmp_path):
    root, work, fake = _weights_fixture(tmp_path)
    rep = _compute_std(root, work, fake, target="2026-10-01T00:00")
    p = rep["projection"]
    assert p["past_target"] is True
    assert p["remaining_days"] == 0.0
    assert p["factor"] == 1.0
    assert p["projected_merges"] == pytest.approx(7.0)
    assert any("target 已过去" in w for w in rep["warnings"])


# ------------------------------------------------------------------ 旧口径窗口

def test_window_and_legacy_windows(tmp_path):
    root, work, fake = _weights_fixture(tmp_path)
    # 追加越窗数据（10-20 早于 since）
    _write_jsonl(work / "daily.jsonl",
                 [_run_row("2026-10-30T10:00:00+08:00"), _run_row("2026-10-20T10:00:00+08:00")])
    _write_jsonl(root / "portal" / "data" / "dev_events.jsonl", [
        _merge_event("2026-10-30 10:00:00"), _merge_event("2026-10-29 10:00:00"),
        _merge_event("2026-10-20 10:00:00"), _merge_event("2026-10-30 11:00:00", kind="task_start"),
    ])
    rep = _compute_std(root, work, fake)
    assert rep["legacy"]["runs_window"] == 1
    assert rep["legacy"]["merge_done_window"] == 2
    # 命令里带窗口起点 --since（now - 7d）
    log_cmd = fake.commands[0]
    assert log_cmd[0] == "log" and any(
        a == "--since=2026-10-24T15:00:00+08:00" for a in log_cmd), log_cmd


# ------------------------------------------------------------------ fail-visible / CLI

def test_git_failure_fail_visible_rc2(tmp_path):
    root, work, fake = _make_repo(tmp_path, merges=[], fail_on=["log"])
    rep = _compute_std(root, work, fake)
    assert rep["ok"] is False
    assert rep["inputs"]["git_ok"] is False
    assert any("git 不可用" in w for w in rep["warnings"])
    mod = _load()
    rc = mod.main(["--repo-root", str(root), "--work-dir", str(work), "--days", "7",
                   "--now", NOW, "--json"], run_fn=fake)
    assert rc == 2


def test_cli_json_and_weights(tmp_path):
    root, work, fake = _weights_fixture(tmp_path)
    mod = _load()
    out = tmp_path / "out"
    rc = mod.main(["--repo-root", str(root), "--work-dir", str(work), "--days", "7",
                   "--now", NOW, "--weights", "data=4,security=4,code=1,docs=0.5",
                   "--target", TARGET, "--out-dir", str(out), "--json"], run_fn=fake)
    assert rc == 0
    # 直接调 main 时 stdout 被 pytest 捕获，这里复核 rep 内容由 compute 再算一次
    rep = _compute_std(root, work, fake, weights={"data": 4.0, "security": 4.0})
    assert rep["weights"]["data"] == 4.0 and rep["weights"]["security"] == 4.0
    files = sorted(p.name for p in out.iterdir())
    assert any(n.endswith(".md") for n in files) and any(n.endswith(".json") for n in files)
    md = next(p for p in out.iterdir() if p.name.endswith(".md")).read_text(encoding="utf-8")
    for section in ("## 一、结论摘要", "## 二、按类型的覆盖与风险权重",
                    "## 三、覆盖判定与归属", "## 四、旧口径对照",
                    "## 五、2026-11-04T10:00 投影", "## 六、假设与风险",
                    "## 七、数据源与 fail-visible", "## 八、复现命令"):
        assert section in md, section
    jrep = json.loads(next(p for p in out.iterdir() if p.name.endswith(".json"))
                      .read_text(encoding="utf-8"))
    assert jrep["summary"]["merges_total"] == 7
    assert jrep["projection"]["target"].startswith("2026-11-04T10:00")


def test_no_out_dir_no_files(tmp_path):
    root, work, fake = _weights_fixture(tmp_path)
    mod = _load()
    before = set(p for p in tmp_path.rglob("*"))
    rc = mod.main(["--repo-root", str(root), "--work-dir", str(work), "--days", "7",
                   "--now", NOW, "--json"], run_fn=fake)
    assert rc == 0
    created = {p for p in tmp_path.rglob("*")} - before
    assert not [p for p in created if p.is_file()]


def test_invalid_args_rc2(tmp_path, capsys):
    root, work, fake = _weights_fixture(tmp_path)
    mod = _load()
    assert mod.main(["--repo-root", str(root), "--work-dir", str(work),
                     "--weights", "data=abc", "--json"], run_fn=fake) == 2
    assert "参数错误" in capsys.readouterr().err
    assert mod.main(["--repo-root", str(root), "--work-dir", str(work),
                     "--target", "not-a-date", "--json"], run_fn=fake) == 2
    assert "参数错误" in capsys.readouterr().err
    assert mod.main(["--repo-root", str(root), "--work-dir", str(work),
                     "--now", "bad-clock", "--json"], run_fn=fake) == 2
    err3 = capsys.readouterr().err
    assert "无法解析" in err3 or "参数错误" in err3
