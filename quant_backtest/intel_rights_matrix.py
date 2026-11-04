"""K-MDC-R00 (马超): per-source rights matrix — honest audit, fail-closed.

Read-only registry of what each source's licensing state ACTUALLY is,
verified against on-disk evidence (config presence, data provenance files),
not assumptions. Every cell that lacks written permission evidence is BLOCK.
This module is an audit harness — it grants nothing, fetches nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

STATES = ("verified", "claimed", "unknown", "BLOCK")


class RightsRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceRight:
    source_id: str
    content_type: str
    licensing_party: str | None
    permitted_use: str | None
    retention_rule: str | None
    revocation_rule: str | None
    state: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise RightsRejected(f"state must be one of {STATES}")
        if self.state == "verified" and not (self.licensing_party and self.evidence):
            raise RightsRejected("verified requires licensing_party + evidence pointer")


def _tushare_state(repo_root: Path) -> SourceRight:
    """Tushare 接口: 有 token 在用(积分制服务), 用户协议在线签署——claimed 级."""
    token_files = list((repo_root / "quant_backtest").glob("*token*")) + list(
        (repo_root / "quant_backtest" / "data").glob("*token*")
    )
    has_data = (
        repo_root / "quant_backtest" / "data" / "tushare_curated" / "top_list.parquet"
    ).exists()
    state = "claimed" if (token_files or has_data) else "unknown"
    return SourceRight(
        "tushare",
        "api_table",
        "Tushare(积分制服务条款)",
        None,
        None,
        None,
        state,
        evidence=f"token_files={len(token_files)},curated_data={has_data};"
        "服务条款在线接受但无书面留存,衍生/再分发范围未核",
    )


def _xq_state(repo_root: Path) -> SourceRight:
    """雪球: 已登录采集(cookie), 但平台用户协议禁止未授权批量采集——BLOCK 级风险."""
    return SourceRight(
        "xueqiu",
        "post",
        None,
        None,
        None,
        None,
        "BLOCK",
        evidence="历史采集用完整cookie(登录态);平台服务条款对自动化采集有限制条款;"
        "无任何书面许可;真实采集继续前须停轮核权(老田 D2 前置)",
    )


def _guba_state(repo_root: Path) -> SourceRight:
    """股吧: 同雪球——登录态/公开页混合, 无书面许可."""
    return SourceRight(
        "guba",
        "post",
        None,
        None,
        None,
        None,
        "BLOCK",
        evidence="公开页+cookie 采集痕迹;无东方财富书面许可;列 BLOCK 待逐源核",
    )


def _tdx_state(repo_root: Path) -> SourceRight:
    """TDX 三表: 通达信本地终端数据+公开行情接口——claimed 级(终端协议随安装接受)."""
    has = (repo_root / "quant_backtest" / "data" / "tdx").exists()
    return SourceRight(
        "tdx",
        "news_table",
        "通达信终端(随安装的服务协议)",
        None,
        None,
        None,
        "claimed" if has else "unknown",
        evidence=f"tdx_data_dir={has};本地终端协议在线接受,新闻表衍生范围未核",
    )


def build_matrix(repo_root: str | Path) -> dict:
    """产出四源×内容类型权利矩阵; 任一 BLOCK 即整体 gate=BLOCK."""
    root = Path(repo_root)
    builders = (_tushare_state, _xq_state, _guba_state, _tdx_state)
    rows = [b(root) for b in builders]
    blocked = [r.source_id for r in rows if r.state == "BLOCK"]
    unknown = [r.source_id for r in rows if r.state == "unknown"]
    return {
        "matrix": [r.__dict__ for r in rows],
        "gate": "BLOCK" if blocked else ("REVIEW" if unknown else "OPEN"),
        "blocked_sources": blocked,
        "unknown_sources": unknown,
        "note": "矩阵为审计快照不授权;BLOCK 源在放行前不得新增真实采集(既有存量另行处置)",
    }


def verify_no_grant(m: dict) -> None:
    """防误用: build_matrix 的输出不得含 verified 状态(审计≠授权)."""
    for row in m["matrix"]:
        if row["state"] == "verified":
            raise RightsRejected(
                "audit snapshot must not claim verified rights; "
                "verification flows through signed evidence only"
            )
