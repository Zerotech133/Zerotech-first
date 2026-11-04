# -*- coding: utf-8 -*-
"""W2 自动 arm：d1_handoffs.jsonl → 自动消费为 armed watch_plan + 过期回收。

（WO-WATCH-AUTOARM-20261104；Phase0 W2）

背景：D1 handoff 即使产生，也没有定时消费器把它 arm 成 watch_plan（当前 watch_plans=0）。
本模块补一个**只读/显式路径、默认零写生产**的扫描器：

```text
d1_handoffs.jsonl（只读扫描，显式 path/env D1_HANDOFF_LEDGER）
   → consumer.consume_handoff(row) 逐条幂等消费（plan_id 去重/冲突 fail-closed）
   → watch_plans.jsonl（planned→armed；已过期→expired；显式 path/env D1_WATCH_PLAN_LEDGER）
   → consumer.expire_due() 回收到期 planned/armed → expired
   → summary/status JSON（显式 --status 原子写；dry-run 零写）
```

只做 arm/回收：**不触发、不建 confirmation、不下单、不实盘、不写 D1 decisions**。

写边界（默认零写）
------------------
- handoff/plan 账本必须显式 ``--ledger``/``--plans`` 或 env
  （``D1_HANDOFF_LEDGER`` / ``D1_WATCH_PLAN_LEDGER``）；两者皆无 → 报错，
  **不使用生产默认路径写盘**；
- 每轮 live：消费 handoff（写 plan 账本）+ expire_due（写 plan 账本）；
- ``--dry-run``：只读 handoff/plan 账本做**预览**（would_create/would_arm/would_expire），
  不调 consume/expire_due、不写 status/plan ledger；
- status 只在显式 ``--status`` 且非 dry-run 时原子写（tmp+replace）；
- 坏 handoff 账本行/坏 JSON → fail-visible（``handoff_ledger_corrupt``），不静默跳过；
- 单条 handoff 非法/plan_id 冲突 → 记入 per-row errors（继续处理其余行，fail-visible）。

CLI::

    python -X utf8 -m agent_platform.watch_plan_autoarm --once \
        --ledger agent_platform/data/d1_handoffs.jsonl \
        --plans agent_platform/data/watch_plans.jsonl \
        [--status agent_platform/data/watch_engine/autoarm_status.json] \
        [--dry-run] [--json]

退出码：0=ok 且无 item 错误；1=有 fatal 错误或 item 错误（fail-visible）/status 写失败；2=用法错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform import d1_handoff as H  # noqa: E402
from agent_platform import watch_plan_consumer as consumer  # noqa: E402

SCHEMA = "watch_plan_autoarm_status/v1"
HANDOFF_LEDGER_ENV = H.LEDGER_ENV          # D1_HANDOFF_LEDGER
PLAN_LEDGER_ENV = consumer.PLAN_LEDGER_ENV  # D1_WATCH_PLAN_LEDGER
STATUS_ENV = "WATCH_AUTOARM_STATUS_PATH"
EXECUTION_FIELDS = (
    "order", "qty", "quantity", "broker", "execute", "trade_id", "fill", "account",
)
MAX_ERROR_DETAILS = 20
MAX_RESULTS = 200


class AutoarmError(ValueError):
    """autoarm 运行错误（code 机器可读）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now_iso(now=None) -> str:
    if now is None:
        return datetime.now().astimezone().replace(microsecond=0).isoformat()
    if isinstance(now, datetime):
        dt = now if now.tzinfo else now.astimezone()
        return dt.replace(microsecond=0).isoformat()
    text = str(now).strip()
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.replace(microsecond=0).isoformat()


def _parse_iso(text):
    candidate = str(text or "").strip()
    if not candidate:
        raise AutoarmError("bad_expires_at", "expires_at 缺失")
    candidate = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
    parsed = datetime.fromisoformat(candidate)
    return parsed if parsed.tzinfo else parsed.astimezone()


def new_run_id(now=None) -> str:
    stamp = _now_iso(now)
    compact = stamp[0:4] + stamp[5:7] + stamp[8:10] + stamp[11:13] + stamp[14:16] + stamp[17:19]
    digest = hashlib.sha256((stamp + "|" + str(os.getpid())).encode("utf-8")).hexdigest()[:6]
    return "ar-%s-%s" % (compact, digest)


def resolve_handoff_ledger(path=None):
    """handoff 账本：显式 path > D1_HANDOFF_LEDGER > None（不隐式用生产路径写）。"""
    if path:
        return str(path)
    return str(os.environ.get(HANDOFF_LEDGER_ENV) or "").strip() or None


def resolve_plan_ledger(path=None):
    """plan 账本：显式 path > D1_WATCH_PLAN_LEDGER > None（不隐式用生产路径写）。"""
    if path:
        return str(path)
    return str(os.environ.get(PLAN_LEDGER_ENV) or "").strip() or None


def write_status_atomic(status, path) -> bool:
    target = Path(str(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        json.dump(status, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(target)
    return True


def read_handoff_rows(path):
    """只读 handoff JSONL；坏行/非 object → fail-visible（不静默跳过）。"""
    target = Path(str(path))
    if not target.is_file():
        return []
    try:
        text = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise AutoarmError("handoff_ledger_unreadable", "读取 handoff 账本失败: %s" % exc) from exc
    rows = []
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise AutoarmError(
                "handoff_ledger_corrupt", "handoff 账本第 %d 行不是合法 JSON" % line_no) from exc
        if not isinstance(row, dict):
            raise AutoarmError(
                "handoff_ledger_corrupt", "handoff 账本第 %d 行不是 object" % line_no)
        rows.append(row)
    return rows


def _record_error(status, code, message, **extra):
    status["errors"] += 1
    if len(status["error_details"]) < MAX_ERROR_DETAILS:
        item = {"code": code, "message": str(message)[:300]}
        item.update({k: v for k, v in extra.items() if v is not None})
        status["error_details"].append(item)


def _base_status(now, dry_run, handoff_ledger, plan_ledger, run_id=None):
    return {
        "schema": SCHEMA,
        "ok": True,
        "run_id": run_id or new_run_id(now),
        "last_run": _now_iso(now),
        "dry_run": bool(dry_run),
        "mode": "once",
        "handoff_ledger": handoff_ledger,
        "plan_ledger": plan_ledger,
        "handoffs_scanned": 0,
        "handoffs_with_plans": 0,
        "plans_seen": 0,
        "created": 0,
        "armed": 0,
        "expired": 0,
        "duplicates": 0,
        "updated": 0,
        "errors": 0,
        "error_details": [],
        "results": [],
        # dry-run 预览字段（live 恒 0）
        "would_create": 0,
        "would_arm": 0,
        "would_expire": 0,
        "status_written": False,
        "paper_only": True,
        "not_a_trade": True,
        "executes": False,
    }


def _finalize(status, status_path, dry_run):
    if status_path and not dry_run:
        try:
            write_status_atomic(status, status_path)
            status["status_written"] = True
        except OSError as exc:
            status["ok"] = False
            status["status_write_error"] = "%s: %s" % (type(exc).__name__, exc)
    elif status_path and dry_run:
        status["status_write_skipped"] = "dry_run"
    status["degraded"] = bool(status["errors"] or status.get("status_write_error"))
    return status


# ── dry-run 预览（只读，不写 plan 账本）─────────────────────────────
def _preview(status, rows, plan_ledger, now):
    stamp = _now_iso(now)
    try:
        existing_rows = consumer.list_plans(path=plan_ledger)
    except Exception as exc:  # noqa: BLE001 — 坏 plan 账本 fail-visible
        raise AutoarmError(
            "plan_ledger_corrupt", "plan 账本不可读: %s" % str(exc)[:200]) from exc
    existing = {str(item.get("plan_id")): item for item in existing_rows}
    for row in rows:
        plans = row.get("watch_plans")
        if not isinstance(plans, list) or not plans:
            continue
        status["handoffs_with_plans"] += 1
        try:
            _norm, incoming = consumer.plans_from_handoff(row)
        except consumer.WatchPlanError as exc:
            _record_error(status, exc.code, str(exc), receipt_id=row.get("receipt_id"))
            continue
        for plan in incoming:
            status["plans_seen"] += 1
            plan_id = plan["plan_id"]
            try:
                expired = _parse_iso(plan.get("expires_at")) <= _parse_iso(stamp)
            except (AutoarmError, ValueError) as exc:
                _record_error(status, "bad_expires_at", str(exc), plan_id=plan_id)
                continue
            current = existing.get(plan_id)
            if current is None:
                status["would_create"] += 1
                if expired:
                    status["would_expire"] += 1
                else:
                    status["would_arm"] += 1
                continue
            same_receipt = str((current.get("source") or {}).get("receipt_id") or "") == str(
                row.get("receipt_id") or "")
            if not same_receipt:
                _record_error(status, "plan_id_conflict",
                              "plan_id 已被其他回执占用", plan_id=plan_id,
                              receipt_id=row.get("receipt_id"))
                continue
            if current.get("status") in ("planned", "armed"):
                if expired:
                    status["would_expire"] += 1
                elif current.get("status") == "planned":
                    status["would_arm"] += 1
    # 预览 expire_due：现存 planned/armed 且已到期
    for item in existing.values():
        if item.get("status") not in ("planned", "armed"):
            continue
        try:
            if _parse_iso(item.get("expires_at")) <= _parse_iso(stamp):
                status["would_expire"] += 1
        except (AutoarmError, ValueError):
            continue


# ── 主流程（live / dry-run）──────────────────────────────────────────
def run(handoff_ledger=None, plan_ledger=None, dry_run=False,
        status_path=None, now=None, run_id=None) -> dict:
    """扫描 handoff → arm/expire（live）或预览（dry_run）；返回 status dict。"""
    hpath = resolve_handoff_ledger(handoff_ledger)
    ppath = resolve_plan_ledger(plan_ledger)
    status = _base_status(now, dry_run, hpath, ppath, run_id=run_id)
    if not hpath:
        status.update(ok=False, error="handoff_ledger_required",
                      reason="需显式 --ledger 或 env %s（不隐式读写生产路径）" % HANDOFF_LEDGER_ENV)
        return _finalize(status, status_path, dry_run)
    if not ppath:
        status.update(ok=False, error="plan_ledger_required",
                      reason="需显式 --plans 或 env %s（不隐式读写生产路径）" % PLAN_LEDGER_ENV)
        return _finalize(status, status_path, dry_run)
    try:
        rows = read_handoff_rows(hpath)
    except AutoarmError as exc:
        status.update(ok=False, error=exc.code, reason=str(exc)[:300])
        return _finalize(status, status_path, dry_run)
    status["handoffs_scanned"] = len(rows)
    try:
        if dry_run:
            _preview(status, rows, ppath, now)
        else:
            _consume_live(status, rows, ppath, now)
    except AutoarmError as exc:
        status.update(ok=False, error=exc.code, reason=str(exc)[:300])
        return _finalize(status, status_path, dry_run)
    return _finalize(status, status_path, dry_run)


def _consume_live(status, rows, plan_ledger, now):
    for row in rows:
        plans = row.get("watch_plans")
        if not isinstance(plans, list) or not plans:
            continue
        status["handoffs_with_plans"] += 1
        try:
            result = consumer.consume_handoff(row, path=plan_ledger, now=now)
        except consumer.WatchPlanError as exc:
            _record_error(status, exc.code, str(exc), receipt_id=row.get("receipt_id"))
            continue
        except Exception as exc:  # noqa: BLE001 — 单条异常不吞、fail-visible
            _record_error(status, "consume_failed",
                          "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                          receipt_id=row.get("receipt_id"))
            continue
        for key in ("created", "armed", "expired", "duplicates", "updated"):
            status[key] += int(result.get(key) or 0)
        status["plans_seen"] += len(result.get("plans") or [])
        for item in result.get("errors") or []:
            _record_error(status, item.get("code", "plan_conflict"),
                          item.get("message", ""), plan_id=item.get("plan_id"),
                          receipt_id=result.get("receipt_id"))
        if len(status["results"]) < MAX_RESULTS:
            status["results"].append({
                "receipt_id": result.get("receipt_id"),
                "created": result.get("created", 0),
                "armed": result.get("armed", 0),
                "expired": result.get("expired", 0),
                "duplicates": result.get("duplicates", 0),
                "updated": result.get("updated", 0),
                "errors": len(result.get("errors") or []),
            })
    # 每轮到过期回收（planned/armed → expired）
    try:
        changed = consumer.expire_due(path=plan_ledger, now=now)
    except Exception as exc:  # noqa: BLE001 — fail-visible
        _record_error(status, "expire_failed",
                      "%s: %s" % (type(exc).__name__, str(exc)[:200]))
        return
    if changed:
        status["expired"] += len(changed)
        status["updated"] += len(changed)
        status["results"].append({
            "receipt_id": None,
            "expire_due": len(changed),
            "plan_ids": [item.get("plan_id") for item in changed[:MAX_RESULTS]],
        })


# ── CLI ──────────────────────────────────────────────────────────────
USAGE = """用法:
    python -X utf8 -m agent_platform.watch_plan_autoarm --once \\
        --ledger <d1_handoffs.jsonl> --plans <watch_plans.jsonl> \\
        [--status <autoarm_status.json>] [--dry-run] [--json]

扫描 handoff → consumer.consume_handoff 逐条幂等 arm；每轮 expire_due 回收到期计划。
路径必须显式或 env（D1_HANDOFF_LEDGER / D1_WATCH_PLAN_LEDGER）；默认不写生产。
--dry-run 只读预览（would_create/would_arm/would_expire），不写 status/plan；失败 fail-visible。"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="W2 自动 arm（d1_handoffs → watch_plans + 过期回收；paper-only）",
        usage=USAGE,
    )
    parser.add_argument("--once", action="store_true", help="单轮扫描（默认单轮）")
    parser.add_argument("--ledger", default="", help="d1_handoffs.jsonl 路径（或 env %s）"
                        % HANDOFF_LEDGER_ENV)
    parser.add_argument("--plans", default="", help="watch_plans.jsonl 路径（或 env %s）"
                        % PLAN_LEDGER_ENV)
    parser.add_argument("--status", default="", help="status JSON 输出路径（显式；dry-run 不写）")
    parser.add_argument("--dry-run", action="store_true", help="只读预览，不写 plan/status")
    parser.add_argument("--json", action="store_true", help="stdout 输出完整 status JSON")
    args = parser.parse_args(argv)
    try:
        status = run(
            handoff_ledger=args.ledger or None,
            plan_ledger=args.plans or None,
            dry_run=bool(args.dry_run),
            status_path=args.status or None,
        )
    except AutoarmError as exc:
        status = {"schema": SCHEMA, "ok": False, "error": exc.code,
                  "reason": str(exc)[:300], "paper_only": True, "executes": False}
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print("[watch_autoarm] ok=%s deferred_errors=%s scanned=%s with_plans=%s "
              "created=%s armed=%s expired=%s duplicates=%s errors=%s dry_run=%s"
              % (status.get("ok"), status.get("degraded"), status.get("handoffs_scanned"),
                 status.get("handoffs_with_plans"), status.get("created"),
                 status.get("armed"), status.get("expired"), status.get("duplicates"),
                 status.get("errors"), status.get("dry_run")))
        if status.get("error"):
            print("[watch_autoarm] error=%s reason=%s"
                  % (status.get("error"), str(status.get("reason") or "")[:300]))
        for item in (status.get("error_details") or [])[:5]:
            print("[watch_autoarm] item-error=%s %s"
                  % (item.get("code"), str(item.get("message") or "")[:200]))
    return 0 if status.get("ok") and not status.get("degraded") else 1


__all__ = [
    "HANDOFF_LEDGER_ENV",
    "PLAN_LEDGER_ENV",
    "SCHEMA",
    "STATUS_ENV",
    "AutoarmError",
    "main",
    "new_run_id",
    "read_handoff_rows",
    "resolve_handoff_ledger",
    "resolve_plan_ledger",
    "run",
    "write_status_atomic",
]


if __name__ == "__main__":
    sys.exit(main())
