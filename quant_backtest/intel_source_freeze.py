"""雪球/股吧存量冻结登记 (K-MDC-R00 治理日裁定 ①)

老田 2026-10-23 21:35 裁定: 存量冻结只读 + 增量停用, 内容源转 Tushare/公开合规源.
本模块只登记冻结快照与停轮指令; 不删除任何数据, 不触任何采集进程配置
(停轮以本登记为准, 采集侧引用此常量).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FROZEN_AT = "2026-10-23T21:35:00+08:00"
FROZEN_BY = "老田"
BASIS = "K-MDC-20261023-R00 gate=BLOCK: 登录态采集+平台条款限制+无书面许可"

FROZEN_SOURCES = ("xueqiu", "guba")
ALLOWED_ACTIVE_SOURCES = ("tushare", "tdx")  # claimed 级可继续增量


@dataclass(frozen=True)
class FreezeRecord:
    source_id: str
    frozen_at: str
    basis: str
    stock_rows: int | None
    stock_note: str

    def as_dict(self) -> dict:
        return self.__dict__


def freeze_snapshot(repo_root: str | Path) -> dict:
    """登记冻结时刻的存量规模(如实计数, 缺目录记 unknown)."""
    root = Path(repo_root)
    xq_dir = root / "quant_backtest" / "data" / "xueqiu"
    rows = None
    manifest = xq_dir / "_manifest.json"
    if manifest.exists():
        import json

        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            rows = m.get("total") or m.get("rows") or m.get("count")
        except Exception:
            rows = None
    records = [
        FreezeRecord(
            "xueqiu",
            FROZEN_AT,
            BASIS,
            rows,
            "存量冻结只读; 增量停轮; 转合规源后可另行解冻(需书面许可)",
        ),
        FreezeRecord(
            "guba",
            FROZEN_AT,
            BASIS,
            None,
            "公开页+cookie 混合采集痕迹; 同口径冻结; 存量并入舆情库只读",
        ),
    ]
    return {
        "frozen_at": FROZEN_AT,
        "frozen_by": FROZEN_BY,
        "records": [r.as_dict() for r in records],
        "allowed_active": list(ALLOWED_ACTIVE_SOURCES),
        "policy": "增量采集禁用; 只读查询不受限; 解冻须书面许可+老田批准",
    }


def assert_source_active(source_id: str) -> None:
    """采集侧闸门: 冻结源在此抛异常 (调用方 catch 后停轮)."""
    if source_id in FROZEN_SOURCES:
        raise PermissionError(
            f"SOURCE_FROZEN: {source_id} 存量冻结/增量停用 @ {FROZEN_AT} ({BASIS}); "
            f"活跃源={ALLOWED_ACTIVE_SOURCES}; 解冻须书面许可+老田批准"
        )
