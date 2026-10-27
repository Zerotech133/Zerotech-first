# -*- coding: utf-8 -*-
"""ARCH1 架构/技术债周巡检（WO-LEGION-ARCH1-PATROL）。

定位：ARCH1 曹操（研发部主官/架构师）的**只读周巡检**——把「质量门是否漂移、ADR 是否断供、
近期架构相关计划文档、在途 worktree 规模」四类信号汇总成一份简报，并 emit patrol_verdict
（agent=task=ARCH1）供 seat_realness 审计维取证。

不做（边界）：
- 不跑回测、不联网、不写数据根、不改代码/配置；
- 只读 git/worktree/文件清单 + 两个既有质量门脚本（_mixin_integrity / unify_nav）的 rc；
- 质量门失败只报 P1 建议开单，不自行修复。

用法：
  python -X utf8 agent_platform/tools/arch1_ops.py weekly [--root PATH] [--dry-run] [--skip-gates] [--json]
退出码：0 正常产出；2 自身错误（仓库根/依赖缺失）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT_DEFAULT = HERE.parents[2]
DEV_REL = Path("agent_platform") / "data" / "dev"
ADR_REL = Path("docs") / "adr"
PLANS_REL = Path("docs") / "plans"

try:  # P0② trace（可选依赖，失败不阻断值守）
    from trace_cost import record as _trace_record
except Exception:  # noqa: BLE001
    _trace_record = None


def _today() -> str:
    return dt.date.today().isoformat()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _emit(root: Path, detail: str, no_emit: bool = False) -> dict:
    if no_emit:
        return {"ok": False, "skipped": True}
    try:
        sys.path.insert(0, str(root / "tools"))
        import events  # noqa: WPS433

        return events.emit(
            kind="patrol_verdict", task="ARCH1", agent="ARCH1", detail=detail[:300]
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:100])}


def _run_gate(root: Path, name: str, script: str, args: list[str]):
    cmd = [sys.executable, "-X", "utf8", str(root / script)] + list(args)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()
        return {
            "name": name,
            "rc": proc.returncode,
            "ok": proc.returncode == 0,
            "tail": tail[-1][:200] if tail else "",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "rc": None,
            "ok": False,
            "tail": "%s: %s" % (type(exc).__name__, str(exc)[:120]),
        }


def _adr_inventory(root: Path) -> dict:
    adr = root / ADR_REL
    files = (
        sorted(adr.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if adr.is_dir()
        else []
    )
    days_since = None
    if files:
        newest = max(p.stat().st_mtime for p in files)
        days_since = round((dt.datetime.now().timestamp() - newest) / 86400, 1)
    return {
        "count": len(files),
        "latest": [p.name for p in files[:5]],
        "newest_age_days": days_since,
        "stale": bool(days_since is not None and days_since > 14),
    }


def _recent_plans(root: Path, days: int = 7) -> list[str]:
    plans = root / PLANS_REL
    if not plans.is_dir():
        return []
    cutoff = dt.datetime.now().timestamp() - days * 86400
    recent = [p for p in plans.glob("*.md") if p.stat().st_mtime >= cutoff]
    recent.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.name for p in recent[:8]]


def _worktree_count(root: Path) -> int:
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return sum(
            1 for ln in (proc.stdout or "").splitlines() if ln.startswith("worktree ")
        )
    except Exception:  # noqa: BLE001
        return -1


def build_weekly(root: Path, skip_gates: bool = False) -> tuple[str, dict]:
    if skip_gates:
        gates = [
            {
                "name": "_mixin_integrity",
                "rc": None,
                "ok": None,
                "tail": "skipped (--skip-gates)",
            },
            {
                "name": "unify_nav",
                "rc": None,
                "ok": None,
                "tail": "skipped (--skip-gates)",
            },
        ]
    else:
        gates = [
            _run_gate(
                root, "_mixin_integrity", "tools/_mixin_integrity.py", ["--check"]
            ),
            _run_gate(root, "unify_nav", "tools/unify_nav.py", ["--check"]),
        ]
    adr = _adr_inventory(root)
    plans = _recent_plans(root)
    wts = _worktree_count(root)
    failed = [g["name"] for g in gates if g.get("ok") is False]
    lines = [
        "# ARCH1 架构/技术债巡检 %s" % _today(),
        "",
        "> 生成：agent_platform/tools/arch1_ops.py weekly ｜ 责任：ARCH1 曹操 ｜ 只读巡检，不修复、不开单",
        "",
        "## 一、质量门（既有门，只读 rc）",
        "| 门 | rc | 结果 | 末行 |",
        "|---|---|---|---|",
    ]
    for g in gates:
        lines.append(
            "| %s | %s | %s | %s |"
            % (
                g["name"],
                g["rc"],
                "✓ 通过"
                if g.get("ok")
                else ("- 跳过" if g.get("ok") is None else "✗ 失败"),
                (g["tail"] or "").replace("|", "/")[:120],
            )
        )
    lines += ["", "## 二、ADR 台账", ""]
    lines.append(
        "- ADR 数量：**%d**；最新：%s"
        % (adr["count"], "、".join(adr["latest"]) if adr["latest"] else "无")
    )
    if adr["newest_age_days"] is not None:
        lines.append(
            "- 最新 ADR 距今：%s 天%s"
            % (
                adr["newest_age_days"],
                "（**断供 >14 天，建议补 ADR**）" if adr["stale"] else "",
            )
        )
    if not adr["count"]:
        lines.append(
            "- ⚠ `docs/adr/` 无 ADR：重大架构决策需按 K-ORG-041 用 `tools/adr.py` 登记"
        )
    lines += ["", "## 三、近期架构相关计划文档（近 7 天）"]
    if plans:
        for name in plans:
            lines.append("- %s" % name)
    else:
        lines.append("- （近 7 天无 docs/plans 变更）")
    lines += [
        "",
        "## 四、研发并行规模（只读）",
        "- 在途 worktree：**%s**" % (wts if wts >= 0 else "未取得"),
    ]
    lines += ["", "## 五、结论与建议"]
    if failed:
        lines.append(
            "- **质量门异常：%s** → P1：开单修复（ARCH1 派 G1/G2 组长，走 dev 路由规则）"
            % "、".join(failed)
        )
    else:
        lines.append("- 质量门：无异常（跳过项除外）")
    if adr["stale"]:
        lines.append("- ADR 断供：建议本周补登（tools/adr.py new）")
    lines += [
        "- 架构债信号：以质量门+ADR+在途 worktree 三项为准；本巡检只报不修。",
        "",
        "## 六、边界",
        "- 只读；不改代码/配置；不跑回测；不联网；质量门失败不自行修复，只建议开单。",
    ]
    summary = {
        "date": _today(),
        "gates": gates,
        "adr": adr,
        "recent_plans": len(plans),
        "worktrees": wts,
        "failed_gates": failed,
    }
    return "\n".join(lines) + "\n", summary


def _out_path(root: Path) -> Path:
    return root / DEV_REL / "arch1" / ("%s_patrol.md" % _today())


def run(
    root: Path,
    dry_run: bool = False,
    no_emit: bool = False,
    skip_gates: bool = False,
    json_out: bool = False,
) -> dict:
    t0 = time.monotonic()
    text, summary = build_weekly(root, skip_gates=skip_gates)
    out = _out_path(root)
    written = ""
    emit = {"ok": False, "skipped": True}
    if not dry_run:
        _write_atomic(out, text)
        written = str(out.relative_to(root)).replace("\\", "/")
        emit = _emit(
            root,
            "arch1_patrol gates=%d/%d adr=%d worktrees=%s"
            % (
                len(summary["gates"]) - len(summary["failed_gates"]),
                len(summary["gates"]),
                summary["adr"]["count"],
                summary["worktrees"],
            ),
            no_emit=no_emit,
        )
    if not dry_run and _trace_record:
        try:
            _trace_record(
                "ARCH1", "arch1_weekly",
                status="ok" if not summary["failed_gates"] else "fail",
                elapsed_ms=(time.monotonic() - t0) * 1000,
                root=root,
                detail="gates=%d/%d adr=%d worktrees=%s"
                % (len(summary["gates"]) - len(summary["failed_gates"]),
                   len(summary["gates"]), summary["adr"]["count"], summary["worktrees"]),
            )
        except Exception:  # noqa: BLE001
            pass
    result = {
        "action": "weekly",
        "dry_run": bool(dry_run),
        "written": written,
        "out": str(out.relative_to(root)).replace("\\", "/"),
        "summary": summary,
        "emit": emit,
    }
    if json_out or dry_run:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        print(
            "[arch1_ops] weekly -> %s ｜ failed_gates=%s ｜ emit=%s"
            % (written, summary["failed_gates"], emit.get("ok"))
        )
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ARCH1 架构/技术债周巡检")
    ap.add_argument("action", choices=("weekly",), nargs="?", default="weekly")
    ap.add_argument("--root", default=str(ROOT_DEFAULT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-emit", action="store_true")
    ap.add_argument("--skip-gates", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / "tools" / "_mixin_integrity.py").is_file():
        print(
            json.dumps(
                {"ok": False, "error": "repo root invalid", "root": str(root)},
                ensure_ascii=False,
            )
        )
        return 2
    try:
        run(
            root,
            dry_run=args.dry_run,
            no_emit=args.no_emit,
            skip_gates=args.skip_gates,
            json_out=args.json,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])},
                ensure_ascii=False,
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
