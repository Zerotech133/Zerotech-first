# -*- coding: utf-8 -*-
"""测试失败基线工具 v0：跑核心套件、记录基线、对比上次（不修改测试）。

基线落 `agent_platform/data/test_baseline/latest.json` + `latest.md`。
已知 live-state/flaky 用例默认 deselect（清单可维护），避免把环境波动当回归。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # 仓库根（tools/ 的上一级）
OUT = ROOT / "agent_platform" / "data" / "test_baseline"
REPO = ROOT

# 核心套件（与 QualityGate tests 检一致的近似集；可按需扩）
SUITES = [
    "agent_platform/tests",
]
# 已知 live-state/flaky（默认排除；与 ROADMAP v3 失败基线清单同步维护）
DESELECT = [
    "agent_platform/tests/test_legion_command.py::test_activity_mapping_covers_real_tasks",
    "agent_platform/tests/test_legion_command.py::test_activity_maps_known_prefixes",
    "agent_platform/tests/test_legion_command.py::test_minutes_bad_length_rejected",
    "agent_platform/tests/test_monday_drill.py::test_read_only_no_side_effects",
    "agent_platform/tests/test_sign_tools.py::test_sign_verify_roundtrip",
    "agent_platform/tests/test_devtask_owner_domain.py::TestIllegalOwnerDegraded::test_空owner判非法并降级",  # 单跑过/全量顺序 flaky（2026-10-28 实测）
]

_SUMMARY_RE = re.compile(
    r"(\d+) passed(?:, (\d+) skipped)?(?:, (\d+) failed)?(?:, (\d+) error)?")


def build_cmd(py: str, suites: list[str] | None = None,
              timeout: str = "90") -> list[str]:
    suites = suites or SUITES
    cmd = [py, "-m", "pytest", *suites, "-q", "-p", "no:cacheprovider",
           "-o", "addopts=", "--timeout=%s" % timeout]
    for d in DESELECT:
        cmd += ["--deselect", d]
    return cmd


def parse_summary(stdout: str) -> dict:
    """pytest 汇总行顺序不固定（如 "3 failed, 2204 passed"），逐项独立匹配。"""
    tail = stdout[-4000:]

    def _n(pat: str) -> int:
        m = re.search(pat, tail)
        return int(m.group(1)) if m else 0

    return {"passed": _n(r"(\d+) passed"),
            "skipped": _n(r"(\d+) skipped"),
            "failed": _n(r"(\d+) failed"),
            "error": _n(r"(\d+) error")}


def run_baseline(py: str | None = None, suites: list[str] | None = None,
                 out_dir: Path | None = None, runner=None,
                 timeout: str = "90") -> dict:
    py = py or sys.executable
    cmd = build_cmd(py, suites, timeout)
    fn = runner or subprocess.run
    started = dt.datetime.now().astimezone()
    r = fn(cmd, cwd=str(REPO), capture_output=True, text=True,
           encoding="utf-8", errors="replace", timeout=1200)
    summary = parse_summary(getattr(r, "stdout", "") or "")
    rec = {"ok": getattr(r, "returncode", 1) == 0,
           "generated_at": started.isoformat(timespec="seconds"),
           "command": cmd, "returncode": getattr(r, "returncode", None),
           "summary": summary,
           "stdout_tail": (getattr(r, "stdout", "") or "")[-3000:],
           "failure_lines": [ln for ln in (getattr(r, "stdout", "") or "").splitlines()
                              if ln.startswith(("FAILED", "ERROR"))][-20:],
           "stderr_tail": (getattr(r, "stderr", "") or "")[-600:],
           "deselected": list(DESELECT)}
    out = Path(out_dir) if out_dir else OUT
    out.mkdir(parents=True, exist_ok=True)
    prev_path = out / "latest.json"
    prev = None
    if prev_path.exists():
        try:
            prev = json.loads(prev_path.read_text(encoding="utf-8"))
        except ValueError:
            prev = None
    rec["delta"] = _delta(prev, rec) if prev else None
    prev_path.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    md = ["# 测试失败基线", "",
          "- 时间：%s" % rec["generated_at"],
          "- 结果：passed=%s skipped=%s failed=%s error=%s" % (
              summary["passed"], summary["skipped"], summary["failed"],
              summary["error"]),
          "- 退出码：%s" % rec["returncode"]]
    if rec["delta"]:
        md.append("- 对比上次：%s" % json.dumps(rec["delta"], ensure_ascii=False))
    (out / "latest.md").write_text("\n".join(md), encoding="utf-8")
    return rec


def _delta(prev: dict, cur: dict) -> dict:
    ps = prev.get("summary") or {}
    cs = cur.get("summary") or {}
    return {k: (cs.get(k) - ps.get(k)) if isinstance(cs.get(k), int)
            and isinstance(ps.get(k), int) else None
            for k in ("passed", "failed", "error")}


def collect_only(py: str | None = None, runner=None) -> dict:
    """testpaths 实测：仓库根 `pytest --collect-only -q` 收集数量。"""
    py = py or sys.executable
    cmd = [py, "-m", "pytest", "--collect-only", "-q", "-p",
           "no:cacheprovider", "-o", "addopts=",
           "--ignore=.worktrees", "--ignore=.venvs"]
    fn = runner or subprocess.run
    r = fn(cmd, cwd=str(REPO), capture_output=True, text=True,
           encoding="utf-8", errors="replace", timeout=600)
    lines = (getattr(r, "stdout", "") or "").strip().splitlines()
    nodeids = [x for x in lines if "::" in x]
    return {"returncode": getattr(r, "returncode", None),
            "collected": len(nodeids),
            "sample": nodeids[:5],
            "note": "仅收集不执行；用于评估 testpaths 收口影响面"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="测试失败基线")
    ap.add_argument("--run", action="store_true", help="实际跑核心套件")
    ap.add_argument("--collect", action="store_true", help="testpaths 实测")
    ap.add_argument("--timeout", default="90")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.collect:
        rep = collect_only()
    elif args.run:
        rep = run_baseline(timeout=args.timeout)
    else:
        p = OUT / "latest.json"
        rep = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {
            "ok": False, "error": "no_baseline_yet"}
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else json.dumps(rep, ensure_ascii=False)[:1000])
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
