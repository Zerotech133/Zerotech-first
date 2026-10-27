# -*- coding: utf-8 -*-
"""数据仓日检总入口：串起各只读工具 → 汇总 → 可选写观察记忆。

用法：
    python tools/datawarehouse/run_all.py --json --dry-run     # 只打印不落盘（默认 dry-run）
    python tools/datawarehouse/run_all.py --json --emit-memory # 写 dept/data/observation（RBAC）
    python tools/datawarehouse/run_all.py --weekly|--monthly   # 子集
退出码：0=全绿 / 1=有告警 / 2=有 not_ready
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))  # tools/ 根：otel_tracing
import common  # noqa: E402  (同目录)

try:  # OTEL 接线（默认关；缺失/失败一律降级，不影响主流程）
    import otel_tracing  # noqa: E402
except Exception:  # noqa: BLE001
    otel_tracing = None
import backup_receipt_check  # noqa: E402
import catalog_snapshot  # noqa: E402
import db_health  # noqa: E402
import db_instance_probe  # noqa: E402
import freshness_report  # noqa: E402
import manifest_check  # noqa: E402
import quality_test  # noqa: E402

GNS = "dept/data/observation"


def _run(name: str, fn) -> dict:
    try:
        r = fn()
        r.setdefault("tool", name)
        return r
    except Exception as e:  # noqa: BLE001
        return {"tool": name, "code": common.EXIT_NOT_READY, "error": "exception: " + str(e)[:120]}


def collect(mode: str = "daily") -> dict:
    """带 OTEL span 的 collect（未启用追踪时零开销直通）。"""
    if otel_tracing is not None:
        with otel_tracing.traced("datawarehouse.run_all.collect", mode=mode):
            return _collect_impl(mode)
    return _collect_impl(mode)


def _collect_impl(mode: str = "daily") -> dict:
    sections = {}
    if mode in ("daily", "weekly", "monthly"):
        sections["catalog"] = _run("catalog_snapshot", catalog_snapshot.collect)
        sections["freshness"] = _run("freshness_report", freshness_report.collect)
        sections["manifest"] = _run("manifest_check", manifest_check.collect)
        sections["backup"] = _run("backup_receipt_check", backup_receipt_check.collect)
        sections["instances"] = _run("db_instance_probe", db_instance_probe.collect)
    if mode in ("weekly", "monthly"):
        sections["quality"] = _run("quality_test", quality_test.collect)
        sections["db_health"] = _run("db_health", db_health.collect)
    codes = [int(s.get("code", 0)) for s in sections.values()]
    code = common.EXIT_NOT_READY if 2 in codes else (common.EXIT_WARN if 1 in codes else common.EXIT_OK)
    return {"tool": "run_all", "mode": mode, "code": code,
            "as_of": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "sections": sections,
            "summary": {k: {"code": v.get("code"), "issues": v.get("issues") or []} for k, v in sections.items()}}


def emit_memory(payload: dict) -> dict:
    """写 DATA1 的 dept/data/observation 记忆（RBAC 由 MemoryStore 校验）。"""
    try:
        sys.path.insert(0, str(common.REPO))
        from agent_platform.engine.memory import MemoryStore  # noqa: WPS433
        grants = {"DATA1": {"read": ["dept/data/*", "agent/DATA1/*"],
                            "write": ["dept/data/observation", "agent/DATA1/*"]}}
        store = MemoryStore(str(common.REPO / "agent_platform" / "data" / "memory"),
                            grants_by_agent=grants, enforce=True)
        store.write("DATA1", GNS, payload)
        return {"ok": True, "gns": GNS}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "gns": GNS, "error": type(e).__name__ + ": " + str(e)[:120]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据仓日检总入口（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--emit-memory", action="store_true")
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--monthly", action="store_true")
    a = ap.parse_args(argv)
    mode = "monthly" if a.monthly else ("weekly" if a.weekly else "daily")
    payload = collect(mode)
    if a.emit_memory:
        payload["memory_write"] = emit_memory(payload)
        if not payload["memory_write"]["ok"]:
            payload["code"] = max(int(payload["code"]), common.EXIT_WARN)
    else:
        payload["memory_write"] = {"ok": False, "skipped": "--emit-memory 未指定（默认 dry-run 不落盘）"}
    return common.emit(payload, as_json=a.json, title="run_all")


if __name__ == "__main__":
    raise SystemExit(main())
