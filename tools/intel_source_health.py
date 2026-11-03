# -*- coding: utf-8 -*-
"""情报来源健康检查（只读，WO-INTEL-HEALTH-20261031 / I1）。

覆盖两类来源，统一输出每源 health：
  A. Phase 2 登记表（agent_platform/data/intel/sources.json）
     guba（股吧，sampling） / osint_video（视频，sampling, disabled） /
     tdx_market（通达信行情，registered, disabled）
     → 只读 registry + sample_path 的 stat/文件数/0 字节数/最新 mtime；
  B. news_collector 9 源（news_collector/status.json sources）
     sina / eastmoney / wallstreetcn / announcement / survey / report /
     world / govpolicy / chinanews
     → 只读 status 的 last_collect/last_ok 与每源 ok/got/new。

纪律（沿 tools/intel_sources.py 判例）：
- 只读：不联网、不写库、不写文件、不打开 news.db/tdx_info.db（仅 stat mtime/大小）；
- 坏输入 fail-visible（registry/status 缺文件/坏 JSON 显式 error，不静默空）；
- 不伪造：缺样本/0 字节/过期按状态与 error 明示，disabled 单独计数。

CLI：
  python tools/intel_source_health.py                 # 文本摘要（非 strict：只报不拦）
  python tools/intel_source_health.py --json          # JSON 契约（intel_source_health/v1）
  python tools/intel_source_health.py --strict        # 启用源不健康 → exit 1
  可注入：--registry PATH --status PATH --now ISO8601
          --stale-hours（news_collector 阈值，默认 2h）
          --sample-stale-hours（登记采样源阈值，默认 30h）

退出码：0=健康/非 strict 且无硬故障；1=输入错误（registry/status 不可读/坏 JSON）、
        启用源缺样本/空样本/扫描错误（硬故障，始终 fail-visible），或 --strict 下
        存在 stale/任意启用源不健康；2=参数错误（argparse）。

JSON schema（intel_source_health/v1）：
{
  "schema": "intel_source_health/v1", "ok": bool, "readonly": true,
  "network": false, "generated_at": "...", "now": "...",
  "thresholds": {"news_stale_hours": 2.0, "sample_stale_hours": 30.0},
  "registry": {"path", "ok", "error", "schema_version", "registry_id", "source_count"},
  "status": {"path", "ok", "error", "last_collect", "updated", "age_s", "stale",
             "source_count"},
  "sources": [{"source_id","name","category","kind","mode","enabled","status",
               "last_ok","age_s","file_count","0byte_count","sample_ok",
               "sample_path","sample_format","stale_after_s","error",
               "last_got","last_new"}],
  "summary": {"total","ok","stale","missing","empty","error","disabled",
              "enabled_total","enabled_bad","news_total","news_ok"},
  "errors": [...]
}
status 取值：ok / stale / missing / empty / error / disabled。
file_count/0byte_count/sample_ok 对 news 源为 null（不适用，如实标注）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import intel_sources as reg  # noqa: E402  复用登记表契约/样本 stat（同一口径）
import intel_source_probe as probe  # noqa: E402  IL-08 三源只读探针（水位/owner/许可/可得性）

SCHEMA = "intel_source_health/v1"
KIND = "intel_source_health"
IL08_SCHEMA = probe.SCHEMA
DEFAULT_IL08_STALE_DAYS = 2.0

DEFAULT_REGISTRY = reg.DEFAULT_REGISTRY
DEFAULT_STATUS = (Path(os.environ.get("AIOS_ROOT", r"C:\aios"))
                  / "news_collector" / "status.json")

DEFAULT_NEWS_STALE_HOURS = 2.0      # news_collector 常态半小时级；>2h 视为 stale
DEFAULT_SAMPLE_STALE_HOURS = 30.0   # 日更采集（guba 每日 17:50 等）；>30h 视为 stale

NEWS_SOURCE_IDS = ("sina", "eastmoney", "wallstreetcn", "announcement",
                   "survey", "report", "world", "govpolicy", "chinanews")
BAD_STATUSES = ("stale", "missing", "empty", "error")
_TIME_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
EXIT_OK = 0
EXIT_UNHEALTHY = 1


def _now_naive(now=None) -> datetime:
    if now is None:
        return datetime.now()
    if isinstance(now, datetime):
        return now.replace(tzinfo=None)
    text = str(now).strip().replace(" ", "T")
    try:
        return datetime.fromisoformat(text).astimezone().replace(tzinfo=None)
    except ValueError as exc:
        raise ValueError("--now 不是合法 ISO8601：%r" % (now,)) from exc


def _parse_time(text):
    s = str(text or "").strip()
    for fmt in _TIME_FMTS:
        n = min(len(s), len(datetime.now().strftime(fmt)))
        try:
            return datetime.strptime(s[:n], fmt)
        except ValueError:
            continue
    return None


def _age_s(now: datetime, dt) -> int | None:
    if dt is None:
        return None
    return max(0, int((now - dt).total_seconds()))


def _iso(now: datetime) -> str:
    return now.strftime("%Y-%m-%d %H:%M:%S")


def load_status(path) -> dict:
    """只读 status.json；返回 {ok, path, error, data, last_collect, updated}。"""
    p = Path(path)
    out = {"path": str(p), "ok": False, "error": None, "data": {},
           "last_collect": None, "updated": None}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        out["error"] = "status_unreadable: %s" % exc
        return out
    try:
        data = json.loads(text)
    except ValueError as exc:
        out["error"] = "status_bad_json: %s" % exc
        return out
    if not isinstance(data, dict):
        out["error"] = "status_not_object"
        return out
    out["data"] = data
    out["last_collect"] = data.get("last_collect")
    out["updated"] = data.get("updated")
    if not isinstance(data.get("sources"), dict):
        out["error"] = "status_sources_missing"
        return out
    out["ok"] = True
    return out


def _registry_source_health(src: dict, now: datetime, stale_s: float) -> dict:
    """登记表来源：registry 字段 + sample_path 只读 stat。"""
    sample_path = str(src.get("sample_path") or "")
    st = reg.path_stats(sample_path) if sample_path else {
        "exists": False, "type": "missing", "files_total": 0,
        "files_nonempty": 0, "newest_nonempty": None, "error": None}
    files_total = int(st.get("files_total") or 0)
    nonempty = int(st.get("files_nonempty") or 0)
    zero_count = max(0, files_total - nonempty)
    newest = _parse_time(st.get("newest_nonempty"))
    age_s = _age_s(now, newest)
    enabled = bool(src.get("enabled", True))
    sample_ok = bool(st.get("exists")) and nonempty >= 1
    status, error = "ok", None
    if st.get("error"):
        status, error = "error", "scan_error: %s" % st["error"]
    elif not st.get("exists"):
        status, error = "missing", "sample_path_missing"
    elif files_total == 0:
        status, error = "empty", "sample_path_empty"
    elif nonempty == 0:
        status, error = "empty", "sample_all_zero_bytes"
    elif age_s is None:
        status, error = "stale", "sample_mtime_unknown"
    elif age_s > stale_s:
        status, error = "stale", "sample_stale"
    if not enabled:
        status = "disabled"  # 禁用源仍保留真实 error/sample 状态（fail-visible）
    return {
        "source_id": str(src.get("source_id") or ""),
        "name": str(src.get("name") or ""),
        "category": "registry",
        "kind": str(src.get("kind") or ""),
        "mode": str(src.get("mode") or ""),
        "enabled": enabled,
        "status": status,
        "last_ok": sample_ok,
        "age_s": age_s,
        "file_count": files_total,
        "0byte_count": zero_count,
        "sample_ok": sample_ok,
        "sample_path": sample_path,
        "sample_format": str(src.get("sample_format") or ""),
        "stale_after_s": int(stale_s),
        "error": error,
        "last_got": None,
        "last_new": None,
    }


def _news_source_health(source_id: str, entry, status_ok: bool,
                        status_age_s, status_stale: bool, status_error,
                        stale_s: float) -> dict:
    """news_collector 来源：status.json 每源 ok/got/new + 整体采集时龄。"""
    enabled = True
    last_ok = entry.get("ok") if isinstance(entry, dict) else None
    status, error = "ok", None
    if not status_ok:
        status, error = "error", "status_unavailable"
    elif not isinstance(entry, dict):
        status, error = "error", "status_entry_not_object"
    elif not entry.get("ok"):
        status, error = "error", "collector_source_not_ok"
    elif status_stale:
        status, error = "stale", "collector_status_stale"
    elif status_age_s is None:
        status, error = "error", "collector_status_time_unknown"
    return {
        "source_id": source_id,
        "name": source_id,
        "category": "news",
        "kind": "news_feed",
        "mode": "pipeline",
        "enabled": enabled,
        "status": status,
        "last_ok": bool(last_ok) if last_ok is not None else None,
        "age_s": status_age_s,
        "file_count": None,       # news 源无落盘样本口径，显式 null
        "0byte_count": None,
        "sample_ok": None,
        "sample_path": None,
        "sample_format": None,
        "stale_after_s": int(stale_s),
        "error": error,
        "last_got": entry.get("got") if isinstance(entry, dict) else None,
        "last_new": entry.get("new") if isinstance(entry, dict) else None,
    }


def build_report(registry_path=None, status_path=None, now=None,
                 news_stale_hours: float = DEFAULT_NEWS_STALE_HOURS,
                 sample_stale_hours: float = DEFAULT_SAMPLE_STALE_HOURS,
                 include_il08: bool = True,
                 il08_stale_days: float = DEFAULT_IL08_STALE_DAYS,
                 il08_paths=None) -> dict:
    """只读生成健康报告（不写任何文件/库）。返回 JSON 契约 dict。

    IL-08（增量，向后兼容）：``include_il08=True`` 时在 legacy 报告上追加顶层
    ``il08`` 键（三源事实盘点：在册/水位/owner/许可/探针可得性）；legacy 键与语义不变。
    """
    clk = _now_naive(now)
    news_stale_s = float(news_stale_hours) * 3600.0
    sample_stale_s = float(sample_stale_hours) * 3600.0
    report = {
        "schema": SCHEMA, "kind": KIND, "ok": False, "readonly": True,
        "network": False, "generated_at": _iso(clk), "now": _iso(clk),
        "thresholds": {"news_stale_hours": float(news_stale_hours),
                       "sample_stale_hours": float(sample_stale_hours)},
        "registry": {"path": str(Path(registry_path or DEFAULT_REGISTRY)),
                     "ok": False, "error": None, "schema_version": None,
                     "registry_id": None, "source_count": 0},
        "status": {"path": str(Path(status_path or DEFAULT_STATUS)), "ok": False,
                   "error": None, "last_collect": None, "updated": None,
                   "age_s": None, "stale": None, "source_count": 0},
        "sources": [], "summary": {}, "errors": [],
    }

    # A. 登记表来源
    registry_data = None
    try:
        registry_data, reg_path = reg.load_registry(registry_path)
        report["registry"].update({
            "path": str(reg_path), "ok": True,
            "schema_version": registry_data.get("schema_version"),
            "registry_id": registry_data.get("registry_id"),
            "source_count": len(registry_data.get("sources") or []),
        })
    except reg.IntelSourcesError as exc:
        report["registry"]["error"] = str(exc)
        report["errors"].append("registry: %s" % exc)

    # B. news_collector status
    st = load_status(status_path or DEFAULT_STATUS)
    status_dt = _parse_time(st.get("updated") or st.get("last_collect"))
    status_age = _age_s(clk, status_dt)
    status_stale = (status_age is None or status_age > news_stale_s)
    report["status"].update({
        "path": st["path"], "ok": st["ok"], "error": st["error"],
        "last_collect": st.get("last_collect"),
        "updated": st.get("updated"),
        "age_s": status_age,
        "stale": status_stale if st["ok"] else None,
        "source_count": len((st.get("data") or {}).get("sources") or {}),
    })
    if not st["ok"]:
        report["errors"].append("status: %s" % st["error"])

    if registry_data is not None:
        for src in registry_data.get("sources") or []:
            report["sources"].append(
                _registry_source_health(src, clk, sample_stale_s))

    status_sources = (st.get("data") or {}).get("sources") or {}
    for sid in sorted(status_sources):
        report["sources"].append(_news_source_health(
            sid, status_sources[sid], st["ok"], status_age, status_stale,
            st["error"], news_stale_s))

    counts = {k: 0 for k in ("ok", "stale", "missing", "empty", "error",
                             "disabled")}
    for src in report["sources"]:
        counts[src["status"]] = counts.get(src["status"], 0) + 1
    enabled = [s for s in report["sources"] if s["enabled"]]
    enabled_bad = [s for s in enabled if s["status"] in BAD_STATUSES]
    news = [s for s in report["sources"] if s["category"] == "news"]
    report["summary"] = {
        "total": len(report["sources"]),
        "ok": counts.get("ok", 0),
        "stale": counts.get("stale", 0),
        "missing": counts.get("missing", 0),
        "empty": counts.get("empty", 0),
        "error": counts.get("error", 0),
        "disabled": counts.get("disabled", 0),
        "enabled_total": len(enabled),
        "enabled_bad": len(enabled_bad),
        "news_total": len(news),
        "news_ok": sum(1 for s in news if s["status"] == "ok"),
    }
    for src in enabled_bad:
        if src["error"]:
            report["errors"].append("%s: %s" % (src["source_id"], src["error"]))
    report["ok"] = bool(
        report["registry"]["ok"] and report["status"]["ok"]
        and report["summary"]["enabled_bad"] == 0)
    if include_il08:
        # 增量：只追加顶层 il08 键；legacy 键/语义/ok/rc 全部不变（IL-08 v1）
        report["il08"] = _build_il08(report, registry_data, clk, il08_stale_days,
                                     il08_paths)
    return report


def _build_il08(report, registry_data, clk, stale_days, il08_paths):
    """构建 IL-08 三源事实盘点；异常降级为 fail-visible 的 il08 块，不影响 legacy。"""
    try:
        return probe.build_il08_inventory(
            registry=registry_data,
            registry_path=report["registry"]["path"],
            registry_ok=report["registry"]["ok"],
            registry_error=report["registry"]["error"],
            now=clk,
            stale_days=stale_days,
            overrides=il08_paths,
        )
    except Exception as exc:  # noqa: BLE001 —— fail-visible，绝不外抛破坏 legacy
        return {
            "schema": IL08_SCHEMA, "kind": probe.KIND, "ok": False,
            "readonly": True, "network": False, "online_health_claimed": False,
            "generated_at": "", "now": "", "no_authoritative_registry": True,
            "registry": {"path": report["registry"]["path"], "ok": False,
                         "error": "il08_build_failed", "reason": str(exc),
                         "registry_id": None, "schema_version": None,
                         "updated_at": None, "phase": None, "source_count": 0,
                         "authoritative": False, "covers_three_sources": False},
            "thresholds": {"stale_days": float(stale_days)},
            "sources": [],
            "summary": {"total": 0, "in_registry": 0, "not_in_registry": [],
                        "probe_available": 0, "probe_unavailable": [],
                        "stale": 0, "errors": 1, "gaps": 0},
            "gaps": [],
            "errors": [{"source_id": "*", "error": "il08_build_failed: %s" % exc,
                        "path": report["registry"]["path"]}],
            "policy": {"readonly": True, "network": False, "writes_db": False,
                       "writes_files": False, "changes_collect_config": False,
                       "starts_stops_services": False,
                       "no_second_registry_source": True,
                       "credentials_read": False},
        }


def format_text(report: dict) -> str:
    lines = [
        "情报来源健康（%s）" % report["schema"],
        "now=%s  registry=%s" % (report["now"], report["registry"]["path"]),
        "status=%s（last_collect=%s age=%ss stale=%s）" % (
            report["status"]["path"], report["status"]["last_collect"],
            report["status"]["age_s"], report["status"]["stale"]),
        "口径：只读检查（registry/status/mtime），不联网、不写库、不写文件。",
        "",
    ]
    for src in report["sources"]:
        if src["category"] == "registry":
            detail = ("files=%s 0byte=%s sample_ok=%s" % (
                src["file_count"], src["0byte_count"], src["sample_ok"]))
        else:
            detail = "got=%s new=%s last_ok=%s" % (
                src["last_got"], src["last_new"], src["last_ok"])
        lines.append("- [%s] %-20s %s  %s  age=%ss  %s%s" % (
            src["category"], src["source_id"], src["status"],
            "enabled=%s" % src["enabled"], src["age_s"], detail,
            "" if not src["error"] else "  error=%s" % src["error"]))
    sm = report["summary"]
    lines += [
        "",
        "汇总：total=%s ok=%s stale=%s missing=%s empty=%s error=%s disabled=%s "
        "| 启用坏=%s | news %s/%s ok" % (
            sm["total"], sm["ok"], sm["stale"], sm["missing"], sm["empty"],
            sm["error"], sm["disabled"], sm["enabled_bad"],
            sm["news_ok"], sm["news_total"]),
        "整体：%s" % ("OK" if report["ok"] else "FAIL（fail-visible）"),
    ]
    if report["errors"]:
        lines.append("问题：")
        lines += ["  - %s" % e for e in report["errors"]]
    return "\n".join(lines)


def _hours_arg(value: str) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("阈值必须是数字（小时）")
    if n <= 0:
        raise argparse.ArgumentTypeError("阈值必须 > 0 小时")
    return n


def _days_arg(value: str) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("阈值必须是数字（天）")
    if n <= 0:
        raise argparse.ArgumentTypeError("阈值必须 > 0 天")
    return n


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="情报来源健康检查（只读 registry/status/mtime；不联网不写库）")
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY),
                    help="来源登记表路径（默认 agent_platform/data/intel/sources.json）")
    ap.add_argument("--status", default=str(DEFAULT_STATUS),
                    help="news_collector status.json（默认 AIOS_ROOT/news_collector/status.json）")
    ap.add_argument("--now", default=None, help="注入时钟（ISO8601，测试/回放）")
    ap.add_argument("--stale-hours", type=_hours_arg,
                    default=DEFAULT_NEWS_STALE_HOURS,
                    help="news_collector 整体采集过期阈值（小时，默认 2）")
    ap.add_argument("--sample-stale-hours", type=_hours_arg,
                    default=DEFAULT_SAMPLE_STALE_HOURS,
                    help="登记表采样源 mtime 过期阈值（小时，默认 30）")
    out = ap.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true", help="输出 JSON 契约（stdout）")
    out.add_argument("--md", action="store_true",
                     help="输出 IL-08 三源盘点 Markdown（stdout，只读事实）")
    ap.add_argument("--strict", action="store_true",
                    help="启用源不健康 → exit 1（输入错误始终 fail-visible）")
    il08 = ap.add_argument_group("IL-08 三源盘点（增量，可注入路径/阈值）")
    il08.add_argument("--stale-days", type=_days_arg, default=DEFAULT_IL08_STALE_DAYS,
                      help="三源水位过期阈值（天，默认 2）")
    il08.add_argument("--xueqiu-db", default=None, help="雪球 sqlite 路径（默认数据根 xueqiu/xueqiu_info.db）")
    il08.add_argument("--guba-dir", default=None, help="股吧 jsonl 目录（默认数据根 guba）")
    il08.add_argument("--tdx-db", default=None, help="TDX sqlite 路径（默认数据根 tdx/tdx_info.db）")
    return ap


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    args = build_parser().parse_args(argv)
    il08_paths = {"xueqiu_db": args.xueqiu_db, "guba_dir": args.guba_dir,
                  "tdx_db": args.tdx_db}
    try:
        report = build_report(args.registry, args.status, args.now,
                              news_stale_hours=args.stale_hours,
                              sample_stale_hours=args.sample_stale_hours,
                              include_il08=bool(args.json or args.md),
                              il08_stale_days=args.stale_days,
                              il08_paths=il08_paths)
    except ValueError as exc:
        print(json.dumps({"ok": False, "schema": SCHEMA, "error": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return EXIT_UNHEALTHY
    if args.md:
        inv = report.get("il08") or {}
        print(probe.format_il08_md(inv))
        if not isinstance(inv, dict) or inv.get("schema") != IL08_SCHEMA:
            return EXIT_UNHEALTHY
        return EXIT_OK
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(format_text(report))
    # 输入不可读 / 启用源缺样本、空样本、扫描错误 → 始终 fail-visible（非 strict 也拦）
    hard_bad = any(
        s["enabled"] and s["status"] in ("missing", "empty", "error")
        for s in report["sources"])
    if not report["registry"]["ok"] or not report["status"]["ok"] or hard_bad:
        return EXIT_UNHEALTHY
    # stale 等软故障只在 --strict 下拦截（默认只报不拦，供看板/巡检）
    if args.strict and not report["ok"]:
        return EXIT_UNHEALTHY
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
