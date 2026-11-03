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
    """quote provider 三选一：quotes-json（离线）/ snapshot（网络）/ none。"""
    if quotes_json:
        return engine.load_quotes_json(quotes_json)
    source = str(source or "none").strip().lower()
    if source == "none":
        return None
    if source == "snapshot":
        return _snapshot_quotes
    raise WatchRunnerError(
        "bad_quote_source",
        "quote source 必须是 none/snapshot 之一（或 --quotes-json）；收到 %r" % source)


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


# ── 单轮 / 循环 ─────────────────────────────────────────────────────
def run_once(plan_ledger=None, trigger_ledger=None, data_dir=None, provider=None,
             dry_run=False, status_path=None, now=None, tick_count=None,
             started_at=None, interval_s=None) -> dict:
    extra = {"mode": "runner", "pid": os.getpid()}
    if interval_s is not None:
        extra["interval_s"] = interval_s
    if started_at:
        extra["started_at"] = started_at
    if tick_count is not None:
        extra["tick_count"] = tick_count
    return engine.evaluate_once(
        plan_ledger=plan_ledger,
        trigger_ledger=trigger_ledger,
        data_dir=data_dir,
        quote_provider=provider,
        dry_run=dry_run,
        status_path=status_path,
        now=now,
        extra_status=extra,
    )


def run(plan_ledger=None, trigger_ledger=None, data_dir=None, provider=None,
        once=True, interval=0.0, max_ticks=None, dry_run=False, status_path=None,
        lock_path=None, sleep=time.sleep, now_fn=None) -> dict:
    """运行 once 或 interval 循环；返回 runner summary（含 last_status）。

    ``sleep``/``now_fn`` 供测试注入（默认 time.sleep / 本地时间）。
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
            )
            statuses.append(last)
            if max_ticks is not None and ticks >= max_ticks:
                break
            if interval and interval > 0:
                sleep(float(interval))
    finally:
        if lock is not None:
            lock.release()
    return {
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
        "paper_only": True,
        "not_a_trade": True,
        "executes": False,
    }


# ── CLI ──────────────────────────────────────────────────────────────
USAGE = """用法:
    python -X utf8 -m agent_platform.watch_runner --once \\
        --plan-ledger <watch_plans.jsonl> [--trigger-ledger <watch_triggers.jsonl>] \\
        [--data-dir <trading_bot_dir>] \\
        (--quotes-json <quotes.json> | --quote-source snapshot | --quote-source none) \\
        [--status <watch_engine/status.json>] [--dry-run] [--lock <runner.lock>] [--json]

    python -X utf8 -m agent_platform.watch_runner --interval 30 [--max-ticks N] ...

默认 quote-source=none（不网络）；snapshot 才会调用 news_collector.quote_snapshot.fetch_quotes。
status 仅在显式 --status 时原子写；--dry-run 零写；成功与否退出码 0/1，用法错误 2。"""


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
    parser.add_argument("--dry-run", action="store_true", help="只报告不写（不建确认/不写 status/ledger）")
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
    if last.get("error"):
        print("[watch_runner] error=%s reason=%s"
              % (last.get("error"), str(last.get("reason") or "")[:300]))


__all__ = [
    "DEFAULT_INTERVAL_S",
    "QUOTE_SOURCES",
    "RunnerLock",
    "SCHEMA",
    "SUGGESTED_STATUS_PATH",
    "WatchRunnerError",
    "build_provider",
    "main",
    "run",
    "run_once",
]


if __name__ == "__main__":
    sys.exit(main())
