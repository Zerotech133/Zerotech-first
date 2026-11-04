# -*- coding: utf-8 -*-
"""盯盘 runner（IFS F3 接线层；WO-WATCH-WIRE-20261104）。

职责：把 `watch_engine` 从「单轮函数」接到可运行的 runner：
- quote provider 三选一：
  * ``--quotes-json <file>``：离线 fixture（测试/灰度，绝不网络）；
  * ``--quote-source snapshot``：调用 ``news_collector.quote_snapshot.fetch_quotes``
    （**网络**，显式选择才启用）；
  * ``--quote-source none``（默认）：不提供 provider（引擎返回 quote_provider_required，
    不猜价格、不建确认）；
- ``--once`` 单轮 / ``--interval N`` 循环（``--max-ticks`` 可限轮次）；
- 每轮调用 ``watch_engine.evaluate_once``，命中只建 **pending 人工确认**（不执行/不下单）；
- status 只写**显式** ``--status <path>``（建议 ``agent_platform/data/watch_engine/status.json``，
  由 API `/api/d1/watch-plans` 只读消费）；默认不写生产路径；``--dry-run`` 零写；
- ``--lock <path>`` 可选单实例锁（O_EXCL 创建；已存在则 fail-visible，不自动杀进程）；
- ``--alert-ledger``（或 env ``WATCH_ALERT_LEDGER_PATH``，WO-WATCH-P0.2）：每个
  ``triggered`` 结果调 ``watch_alerts.emit_event``；默认不写账本（事件 dry-run +
  dispatch 恒 not_sent，不接 Feishu）；失败 fail-visible（alerts_failed/alert_errors）。
- snapshot provider 进程内跨轮 prev 缓存（WO-WATCH-P0.2）：下一 tick 给 quote 注入
  ``prev``（cross 用）；首轮/``--once`` 无 prev → 仍 ambiguous（不猜）。
- 不写 D1 decisions/paper 账；不启停服务；不实盘；异常 fail-visible。

status 兼容（API 期望字段，WO-WATCH-WIRE）：engine status 现带
``engine_state/last_tick_at/heartbeat_at/armed_count/tick_count/started_at``，
旧字段（run_id/armed/last_run/...）保持不变。

CLI::

    # 离线 fixture（推荐测试/灰度）
    python -X utf8 -m agent_platform.watch_runner --once \
        --plan-ledger <watch_plans.jsonl> --quotes-json <quotes.json> \
        --status <watch_engine/status.json> --json

    # 显式网络快照（真实行情；仅在明确选择时启用）
    python -X utf8 -m agent_platform.watch_runner --interval 30 --max-ticks 120 \
        --plan-ledger <watch_plans.jsonl> --trigger-ledger <watch_triggers.jsonl> \
        --data-dir <trading_bot_dir> --quote-source snapshot \
        --status <watch_engine/status.json> --lock <watch_engine/runner.lock>

    # dry-run：只报告，不写 status/ledger/confirmation
    python -X utf8 -m agent_platform.watch_runner --once --plan-ledger <...> \
        --quotes-json <quotes.json> --dry-run --json

退出码：0=最后一轮 ok；1=引擎/行情/锁失败；2=用法错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform import watch_engine as engine  # noqa: E402

SCHEMA = "watch_runner/v1"
QUOTE_SOURCES = ("none", "snapshot")
#: 建议 status 落盘路径（只是建议；仅 --status 显式时写，无隐式生产写入）
SUGGESTED_STATUS_PATH = (REPO / "agent_platform" / "data"
                         / "watch_engine" / "status.json")
DEFAULT_INTERVAL_S = 30.0
ALERT_LEDGER_ENV = "WATCH_ALERT_LEDGER_PATH"
#: alert 计数在 engine status 中的字段（runner 后处理写入；engine 本身不改）
ALERT_COUNTER_FIELDS = (
    "alerts_emitted", "alerts_deduped", "alerts_dry_run",
    "alerts_failed", "alerts_not_sent",
)


class WatchRunnerError(ValueError):
    """runner 配置/运行错误（code 机器可读）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


# ── quote providers ─────────────────────────────────────────────────
def _snapshot_quotes(symbols):
    """snapshot provider：经 news_collector.quote_snapshot 拉实时快照（网络）。

    显式 ``--quote-source snapshot`` 才启用；测试 monkeypatch 本函数，不打网络。
    返回 {engine_symbol: {"last": price, "ts": ts, "name": ...}}。
    """
    from news_collector import quote_snapshot as qs

    mapping = {}
    codes = []
    for symbol in symbols:
        code = qs.normalize_code(symbol)
        mapping[code] = symbol
        codes.append(code)
    records = qs.fetch_quotes(codes) or []
    out = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        symbol = mapping.get(str(rec.get("code") or ""))
        price = rec.get("price")
        if not symbol or price is None:
            continue
        quote = {"last": price, "ts": rec.get("ts"),
                 "name": rec.get("name") or symbol}
        if rec.get("pct") is not None:
            quote["pct"] = rec["pct"]
        out[symbol] = quote
    return out


def build_provider(source="none", quotes_json=None):
    """quote provider 三选一：quotes-json（离线）/ snapshot（网络）/ none。

    snapshot 返回带**进程内跨轮 prev 缓存**的 SnapshotProvider（cross 条件第二 tick
    起可评估；首轮无 prev 仍由引擎判 ambiguous，不猜）。
    """
    if quotes_json:
        return engine.load_quotes_json(quotes_json)
    source = str(source or "none").strip().lower()
    if source == "none":
        return None
    if source == "snapshot":
        return SnapshotProvider()
    raise WatchRunnerError(
        "bad_quote_source",
        "quote source 必须是 none/snapshot 之一（或 --quotes-json）；收到 %r" % source)


class SnapshotProvider:
    """snapshot provider + 进程内跨轮 prev 缓存（WO-WATCH-P0.2）。

    - 每个 tick 调一次 ``_snapshot_quotes``（默认网络；测试 monkeypatch）；
    - 本进程内缓存 ``symbol → 上一次 last price``；下一 tick 把它注入 quote 的
      ``prev``（引擎 cross_above/cross_below 使用）；
    - 首轮无缓存 → 不注入 prev → 引擎按 ambiguous 处理（不猜「第一次见当前价」）；
    - quote 自带显式 prev 时不覆盖；只有拿到合法当前价才更新缓存。
    """

    def __init__(self, fetch=None):
        self._fetch = fetch
        self._last = {}

    def _fetch_quotes(self, symbols):
        fetch = self._fetch or _snapshot_quotes
        quotes = fetch(list(symbols)) or {}
        if not isinstance(quotes, dict):
            raise WatchRunnerError("snapshot_invalid", "snapshot provider 必须返回 mapping")
        return quotes

    @staticmethod
    def _numeric(quote, keys):
        for key in keys:
            value = quote.get(key)
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value.strip())
                except ValueError:
                    continue
        return None

    def __call__(self, symbols):
        quotes = self._fetch_quotes(symbols)
        out = {}
        for symbol in symbols:
            quote = quotes.get(symbol)
            if not isinstance(quote, dict):
                out[symbol] = quote
                continue
            enriched = dict(quote)
            current = self._numeric(enriched, engine.QUOTE_FIELDS)
            previous = self._last.get(symbol)
            if (previous is not None and "prev" not in enriched
                    and self._numeric(enriched, engine.PREV_FIELDS) is None):
                enriched["prev"] = previous
            out[symbol] = enriched
            if current is not None:
                self._last[symbol] = current
        return out


# ── 单实例锁（可选；不自动杀进程/不自动删别人的锁）─────────────────
class RunnerLock:
    """O_EXCL 单实例锁；已存在 → fail-visible（人工确认进程已退出手动清理）。"""

    def __init__(self, path):
        self.path = Path(str(path))
        self.acquired = False

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise WatchRunnerError(
                "runner_locked",
                "已有 runner 持有锁 %s（若确认进程已退出，可人工删除该锁文件再启动；"
                "runner 不自动删/杀）" % self.path) from exc
        except OSError as exc:
            raise WatchRunnerError("lock_error", "创建 runner 锁失败: %s" % exc) from exc
        try:
            os.write(fd, json.dumps({
                "pid": os.getpid(), "started_at": _now_iso(),
            }, ensure_ascii=False).encode("utf-8"))
        finally:
            os.close(fd)
        self.acquired = True
        return self

    def release(self):
        if not self.acquired:
            return
        try:
            os.unlink(str(self.path))
        except OSError:
            pass
        self.acquired = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


# ── status 累计读取（tick_count/started_at 跨进程继续）───────────────
def _read_previous_status(status_path):
    if not status_path:
        return {}
    try:
        data = json.loads(Path(str(status_path)).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    if isinstance(data.get("tick_count"), int) and not isinstance(data.get("tick_count"), bool):
        out["tick_count"] = data["tick_count"]
    if isinstance(data.get("started_at"), str) and data.get("started_at").strip():
        out["started_at"] = data["started_at"].strip()
    return out


# ── alert 接线（watch_alerts；默认 dry-run/not_sent）─────────────────
_ALERT_ACTION_MAP = {
    "BUY": "BUY", "买入": "BUY", "多": "BUY", "LONG": "BUY", "B": "BUY",
    "SELL": "SELL", "卖出": "SELL", "空": "SELL", "SHORT": "SELL", "S": "SELL",
}
_ALERT_ACTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,31}$")


def resolve_alert_ledger(path=None) -> str:
    """alert 账本路径：显式参数 > WATCH_ALERT_LEDGER_PATH > 空（默认不写）。"""
    if path:
        return str(path)
    return str(os.environ.get(ALERT_LEDGER_ENV) or "").strip()


def _alert_action(conditions) -> str:
    raw = str((conditions or {}).get("action")
              or (conditions or {}).get("side") or "").strip()
    if not raw:
        return "watch"
    mapped = _ALERT_ACTION_MAP.get(raw) or _ALERT_ACTION_MAP.get(raw.upper())
    if mapped:
        return mapped
    return raw if _ALERT_ACTION_RE.match(raw) else "watch"


def _plan_conditions(plan_ledger, plan_id) -> dict:
    try:
        from agent_platform import watch_plan_consumer as consumer
        plan = consumer.get_plan(plan_id, path=plan_ledger)
    except Exception:  # noqa: BLE001 — 只读取证失败不阻断 alert 计数（fail-visible）
        return {}
    conditions = (plan or {}).get("conditions") if isinstance(plan, dict) else None
    return conditions if isinstance(conditions, dict) else {}


def _build_alert_event(result, status, plan_ledger):
    from agent_platform import watch_alerts as alert_mod
    threshold = result.get("threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise alert_mod.WatchAlertError(
            "unsupported_threshold",
            "watch_alert 需要标量 threshold（range 条件暂不支持）: %r" % (threshold,))
    conditions = _plan_conditions(plan_ledger, result.get("plan_id"))
    return alert_mod.build_event(
        plan_id=result.get("plan_id"),
        symbol=result.get("symbol"),
        action=_alert_action(conditions),
        observed=result.get("observed"),
        threshold=threshold,
        quote_as_of=status.get("last_tick_at") or _now_iso(),
    )


def apply_alerts(status, plan_ledger=None, alert_ledger=None, sink=None) -> dict:
    """为 status.results 中 ``status=="triggered"`` 的结果生成 watch_alert 事件。

    - 显式 alert ledger（参数/env）→ ``emit_event`` 落账 + dispatch（默认 not_sent）；
    - 无账本 → ``emit_event(dry_run=True, ledger=None)``：只返回事件/回执，**不写盘**，
      dispatch 恒 not_sent（默认行为）；
    - 失败 fail-visible：计入 ``alerts_failed``/``alert_errors``，不抛断 runner；
    - 计数写入 status（alerts_*），返回计数 dict。
    """
    from agent_platform import watch_alerts as alert_mod

    target = resolve_alert_ledger(alert_ledger)
    errors = [dict(x) for x in (status.get("alert_errors") or []) if isinstance(x, dict)]
    counters = {
        "alerts_enabled": bool(target),
        "alert_ledger": target or None,
        "alerts_emitted": 0,
        "alerts_deduped": 0,
        "alerts_dry_run": 0,
        "alerts_failed": 0,
        "alerts_not_sent": 0,
        "alert_errors": errors,
    }
    results = status.get("results") if isinstance(status.get("results"), list) else []
    for result in results:
        if not isinstance(result, dict) or result.get("status") != "triggered":
            continue
        try:
            event = _build_alert_event(result, status, plan_ledger)
            receipt = alert_mod.emit_event(
                event,
                ledger_path_arg=target or None,
                dry_run=not target,
                sink=sink,
            )
        except Exception as exc:  # noqa: BLE001 — 告警失败不阻断 runner，但必须可见
            counters["alerts_failed"] += 1
            if len(errors) < 10:
                errors.append({
                    "plan_id": result.get("plan_id"),
                    "code": getattr(exc, "code", "alert_failed"),
                    "message": str(exc)[:300],
                })
            continue
        dispatch = receipt.get("dispatch") or {}
        if dispatch.get("status") == "not_sent":
            counters["alerts_not_sent"] += 1
        if receipt.get("written"):
            counters["alerts_emitted"] += 1
        elif receipt.get("duplicate"):
            counters["alerts_deduped"] += 1
        else:
            counters["alerts_dry_run"] += 1
    counters["alert_errors"] = errors[-10:]
    status.update(counters)
    return counters


# ── 单轮 / 循环 ─────────────────────────────────────────────────────
def run_once(plan_ledger=None, trigger_ledger=None, data_dir=None, provider=None,
             dry_run=False, status_path=None, now=None, tick_count=None,
             started_at=None, interval_s=None, alert_ledger=None,
             alert_sink=None) -> dict:
    extra = {"mode": "runner", "pid": os.getpid()}
    if interval_s is not None:
        extra["interval_s"] = interval_s
    if started_at:
        extra["started_at"] = started_at
    if tick_count is not None:
        extra["tick_count"] = tick_count
    status = engine.evaluate_once(
        plan_ledger=plan_ledger,
        trigger_ledger=trigger_ledger,
        data_dir=data_dir,
        quote_provider=provider,
        dry_run=dry_run,
        status_path=status_path,
        now=now,
        extra_status=extra,
    )
    apply_alerts(status, plan_ledger=plan_ledger, alert_ledger=alert_ledger,
                 sink=alert_sink)
    if status_path and not dry_run and status.get("status_written"):
        # engine 先写 status；runner 把 alert 计数补写回显式 status 文件（原子重写）
        try:
            engine.write_status_atomic(status, status_path)
        except OSError as exc:
            status["alert_status_write_error"] = "%s: %s" % (type(exc).__name__, exc)
    return status


def run(plan_ledger=None, trigger_ledger=None, data_dir=None, provider=None,
        once=True, interval=0.0, max_ticks=None, dry_run=False, status_path=None,
        lock_path=None, sleep=time.sleep, now_fn=None, alert_ledger=None) -> dict:
    """运行 once 或 interval 循环；返回 runner summary（含 last_status 与 alert 计数）。

    ``sleep``/``now_fn`` 供测试注入（默认 time.sleep / 本地时间）；
    ``alert_ledger`` 可选（默认不写，事件 dry-run + dispatch not_sent）。
    """
    if once:
        max_ticks = 1
    elif max_ticks is not None and max_ticks < 1:
        raise WatchRunnerError("bad_max_ticks", "max_ticks 必须 >= 1")
    lock = RunnerLock(lock_path) if lock_path else None
    if lock is not None:
        lock.acquire()
    started_at = _now_iso() if now_fn is None else now_fn()
    previous = _read_previous_status(status_path)
    base_ticks = int(previous.get("tick_count") or 0)
    if previous.get("started_at"):
        started_at = previous["started_at"]
    ticks = 0
    statuses = []
    last = None
    try:
        while True:
            ticks += 1
            last = run_once(
                plan_ledger=plan_ledger,
                trigger_ledger=trigger_ledger,
                data_dir=data_dir,
                provider=provider,
                dry_run=dry_run,
                status_path=status_path,
                tick_count=base_ticks + ticks,
                started_at=started_at,
                interval_s=None if once else float(interval),
                alert_ledger=alert_ledger,
            )
            statuses.append(last)
            if max_ticks is not None and ticks >= max_ticks:
                break
            if interval and interval > 0:
                sleep(float(interval))
    finally:
        if lock is not None:
            lock.release()
    alert_totals = {
        field: sum(int((status or {}).get(field) or 0) for status in statuses)
        for field in ALERT_COUNTER_FIELDS
    }
    alert_errors = []
    for status in statuses:
        for item in (status or {}).get("alert_errors") or []:
            if isinstance(item, dict) and len(alert_errors) < 10:
                alert_errors.append(item)
    ledger = resolve_alert_ledger(alert_ledger)
    summary = {
        "schema": SCHEMA,
        "ok": bool(last and last.get("ok")),
        "mode": "once" if once else "interval",
        "ticks": ticks,
        "tick_count_total": (last or {}).get("tick_count"),
        "started_at": started_at,
        "last_tick_at": (last or {}).get("last_tick_at"),
        "heartbeat_at": (last or {}).get("heartbeat_at"),
        "armed_count": (last or {}).get("armed_count"),
        "dry_run": bool(dry_run),
        "lock_used": bool(lock_path),
        "last_status": last,
        "statuses": statuses,
        # alert 计数（本轮/累计；默认无账本 → dry_run + not_sent）
        **alert_totals,
        "alerts": {
            **alert_totals,
            "enabled": bool(ledger),
            "ledger": ledger or None,
            "errors": alert_errors,
        },
        "paper_only": True,
        "not_a_trade": True,
        "executes": False,
    }
    return summary


# ── CLI ──────────────────────────────────────────────────────────────
USAGE = """用法:
    python -X utf8 -m agent_platform.watch_runner --once \\
        --plan-ledger <watch_plans.jsonl> [--trigger-ledger <watch_triggers.jsonl>] \\
        [--data-dir <trading_bot_dir>] \\
        (--quotes-json <quotes.json> | --quote-source snapshot | --quote-source none) \\
        [--alert-ledger <watch_alerts.jsonl>] \\
        [--status <watch_engine/status.json>] [--dry-run] [--lock <runner.lock>] [--json]

    python -X utf8 -m agent_platform.watch_runner --interval 30 [--max-ticks N] ...

默认 quote-source=none（不网络）；snapshot 才会调用 news_collector.quote_snapshot.fetch_quotes，
并在进程内跨轮缓存 prev（cross 条件第二 tick 起可评估；首轮 ambiguous）。
--alert-ledger（或 env WATCH_ALERT_LEDGER_PATH）可选：每个 triggered 结果写一条
watch_alert/v1；默认不写账本，事件 dry-run + dispatch 恒 not_sent（不接 Feishu）。
status 仅在显式 --status 时原子写（含 alert 计数）；--dry-run 零写；退出码 0/1/2。"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="盯盘 runner（watch_engine 接线：quote provider + once/interval + status）",
        usage=USAGE,
    )
    parser.add_argument("--once", action="store_true", help="单轮（默认）")
    parser.add_argument("--interval", type=float, default=None, metavar="SECONDS",
                        help="循环间隔秒（与 --once 互斥；默认单轮）")
    parser.add_argument("--max-ticks", type=int, default=None, help="循环最大轮次（可选）")
    parser.add_argument("--plan-ledger", default="", help="plan 账本路径（缺省 consumer 默认/env）")
    parser.add_argument("--trigger-ledger", default="", help="trigger 账本路径（显式/env）")
    parser.add_argument("--data-dir", default="", help="trading_bot 数据目录（confirmation 落盘）")
    parser.add_argument("--quotes-json", default="", help="离线 quotes JSON（{symbol: quote}；不网络）")
    parser.add_argument("--quote-source", choices=QUOTE_SOURCES, default="none",
                        help="quote 源：none（默认，不网络）/ snapshot（网络，显式选择）")
    parser.add_argument("--status", default="", help="status JSON 输出路径（显式；建议 %s）"
                        % SUGGESTED_STATUS_PATH)
    parser.add_argument("--alert-ledger", default="",
                        help="watch_alert 账本路径（可选；缺省读 env %s，不配置则事件 dry-run/not_sent 不写）"
                        % ALERT_LEDGER_ENV)
    parser.add_argument("--dry-run", action="store_true", help="只报告不写（不建确认/不写 status/ledger/alert）")
    parser.add_argument("--lock", default="", help="单实例锁文件路径（可选；已存在→失败，不自动杀进程）")
    parser.add_argument("--json", action="store_true", help="stdout 输出完整 runner summary JSON")
    args = parser.parse_args(argv)
    if args.once and args.interval is not None:
        print("[watch_runner] ERROR: --once 与 --interval 互斥", file=sys.stderr)
        return 2
    if args.interval is not None and args.interval <= 0:
        print("[watch_runner] ERROR: --interval 必须 > 0", file=sys.stderr)
        return 2
    if args.max_ticks is not None and args.max_ticks < 1:
        print("[watch_runner] ERROR: --max-ticks 必须 >= 1", file=sys.stderr)
        return 2
    try:
        provider = build_provider(args.quote_source, quotes_json=args.quotes_json or None)
    except engine.WatchEngineError as exc:
        payload = {"schema": SCHEMA, "ok": False, "error": exc.code,
                   "reason": str(exc)[:300], "paper_only": True, "executes": False}
        _print(payload, args.json)
        return 1
    except WatchRunnerError as exc:
        payload = {"schema": SCHEMA, "ok": False, "error": exc.code,
                   "reason": str(exc)[:300], "paper_only": True, "executes": False}
        _print(payload, args.json)
        return 1
    try:
        summary = run(
            plan_ledger=args.plan_ledger or None,
            trigger_ledger=args.trigger_ledger or None,
            data_dir=args.data_dir or None,
            provider=provider,
            once=args.interval is None,
            interval=args.interval or 0,
            max_ticks=args.max_ticks,
            dry_run=bool(args.dry_run),
            status_path=args.status or None,
            lock_path=args.lock or None,
            alert_ledger=args.alert_ledger or None,
        )
    except WatchRunnerError as exc:
        payload = {"schema": SCHEMA, "ok": False, "error": exc.code,
                   "reason": str(exc)[:300], "paper_only": True, "executes": False}
        _print(payload, args.json)
        return 1
    _print(summary, args.json)
    return 0 if summary.get("ok") else 1


def _print(summary, as_json):
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    last = summary.get("last_status") or {}
    print("[watch_runner] ok=%s mode=%s ticks=%s tick_count=%s engine_state=%s "
          "armed=%s triggered=%s errors=%s dry_run=%s"
          % (summary.get("ok"), summary.get("mode"), summary.get("ticks"),
             last.get("tick_count"), last.get("engine_state"),
             last.get("armed"), last.get("triggered"), last.get("errors"),
             summary.get("dry_run")))
    print("[watch_runner] alerts: emitted=%s deduped=%s dry_run=%s failed=%s "
          "not_sent=%s ledger=%s"
          % (summary.get("alerts_emitted"), summary.get("alerts_deduped"),
             summary.get("alerts_dry_run"), summary.get("alerts_failed"),
             summary.get("alerts_not_sent"),
             (summary.get("alerts") or {}).get("ledger")))
    if last.get("error"):
        print("[watch_runner] error=%s reason=%s"
              % (last.get("error"), str(last.get("reason") or "")[:300]))


__all__ = [
    "ALERT_COUNTER_FIELDS",
    "ALERT_LEDGER_ENV",
    "DEFAULT_INTERVAL_S",
    "QUOTE_SOURCES",
    "RunnerLock",
    "SCHEMA",
    "SUGGESTED_STATUS_PATH",
    "SnapshotProvider",
    "WatchRunnerError",
    "apply_alerts",
    "build_provider",
    "main",
    "resolve_alert_ledger",
    "run",
    "run_once",
]


if __name__ == "__main__":
    sys.exit(main())
