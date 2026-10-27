"""K-MDC-A4 (满宠): LHB / top-holders read-only shadow projection.

Maps three curated parquet datasets into the intel evidence model WITHOUT
copying rows or touching the quant store: every probe returns provenance
paths, row counts, schema, clock-semantics notes (occurred≠disclosed) and
honest unknowns. Fail-closed on missing/renamed columns; zero network,
zero writes to source data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ShadowRejected(RuntimeError):
    """Fail-closed shadow rejection."""


@dataclass(frozen=True)
class ShadowCatalog:
    key: str
    path: Path
    rows: int
    columns: tuple
    clock_notes: dict

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "path": str(self.path),
            "rows": self.rows,
            "columns": list(self.columns),
            "clock_notes": self.clock_notes,
        }


_LHB_REQUIRED = ("trade_date", "ts_code", "name", "l_buy", "l_sell", "reason")
_HOLD_REQUIRED = ("ts_code", "ann_date", "end_date", "holder_name", "hold_ratio")


def _load_parquet(path: Path):
    import pandas as pd  # deferred: heavy import stays out of module init

    if not path.exists():
        raise ShadowRejected(f"DATASET_MISSING: {path}")
    return pd.read_parquet(path)


def lhb_shadow(root: str | Path, *, limit: int = 5) -> dict:
    """龙虎榜 shadow: trade_date is BOTH occurrence and public list date."""
    path = Path(root) / "top_list.parquet"
    df = _load_parquet(path)
    missing = [c for c in _LHB_REQUIRED if c not in df.columns]
    if missing:
        raise ShadowRejected(f"SCHEMA_DRIFT: lhb missing {missing}")
    recent = df.tail(limit)
    return {
        "catalog": ShadowCatalog(
            "lhb",
            path,
            len(df),
            tuple(df.columns),
            {
                "occurred_at": "trade_date (list date = event date, 天级精度)",
                "disclosed_at": "trade_date (榜单随收盘公开发布)",
                "first_seen_at": "unknown — 入库时刻未事件化, 不可倒填",
                "available_at": "unknown — 消费者可读时刻未事件化",
            },
        ).as_dict(),
        "preview": recent[["trade_date", "ts_code", "name", "l_buy", "l_sell"]].to_dict(
            "records"
        ),
    }


def holders_shadow(root: str | Path, *, floater: bool = False, limit: int = 5) -> dict:
    """十大(流通)股东 shadow: end_date=occurred, ann_date=disclosed — 两条时钟链分离."""
    name = "top10_floatholders" if floater else "top10_holders"
    path = Path(root) / f"{name}.parquet"
    df = _load_parquet(path)
    missing = [c for c in _HOLD_REQUIRED if c not in df.columns]
    if missing:
        raise ShadowRejected(f"SCHEMA_DRIFT: {name} missing {missing}")
    late = (df["ann_date"] < df["end_date"]).sum()
    return {
        "catalog": ShadowCatalog(
            name,
            path,
            len(df),
            tuple(df.columns),
            {
                "occurred_at": "end_date (报告期末, 天级)",
                "disclosed_at": f"ann_date (公告日; 实测 {late} 行 ann<end 视为脏数据待核)",
                "first_seen_at": "unknown — 采集入库时刻未事件化",
                "available_at": "unknown — 消费者可读时刻未事件化",
                "holder_identity": "holder_name 仅字符串 — 同名异人/一人多名未消歧, 归 D1 判据",
            },
        ).as_dict(),
        "preview": df.tail(limit)[
            ["ts_code", "ann_date", "end_date", "holder_name", "hold_ratio"]
        ].to_dict("records"),
    }


def cross_signal_shadow(root: str | Path, holder_name: str) -> dict:
    """席位/股东×龙虎榜候选关联: 只提调查问题, 不出事实 (老田场景④的候选生成器).

    Returns co-occurrence candidates where a top holder's stock later appears
    on the LHB list — every hit is a QUESTION (candidate), never a fact.
    """
    root = Path(root)
    hold = _load_parquet(root / "top10_holders.parquet")
    lhb = _load_parquet(root / "top_list.parquet")
    if not isinstance(holder_name, str) or not holder_name.strip():
        raise ShadowRejected("holder_name required")
    held = hold[hold["holder_name"] == holder_name]["ts_code"].unique().tolist()
    hits = lhb[lhb["ts_code"].isin(held)][["trade_date", "ts_code", "name", "reason"]]
    return {
        "holder_name": holder_name,
        "held_codes": len(held),
        "lhb_hits": hits.to_dict("records"),
        "verdict": "candidate_only — 持有与上榜时间邻近不构成因果, 须经证据链+人审",
    }
