# -*- coding: utf-8 -*-
"""数据仓数据质量门禁（freshness gate）可复用模块。

默认 **off**：不评估、不阻断、不写任何文件（`DW_AGE_GATE_MODE` 未设置或为 off）。
`DW_AGE_GATE_MODE=warn`：只评估并记录 quality（日志 + 调用方可将 quality 写入缓存/响应），
**绝不阻断**。`deny` 为可选严格模式（本模块供未来接入，当前生产链未启用）。

设计原则：
- 只读：读取 `C:\\Temp\\dw_full_inventory.json`（不存在时用内置快照）与 raw 目录文件名；
  不写 DB、不写数据仓；不 import 任何业务/交易/信号模块。
- asof 口径 = 数据日期（primary_date_col 的 last_date），不是文件 mtime。
- curated 是平台唯一消费口径；raw 仅作新鲜度信号与证据补充，禁止静默混用。
- 本模块不接每日任务、不改交易/信号链；接入方式见 deliverables 文档。

CLI 示例（只读 dry-run）::

    python -m zhice_os_v2.infrastructure.data_quality.age_gate --mode dry-run --include-raw
    python -m zhice_os_v2.infrastructure.data_quality.age_gate --mode warn --json
    python -m zhice_os_v2.infrastructure.data_quality.age_gate --compare-7
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

ENV_MODE = "DW_AGE_GATE_MODE"
DEFAULT_MODE: GateMode = "off"
VALID_MODES: tuple[GateMode, ...] = ("off", "warn", "deny")

DEFAULT_INVENTORY = Path(r"C:\Temp\dw_full_inventory.json")
CACHE_ARTIFACT = Path(r"C:\Temp\dw_multidim_cache.json")
CACHE_TTL_HOURS = 24
RAW_ROOT = Path(r"C:\项目\老田的AI助手\quant_backtest\data\tushare")
CURATED_ROOT = Path(r"C:\项目\老田的AI助手\quant_backtest\data\tushare_curated")

# 平台已消费的 curated 数据集（来自 dw_full_inventory.mapping_summary.curated_consumed）
CONSUMED_DATASETS: tuple[str, ...] = (
    "anns_d", "balancesheet", "cyq_perf", "daily_basic", "fina_indicator", "hk_basic",
    "hk_fina_indicator", "income", "index_weight", "limit_list_d", "margin_detail",
    "moneyflow", "research_report", "stock_basic", "sw_l1_member", "top_list",
    "us_basic", "us_fina_indicator",
)

# 数据频率与最大允许龄期（日历日）
CADENCE_MAX_AGE: dict[str, int] = {
    "daily": 7, "trading_daily": 7, "weekly": 14, "monthly": 30,
    "quarterly": 120, "pit_event": 60, "static": 3650, "no_date": 3650,
}

# (cadence, consumer_class, severity, note)
REGISTRY: dict[str, tuple[str, str, str, str]] = {
    "anns_d": ("daily", "presentation", "P1", "公告（curated 版本滞后；证据链用 raw 最新）"),
    "cyq_perf": ("trading_daily", "presentation", "P1", "筹码指标"),
    "index_weight": ("monthly", "presentation", "P2", "指数权重（月度）"),
    "limit_list_d": ("trading_daily", "presentation", "P1", "涨跌停"),
    "margin_detail": ("trading_daily", "presentation", "P1", "融资融券"),
    "sw_l1_member": ("pit_event", "presentation", "P2", "申万 L1 PIT 行业成员（事件型，天然滞后）"),
    "top_list": ("trading_daily", "presentation", "P1", "龙虎榜"),
    "moneyflow": ("trading_daily", "presentation", "P1", "资金流"),
    "research_report": ("trading_daily", "presentation", "P2", "研报"),
    "daily_basic": ("trading_daily", "presentation", "P1", "日频指标"),
    "balancesheet": ("quarterly", "evidence", "P2", "财报（按期末，非过期）"),
    "income": ("quarterly", "evidence", "P2", "利润表（按期末）"),
    "fina_indicator": ("quarterly", "evidence", "P2", "财务指标（按期末）"),
    "hk_fina_indicator": ("quarterly", "evidence", "P2", "港股财务"),
    "us_fina_indicator": ("quarterly", "evidence", "P2", "美股财务"),
    "stock_basic": ("static", "presentation", "P3", "股票基础信息"),
    "hk_basic": ("static", "presentation", "P3", "港股基础信息"),
    "us_basic": ("static", "presentation", "P3", "美股基础信息"),
}

# curated/raw 双层统一：raw 落地目录名（用于判断 curated 是否滞后）
RAW_OVERLAY: dict[str, str] = {
    "anns_d": "anns_d", "cyq_perf": "cyq_perf", "index_weight": "index_weight",
    "limit_list_d": "limit_list_d", "margin_detail": "margin_detail",
    "top_list": "top_list", "sw_l1_member": "sw_l1_member",
}

# 已知过期消费源（inventory 口径，用于 --compare-7 对照）
EXPECTED_STALE_CONSUMED: tuple[str, ...] = (
    "anns_d", "cyq_perf", "index_weight", "limit_list_d",
    "margin_detail", "sw_l1_member", "top_list",
)

# inventory 缺失时兜底（值与 2026-11-02 01:13 的 dw_full_inventory 一致）
BUILTIN_SNAPSHOT: dict[str, Any] = {
    "generated_at": "2026-11-02T01:13:41+08:00",
    "reference_date": "2026-10-31",
    "datasets": {
        d: {"last_date": ld, "primary_date_col": pc, "stale_days": sd}
        for d, ld, pc, sd in (
            ("anns_d", "2026-10-23", "ann_date", 8),
            ("cyq_perf", "2026-10-21", "trade_date", 10),
            ("index_weight", "2026-09-30", "trade_date", 31),
            ("limit_list_d", "2026-10-14", "trade_date", 17),
            ("margin_detail", "2026-10-22", "trade_date", 9),
            ("sw_l1_member", "2026-08-27", "in_date", 65),
            ("top_list", "2026-10-23", "trade_date", 8),
            ("moneyflow", "2026-10-23", "trade_date", 8),
            ("research_report", "2026-10-23", "trade_date", 8),
            ("daily_basic", "2026-10-23", "trade_date", 8),
            ("stock_basic", "2026-10-30", "list_date", 1),
            ("hk_basic", "2026-10-30", "list_date", 1),
            ("us_basic", "2026-10-30", "list_date", 1),
        )
    },
}

GateMode = Literal["off", "warn", "deny"]


class StaleDataError(RuntimeError):
    """deny 模式下存在被阻断的过期消费源（供未来严格接入使用）。"""


@dataclass(frozen=True)
class SourceVerdict:
    """单个数据集的过期判定。"""

    dataset: str
    layer: str
    cadence: str
    consumer_class: str
    severity: str
    last_date: str | None
    age_days: int | None
    max_age_days: int
    state: str
    action: str
    reason: str
    raw_last_date: str | None = None
    raw_kind: str = "skipped"
    consumers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset, "layer": self.layer, "cadence": self.cadence,
            "consumer_class": self.consumer_class, "severity": self.severity,
            "last_date": self.last_date, "age_days": self.age_days,
            "max_age_days": self.max_age_days, "state": self.state,
            "action": self.action, "reason": self.reason,
            "raw_last_date": self.raw_last_date, "raw_kind": self.raw_kind,
            "consumers": list(self.consumers),
        }


@dataclass
class GateReport:
    """一次门禁评估的完整结果（可直接作为 quality 块写入缓存/响应）。"""

    asof: str
    reference_date: str
    mode: str
    enabled: bool
    verdicts: list[SourceVerdict] = field(default_factory=list)
    inventory_source: str = "builtin-snapshot"
    inventory_generated_at: str | None = None
    generated_at: str = field(
        default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))

    @property
    def stale_datasets(self) -> list[str]:
        return [v.dataset for v in self.verdicts
                if v.state in ("STALE", "CURATED_LAG_RAW_FRESH")]

    @property
    def blocking_datasets(self) -> list[str]:
        return [v.dataset for v in self.verdicts if v.action == "deny"]

    def quality_block(self) -> dict[str, Any]:
        """写入缓存/响应体的 quality 块（off 模式仅标记 disabled，不暴露明细）。"""
        if not self.enabled:
            return {"gate": "dw_age_gate/v1", "mode": "off", "enabled": False,
                    "checked": 0, "degraded": None, "asof": None, "stale_sources": []}
        return {
            "gate": "dw_age_gate/v1", "mode": self.mode, "enabled": True,
            "asof": self.asof, "reference_date": self.reference_date,
            "checked": len(self.verdicts), "generated_at": self.generated_at,
            "degraded": bool(self.stale_datasets),
            "stale_sources": [
                {"dataset": v.dataset, "state": v.state, "last_date": v.last_date,
                 "age_days": v.age_days, "max_age_days": v.max_age_days,
                 "consumer_class": v.consumer_class, "raw_last_date": v.raw_last_date}
                for v in self.verdicts if v.state in ("STALE", "CURATED_LAG_RAW_FRESH")
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "zhice.dw_age_gate/v1", "read_only": True,
            "generated_at": self.generated_at, "asof": self.asof,
            "reference_date": self.reference_date, "mode": self.mode,
            "enabled": self.enabled, "inventory_source": self.inventory_source,
            "inventory_generated_at": self.inventory_generated_at,
            "summary": {
                "total": len(self.verdicts),
                "fresh": sum(1 for v in self.verdicts
                             if v.state in ("FRESH", "FINANCIAL_PERIOD", "STATIC")),
                "stale": sum(1 for v in self.verdicts if v.state == "STALE"),
                "curated_lag_raw_fresh": sum(
                    1 for v in self.verdicts if v.state == "CURATED_LAG_RAW_FRESH"),
                "no_date": sum(1 for v in self.verdicts if v.state in ("NO_DATE", "UNKNOWN")),
                "blocked": len(self.blocking_datasets),
                "stale_datasets": self.stale_datasets,
            },
            "quality": self.quality_block(),
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


# --------------------------------------------------------------------------- #
# 输入加载（只读）
# --------------------------------------------------------------------------- #
def gate_mode_from_env(default: GateMode | None = None) -> GateMode:
    """读取 DW_AGE_GATE_MODE；未设置/非法 → 默认 off（非法值只告警，不抛错）。"""
    raw = (os.environ.get(ENV_MODE) or "").strip().lower()
    if raw in VALID_MODES:
        return raw  # type: ignore[return-value]
    if raw:
        logger.warning("dw_age_gate: 非法 %s=%r，回退默认 %s", ENV_MODE, raw, default or DEFAULT_MODE)
    return default or DEFAULT_MODE


def load_inventory(path: str | Path | None = None) -> dict[str, Any]:
    """加载 dw_full_inventory；缺失/损坏时回落内置快照（保证可用）。"""
    p = Path(path) if path else DEFAULT_INVENTORY
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            datasets: dict[str, Any] = {}
            for item in (data.get("curated") or {}).get("datasets") or []:
                if isinstance(item, dict) and item.get("dataset"):
                    datasets[item["dataset"]] = {
                        "last_date": item.get("last_date"),
                        "primary_date_col": item.get("primary_date_col"),
                        "consumers": item.get("consumers") or [],
                        "status": item.get("status") or {},
                    }
            if datasets:
                return {
                    "source": str(p),
                    "generated_at": data.get("generated_at"),
                    "reference_date": data.get("reference_date"),
                    "datasets": datasets,
                }
        except (OSError, ValueError) as exc:  # noqa: BLE001 - 降级到内置快照
            logger.warning("dw_age_gate: inventory 读取失败 %s: %s", p, exc)
    return {"source": "builtin-snapshot", **BUILTIN_SNAPSHOT}


def raw_latest_date(dataset: str) -> tuple[str | None, str]:
    """扫描 raw 落地目录的最近分区日期（只列文件名，不读 parquet）。"""
    folder = RAW_ROOT / RAW_OVERLAY.get(dataset, dataset)
    if not folder.is_dir():
        return None, "raw_dir_missing"
    best: date | None = None
    best_kind = "none"
    for p in folder.glob("*.parquet"):
        m = re.search(r"d_([0-9]{8})", p.name)
        if m:
            try:
                d = datetime.strptime(m.group(1), "%Y%m%d").date()
            except ValueError:
                continue
            if best is None or d > best:
                best, best_kind = d, "daily_partition"
        else:
            m = re.search(r"_([0-9]{6})(?:_|\.)", p.name)
            if m:
                try:
                    d = datetime.strptime(m.group(1) + "01", "%Y%m%d").date()
                except ValueError:
                    continue
                if best is None or d > best:
                    best, best_kind = d, "monthly_partition"
    return (best.isoformat() if best else None), best_kind


# --------------------------------------------------------------------------- #
# 评估（纯函数，不写文件/DB）
# --------------------------------------------------------------------------- #
def _age_days(asof: date, last: str | None) -> int | None:
    if not last:
        return None
    try:
        return (asof - date.fromisoformat(str(last)[:10])).days
    except ValueError:
        return None


def _classify(entry: dict[str, Any], cadence: str, asof: date,
              raw_date: str | None) -> dict[str, Any]:
    last = entry.get("last_date")
    status = entry.get("status") or {}
    max_age = CADENCE_MAX_AGE.get(cadence, 3)
    age = _age_days(asof, last)
    if not last and cadence in ("static", "no_date"):
        state = "STATIC"
    elif not last:
        state = "NO_DATE"
    elif status.get("financial_period"):
        state = "FINANCIAL_PERIOD"
    elif age is None:
        state = "UNKNOWN"
    elif age > max_age:
        state = "STALE"
    else:
        state = "FRESH"
    raw_age = _age_days(asof, raw_date) if raw_date else None
    if state == "STALE" and raw_date and raw_age is not None and raw_age < (age or 0):
        state = "CURATED_LAG_RAW_FRESH"
    return {"last_date": last, "age_days": age, "max_age_days": max_age, "state": state,
            "raw_last_date": raw_date, "raw_age_days": raw_age}


def _decide(state: str, mode: str, consumer_class: str) -> tuple[str, str]:
    if state in ("FRESH", "FINANCIAL_PERIOD", "STATIC"):
        return "allow", "数据新鲜（或按期末/静态口径不判过期）"
    if mode == "off":
        return "allow", "gate=off：仅诊断，不阻断"
    if mode == "warn":
        return "allow", "gate=warn：记录 quality/degraded，不阻断"
    if state == "CURATED_LAG_RAW_FRESH" and consumer_class in ("presentation", "evidence"):
        return "allow", "deny：展示/证据类不阻断但标 degraded（curated 滞后 raw 更新）"
    return "deny", "deny：过期源 fail-closed（禁止交易/信号类静默消费）"


def evaluate(asof: str | date | None = None, mode: str | None = None,
             include_raw: bool = False, force: bool = False) -> GateReport:
    """评估 18 个平台消费源的新鲜度，返回 GateReport；不写任何文件/DB。

    mode 解析优先级：参数 > DW_AGE_GATE_MODE > "off"。
    - off：默认 fast no-op（不评估，返回 enabled=False；force=True 时仍出诊断明细）。
    - warn：评估并记录 quality，不阻断（action=allow）。
    - deny：评估，过期交易/信号类给 deny（调用方决定是否阻断）。
    """
    resolved = (mode or gate_mode_from_env()).strip().lower()
    if resolved not in VALID_MODES:
        resolved = DEFAULT_MODE
    inv = load_inventory(DEFAULT_INVENTORY)
    ref = asof or inv.get("reference_date") or date.today().isoformat()
    try:
        asof_d = date.fromisoformat(str(ref)[:10])
    except ValueError:
        asof_d = date.today()
    enabled = force or resolved != "off"
    verdicts: list[SourceVerdict] = []
    if enabled:
        for ds in CONSUMED_DATASETS:
            entry = (inv.get("datasets") or {}).get(ds) or {}
            cadence, consumer_class, severity, note = REGISTRY.get(
                ds, ("no_date", "presentation", "P3", ""))
            raw_date, raw_kind = (
                raw_latest_date(ds) if include_raw and ds in RAW_OVERLAY else (None, "skipped"))
            c = _classify(entry, cadence, asof_d, raw_date)
            action, reason = _decide(c["state"], resolved, consumer_class)
            consumers = tuple(
                str(x.get("script", x)) if isinstance(x, dict) else str(x)
                for x in (entry.get("consumers") or []))
            verdicts.append(SourceVerdict(
                dataset=ds, layer="curated", cadence=cadence,
                consumer_class=consumer_class, severity=severity,
                last_date=c["last_date"], age_days=c["age_days"],
                max_age_days=c["max_age_days"], state=c["state"],
                action=action, reason=reason, raw_last_date=c["raw_last_date"],
                raw_kind=raw_kind, consumers=consumers))
    report = GateReport(
        asof=asof_d.isoformat(),
        reference_date=str(inv.get("reference_date") or "")[:10],
        mode=resolved, enabled=enabled, verdicts=verdicts,
        inventory_source=str(inv.get("source")), inventory_generated_at=inv.get("generated_at"))
    if resolved == "warn" and report.stale_datasets:
        logger.warning("dw_age_gate warn: %d 个被消费源过期（不阻断）: %s",
                       len(report.stale_datasets), ", ".join(report.stale_datasets))
    return report


def preflight_cache_build(producer: str = "dw_multidim_cache",
                          asof: str | date | None = None) -> GateReport:
    """缓存构建前置：按 DW_AGE_GATE_MODE 返回 quality 报告。

    - off（默认）：快速 no-op，不读 inventory、不评估、不写任何东西。
    - warn：评估并记录 quality（日志），**不阻断**。
    - deny：评估；存在被阻断的过期源时抛 StaleDataError（供未来严格模式）。
    调用方可用 ``attach_quality(payload, report)`` 把 quality 写入缓存 JSON
    （off 模式不会改动缓存内容）。
    """
    mode = gate_mode_from_env()
    if mode == "off":
        return GateReport(asof="", reference_date="", mode="off", enabled=False)
    report = evaluate(asof=asof, mode=mode, include_raw=True, force=True)
    if mode == "deny" and report.blocking_datasets:
        raise StaleDataError(
            f"{producer}: stale consumed sources blocked: {', '.join(report.blocking_datasets)}")
    logger.info("dw_age_gate %s: mode=%s stale=%d degraded=%s", producer, mode,
                len(report.stale_datasets), bool(report.stale_datasets))
    return report


def attach_quality(payload: dict[str, Any], report: GateReport) -> dict[str, Any]:
    """把 quality 块挂到缓存/响应字典；**off（disabled）模式不写入，保持完全 no-op**。"""
    if report.enabled:
        payload["quality"] = report.quality_block()
    return payload



# --------------------------------------------------------------------------- #
# CLI（只读；仅 --report 显式写报告 JSON）
# --------------------------------------------------------------------------- #
def _render_table(report: GateReport) -> str:
    q = report.quality_block()
    lines = [
        f"dw_age_gate（infrastructure/data_quality，只读） asof={report.asof} "
        f"mode={report.mode} enabled={report.enabled}",
        f"inventory={report.inventory_source} generated_at={report.inventory_generated_at}",
        f"checked={q['checked']} stale={len(report.stale_datasets)} "
        f"degraded={q['degraded']} blocked={len(report.blocking_datasets)}",
        "-" * 112,
        f"{'dataset':<18}{'cadence':<14}{'last':<12}{'age':>4}{'max':>5}  "
        f"{'state':<24}{'action':<10}class",
        "-" * 112,
    ]
    for v in report.verdicts:
        lines.append(f"{v.dataset:<18}{v.cadence:<14}{str(v.last_date):<12}"
                     f"{str(v.age_days):>4}{str(v.max_age_days):>5}  {v.state:<24}"
                     f"{v.action:<10}{v.consumer_class}")
    lines.append("-" * 112)
    if report.stale_datasets:
        lines.append(f"stale_datasets: {', '.join(report.stale_datasets)}")
    lines.append("提示：默认 DW_AGE_GATE_MODE=off（no-op）；warn 只记录不阻断；本模块未接每日任务。")
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="数据仓 freshness gate（默认 off；只读）")
    ap.add_argument("--mode", choices=("dry-run", "off", "warn", "deny"), default="dry-run",
                    help="dry-run=仅报告（等同 warn 判定但不写日志/不阻断）；默认 dry-run")
    ap.add_argument("--asof", help="评估基准日 YYYY-MM-DD（默认 inventory.reference_date）")
    ap.add_argument("--include-raw", action="store_true", help="扫描 raw 分区文件名，标注 curated/raw 关系")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--report", help="显式写出 JSON 报告到该路径（仅 C:\\Temp 建议）")
    ap.add_argument("--compare-7", action="store_true", help="与已知 7 个过期消费源对照")
    ap.add_argument("--strict", action="store_true", help="存在过期被消费源时退出码 2")
    args = ap.parse_args(argv)

    mode = "warn" if args.mode == "dry-run" else args.mode
    report = evaluate(asof=args.asof, mode=mode, include_raw=args.include_raw, force=True)
    if args.mode == "off":
        report.enabled = False
    if args.compare_7:
        actual = set(report.stale_datasets)
        expected = set(EXPECTED_STALE_CONSUMED)
        missing, extra = sorted(expected - actual), sorted(actual - expected)
        print(f"compare-7: expected={len(expected)} actual={len(actual)} "
              f"match={not missing and not extra}")
        if missing:
            print(f"  missing: {missing}")
        if extra:
            print(f"  extra: {extra}")
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render_table(report))
    if args.report:
        Path(args.report).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[dw_age_gate] report written: {args.report}", file=sys.stderr)
    if args.strict and report.stale_datasets:
        print(f"[dw_age_gate] strict: stale consumed sources: {report.stale_datasets}",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
