"""K-MDC-A5 (郑玄): seat-detail shadow — real pairwise seat co-occurrence.

tdx/ts_top_inst 分区 parquet (exalter/side/net_buy) 接入:
D2 batch_co_occurrence 升级为真两两席位共现 (同日同票, 买卖方向标注).
替换 E2 的近似演示 (榜单信号×原因). 读全部分区, 不 tail.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from intel_relation_rules import RelationCandidate, RelationRejected, co_occurrence

_REQUIRED = ("ts_code", "trade_date", "exalter", "side")


def load_seat_rows(repo_root: str | Path, *, limit_parts: int | None = None) -> pd.DataFrame:
    root = Path(repo_root)
    candidates = [root / "quant_backtest" / "data" / "tdx" / "ts_top_inst",
                  root / "data" / "tdx" / "ts_top_inst"]
    candidates += [p / "quant_backtest" / "data" / "tdx" / "ts_top_inst"
                   for p in root.parents]
    root_dir = next((c for c in candidates if c.exists()), None)
    if root_dir is None:
        raise RelationRejected("DATASET_MISSING: ts_top_inst not found")
    parts = sorted(root_dir.glob("part_*.parquet"))
    if limit_parts:
        parts = parts[-limit_parts:]
    frames = [pd.read_parquet(p, columns=list(_REQUIRED)) for p in parts]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=list(_REQUIRED))
    missing = [c for c in _REQUIRED if c not in df.columns]
    if missing:
        raise RelationRejected(f"SCHEMA_DRIFT: seat detail missing {missing}")
    return df


def seat_pairs(df: pd.DataFrame, *, max_pairs_per_day: int = 40) -> list[RelationCandidate]:
    """同日同票席位两两配对 → 真共现候选.

    side 标注: 0=买方 1=卖方 (Tushare 口径); pair 注明方向组合.
    同组 >10 席折叠为群体问题 (防爆炸, 与 D2 batch 同语义).
    """
    out: list[RelationCandidate] = []
    side_map = {"0": "买", "1": "卖"}
    for (d, code), grp in df.groupby(["trade_date", "ts_code"]):
        seats = grp["exalter"].drop_duplicates().tolist()
        if len(seats) < 2:
            continue
        key = f"top_inst:{d}:{code}"
        if len(seats) > 10:
            out.append(RelationCandidate(
                "co_occurrence", seats[0], f"{len(seats)}席群体", key,
                question=f"{d} {code} 有 {len(seats)} 家营业部同榜——群体交易日, 逐对明细见数据",
                basis=f"top_inst_group:{d}:{code}:{len(seats)}"))
            continue
        sides = {r["exalter"]: side_map.get(str(r["side"]), "?")
                 for _, r in grp.iterrows()}
        made = 0
        for i in range(len(seats)):
            for j in range(i + 1, len(seats)):
                if made >= max_pairs_per_day:
                    out.append(RelationCandidate(
                        "co_occurrence", "…", f"{code}截断", key,
                        question=f"{d} {code} 配对超上限{max_pairs_per_day}, 已截断可见",
                        basis=f"top_inst_trunc:{d}:{code}"))
                    break
                a, b = sorted((seats[i], seats[j]))
                c = co_occurrence(a, b, str(code), str(d), key)
                # 增强问题: 方向组合
                combo = f"{sides.get(a,'?')}{sides.get(b,'?')}"
                c = RelationCandidate(c.rel_type, c.left, c.right, c.evidence_key,
                                      f"{c.question}（方向: {a[:10]}={sides.get(a,'?')}/"
                                      f"{b[:10]}={sides.get(b,'?')} 组合{combo}）", c.basis)
                out.append(c)
                made += 1
            else:
                continue
            break
    return out


def top_net_buy_seats(df: pd.DataFrame, *, n: int = 10) -> list[dict]:
    """净买额头部营业部排行 (只报实况)."""
    agg = df.groupby("exalter")["net_buy"].sum().sort_values(ascending=False)
    return [{"seat": s, "net_buy_total": float(v)} for s, v in agg.head(n).items()]
