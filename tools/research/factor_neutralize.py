"""因子横截面中性化：行业 + 市值。

研究用途。对每个交易日做同一截面回归：

    factor ~ 1 + log(total_mv) + industry_dummies

取残差作为中性化后的因子值。只使用 T 日截面信息，不含未来数据。

本模块不依赖 factor_lib 的评估逻辑，只返回残差矩阵；由 `factor_lib.run_eval(..., neutralize=...)`
组装临时 FactorSpec 后复用同一套 20 日 CAR / IC / 五分组评测。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


NEUTRALIZE_MODES = ("industry", "size")


def _industry_series(panel) -> pd.Series | None:
    raw = panel.load_meta.get("industry")
    if raw is None:
        return None
    ser = pd.Series(raw).reindex(panel.codes)
    return ser.fillna("UNKNOWN").astype(str)


def _size_frame(panel) -> pd.DataFrame | None:
    if "total_mv" not in panel.fields:
        return None
    mv = panel.fields["total_mv"].reindex(index=panel.dates, columns=panel.codes)
    return np.log(mv.where(mv > 0))


def _design_for_day(industry: pd.Series | None, ind_dummies: pd.DataFrame | None,
                    size_s: pd.Series | None, codes, rows: np.ndarray) -> np.ndarray:
    parts = [np.ones((len(rows), 1), dtype=float)]
    if size_s is not None:
        vals = size_s.iloc[rows].to_numpy(dtype=float)
        parts.append(np.asarray(vals, dtype=float).reshape(-1, 1))
    if ind_dummies is not None:
        d = ind_dummies.iloc[rows].to_numpy(dtype=float)
        parts.append(np.asarray(d, dtype=float))
    return np.hstack(parts) if len(parts) > 1 else parts[0]


def neutralize_matrix(panel, values: pd.DataFrame, *, industry: bool = True, size: bool = True,
                      min_obs: int = 30, eligible: bool = True) -> tuple[pd.DataFrame, dict]:
    """对单因子矩阵做逐日行业/市值中性化，返回残差矩阵与统计信息。"""
    vals = values.reindex(index=panel.dates, columns=panel.codes)
    if not isinstance(vals, pd.DataFrame):
        vals = pd.DataFrame(vals, index=panel.dates, columns=panel.codes)
    ind = _industry_series(panel) if industry else None
    size_df = _size_frame(panel) if size else None
    elig = panel.eligible_mask() if eligible else None
    if isinstance(elig, pd.DataFrame):
        elig = elig.reindex(index=panel.dates, columns=panel.codes)

    dummies = None
    if ind is not None:
        day_ind = ind.reindex(panel.codes)
        dummies = pd.get_dummies(day_ind, prefix="ind", drop_first=True, dtype=float)
        dummies = dummies.reindex(columns=sorted(dummies.columns)).fillna(0.0)
    out = pd.DataFrame(np.nan, index=panel.dates, columns=panel.codes, dtype=float)
    n_obs = 0
    n_days = 0
    for i in range(len(panel.dates)):
        y = vals.iloc[i].to_numpy(dtype=float)
        mask = np.isfinite(y)
        if elig is not None:
            mask &= elig.iloc[i].to_numpy(dtype=bool)
        if size_df is not None:
            mask &= np.isfinite(size_df.iloc[i].to_numpy(dtype=float))
        rows = np.flatnonzero(mask)
        if len(rows) < min_obs:
            continue
        size_s = None
        if size_df is not None:
            size_s = size_df.iloc[i]
        X = _design_for_day(ind, dummies, size_s, panel.codes, rows)
        yv = y[rows]
        try:
            beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
        except np.linalg.LinAlgError:  # pragma: no cover - 极端共线
            continue
        out.iloc[i, rows] = yv - X @ beta
        n_obs += int(len(rows))
        n_days += 1
    info = {
        "modes": [m for m in NEUTRALIZE_MODES if (m == "industry" and industry) or (m == "size" and size)],
        "min_obs": int(min_obs),
        "eligible": bool(eligible),
        "n_days": int(n_days),
        "n_obs": int(n_obs),
        "size_available": size_df is not None,
        "industry_available": ind is not None,
    }
    return out, info


def build_neutralized_specs(panel, names: list[str] | tuple[str, ...], *,
                            industry: bool = True, size: bool = True,
                            min_obs: int = 30) -> tuple[dict, dict]:
    """计算原始因子并构造中性化后的临时 FactorSpec（供 evaluate_panel 使用）。"""
    from factor_lib import FACTORS, FactorSpec

    result = {}
    raw = {}
    infos = {}
    for name in names:
        if name not in FACTORS:
            raise KeyError(f"未注册因子: {name}")
        raw[name] = FACTORS[name].compute(panel)
    for name, mat in raw.items():
        neut, info = neutralize_matrix(panel, mat, industry=industry, size=size, min_obs=min_obs)
        base = FACTORS[name]
        result[name] = FactorSpec(
            name=base.name,
            family=f"{base.family}-中性化",
            kind=base.kind,
            direction=base.direction,
            description=f"{base.description}（行业+市值中性化残差）",
            source="factor_neutralize.industry+size",
            requires=(),
            compute=(lambda _panel, _mat=neut: _mat),
        )
        infos[name] = info
    return result, {"factors": infos, "industry": bool(industry), "size": bool(size),
                    "min_obs": int(min_obs)}


def parse_modes(text: str | None) -> tuple[bool, bool]:
    if not text:
        return False, False
    parts = {x.strip().lower() for x in str(text).split(",") if x.strip()}
    if "none" in parts or "off" in parts:
        return False, False
    bad = parts - set(NEUTRALIZE_MODES)
    if bad:
        raise ValueError(f"未知中性化模式: {sorted(bad)}；可选 {NEUTRALIZE_MODES}")
    return "industry" in parts, "size" in parts
