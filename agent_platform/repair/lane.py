# -*- coding: utf-8 -*-
"""关羽修复车道 v1：事故/失败 triage → 批次 → pytest 运行 → 留证。

边界：
  · 只读 triage（evals/incidents 草稿 + dev_events guard_fail/blocked），不自动改工单/代码；
  · 批次 target 必须是仓库内 .py 测试文件（路径白名单，防任意命令）；
  · 运行 = `python -m pytest <target> -q`（无 shell、超时、输出截断），结果落 data/repair/lane.json；
  · 修复动作本身仍走工单/worktree/评审门；本模块只做"发现+验证+证据"。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
import logging

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
DATA = PLATFORM / "data" / "repair"
LANE = DATA / "lane.json"
INCIDENTS = PLATFORM / "evals" / "incidents"
MAIN_REPO = REPO


def _main_repo() -> Path:
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            common = Path(r.stdout.strip())
            if not common.is_absolute():
                common = (REPO / common).resolve()
            return common.parent
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/repair/lane.py", exc)
    return REPO


def _state() -> dict:
    try:
        return json.loads(LANE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"schema": "guanyu_repair_lane/v1", "batches": [], "updated_at": ""}


def _save(st: dict) -> None:
    st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    DATA.mkdir(parents=True, exist_ok=True)
    LANE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def _module_of(text: str) -> str:
    s = str(text or "")
    m = re.search(r"([A-Za-z0-9_./\\-]+\.py)", s)
    if m:
        return m.group(1).replace("\\", "/")
    m = re.search(r"([A-Za-z][A-Za-z0-9_-]{2,})-", s)
    return m.group(1) if m else "unknown"


def triage() -> dict:
    st = _state()
    known = {b.get("module") for b in st.get("batches") or []}
    found = []
    if INCIDENTS.is_dir():
        for f in sorted(INCIDENTS.glob("INC-*.json"))[:100]:
            try:
                rec = json.loads(f.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            src = rec.get("source_event") or {}
            module = _module_of("%s %s" % (src.get("task"), src.get("detail")))
            found.append(
                {
                    "module": module,
                    "incident": rec.get("id"),
                    "evidence": str(src.get("detail") or "")[:200],
                    "kind": src.get("kind"),
                }
            )
    main = _main_repo()
    ev = main / "portal" / "data" / "dev_events.jsonl"
    if ev.is_file():
        try:
            lines = ev.read_text(encoding="utf-8", errors="replace").splitlines()[
                -2000:
            ]
        except OSError:
            lines = []
        for ln in lines:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if str(e.get("kind")) not in ("guard_fail", "blocked"):
                continue
            module = _module_of("%s %s" % (e.get("task"), e.get("detail")))
            found.append(
                {
                    "module": module,
                    "incident": "EV-" + str(e.get("ts")),
                    "evidence": str(e.get("detail") or "")[:200],
                    "kind": e.get("kind"),
                }
            )
    created = 0
    for item in found:
        mod = item["module"]
        if not mod or mod == "unknown" or mod in known:
            continue
        st.setdefault("batches", []).append(
            {
                "id": "RB-%s" % uuid.uuid4().hex[:8],
                "module": mod,
                "incidents": [item["incident"]],
                "kind": item.get("kind"),
                "evidence": item.get("evidence"),
                "target": "",
                "status": "pending",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "last_result": None,
            }
        )
        known.add(mod)
        created += 1
    _save(st)
    return {
        "ok": True,
        "found": len(found),
        "new_batches": created,
        "pending": len(st.get("batches") or []),
        "batches": st.get("batches"),
    }


def create_batch(module: str, target: str = "") -> dict:
    module = str(module or "").strip()[:200]
    if not module:
        return {"ok": False, "error": "module_required"}
    st = _state()
    st.setdefault("batches", []).append(
        {
            "id": "RB-%s" % uuid.uuid4().hex[:8],
            "module": module,
            "incidents": [],
            "kind": "manual",
            "evidence": "",
            "target": str(target or ""),
            "status": "pending",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "last_result": None,
        }
    )
    _save(st)
    return {"ok": True, "batch": st["batches"][-1], "count": len(st["batches"])}


def _safe_target(target: str) -> Path:
    if not target:
        raise ValueError("target_missing")
    p = Path(target)
    if not p.is_absolute():
        p = MAIN_REPO / p
    p = p.resolve()
    if not str(p).startswith(str(MAIN_REPO.resolve()) + "\\") and not str(p).startswith(
        str(MAIN_REPO.resolve()) + "/"
    ):
        raise ValueError("target_outside_repo")
    if p.suffix != ".py" or not p.is_file():
        raise ValueError("target_must_be_existing_py")
    return p


def run_batch(batch_id: str, timeout: int = 300) -> dict:
    st = _state()
    batch = next(
        (b for b in st.get("batches") or [] if b.get("id") == str(batch_id)), None
    )
    if batch is None:
        return {"ok": False, "error": "batch_not_found", "id": batch_id}
    try:
        target = _safe_target(str(batch.get("target") or ""))
    except ValueError as e:
        return {
            "ok": False,
            "error": str(e),
            "id": batch_id,
            "note": "先给 batch 设置 target（仓库内 .py 测试文件）",
        }
    t0 = time.time()
    try:
        # 2026-10-27 WO-AUDIT6-LANE：子进程 pytest 做 hermetic 隔离——
        # 全量套件并发跑时偶发 collection FileNotFoundError（相邻进程 TEMP/缓存竞态）。
        # 独立 --basetemp + TEMP/TMP 私有目录 + 清 PYTEST_CURRENT_TEST/ADDOPTS，
        # 并在 collection 错误时换新 basetemp 重试一次（仅一次，防放大故障）。
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.pop("PYTEST_CURRENT_TEST", None)
        env.pop("PYTEST_ADDOPTS", None)

        def _run_once():
            base = tempfile.mkdtemp(prefix="lane_pytest_")
            env2 = dict(env)
            env2["TEMP"] = env2["TMP"] = base
            return subprocess.run(
                [sys.executable, "-m", "pytest", str(target), "-q",
                 "-p", "no:cacheprovider",
                 "--rootdir", str(MAIN_REPO),
                 "--confcutdir", str(MAIN_REPO),
                 "--basetemp", base],
                cwd=str(MAIN_REPO),
                env=env2,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=max(1, min(int(timeout), 900)),
            )

        r = _run_once()
        combined = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 and (
                "ERROR collecting" in combined
                or "error during collection" in combined):
            r = _run_once()
        ok = r.returncode == 0
        tail = ((r.stdout or "") + "\n" + (r.stderr or ""))[-1200:]
        result = {
            "ok": ok,
            "rc": r.returncode,
            "tail": tail,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
    except subprocess.TimeoutExpired:
        ok = False
        result = {
            "ok": False,
            "error": "timeout",
            "timeout": timeout,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
    batch["status"] = "passed" if ok else "failed"
    batch["last_result"] = result
    batch["last_run_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _save(st)
    return {"ok": ok, "batch": batch, "result": result}


def recent(limit: int = 20) -> dict:
    st = _state()
    batches = list(st.get("batches") or [])[-max(1, min(int(limit), 100)) :]
    counts = {}
    for b in batches:
        counts[b.get("status") or "pending"] = (
            counts.get(b.get("status") or "pending", 0) + 1
        )
    return {
        "ok": True,
        "count": len(batches),
        "status_counts": counts,
        "batches": batches,
        "lane": str(LANE),
        "note": "triage 只产批次；修复需人工/工单执行，run_batch 只跑 pytest 验证",
    }


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="关羽修复车道 v1")
    ap.add_argument("--triage", action="store_true")
    ap.add_argument("--create", nargs=2, metavar=("MODULE", "TARGET"))
    ap.add_argument("--run", default="")
    ap.add_argument("--recent", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.triage:
        rep = triage()
    elif args.create:
        rep = create_batch(args.create[0], args.create[1])
    elif args.run:
        rep = run_batch(args.run)
    else:
        rep = recent()
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
