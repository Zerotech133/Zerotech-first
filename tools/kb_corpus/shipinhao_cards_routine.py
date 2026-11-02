# -*- coding: utf-8 -*-
r"""视频号学习卡·常态化流水线（候选重提炼 → 统一预审 → 回写预审状态 → Top 抽审清单 → 指标）。

用法（日常由计划任务调用）：
  python tools/kb_corpus/shipinhao_cards_routine.py --dry-run        # 只检查是否有新内容
  python tools/kb_corpus/shipinhao_cards_routine.py --write          # 跑完整流水线
  python tools/kb_corpus/shipinhao_cards_routine.py --write --force  # 忽略指纹强制重跑

设计：
  - 输入：知识库 30_知识单元/<pid>_units_draft.jsonl（学习流水线产出）
  - 幂等：units 指纹（size+mtime+sha256）未变且无 --force → 直接 no-op
  - 预审：card_precheck_generic.py（标题已动态化）；预审结果回写候选 JSON 的
    `precheck.action`（pass/reject），`status` 保持 pending_review（人工抽审后才改）
  - 输出：proposals/review_top(研究目录 + 平台 deliverables 副本)、预审状态映射、
    metrics JSONL；异常 rc=2（告警链），no-op rc=0
  - 锁：C:\Temp\shipinhao_cards.lock（单实例）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(r"C:\项目\老田的AI助手")
KB = Path(r"C:\项目\知识库")
UNITS_DIR = KB / "30_知识单元"
REPORTS = REPO / "research" / "reports"
PORTAL = REPO / "portal" / "static"
DELIV = REPO / "deliverables"
PLATFORM_DELIV = Path(r"C:\项目\deliverables")
STATE = REPORTS / "card_routine_state.json"
METRICS = REPORTS / "card_routine_metrics.jsonl"
LOCK = Path(r"C:\Temp\shipinhao_cards.lock")
PY = r"C:\Program Files\Python311\python.exe"
PY312 = r"C:\项目\py312\python.exe"
DATE = datetime.now().strftime("%Y%m%d")
DEFAULT_PID = "视频号学习_8博主"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def units_path(pid: str) -> Path:
    return UNITS_DIR / f"{pid}_units_draft.jsonl"


def fingerprint(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return "%d|%d|%s" % (p.stat().st_size, int(p.stat().st_mtime), h.hexdigest()[:16])


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(d: dict) -> None:
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    tail = ((r.stdout or "") + ("\n" + r.stderr if (r.stderr or "").strip() else "")).strip()
    return r.returncode, "%.1fs | %s" % (time.time() - t0, tail[-500:])


def latest_candidate(pid: str) -> Path | None:
    cands = sorted(REPORTS.glob(f"card_candidates_{pid}_v2_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="视频号学习卡常态化流水线")
    ap.add_argument("--pid", default=DEFAULT_PID)
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略 units 指纹强制重跑")
    args = ap.parse_args(argv)
    if not args.dry_run and not args.write:
        args.dry_run = True

    started = time.time()
    out = {"tool": "shipinhao_cards_routine", "ts": now_iso(), "pid": args.pid,
           "mode": "write" if args.write else "dry_run", "steps": [], "ok": False, "rc": 0}

    up = units_path(args.pid)
    if not up.exists():
        out.update(ok=False, rc=2, error="units_missing: %s" % up)
        print(json.dumps(out, ensure_ascii=False))
        return 2
    fp = fingerprint(up)
    st = load_state()
    same = (st.get("pid") == args.pid and st.get("units_fingerprint") == fp
            and st.get("last_status") == "ok")
    out["units"] = {"path": str(up), "fingerprint": fp,
                    "size": up.stat().st_size, "mtime": datetime.fromtimestamp(up.stat().st_mtime).isoformat(timespec="seconds")}
    out["changed"] = (not same) or args.force
    if same and not args.force:
        out.update(ok=True, rc=0, note="no_new_content: units 指纹未变，跳过")
        print(json.dumps(out, ensure_ascii=False))
        return 0

    # 单实例锁
    if args.write:
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, (now_iso() + " pid=%d" % os.getpid()).encode())
            os.close(fd)
        except FileExistsError:
            out.update(ok=False, rc=2, error="locked: %s exists" % LOCK)
            print(json.dumps(out, ensure_ascii=False))
            return 2
    try:
        if args.dry_run:
            out.update(ok=True, rc=0, note="changed=yes，dry_run 仅检查（未执行重提炼/预审）")
            print(json.dumps(out, ensure_ascii=False))
            return 0

        # 1) 候选重提炼
        rc1, log1 = run([PY, "-X", "utf8", "tools/kb_corpus/card_reextract.py",
                         "--pid", args.pid, "--write"])
        out["steps"].append({"step": "reextract", "rc": rc1, "log": log1})
        if rc1 != 0:
            raise RuntimeError("reextract rc=%s" % rc1)
        cand = latest_candidate(args.pid)
        if not cand:
            raise RuntimeError("candidate_not_found_after_reextract")
        out["candidate"] = str(cand)

        # 2) 统一预审
        prefix = "%s_routine" % args.pid
        rc2, log2 = run([PY312, "-X", "utf8", "tools/kb_corpus/card_precheck_generic.py",
                         "--candidates", str(cand), "--pid", args.pid,
                         "--prefix", prefix, "--top", str(args.top)])
        out["steps"].append({"step": "precheck", "rc": rc2, "log": log2})
        if rc2 != 0:
            raise RuntimeError("precheck rc=%s" % rc2)
        prop_p = REPORTS / f"card_proposals_{prefix}_{DATE}.json"
        review_p = REPORTS / f"card_review_top_{prefix}_{DATE}.md"
        if not prop_p.exists() or not review_p.exists():
            raise RuntimeError("precheck_outputs_missing: %s / %s" % (prop_p.name, review_p.name))
        prop = json.loads(prop_p.read_text(encoding="utf-8"))
        cards = prop.get("cards") or []
        summary = prop.get("summary") or {}
        stats = {"candidates": len(cards), "pass": summary.get("pass", 0),
                 "reject": summary.get("reject", 0), "revise": summary.get("revise", 0)}
        out["stats"] = stats

        # 3) 预审结果回写候选 JSON（status 保持 pending_review，写 precheck）
        cdoc = json.loads(cand.read_text(encoding="utf-8"))
        citems = cdoc.get("candidates") or cdoc.get("cards") or []
        by_id = {str(c.get("card_id")): c for c in citems}
        status_map = {}
        for pc in cards:
            cid = str(pc.get("card_id"))
            rev = pc.get("review") or {}
            boxes = rev.get("checkboxes") or {}
            action = "pass" if boxes.get("pass") else ("reject" if boxes.get("reject") else
                                                      ("revise" if boxes.get("revise") else "pending_review"))
            issues = [x for x in str(rev.get("notes") or "").split(";") if x]
            item = by_id.get(cid)
            if item is not None:
                item["precheck"] = {"action": action, "issues": issues}
                item["review"] = rev
            status_map[cid] = {"action": action, "issues": issues}
        cand.write_text(json.dumps(cdoc, ensure_ascii=False, indent=1), encoding="utf-8")
        (PORTAL / f"card_candidates_{args.pid}_v2.json").write_text(
            json.dumps(cdoc, ensure_ascii=False, indent=1), encoding="utf-8")
        status_p = REPORTS / f"card_precheck_status_{args.pid}_{DATE}.json"
        status_p.write_text(json.dumps({"generated_at": now_iso(), "pid": args.pid,
                                        "source": cand.name, "stats": stats,
                                        "status": status_map}, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        out["status_map"] = str(status_p)

        # 4) Top 抽审清单 → 平台 deliverables（同名覆盖，latest 即最新）
        DELIV.mkdir(exist_ok=True)
        plat_name = f"{datetime.now():%Y-%m-%d}_视频号候选Top{args.top}抽审清单.md"
        (DELIV / f"{datetime.now():%Y-%m-%d}_视频号候选Top{args.top}抽审清单_例行.md").write_text(
            review_p.read_text(encoding="utf-8"), encoding="utf-8")
        PLATFORM_DELIV.mkdir(exist_ok=True)
        (PLATFORM_DELIV / plat_name).write_text(review_p.read_text(encoding="utf-8"), encoding="utf-8")
        out["review_md"] = str(review_p)
        out["deliverable"] = str(PLATFORM_DELIV / plat_name)

        # 5) 指标 + 状态
        rate = (stats["pass"] / stats["candidates"]) if stats["candidates"] else 0.0
        metric = {"ts": now_iso(), "pid": args.pid, "date": DATE,
                  "units_size": up.stat().st_size, "candidates": stats["candidates"],
                  "pass": stats["pass"], "reject": stats["reject"], "revise": stats["revise"],
                  "pass_rate": round(rate, 4), "top": args.top,
                  "duration_s": round(time.time() - started, 1),
                  "candidate_file": cand.name}
        with METRICS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(metric, ensure_ascii=False) + "\n")
        out["metric"] = metric
        ok = stats["candidates"] > 0 and rate >= 0.05
        out.update(ok=ok, rc=0 if ok else 2,
                   note="ok" if ok else "anomaly: zero_candidates_or_pass_rate_too_low")
        save_state({"pid": args.pid, "units_fingerprint": fp, "last_run": now_iso(),
                    "last_status": "ok" if ok else "anomaly", "stats": stats,
                    "candidate_file": cand.name})
        print(json.dumps(out, ensure_ascii=False))
        return out["rc"]
    except Exception as exc:  # noqa: BLE001
        out.update(ok=False, rc=2, error="%s: %s" % (type(exc).__name__, str(exc)[:300]))
        try:
            save_state({"pid": args.pid, "units_fingerprint": fp, "last_run": now_iso(),
                        "last_status": "error", "error": out["error"]})
        except OSError:
            pass
        print(json.dumps(out, ensure_ascii=False))
        return 2
    finally:
        if args.write and LOCK.exists():
            try:
                LOCK.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
