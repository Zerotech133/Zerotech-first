# -*- coding: utf-8 -*-
"""席位评测跑批器（P0① · WO-EVAL-SEATS-20261027）。

黄金集 = agent_platform/evals/seat_golden.json：每席若干条确定性检查
（命令 rc/stdout 或文件存在/包含），零 LLM 调用、零副作用（全部 --dry-run）。
评分 = 通过数/总数；默认只报不拦（--strict 才退出 1）。

用法：
  python -X utf8 agent_platform/tools/seat_eval.py
  python -X utf8 agent_platform/tools/seat_eval.py --seat ARCH1 --json
  python -X utf8 agent_platform/tools/seat_eval.py --no-write --strict
退出码：0 正常（或全过）；1 --strict 且有失败；2 配置/输入错误。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
GOLDEN = REPO / "agent_platform" / "evals" / "seat_golden.json"
OUT_DIR = REPO / "agent_platform" / "data" / "academy" / "evals"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("[seat_eval] 无法读取 %s (%s)" % (path, exc))
        raise SystemExit(2)


def _run_case(case: dict, root: Path) -> dict:
    cid = case.get("id") or "?"
    kind = case.get("kind")
    try:
        if kind == "cmd":
            argv = [sys.executable, "-X", "utf8", *case.get("args", [])]
            r = subprocess.run(argv, cwd=str(root), capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=int(case.get("timeout", 120)))
            out = (r.stdout or "") + "\n" + (r.stderr or "")
            ok = r.returncode == int(case.get("expect_rc", 0))
            missing = [s for s in case.get("contains", []) if s not in out]
            ok = ok and not missing
            detail = "rc=%s" % r.returncode
            if missing:
                detail += " missing=%s" % missing
            return {"id": cid, "kind": kind, "ok": bool(ok), "detail": detail}
        if kind == "file":
            p = (root / case.get("path", "")).resolve()
            ok = p.is_file()
            detail = "path=%s exists=%s" % (case.get("path"), ok)
            needle = case.get("contains")
            if ok and needle:
                text = p.read_text(encoding="utf-8", errors="replace")
                if needle not in text:
                    ok = False
                    detail += " missing_contains=%s" % needle
            return {"id": cid, "kind": kind, "ok": bool(ok), "detail": detail}
        return {"id": cid, "kind": kind, "ok": False, "detail": "unknown kind"}
    except subprocess.TimeoutExpired:
        return {"id": cid, "kind": kind, "ok": False, "detail": "timeout"}
    except OSError as exc:
        return {"id": cid, "kind": kind, "ok": False, "detail": "OSError: %s" % exc}


def run(root: Path, golden_path: Path, only_seat: str | None = None) -> dict:
    data = _read_json(golden_path)
    seats = data.get("seats") or {}
    if only_seat:
        seats = {k: v for k, v in seats.items() if k == only_seat}
    results = []
    for seat, spec in seats.items():
        cases = spec.get("cases") or []
        rs = [_run_case(c, root) for c in cases]
        passed = sum(1 for r in rs if r["ok"])
        total = len(rs)
        results.append({
            "seat": seat,
            "label": spec.get("label", ""),
            "passed": passed,
            "total": total,
            "rate": round(passed / total, 3) if total else 0.0,
            "cases": rs,
        })
    overall_passed = sum(r["passed"] for r in results)
    overall_total = sum(r["total"] for r in results)
    return {
        "schema": "seat_eval_report/v1",
        "date": dt.date.today().isoformat(),
        "golden": str(golden_path),
        "seats": results,
        "overall": {
            "passed": overall_passed,
            "total": overall_total,
            "rate": round(overall_passed / overall_total, 3) if overall_total else 0.0,
        },
    }


def _print_table(report: dict) -> None:
    print("席位评测（黄金集）%s ｜ 总计 %d/%d = %.1f%%"
          % (report["date"], report["overall"]["passed"], report["overall"]["total"],
             report["overall"]["rate"] * 100))
    for s in report["seats"]:
        print("  %-6s %-18s %d/%d (%.1f%%)"
              % (s["seat"], s["label"], s["passed"], s["total"], s["rate"] * 100))
        for c in s["cases"]:
            if not c["ok"]:
                print("      FAIL %-28s %s" % (c["id"], c["detail"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="席位黄金集评测跑批")
    ap.add_argument("--seat", default=None, help="只跑某席（如 ARCH1）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（不写文件）")
    ap.add_argument("--no-write", action="store_true", help="不落盘")
    ap.add_argument("--strict", action="store_true", help="有失败则退出 1")
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--golden", default=str(GOLDEN))
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    golden = Path(args.golden)
    if not golden.is_file():
        print("[seat_eval] 黄金集不存在: %s" % golden)
        return 2
    report = run(root, golden, args.seat)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print_table(report)
    if not args.no_write and not args.json:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / ("seat_eval_%s.json" % dt.date.today().isoformat())
        out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("报告: %s" % out)
    if args.strict and report["overall"]["passed"] < report["overall"]["total"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
