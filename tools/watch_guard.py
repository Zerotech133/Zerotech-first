# -*- coding: utf-8 -*-
"""盯盘 W4+W7 守卫：过期回收 + runner/8789 心跳 watchdog（只读 + 显式路径）。

背景（S1 W4/W7）
----------------
- **W4**：计划过期无自动回收——`watch_plan_consumer.expire_due()` 已存在，但没有外部
  守卫按需触发/报告；
- **W7**：runner/8789 无外部 watchdog——进程挂了 API 只是把 `engine_state` 显示 stale，
  不会告警。本工具从外部独立判定并给出可脚本化的退出码/告警事件。

判定口径（与 `portal/api_d1_handoff.py` 的 API 口径对齐）
---------------------------------------------------------
- 读 **watch_engine status.json**（`--status` > env ``WATCH_ENGINE_STATUS_PATH`` >
  建议只读默认路径 ``agent_platform/data/watch_engine/status.json``）：
  * 心跳取 ``last_tick_at`` / ``heartbeat_at`` / ``last_heartbeat_at`` / ``updated_at`` /
    ``generated_at`` / ``started_at`` 首个可解析者（ISO8601，带时区；未带时区按本地）；
  * **age_s = now - 心跳 > stale_after_s（默认 180s）→ stale**；
  * ``engine_state=error`` → error；status 缺失/坏 JSON/无心跳/心跳在未来（时钟异常）/
    ``paper_only=false``/``executes=true`` → error（fail-visible，不假绿）；
  * 只透出白名单标量（engine_state/心跳/age/stale/…），不输出 token/凭据。
- **探测 8789 `/watch-admin`** 与 **8091 `/api/d1/watch-plans`**：只读 GET，期望 HTTP 200；
  非 200/网络错 → error（记录状态码/延迟，不掩盖）。
- **可选过期回收**：``--expire-due --plan-ledger <显式路径>``（或 env
  ``D1_WATCH_PLAN_LEDGER``）才调用 ``watch_plan_consumer.expire_due()``；``--dry-run``
  只列到期计划不写；**默认不写任何 plan ledger**，也绝不调用默认生产账本。
- **告警事件**：``--alert-file <显式路径>`` 且 overall != ok 时 append JSONL
  （``watch_guard_alert/v1``）；不接 Feishu/不推送；未给路径零写。
- 输出：``--json`` / ``--markdown`` / 默认人读行。退出码：0=ok / 1=异常（stale/error）/
  2=参数/用法错误（fail-visible）。

写边界
------
- 只读服务/文件；唯一显式写：
  1) ``--alert-file``（显式路径，append 告警事件）；
  2) ``--expire-due`` + 显式 plan ledger（调用既有 ``expire_due`` 写 plan 状态）；
  两者都未提供 → **零写**。不启停 8789/8091，不写生产数据，不接 Feishu，不下单。

CLI::

    # 只读健康检查（默认 180s 阈值；不写）
    python -X utf8 tools/watch_guard.py --json
    python -X utf8 tools/watch_guard.py --status agent_platform/data/watch_engine/status.json --markdown

    # 异常时写告警事件（append JSONL；显式路径才写）
    python -X utf8 tools/watch_guard.py --alert-file C:\\Temp\\watch_guard_alerts.jsonl --json

    # 过期回收入口（显式 plan ledger；--dry-run 只报告不写）
    python -X utf8 tools/watch_guard.py --expire-due --plan-ledger <watch_plans.jsonl> --dry-run --json
    python -X utf8 tools/watch_guard.py --expire-due --plan-ledger <watch_plans.jsonl> --json

接线（Lead/后续单）
-------------------
- 定时任务（计划任务/cron）周期执行本脚本；exit != 0 时由外层通知（本工具不接 Feishu）。
- ``--alert-file`` 可由既有 watch_alerts 链路只读消费/汇总；告警事件本身 paper_only/not_a_trade。
- 生产 plan ledger 路径由 Lead 显式指定；本工具不提供隐式默认写入。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SCHEMA = "watch_guard_report/v1"
ALERT_SCHEMA = "watch_guard_alert/v1"

STATUS_ENV = "WATCH_ENGINE_STATUS_PATH"
PLAN_LEDGER_ENV = "D1_WATCH_PLAN_LEDGER"
DEFAULT_STATUS_PATH = (REPO / "agent_platform" / "data"
                       / "watch_engine" / "status.json")
DEFAULT_WATCH_ADMIN_URL = "http://127.0.0.1:8789/watch-admin"
DEFAULT_WATCH_PLANS_URL = "http://127.0.0.1:8091/api/d1/watch-plans"

#: 心跳 stale 阈值（与 portal/api_d1_handoff.py ENGINE_STALE_AFTER_S 对齐）
STALE_AFTER_S = 180
#: status.json 防御性读取上限（与 API 对齐）
STATUS_MAX_BYTES = 256 * 1024
#: 允许的时钟前向偏差；心跳比 now 还未来超过该值 → 视为时钟异常（fail-visible）
CLOCK_SKEW_S = 60
#: 心跳时间字段（首个可解析者，与 API 对齐）
HEARTBEAT_KEYS = (
    "last_tick_at", "heartbeat_at", "last_heartbeat_at",
    "updated_at", "generated_at", "started_at",
)
STATE_KEYS = ("engine_state", "state", "status")


# ── 基础工具 ─────────────────────────────────────────────────────────
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value) -> "datetime | None":
    """ISO8601 → aware datetime；缺失/非法返回 None（不猜）。"""
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _iso(dt: datetime) -> str:
    return dt.astimezone().replace(microsecond=0).isoformat()


def _get(url: str, timeout: float = 3.0) -> dict:
    """只读 GET；期望 200（不做鉴权、不发 Cookie/Authorization、不带 token）。"""
    started = time.time()
    row = {"url": url, "status": 0, "ok": False, "latency_ms": None,
           "content_type": None, "error": None}
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = int(resp.status)
            content_type = resp.headers.get("Content-Type") if resp.headers else None
            resp.read(2048)  # 丢弃 body：不落盘/不输出（防夹带敏感内容）
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也是证据：status 保留
        status = int(exc.code)
        content_type = exc.headers.get("Content-Type") if exc.headers else None
    except Exception as exc:  # noqa: BLE001
        row["latency_ms"] = int((time.time() - started) * 1000)
        row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        return row
    row["latency_ms"] = int((time.time() - started) * 1000)
    row["status"] = status
    row["content_type"] = content_type
    row["ok"] = status == 200
    if not row["ok"]:
        row["error"] = "expected_200_got_%s" % status
    return row


def resolve_status_path(path=None) -> Path:
    """status 路径：显式 --status > env WATCH_ENGINE_STATUS_PATH > 只读建议默认路径。"""
    if path:
        return Path(path)
    env = str(os.environ.get(STATUS_ENV) or "").strip()
    return Path(env) if env else DEFAULT_STATUS_PATH


def resolve_plan_ledger(path=None):
    """plan ledger 路径：显式 --plan-ledger > env D1_WATCH_PLAN_LEDGER > None。

    None 表示「未显式指定」——本工具绝不隐式写生产默认账本。
    """
    if path:
        return Path(path)
    env = str(os.environ.get(PLAN_LEDGER_ENV) or "").strip()
    return Path(env) if env else None


# ── status.json 心跳判定 ─────────────────────────────────────────────
def load_status(path: Path, stale_after: int = STALE_AFTER_S,
                now: "datetime | None" = None) -> dict:
    """读取/判定 watch_engine status（fail-visible；不伪造心跳）。"""
    now = now or _now_utc()
    row = {
        "path": str(path), "exists": False, "bytes": 0,
        "engine_state": None, "heartbeat_at": None, "age_s": None,
        "stale": True, "stale_after_s": int(stale_after),
        "error_code": None, "note": None,
    }
    try:
        stat = path.stat()
    except OSError:
        row["note"] = "status.json 缺失（runner 未运行/未接线；不伪造）"
        row["error_code"] = "status_missing"
        return row
    row["exists"] = True
    row["bytes"] = int(stat.st_size)
    if stat.st_size > STATUS_MAX_BYTES:
        row["note"] = "status.json 超过 %d bytes，拒绝读取" % STATUS_MAX_BYTES
        row["error_code"] = "status_too_large"
        return row
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        row["note"] = "status.json 不可读/非法 JSON：%s" % str(exc)[:200]
        row["error_code"] = "status_invalid_json"
        return row
    if not isinstance(data, dict):
        row["note"] = "status.json 顶层不是 object（不伪造）"
        row["error_code"] = "status_not_object"
        return row

    state = ""
    for key in STATE_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            state = value.strip().lower()
            break
    row["engine_state"] = state or None
    if state == "error":
        row["error_code"] = "engine_state_error"
        row["note"] = "engine_state=error（runner 报错，非心跳陈旧）"
        return row

    heartbeat = None
    for key in HEARTBEAT_KEYS:
        heartbeat = _parse_ts(data.get(key))
        if heartbeat is not None:
            break
    if heartbeat is None:
        row["note"] = ("status.json 缺可解析心跳时间（%s）"
                       % "/".join(HEARTBEAT_KEYS))
        row["error_code"] = "heartbeat_unparsable"
        return row
    row["heartbeat_at"] = _iso(heartbeat)
    age_s = int((now - heartbeat).total_seconds())
    if age_s < -CLOCK_SKEW_S:
        row["age_s"] = age_s
        row["note"] = "心跳时间在未来 %ss（时钟异常，不可信）" % abs(age_s)
        row["error_code"] = "heartbeat_future"
        return row
    row["age_s"] = max(0, age_s)
    row["stale"] = row["age_s"] > int(stale_after)
    if row["stale"]:
        row["note"] = "心跳 age=%ss > stale_after_s=%ss（runner 可能已挂）" % (
            row["age_s"], int(stale_after))

    # 安全标记：status 明示不可交易/只读；出现反向标记 → error（防状态被污染）
    if data.get("paper_only") is False:
        row["error_code"] = "unsafe_status_paper_only_false"
        row["note"] = "status.paper_only=false（预期 True，拒绝判绿）"
    elif data.get("executes") is True:
        row["error_code"] = "unsafe_status_executes_true"
        row["note"] = "status.executes=true（预期 False，拒绝判绿）"
    return row


# ── 可选过期回收 ─────────────────────────────────────────────────────
def check_expire(plan_ledger: Path, dry_run: bool = False,
                 now: "datetime | None" = None) -> dict:
    """可选调用 watch_plan_consumer.expire_due（显式 ledger；dry-run 不写）。"""
    now = now or _now_utc()
    row = {"plan_ledger": str(plan_ledger), "dry_run": bool(dry_run),
           "status": "error", "due": None, "changed": None,
           "plan_ids": [], "error": None}
    try:
        from agent_platform import watch_plan_consumer as consumer
    except Exception as exc:  # noqa: BLE001
        row["error"] = "import_failed: %s: %s" % (type(exc).__name__, str(exc)[:160])
        return row
    try:
        if dry_run:
            plans = consumer.list_plans(path=str(plan_ledger))
            due = []
            for plan in plans:
                if str(plan.get("status") or "").lower() not in ("planned", "armed"):
                    continue
                expires = _parse_ts(plan.get("expires_at"))
                if expires is not None and expires <= now:
                    due.append(plan)
            row["status"] = "dry_run"
            row["due"] = len(due)
            row["changed"] = 0
            row["plan_ids"] = [str(p.get("plan_id")) for p in due[:50]
                               if p.get("plan_id")]
            row["note"] = "dry-run：只列到期计划，不写 plan ledger"
            return row
        changed = consumer.expire_due(path=str(plan_ledger), now=_iso(now))
        changed = list(changed or [])
        row["status"] = "expired"
        row["due"] = len(changed)
        row["changed"] = len(changed)
        row["plan_ids"] = [str(item.get("plan_id")) for item in changed[:50]
                           if item.get("plan_id")]
        return row
    except Exception as exc:  # noqa: BLE001
        row["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:180])
        return row


# ── 汇总判定 / 输出 / 告警 ──────────────────────────────────────────
def check(status_path=None, stale_after: int = STALE_AFTER_S,
          watch_admin_url: str = DEFAULT_WATCH_ADMIN_URL,
          watch_plans_url: str = DEFAULT_WATCH_PLANS_URL,
          timeout: float = 3.0, expire_plan_ledger=None,
          expire_dry_run: bool = False,
          now: "datetime | None" = None) -> dict:
    """执行一次守卫检查（纯读；仅显式 expire_plan_ledger 才可能有写）。"""
    now = now or _now_utc()
    status = load_status(resolve_status_path(status_path), stale_after, now)
    watch_admin = _get(watch_admin_url, timeout)
    watch_admin["name"] = "watch-admin-8789"
    watch_plans = _get(watch_plans_url, timeout)
    watch_plans["name"] = "watch-plans-8091"
    expire = None
    if expire_plan_ledger is not None:
        expire = check_expire(Path(expire_plan_ledger), dry_run=expire_dry_run,
                              now=now)

    errors = []
    warnings = []
    if status.get("error_code"):
        errors.append("status:%s" % status["error_code"])
    for probe in (watch_admin, watch_plans):
        if not probe["ok"]:
            errors.append("%s_down(status=%s)" % (probe["name"], probe["status"]))
    if expire is not None and expire["status"] == "error":
        errors.append("expire_failed:%s" % (expire.get("error") or "unknown"))
    if status.get("stale") and not status.get("error_code"):
        warnings.append("heartbeat_stale(age_s=%s>stale_after_s=%s)" % (
            status.get("age_s"), stale_after))
    if not status.get("engine_state"):
        warnings.append("engine_state_missing")

    if errors:
        overall = "error"
    elif status.get("stale"):
        overall = "stale"
    else:
        overall = "ok"
    return {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "overall": overall,
        "ok": overall == "ok",
        "stale_after_s": int(stale_after),
        "status": status,
        "checks": {"watch_admin": watch_admin, "watch_plans": watch_plans},
        "expire": expire,
        "errors": errors,
        "warnings": warnings,
        "paper_only": True,
        "not_a_trade": True,
    }


def render_markdown(report: dict) -> str:
    status = report["status"]
    lines = [
        "# 盯盘 W4/W7 守卫报告",
        "",
        "- 生成：%s｜overall：**%s**｜心跳阈值：%ss" % (
            report["generated_at"], report["overall"], report["stale_after_s"]),
        "",
        "| 检查 | 结果 | 状态码/age | 延迟(ms) | 说明 |",
        "|---|---|---|---|---|",
        "| watch_engine status | %s | state=%s age=%s stale=%s | - | %s |" % (
            "OK" if not status.get("error_code") and not status.get("stale")
            else ("STALE" if status.get("stale") and not status.get("error_code")
                  else "ERROR"),
            status.get("engine_state"), status.get("age_s"), status.get("stale"),
            status.get("note") or ""),
    ]
    for key in ("watch_admin", "watch_plans"):
        probe = report["checks"][key]
        lines.append("| %s | %s | %s | %s | %s |" % (
            probe.get("name") or key,
            "OK" if probe["ok"] else "DOWN",
            probe.get("status"),
            probe.get("latency_ms"),
            probe.get("error") or ""))
    if report.get("expire"):
        expire = report["expire"]
        lines.append("| expire_due | %s | due=%s changed=%s | - | %s |" % (
            expire.get("status"), expire.get("due"), expire.get("changed"),
            expire.get("note") or expire.get("error") or ""))
    if report.get("errors"):
        lines += ["", "**errors**：%s" % "；".join(report["errors"])]
    if report.get("warnings"):
        lines += ["", "**warnings**：%s" % "；".join(report["warnings"])]
    lines += ["", "只读守卫；不接 Feishu/不下单（paper_only=true, not_a_trade=true）。"]
    return "\n".join(lines)


def write_alert(report: dict, alert_file) -> "Path | None":
    """显式 alert-file 时 append 告警事件（仅 overall != ok；不接 Feishu）。"""
    if alert_file is None or report.get("overall") == "ok":
        return None
    target = Path(alert_file)
    event = {
        "schema": ALERT_SCHEMA,
        "generated_at": report["generated_at"],
        "overall": report["overall"],
        "stale_after_s": report["stale_after_s"],
        "status": {
            "state": report["status"].get("engine_state"),
            "age_s": report["status"].get("age_s"),
            "stale": report["status"].get("stale"),
            "error_code": report["status"].get("error_code"),
            "path": report["status"].get("path"),
        },
        "checks": {
            name: {"status": probe.get("status"), "ok": probe.get("ok"),
                   "latency_ms": probe.get("latency_ms"),
                   "error": probe.get("error")}
            for name, probe in report["checks"].items()
        },
        "expire": None if report.get("expire") is None else {
            "status": report["expire"].get("status"),
            "due": report["expire"].get("due"),
            "changed": report["expire"].get("changed"),
            "plan_ledger": report["expire"].get("plan_ledger"),
            "error": report["expire"].get("error"),
        },
        "errors": report["errors"],
        "warnings": report["warnings"],
        "paper_only": True,
        "not_a_trade": True,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise RuntimeError("写 alert-file 失败: %s" % exc) from exc
    return target


# ── CLI ─────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="盯盘 W4/W7 守卫（只读；显式路径才写）")
    parser.add_argument("--status", default="",
                        help="watch_engine status.json 路径（默认 env %s 或只读建议路径）"
                             % STATUS_ENV)
    parser.add_argument("--stale-after", type=int, default=STALE_AFTER_S,
                        help="心跳 stale 阈值秒（默认 %d）" % STALE_AFTER_S)
    parser.add_argument("--watch-admin-url", default=DEFAULT_WATCH_ADMIN_URL,
                        help="8789 /watch-admin 探针 URL")
    parser.add_argument("--watch-plans-url", default=DEFAULT_WATCH_PLANS_URL,
                        help="8091 /api/d1/watch-plans 探针 URL")
    parser.add_argument("--timeout", type=float, default=3.0, help="探针超时秒（默认 3）")
    parser.add_argument("--expire-due", action="store_true",
                        help="调用 watch_plan_consumer.expire_due（需显式 --plan-ledger）")
    parser.add_argument("--plan-ledger", default="",
                        help="显式 watch_plan 账本路径（或 env %s）；不提供则绝不写"
                             % PLAN_LEDGER_ENV)
    parser.add_argument("--dry-run", action="store_true",
                        help="不写 plan ledger（expire 只报告到期）；alert-file 仍按显式路径")
    parser.add_argument("--alert-file", default="",
                        help="显式告警事件 JSONL（append；仅 overall!=ok；不接 Feishu）")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)

    if args.json and args.markdown:
        print("[watch_guard] ERROR: --json 与 --markdown 互斥", file=sys.stderr)
        return 2
    if args.stale_after <= 0:
        print("[watch_guard] ERROR: --stale-after 必须 > 0", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("[watch_guard] ERROR: --timeout 必须 > 0", file=sys.stderr)
        return 2
    expire_ledger = resolve_plan_ledger(args.plan_ledger or None)
    if args.expire_due and expire_ledger is None:
        print("[watch_guard] ERROR: --expire-due 需要显式 --plan-ledger 或环境变量 %s"
              "（不隐式写生产默认账本）" % PLAN_LEDGER_ENV, file=sys.stderr)
        return 2
    if args.expire_due and not args.dry_run:
        # 生产写仅允许显式 path/env：此处已由 resolve_plan_ledger 校验
        pass

    report = check(status_path=args.status or None,
                   stale_after=args.stale_after,
                   watch_admin_url=args.watch_admin_url,
                   watch_plans_url=args.watch_plans_url,
                   timeout=args.timeout,
                   expire_plan_ledger=(expire_ledger if args.expire_due else None),
                   expire_dry_run=args.dry_run)
    try:
        alert_path = write_alert(report, args.alert_file or None)
    except RuntimeError as exc:
        print("[watch_guard] ERROR: %s" % exc, file=sys.stderr)
        return 2
    if alert_path is not None:
        report["alert_file"] = str(alert_path)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.markdown:
        print(render_markdown(report))
    else:
        print("[watch_guard] overall=%s ok=%s status=%s age=%ss stale=%s "
              "watch_admin=%s watch_plans=%s expire=%s"
              % (report["overall"], report["ok"],
                 report["status"].get("engine_state"), report["status"].get("age_s"),
                 report["status"].get("stale"),
                 report["checks"]["watch_admin"].get("status"),
                 report["checks"]["watch_plans"].get("status"),
                 (report.get("expire") or {}).get("status")))
        if report["errors"]:
            print("[watch_guard] errors: %s" % "；".join(report["errors"]))
    return 0 if report["ok"] else 1


__all__ = [
    "ALERT_SCHEMA",
    "CLOCK_SKEW_S",
    "DEFAULT_STATUS_PATH",
    "DEFAULT_WATCH_ADMIN_URL",
    "DEFAULT_WATCH_PLANS_URL",
    "HEARTBEAT_KEYS",
    "PLAN_LEDGER_ENV",
    "SCHEMA",
    "STALE_AFTER_S",
    "STATUS_ENV",
    "check",
    "check_expire",
    "load_status",
    "main",
    "render_markdown",
    "resolve_plan_ledger",
    "resolve_status_path",
    "write_alert",
]


if __name__ == "__main__":
    sys.exit(main())
