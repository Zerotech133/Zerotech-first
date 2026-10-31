# -*- coding: utf-8 -*-
"""huangzhong_1104_run.py — 11-04 复核一键执行器（只读编排，v1）。

一条命令按顺序执行 11-04 复核所需的 12 个只读工具，汇总 JSON + 中文 md，
供 2026-11-04 10:00 复核使用；不改变任何判定逻辑、不写 work/。

**必须在主仓（C:\\项目\\老田的AI助手）运行**：子工具按其自身路径定位 work/
数据，在 worktree 内运行会因缺 work/ 数据而误报 BLOCKER。

约定：
  · 子进程统一经模块级 _run(cmd, cwd, timeout) 执行（测试可注入）；
  · review/go_no_go 的 rc=1 属业务正常（六条未全过/建议 NO-GO），不算执行失败；
  · preflight blockers>0 / 子进程 rc=2 / 关键步骤失败 → 汇总 fail（rc=2）；
  · 非关键步骤 warn（rc=1/ok=False）→ 汇总 warn（rc=1）；全绿 → ok（rc=0）。
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# name, argv（相对仓库根）, critical, 可接受的返回码
STEPS = [
    {"name": "preflight", "argv": ["tools/huangzhong_1104_preflight.py", "--json"],
     "critical": True, "accept_rc": (0, 1)},
    {"name": "review", "argv": ["tools/huangzhong_review.py", "--json"],
     "critical": True, "accept_rc": (0, 1)},
    {"name": "weekly", "argv": ["tools/huangzhong_daily.py", "--weekly", "--json"],
     "critical": True, "accept_rc": (0,)},
    {"name": "receipt", "argv": ["tools/huangzhong_1104_receipt.py", "--json"],
     "critical": False, "accept_rc": (0,)},
    {"name": "risk_coverage", "argv": ["tools/huangzhong_risk_coverage.py", "--days", "7", "--json"],
     "critical": False, "accept_rc": (0,)},
    {"name": "corpus", "argv": ["tools/huangzhong_corpus.py", "--json"],
     "critical": False, "accept_rc": (0,)},
    {"name": "peer_verify", "argv": ["tools/huangzhong_peer_verify.py", "--json"],
     "critical": False, "accept_rc": (0, 1)},
    {"name": "evidence_lint", "argv": ["tools/huangzhong_evidence_lint.py", "--json"],
     "critical": False, "accept_rc": (0,)},
    {"name": "incidents", "argv": ["tools/huangzhong_incidents.py", "verify", "--json"],
     "critical": False, "accept_rc": (0,)},
    {"name": "health", "argv": ["tools/huangzhong_health.py", "--json"],
     "critical": False, "accept_rc": (0, 1)},
    {"name": "perf_signal", "argv": ["tools/huangzhong_perf_signal.py", "--json"],
     "critical": False, "accept_rc": (0, 1)},
    {"name": "security_signal", "argv": ["tools/huangzhong_security_signal.py", "--json"],
     "critical": False, "accept_rc": (0, 1)},
]


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _iso(ts: dt.datetime) -> str:
    return ts.isoformat(timespec="seconds")


def _run(cmd: list, cwd: Path | None = None, timeout: int = 600):
    """默认执行器（测试可 monkeypatch）。"""
    return subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout)


def _extract_json(text: str):
    if not text:
        return None
    i = text.find("{")
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[i:])[0]
    except ValueError:
        return None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _summarize(name: str, d: dict) -> dict:
    """从各工具 JSON 提取关键字段（缺失即 None，不编造）。"""
    if not isinstance(d, dict):
        return {}
    if name == "preflight":
        return {"ready": d.get("ready"),
                "blockers": len(d.get("blockers") or []),
                "warnings": len(d.get("warnings") or []),
                "mature_at_1030": ((d.get("maturity") or {}).get("mature_at_1030")),
                "earliest_window_end": ((d.get("maturity") or {}).get("earliest_window_end"))}
    if name == "review":
        s = d.get("summary") or {}
        return {"passed": s.get("passed"), "total": s.get("total"),
                "overall": s.get("overall_pass")}
    if name == "weekly":
        return {"runs": d.get("runs"), "merge_done": d.get("merge_done"),
                "coverage": d.get("coverage_rate")}
    if name == "receipt":
        return {"draft": (d.get("draft") or {}).get("path")}
    if name == "risk_coverage":
        s = d.get("summary") or {}
        return {"risk_weighted": s.get("risk_weighted_coverage"),
                "equal": s.get("equal_coverage_all"),
                "merges": s.get("merges_total")}
    if name == "corpus":
        return {"total": d.get("total") or d.get("corpus_total") or d.get("count"),
                "legacy": d.get("legacy_count"),
                "drill": d.get("drill_count")}
    if name == "peer_verify":
        return {"status": d.get("status"),
                "agreement": d.get("agreement_rate") or d.get("agreement")}
    if name == "evidence_lint":
        return {"completeness": d.get("completeness"),
                "scanned": d.get("scanned")}
    if name == "incidents":
        return {"exists": d.get("exists"), "rows": d.get("rows"),
                "issues": d.get("issue_count")}
    if name == "health":
        return {"status": d.get("status")}
    if name == "perf_signal":
        return {"status": d.get("status")}
    if name == "security_signal":
        return {"status": d.get("status")}
    return {}


def run_pipeline(out_dir: Path, timeout: int = 600, write: bool = True,
                 run_fn=None) -> dict:
    run_fn = run_fn or _run
    now = _now()
    steps = []
    for spec in STEPS:
        name = spec["name"]
        cmd = [PY, "-X", "utf8", *spec["argv"]]
        rc, stdout, stderr, err = None, "", "", None
        try:
            r = run_fn(cmd, cwd=ROOT, timeout=timeout)
            rc = getattr(r, "returncode", None)
            stdout = getattr(r, "stdout", "") or ""
            stderr = getattr(r, "stderr", "") or ""
        except Exception as e:  # 超时/执行异常 → fail-visible
            err = "%s: %s" % (type(e).__name__, e)
        data = _extract_json(stdout)
        ok = (rc in spec["accept_rc"]) and (data is not None)
        # rc≠0 但被 accept_rc 容忍 → warn（review rc=1 为业务正常，除外；
        # preflight rc=1=有 warning、health/perf/security rc=1=warn）。
        soft_warn = ok and rc != 0 and name != "review"
        steps.append({
            "name": name, "rc": rc, "accepted": rc in spec["accept_rc"],
            "ok": ok, "critical": spec["critical"], "soft_warn": soft_warn,
            "summary": _summarize(name, data),
            "error": err or (None if ok else ("no-json" if data is None else "rc-not-accepted")),
            "stderr_tail": (stderr or "")[-300:] if not ok else "",
        })
    pre = next(s for s in steps if s["name"] == "preflight")
    critical_bad = [s["name"] for s in steps if s["critical"] and not s["ok"]]
    hard_bad = [s["name"] for s in steps if s["rc"] == 2]
    warn_bad = [s["name"] for s in steps if s.get("soft_warn")
                or (not s["critical"] and not s["ok"])
                and s["name"] not in hard_bad]
    if critical_bad or hard_bad or (pre["summary"].get("blockers") or 0) > 0:
        status = "fail"
    elif warn_bad or (pre["summary"].get("warnings") or 0) > 0:
        status = "warn"
    else:
        status = "ok"
    rep = {
        "ok": status != "fail",
        "tool": "huangzhong_1104_run.py",
        "version": "v1 (2026-10-31)",
        "generated_at": _iso(now),
        "status": status,
        "repo_root": str(ROOT),
        "steps": steps,
        "critical_bad": critical_bad,
        "hard_bad": hard_bad,
        "warn_steps": warn_bad,
        "report_md": None,
    }
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        md = out_dir / ("2026-11-04_黄忠复核执行汇总_%s.md" % now.strftime("%Y%m%d_%H%M%S"))
        md.write_text(_render_md(rep), encoding="utf-8")
        rep["report_md"] = str(md)
    return rep


def _fmt(v):
    return "—" if v is None else str(v)


def _render_md(rep: dict) -> str:
    lines = [
        "# 黄忠 11-04 复核执行汇总（%s）" % rep["generated_at"],
        "",
        "> 工具：`tools/huangzhong_1104_run.py` v1（只读编排）｜状态：**%s**" % rep["status"],
        "> 说明：本汇总只搬运子工具结果，不改变任何判定；Go/No-Go 由老田 L3 决策。",
        "",
        "## 一、执行总览",
        "",
        "| 步骤 | rc | 状态 | 关键结果 |",
        "|---|---|---|---|",
    ]
    for s in rep["steps"]:
        sm = s["summary"]
        if s["name"] == "preflight":
            extra = "ready=%s blockers=%s warnings=%s mature_at_1030=%s" % (
                _fmt(sm.get("ready")), _fmt(sm.get("blockers")),
                _fmt(sm.get("warnings")), _fmt(sm.get("mature_at_1030")))
        elif s["name"] == "review":
            extra = "六条 %s/%s（overall=%s）" % (
                _fmt(sm.get("passed")), _fmt(sm.get("total")), _fmt(sm.get("overall")))
        elif s["name"] == "weekly":
            extra = "runs=%s merge=%s coverage=%s" % (
                _fmt(sm.get("runs")), _fmt(sm.get("merge_done")), _fmt(sm.get("coverage")))
        elif s["name"] == "receipt":
            extra = "draft=%s" % _fmt(sm.get("draft"))
        elif s["name"] == "risk_coverage":
            extra = "风险加权=%s 等权=%s" % (
                _fmt(sm.get("risk_weighted")), _fmt(sm.get("equal")))
        elif s["name"] == "corpus":
            extra = "total=%s legacy=%s drill=%s" % (
                _fmt(sm.get("total")), _fmt(sm.get("legacy")), _fmt(sm.get("drill")))
        elif s["name"] == "peer_verify":
            extra = "status=%s agreement=%s" % (
                _fmt(sm.get("status")), _fmt(sm.get("agreement")))
        elif s["name"] == "evidence_lint":
            extra = "完整率=%s scanned=%s" % (
                _fmt(sm.get("completeness")), _fmt(sm.get("scanned")))
        elif s["name"] == "incidents":
            extra = "exists=%s rows=%s" % (
                _fmt(sm.get("exists")), _fmt(sm.get("rows")))
        else:
            extra = "status=%s" % _fmt(sm.get("status"))
        lines.append("| %s | %s | %s | %s |" % (
            s["name"], _fmt(s["rc"]), "✓" if s["ok"] else "✗", extra))
    lines += ["", "## 二、异常与建议", ""]
    if rep["status"] == "ok":
        lines.append("- 全部步骤通过；可进入人工核对（六条/三口径覆盖率/④满窗）与 Go/No-Go。")
    else:
        if rep["critical_bad"]:
            lines.append("- 关键步骤失败：%s（需先处理）" % "、".join(rep["critical_bad"]))
        if rep["hard_bad"]:
            lines.append("- 硬失败（rc=2）：%s" % "、".join(rep["hard_bad"]))
        if rep["warn_steps"]:
            lines.append("- 警告步骤：%s（可人工复核后继续）" % "、".join(rep["warn_steps"]))
    lines += ["", "## 三、复核动作提示", "",
              "1. 核对 receipt 草案的六条/三口径覆盖率/L3 提示；",
              "2. ④ 满窗：preflight.mature_at_1030 与最早满窗时间；",
              "3. 人工签字后写 `deliverables/2026-11-04_黄忠观察期复核回执.md`；",
              "4. Go = 六条 6/6 + 覆盖率达标；Go 需老田 L3 单独批。", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="黄忠 11-04 复核一键执行器（只读编排）")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--out-dir", default=str(Path(r"C:\项目\deliverables")),
                    help="汇总 md 输出目录（默认 C:\\项目\\deliverables）")
    ap.add_argument("--no-write", action="store_true", help="不写 md（纯只读）")
    ap.add_argument("--timeout", type=int, default=600, help="单步超时秒（默认 600）")
    args = ap.parse_args(argv)
    rep = run_pipeline(Path(args.out_dir), timeout=args.timeout,
                       write=not args.no_write)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("黄忠 11-04 一键执行：status=%s rc=%s"
              % (rep["status"], 0 if rep["status"] == "ok" else (1 if rep["status"] == "warn" else 2)))
        for s in rep["steps"]:
            print("  %-16s rc=%-3s %s" % (s["name"], _fmt(s["rc"]), "✓" if s["ok"] else "✗"))
        if rep.get("report_md"):
            print("汇总 md → %s" % rep["report_md"])
    return 0 if rep["status"] == "ok" else (1 if rep["status"] == "warn" else 2)


if __name__ == "__main__":
    sys.exit(main())
