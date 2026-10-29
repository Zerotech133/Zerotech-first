# -*- coding: utf-8 -*-
"""赵云评估飞轮：跑行为目录，产出 session/trace/span 三层指标。

用法：
    python agent_platform/evals/run_evals.py --json
    python agent_platform/evals/run_evals.py --smoke --json      # QualityGate 快速子集
    python agent_platform/evals/run_evals.py                     # 人类可读摘要

设计：
  · 行为来源 behaviors.json；check 类型：skill（技能 verify）/builtin（内置断言）/file；
  · 指标：session（行为通过率）、trace（技能/工具路径是否存在）、span（各断言耗时）、
    覆盖率（有断言的行为/总行为）、harmful_call_rate（保留字段，v1=0）；
  · 结果落 evals/reports/latest.json + by-date 历史；不自动改任何真源。
"""

from __future__ import annotations

import argparse
import os
import json
import sys
import time
from pathlib import Path
import logging

# Windows：默认 locale=GBK，子进程 text=True 未指定编码会解码崩；统一 UTF-8 模式（仅进程环境）。
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

try:  # Windows 管道输出统一 UTF-8（子进程 JSON/中文摘要防 cp936 解码失败）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/evals/run_evals.py", exc)

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
EVALS = PLATFORM / "evals"
BEHAVIORS = EVALS / "behaviors.json"
REPORTS = EVALS / "reports"


def _load() -> dict:
    return json.loads(BEHAVIORS.read_text(encoding="utf-8-sig"))


def _skill_verify(name: str) -> dict:
    sys.path.insert(0, str(PLATFORM / "tools"))
    from skill_registry import verify  # noqa: WPS433

    return verify(name)


def _builtin(name: str) -> dict:
    if name == "skills_registry_total":
        sys.path.insert(0, str(PLATFORM / "tools"))
        from skill_registry import load_registry  # noqa: WPS433

        reg = load_registry()
        bad = [
            s["name"]
            for s in reg.get("skills") or []
            if not (REPO / s["path"]).exists()
            or not (REPO / Path(s["path"]).parent / "scripts" / "verify.py").exists()
        ]
        return {
            "ok": reg.get("total") == 14 and not bad,
            "detail": "total=%s bad=%s" % (reg.get("total"), bad[:3]),
        }
    if name == "selfaudit_source":
        sys.path.insert(0, str(PLATFORM / "tools"))
        from capability_self_audit import check  # noqa: WPS433

        rep = check()
        return {
            "ok": rep.get("source") == "skills_registry" and rep.get("ok") is True,
            "detail": "source=%s drift=%s"
            % (rep.get("source"), len(rep.get("drift") or [])),
        }
    if name == "trace_writable":
        sys.path.insert(0, str(PLATFORM))
        from observability import trace as tr  # noqa: WPS433

        p = tr.DB
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            probe = p.parent / ".eval_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return {"ok": True, "detail": str(p.parent)}
        except OSError as e:
            return {"ok": False, "detail": str(e)[:160]}
    if name == "approval_kinds":
        sys.path.insert(0, str(PLATFORM))
        from legion_command import zhaoyun_cap2 as c2  # noqa: WPS433

        need = {
            "memory_quarantine",
            "capability_learn",
            "model_change",
            "task_intervention",
        }
        return {
            "ok": need.issubset(set(c2.REQUESTS_KIND)),
            "detail": "kinds=%s" % (list(c2.REQUESTS_KIND),),
        }
    if name == "approval_signer_required":
        sys.path.insert(0, str(PLATFORM))
        from legion_command import zhaoyun_cap2 as c2  # noqa: WPS433

        res = c2.approve(PLATFORM, "NO-SUCH-REQUEST", "approve", "")
        return {
            "ok": res.get("error") == "signer_required",
            "detail": "error=%s" % res.get("error"),
        }
    return {"ok": False, "detail": "unknown_builtin:%s" % name}


def run(check_name: str, item: dict) -> dict:
    t0 = time.time()
    try:
        if item.get("skill"):
            r = _skill_verify(str(item["skill"]))
        elif item.get("builtin"):
            r = _builtin(str(item["builtin"]))
        elif item.get("file"):
            p = Path(item["file"])
            p = p if p.is_absolute() else REPO / p
            r = {"ok": p.exists(), "detail": str(p)}
        else:
            r = {"ok": False, "detail": "no_check"}
    except Exception as e:  # noqa: BLE001
        r = {"ok": False, "detail": "%s: %s" % (type(e).__name__, str(e)[:160])}
    return {
        "id": check_name,
        "ok": bool(r.get("ok")),
        "detail": str(r.get("detail") or "")[:200],
        "elapsed_ms": int((time.time() - t0) * 1000),
    }


def build_report(smoke: bool = False, judge: bool = False) -> dict:
    data = _load()
    items = [b for b in data.get("behaviors") or [] if isinstance(b, dict)]
    if smoke:
        items = [b for b in items if b.get("smoke")]
    results = [run(b["id"], b) for b in items]
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    high_failed = [
        r["id"]
        for b, r in zip(items, results)
        if b.get("severity") == "high" and not r["ok"]
    ]
    rep = {
        "schema": "zhaoyun_evals_report/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "smoke": bool(smoke),
        "session": {
            "passed": passed,
            "total": total,
            "rate": round(passed / total, 3) if total else 0.0,
        },
        "trace": {"behaviors_with_assertions": total, "skills_registry": 14},
        "span": {
            "slowest_ms": max((r["elapsed_ms"] for r in results), default=0),
            "avg_ms": round(sum(r["elapsed_ms"] for r in results) / total, 1)
            if total
            else 0.0,
        },
        "coverage": {
            "behaviors": len(items),
            "assertions": total,
            "rate": round(total / max(1, len(items)), 3),
        },
        "harmful_call_rate": 0.0,
        "high_failed": high_failed,
        "ok": not high_failed,
        "failures": [r for r in results if not r["ok"]],
        "results": results,
    }
    if judge and not smoke:
        try:
            sys.path.insert(0, str(EVALS))
            from judge import judge_text  # noqa: WPS433

            summary = "评估报告：session %d/%d，失败 %s" % (
                passed,
                total,
                [r["id"] for r in rep["failures"][:5]],
            )
            rep["judge"] = judge_text(summary)
        except Exception as e:  # noqa: BLE001
            rep["judge"] = {
                "ok": False,
                "error": "judge_failed",
                "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
            }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "latest.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    day = time.strftime("%Y%m%d")
    (REPORTS / ("%s_%s.json" % (day, "smoke" if smoke else "full"))).write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="赵云评估飞轮")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument(
        "--judge", action="store_true", help="全量评估后追加 LLM-as-judge（预算内）"
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    rep = build_report(args.smoke, judge=args.judge)
    passed = rep["session"]["passed"]
    total = rep["session"]["total"]
    if args.json:
        print(json.dumps(rep, ensure_ascii=False))
    elif not args.quiet:
        print(
            "评估 | passed %d/%d (%.1f%%) | high_failed=%s | 最慢 %dms"
            % (
                passed,
                total,
                (passed / total * 100) if total else 0,
                rep["high_failed"],
                rep["span"]["slowest_ms"],
            )
        )
        for r in rep["failures"]:
            print(" [FAIL]", r["id"], r["detail"])
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
