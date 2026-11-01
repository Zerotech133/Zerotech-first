# -*- coding: utf-8 -*-
"""黄忠盲审抽样包生成器单测（tmp 夹具 + fake _run，不碰真实 work/、不落 deliverables）。

覆盖 P3-C 要求：
  · PASS 抽样确定性与数量（seed 可复现；默认 seed 按当日+固定盐）；
  · 窗口/演练/legacy 排除与 skipped 统计；同 sha 去重；
  · 盲审包 md **不含 verdict/理由**（不出现 verdict 字样），含 sha/改动摘要/证据路径/六条问题；
  · 密封答案 key 含真实 verdict、证据年龄、复核人建议位；
  · 样本不足 → fail-visible（不生成半成品）；--no-write 不落盘；
  · git show --stat 经 _run 注入（失败可见不编造）；CLI --json/退出码。
"""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_blind_audit", _TOOLS / "huangzhong_blind_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _now():
    return datetime(2026, 11, 1, 7, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _acceptance(sha="a" * 40, verdict="PASS", generated_at=None, drill=None,
                legacy=False, baseline_path=r"C:\repo\agent_platform\data\latest.json",
                redteam_path=r"C:\repo\work\p1\redteam\redteam_1.json"):
    gen = generated_at or _iso(_now() - timedelta(hours=1))
    data = {
        "ok": True,
        "acceptance_id": "HZ-%s-20261101" % sha[:8],
        "generated_at": gen,
        "mode": "observe",
        "blocking": False,
        "commit": {"ok": True, "sha": sha, "subject": "merge sample"},
        "evidence": {
            "baseline": {"ok": True, "generated_at": _iso(_now() - timedelta(hours=2)),
                         "age_hours": 2.0, "stale": False, "path": baseline_path},
            "redteam": {"ok": True, "generated_at": _iso(_now() - timedelta(hours=3)),
                        "age_hours": 3.0, "stale": False, "path": redteam_path},
        },
        "verdict": verdict,
        "reasons": ["样例理由"],
        "rollback_point": "git revert " + sha[:12],
    }
    if drill is not None:
        data["drill"] = drill
    if legacy:
        for kind in ("baseline", "redteam"):
            data["evidence"][kind].pop("age_hours", None)
            data["evidence"][kind].pop("stale", None)
    return data


def _write_work(tmp_path, records: dict) -> Path:
    work = tmp_path / "work" / "huangzhong"
    work.mkdir(parents=True, exist_ok=True)
    for name, data in records.items():
        p = work / name
        if isinstance(data, str):
            p.write_text(data, encoding="utf-8")
        else:
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return work


_DEFAULT_STAT = """abc1234 merge task/X: sample payload
 src/foo.py   | 12 +++++-------
 tools/bar.py |  3 +++
 2 files changed, 12 insertions(+), 3 deletions(-)
"""


class _FakeRun:
    """fake 模块级 _run：返回固定 git show --stat 文本（可配置失败）。"""

    def __init__(self, stat_text=_DEFAULT_STAT, ok=True):
        self.stat_text = stat_text
        self.ok = ok
        self.calls = []

    def __call__(self, cmd, cwd=None, timeout=None):
        self.calls.append({"cmd": [str(c) for c in cmd], "cwd": cwd,
                           "timeout": timeout})
        if not self.ok:
            return {"ok": False, "returncode": 128, "stdout": "",
                    "stderr": "fatal: bad object"}
        return {"ok": True, "returncode": 0, "stdout": self.stat_text,
                "stderr": ""}


def _build(h, work, out, fake, **kw):
    kw.setdefault("samples", 2)
    kw.setdefault("seed", "seed-1")
    kw.setdefault("write", False)
    kw.setdefault("now", _now())
    return h.build(work_dir=work, out_dir=out, repo_root=work.parent / "repo",
                   runner=fake, **kw)


def _passing_work(tmp_path, n=6):
    records = {}
    for i in range(n):
        sha = ("%02d" % (i + 1)) * 20  # 40 hex
        records["acceptance_%s.json" % sha[:8]] = _acceptance(sha)
    return _write_work(tmp_path, records)


# ---------------------------------------------------------------- 抽样确定性

def test_deterministic_sampling_and_count(tmp_path):
    h = _load()
    work = _passing_work(tmp_path, 6)
    out = tmp_path / "out"
    fake = _FakeRun()
    rep1 = _build(h, work, out, fake, seed="seed-1")
    rep2 = _build(h, work, out, fake, seed="seed-1")
    assert rep1["ok"] is True
    assert rep1["candidates"] == 6
    assert rep1["samples_requested"] == 2 and rep1["samples_actual"] == 2
    assert [s["sha"] for s in rep1["samples"]] == [s["sha"] for s in rep2["samples"]]
    assert [s["label"] for s in rep1["samples"]] == ["A", "B"]
    assert rep1["seed_fingerprint"] == rep2["seed_fingerprint"]
    assert len(rep1["seed_fingerprint"]) == 8
    # 不同 seed → 抽样集合发生变化（确定性抽样可复现且 seed 生效）
    rep3 = _build(h, work, out, fake, seed="seed-2")
    assert {s["sha"] for s in rep3["samples"]} != {s["sha"] for s in rep1["samples"]}
    # 只抽样、不写盘（--no-write 语义）
    assert rep1["written"] is False


def test_default_seed_is_date_based(tmp_path):
    h = _load()
    work = _passing_work(tmp_path, 4)
    out = tmp_path / "out"
    fake = _FakeRun()
    rep1 = _build(h, work, out, fake, seed=None, samples=1)
    rep2 = _build(h, work, out, fake, seed=None, samples=1)
    assert rep1["seed_source"] == "default"
    assert "2026-11-01" in rep1["seed"]
    assert rep1["seed"] == rep2["seed"]
    assert [s["sha"] for s in rep1["samples"]] == [s["sha"] for s in rep2["samples"]]


def test_window_drill_legacy_and_bad_skipped(tmp_path):
    records = {
        "acceptance_aa000001.json": _acceptance("a" * 40),
        "acceptance_bb000001.json": _acceptance("b" * 40, drill=True),
        "acceptance_cc000001.json": _acceptance("c" * 40, legacy=True),
        "acceptance_dd000001.json": _acceptance(
            "d" * 40, generated_at=_iso(_now() - timedelta(days=30))),
        "acceptance_ee000001.json": _acceptance("e" * 40, verdict="OBSERVE"),
        "acceptance_ff000001.json": "{not json",
    }
    work = _write_work(tmp_path, records)
    h = _load()
    rep = _build(h, work, tmp_path / "out", _FakeRun(), samples=1)
    assert rep["ok"] is True and rep["candidates"] == 1
    skipped = rep["skipped"]
    assert skipped["drill"] == 1 and skipped["legacy"] == 1
    assert skipped["window"] == 1 and skipped["not_pass"] == 1 and skipped["bad"] == 1
    # include_legacy 可纳入 legacy
    rep2 = _build(h, work, tmp_path / "out", _FakeRun(), samples=1,
                  include_legacy=True)
    assert rep2["candidates"] == 2


# ------------------------------------------------------------ 盲审包/密封 key

def test_blind_md_hides_verdict(tmp_path):
    h = _load()
    work = _passing_work(tmp_path, 3)
    rep = _build(h, work, tmp_path / "out", _FakeRun(), samples=2, write=False)
    md = rep["blind_md"]
    # 去掉环境路径再做"不含 verdict 字样"断言（tmp 目录名可能含测试函数名）
    body = md.replace(str(work), "<work>").replace(str(tmp_path), "<tmp>")
    low = body.lower()
    assert "verdict" not in low
    for word in ("PASS", "REJECT", "OBSERVE"):
        assert word not in body
    for s in rep["samples"]:
        assert s["short_sha"] in md and s["sha"] in md
    assert "改动摘要" in md and "git show --stat" in md
    assert "待审问题清单" in md and "黄忠六条对应" in md
    assert "证据路径" in md
    assert r"C:\repo\agent_platform\data\latest.json" in md
    # 不引用验收单文件本身（避免被反查 verdict）
    assert "acceptance_" not in md


def test_sealed_key_contains_verdict_and_age(tmp_path):
    h = _load()
    work = _passing_work(tmp_path, 3)
    rep = _build(h, work, tmp_path / "out", _FakeRun(), samples=2, write=False)
    key = rep["sealed_key"]
    assert key["sealed"] is True
    assert len(key["samples"]) == 2
    sample = key["samples"][0]
    assert sample["verdict"] == "PASS"
    assert sample["acceptance_file"].startswith("acceptance_")
    assert sample["evidence_age"] == {"baseline": 2.0, "redteam": 3.0}
    assert sample["stale"] == {"baseline": False, "redteam": False}
    assert sample["suggested_reviewer_slot"] == ""
    assert sample["commit_sha"] == rep["samples"][0]["sha"]


def test_git_show_failure_visible(tmp_path):
    h = _load()
    work = _passing_work(tmp_path, 2)
    rep = _build(h, work, tmp_path / "out", _FakeRun(ok=False), samples=1)
    assert rep["ok"] is True
    assert rep["samples"][0]["stat"]["ok"] is False
    assert rep["warnings"] and "改动摘要不可读" in rep["warnings"][0]
    assert "改动摘要不可读" in rep["blind_md"]
    # key 仍含 verdict（不被 git 失败影响）
    assert rep["sealed_key"]["samples"][0]["verdict"] == "PASS"


def test_git_stat_parsed_and_cmd_injected(tmp_path):
    h = _load()
    work = _passing_work(tmp_path, 2)
    fake = _FakeRun()
    rep = _build(h, work, tmp_path / "out", fake, samples=1)
    stat = rep["samples"][0]["stat"]
    assert stat["ok"] is True
    assert stat["summary"] == {"files_changed": 2, "insertions": 12,
                               "deletions": 3}
    assert stat["files"][:2] == ["src/foo.py", "tools/bar.py"]
    assert fake.calls and "show" in fake.calls[0]["cmd"]
    assert "--stat" in fake.calls[0]["cmd"]


# ---------------------------------------------------------------- 失败/落盘

def test_insufficient_samples_fail_visible(tmp_path):
    h = _load()
    work = _passing_work(tmp_path, 1)
    out = tmp_path / "out"
    rep = _build(h, work, out, _FakeRun(), samples=2, write=True)
    assert rep["ok"] is False and rep["insufficient"] is True
    assert "样本不足" in rep["error"]
    assert rep["written"] is False and rep["samples"] == []
    assert not out.exists()  # 不生成半成品包


def test_no_candidates_fail_visible(tmp_path):
    h = _load()
    work = _write_work(tmp_path, {"acceptance_x.json":
                                  _acceptance("a" * 40, verdict="REJECT")})
    rep = _build(h, work, tmp_path / "out", _FakeRun(), samples=1)
    assert rep["ok"] is False and "无符合条件的验收样本" in rep["error"]


def test_no_write_vs_write(tmp_path):
    h = _load()
    work = _passing_work(tmp_path, 3)
    out = tmp_path / "out"
    dry = _build(h, work, out, _FakeRun(), samples=2, write=False)
    assert dry["written"] is False and dry["mode"] == "no_write"
    assert not out.exists()
    wet = _build(h, work, out, _FakeRun(), samples=2, write=True)
    assert wet["ok"] is True and wet["written"] is True and wet["mode"] == "write"
    blind_path, key_path = Path(wet["blind_md_path"]), Path(wet["key_path"])
    assert blind_path.is_file() and key_path.is_file()
    assert blind_path.read_text(encoding="utf-8") == wet["blind_md"]
    key = json.loads(key_path.read_text(encoding="utf-8"))
    assert key["sealed"] is True
    assert key["samples"][0]["verdict"] == "PASS"


def test_work_dir_missing_fail_visible(tmp_path):
    h = _load()
    rep = h.build(work_dir=tmp_path / "nope", out_dir=tmp_path / "out",
                  repo_root=tmp_path / "repo", runner=_FakeRun(), now=_now())
    assert rep["ok"] is False and "work 目录不存在" in rep["error"]


# ---------------------------------------------------------------- CLI

def test_cli_json_and_human_output(tmp_path, capsys, monkeypatch):
    h = _load()
    work = _passing_work(tmp_path, 3)
    out = tmp_path / "deliverables"
    monkeypatch.setattr(h, "_run", _FakeRun())  # main 无 runner 参数 → 模块级 _run
    rc = h.main(["--json", "--no-write", "--samples", "2", "--seed", "cli-seed",
                 "--work-dir", str(work), "--out-dir", str(out)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    for key in ("ok", "generated_at", "mode", "work_dir", "out_dir", "days",
                "samples_requested", "samples_actual", "seed", "seed_source",
                "seed_fingerprint", "candidates", "skipped", "insufficient",
                "warnings", "samples", "blind_md", "sealed_key",
                "blind_md_path", "key_path", "written"):
        assert key in data, key
    assert data["written"] is False and data["samples_actual"] == 2
    assert data["seed_source"] == "cli" and data["seed_fingerprint"]
    rc = h.main(["--samples", "2", "--seed", "cli-seed",
                 "--work-dir", str(work), "--out-dir", str(out)])
    assert rc == 0
    human = capsys.readouterr().out
    assert "盲审抽样" in human and "A=" in human and "written=True" in human


def test_cli_invalid_samples(tmp_path):
    h = _load()
    for bad in ("0", "-1", "abc"):
        with pytest.raises(SystemExit):
            h.main(["--samples", bad, "--work-dir", str(tmp_path),
                    "--no-write"])
