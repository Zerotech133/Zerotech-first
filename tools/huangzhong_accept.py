# -*- coding: utf-8 -*-
"""黄忠 · 验收守门官 v0（观察模式，**不阻断 merge**）。

输入：一个 commit（默认 HEAD）+ 现有证据（测试基线 latest.json、红队最新报告）。
输出：PASS / REJECT / OBSERVE 验收单（work/huangzhong/acceptance_<sha8>.json/md）。
规则（观察期）：
  · 基线 failed>0 或红队 pass_rate<0.8 → REJECT（只报告，不阻断）
  · 证据缺失/无法判定 → OBSERVE
  · 全部满足 → PASS
边界：只读；不写业务代码、不改 org/bundle、不代批；最终准入由老田/merge 门决定。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "agent_platform" / "data" / "test_baseline" / "latest.json"
REDTEAM_DIR = ROOT / "work" / "p1" / "redteam"
OUT = ROOT / "work" / "huangzhong"
MIN_REDTEAM_RATE = 0.8


def _run(cmd: list[str], cwd: Path | None = None, runner=None) -> dict:
    fn = runner or subprocess.run
    try:
        r = fn(cmd, cwd=str(cwd or ROOT), capture_output=True, text=True,
               encoding="utf-8", errors="replace", timeout=60)
        return {"ok": getattr(r, "returncode", 1) == 0,
                "stdout": getattr(r, "stdout", "") or "",
                "stderr": getattr(r, "stderr", "") or ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stdout": "", "stderr": "%s: %s" % (type(e).__name__, e)}


def _commit_info(sha: str, runner=None) -> dict:
    r = _run(["git", "show", "-s", "--format=%H%n%an%n%ci%n%s", sha], runner=runner)
    if not r["ok"]:
        return {"ok": False, "error": "git_show_failed", "detail": r["stderr"][-200:]}
    lines = r["stdout"].strip().splitlines()
    return {"ok": True, "sha": lines[0], "author": lines[1] if len(lines) > 1 else "",
            "date": lines[2] if len(lines) > 2 else "",
            "subject": lines[3] if len(lines) > 3 else ""}


def _baseline() -> dict:
    try:
        d = json.loads(BASELINE.read_text(encoding="utf-8"))
        s = d.get("summary") or {}
        return {"ok": True, "passed": s.get("passed"), "failed": s.get("failed"),
                "error": s.get("error"), "generated_at": d.get("generated_at"),
                "path": str(BASELINE)}
    except (OSError, ValueError):
        return {"ok": False, "error": "baseline_missing"}


def _latest_redteam() -> dict:
    try:
        files = sorted(REDTEAM_DIR.glob("redteam_*.json"),
                       key=lambda p: p.stat().st_mtime)
        if not files:
            return {"ok": False, "error": "redteam_missing"}
        d = json.loads(files[-1].read_text(encoding="utf-8"))
        return {"ok": True, "total": d.get("total"), "passed": d.get("passed"),
                "pass_rate": d.get("pass_rate"), "generated_at": d.get("generated_at"),
                "path": str(files[-1])}
    except (OSError, ValueError):
        return {"ok": False, "error": "redteam_unreadable"}


def _verdict(base: dict, rt: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not base.get("ok"):
        reasons.append("测试基线缺失")
    elif (base.get("failed") or 0) > 0 or (base.get("error") or 0) > 0:
        reasons.append("基线失败：failed=%s error=%s"
                       % (base.get("failed"), base.get("error")))
    if not rt.get("ok"):
        reasons.append("红队报告缺失")
    elif (rt.get("pass_rate") or 0) < MIN_REDTEAM_RATE:
        reasons.append("红队通过率 %.0f%% < %.0f%%"
                       % ((rt.get("pass_rate") or 0) * 100, MIN_REDTEAM_RATE * 100))
    if reasons:
        hard = [r for r in reasons if "缺失" not in r]
        return ("REJECT" if hard else "OBSERVE"), reasons
    return "PASS", []


def accept(commit: str = "HEAD", out_dir: Path | None = None,
           runner=None) -> dict:
    info = _commit_info(commit, runner=runner)
    base = _baseline()
    rt = _latest_redteam()
    if not info.get("ok"):
        verdict, reasons = "OBSERVE", ["commit 无法读取"]
    else:
        verdict, reasons = _verdict(base, rt)
    rep = {
        "ok": True,
        "acceptance_id": "HZ-%s-%s" % (
            (info.get("sha") or "unknown")[:8],
            dt.datetime.now().strftime("%Y%m%d%H%M%S")),
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "observe",
        "blocking": False,
        "commit": info,
        "evidence": {"baseline": base, "redteam": rt},
        "verdict": verdict,
        "reasons": reasons,
        "rollback_point": ("git revert %s" % info["sha"][:12]
                           if info.get("ok") else "无（commit 不可读）"),
        "closed_by": "老田（人工）",
        "read_only": True,
        "note": "观察模式 v0：只出验收单，不阻断 merge；一周观察后再决定是否接入 merge 前置",
    }
    out = Path(out_dir) if out_dir else OUT
    out.mkdir(parents=True, exist_ok=True)
    tag = (info.get("sha") or "unknown")[:8]
    jp = out / ("acceptance_%s.json" % tag)
    jp.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# 黄忠验收单（观察模式）", "",
          "- 验收号：%s" % rep["acceptance_id"],
          "- Commit：%s %s" % (info.get("sha", "")[:12] if info.get("ok") else "?",
                               info.get("subject", "") if info.get("ok") else ""),
          "- 结论：**%s**（blocking=%s）" % (verdict, rep["blocking"]),
          "- 基线：%s" % (json.dumps(base.get("passed")) + " passed / "
                          + json.dumps(base.get("failed")) + " failed"
                          if base.get("ok") else base.get("error")),
          "- 红队：%s" % (("%.0f%%" % ((rt.get("pass_rate") or 0) * 100))
                          if rt.get("ok") else rt.get("error")),
          "",
          "## 理由", *(["- " + x for x in reasons] or ["- 全部证据满足（PASS）"]),
          "", "## 回滚点", "- " + rep["rollback_point"],
          "", "> 观察模式：不阻断 merge；最终准入由老田人工决定。"]
    mp = out / ("acceptance_%s.md" % tag)
    mp.write_text("\n".join(md), encoding="utf-8")
    rep["report_json"] = str(jp)
    rep["report_md"] = str(mp)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="黄忠验收守门 v0（观察模式）")
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rep = accept(args.commit, Path(args.out_dir) if args.out_dir else None)
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json
          else "验收：%s（%s）→ %s" % (rep["verdict"], rep["commit"].get("sha", "")[:8],
                                       rep["report_md"]))
    return 0 if rep["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
