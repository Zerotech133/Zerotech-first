"""tools/research/factor_lib_financial.py · B3 PIT 财报因子（Growth 15 / Quality 59 / Value 11）。

设计：
- 从 ``quant_backtest/data/tushare/{income,balancesheet,cashflow,fina_indicator}``
  读取报表，按 ``ann_date`` 做 PIT（只使用公告日 <= 交易日的最新一期）；
- 先在报表层面计算单季/ TTM/年度指标和比率，再按公告日前向填充到交易日，
  最后做截面排名；
- 因子函数统一签名为 ``fn(panel) -> DataFrame``，通过 ``get_financial(panel)``
  懒加载并缓存 ``FinancialData``；测试可把 ``FinancialData`` 注入
  ``panel.load_meta["financial"]`` 避免读盘。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd

SOURCE = "tushare.factor_list"
BATCH_B3 = "B3_financial_pit"
DEFAULT_ROOT = Path("quant_backtest") / "data" / "tushare"

# ---------------------------------------------------------------- 字段清单
INCOME_COLS = (
    "total_revenue", "revenue", "oper_cost", "operate_profit", "total_profit",
    "n_income", "n_income_attr_p", "basic_eps", "diluted_eps", "ebit", "ebitda",
    "income_tax", "sell_exp", "admin_exp", "fin_exp", "biz_tax_surchg",
    "fin_exp_int_exp", "invest_income", "total_cogs",
)
BALANCE_COLS = (
    "total_assets", "total_liab", "total_hldr_eqy_exc_min_int",
    "total_hldr_eqy_inc_min_int", "total_cur_assets", "total_cur_liab",
    "inventories", "money_cap", "trad_asset", "accounts_receiv",
    "accounts_receiv_bill", "fix_assets", "lt_rec", "adv_receipts",
    "prepayment", "defer_tax_assets", "total_ncl", "total_share",
    "contract_assets", "contract_liab",
)
CASHFLOW_COLS = (
    "n_cashflow_act", "n_cashflow_inv_act", "n_cash_flows_fnc_act",
    "free_cashflow", "stot_out_inv_act", "stot_inflows_inv_act",
    "c_inf_fr_operate_a", "c_paid_to_for_empl", "c_pay_dist_dpcp_int_exp",
    "stot_cash_in_fnc_act", "stot_cashout_fnc_act", "c_pay_acq_const_fiolta",
)
FINA_COLS = ("eps", "profit_dedt", "gross_margin", "current_ratio", "quick_ratio",
             "cash_ratio", "assets_turn", "netprofit_margin", "grossprofit_margin",
             "roe", "roa", "debt_to_assets", "ocf_to_debt")

FLOW_INCOME = ("total_revenue", "revenue", "oper_cost", "operate_profit",
               "total_profit", "n_income", "n_income_attr_p", "basic_eps",
               "diluted_eps", "ebit", "ebitda", "income_tax", "sell_exp",
               "admin_exp", "fin_exp", "biz_tax_surchg", "fin_exp_int_exp",
               "invest_income", "total_cogs")
FLOW_CASH = ("n_cashflow_act", "n_cashflow_inv_act", "n_cash_flows_fnc_act",
             "free_cashflow", "stot_out_inv_act", "stot_inflows_inv_act",
             "c_inf_fr_operate_a", "c_paid_to_for_empl",
             "c_pay_dist_dpcp_int_exp", "stot_cash_in_fnc_act",
             "stot_cashout_fnc_act", "c_pay_acq_const_fiolta")

# 需要年度值的流量科目（用于 *_y 因子）
ANNUAL_COLS = ("revenue", "total_revenue", "n_income", "n_income_attr_p",
               "basic_eps", "operate_profit", "total_profit", "oper_cost",
               "ebit", "ebitda", "n_cashflow_act")


# ---------------------------------------------------------------- 工具
def _num(df: pd.DataFrame, name: str) -> pd.Series:
    if name not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[name], errors="coerce")


def _safe_div(a: pd.Series | pd.DataFrame, b: pd.Series | pd.DataFrame) -> Any:
    """逐元素安全除法，分母 0/NaN → NaN。"""
    if isinstance(a, pd.DataFrame) or isinstance(b, pd.DataFrame):
        out = a / b.replace(0.0, np.nan)
        if isinstance(out, pd.DataFrame):
            return out.replace([np.inf, -np.inf], np.nan)
        return out
    out = a / b.replace(0.0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def _pct(a: pd.Series | pd.DataFrame, b: pd.Series | pd.DataFrame) -> Any:
    return _safe_div(a, b) - 1.0


def _rolling_rank(panel: Any) -> Callable[[pd.DataFrame], pd.DataFrame]:
    def rank(df: pd.DataFrame) -> pd.DataFrame:
        return df.rank(axis=1, pct=True)
    return rank


def _rank(df: pd.DataFrame) -> pd.DataFrame:
    return df.rank(axis=1, pct=True)


# ---------------------------------------------------------------- 报表加载
def _read_table(root: Path, table: str, columns: Iterable[str],
                codes: set[str] | None = None) -> pd.DataFrame:
    folder = Path(root) / table
    files = sorted(folder.glob("*.parquet"))
    if codes is not None:
        wanted = {c.replace(".", "_") for c in codes}
        files = [f for f in files if f.stem in wanted]
    frames: list[pd.DataFrame] = []
    for file in files:
        try:
            df = pd.read_parquet(file)
        except Exception:  # pragma: no cover - 坏文件跳过
            continue
        keep = ["ts_code", "ann_date", "end_date"] + [c for c in columns if c in df.columns]
        if "report_type" in df.columns:
            keep.append("report_type")
        frames.append(df[keep].copy())
    if not frames:
        return pd.DataFrame(columns=["ts_code", "ann_date", "end_date"])
    out = pd.concat(frames, ignore_index=True)
    out["ts_code"] = out["ts_code"].astype(str)
    out["ann_date"] = pd.to_datetime(out["ann_date"], errors="coerce")
    out["end_date"] = pd.to_datetime(out["end_date"], errors="coerce")
    if "report_type" in out.columns:
        out = out[out["report_type"].astype(str).isin(("1", "1.0", "")) | out["report_type"].isna()]
    out = out.sort_values(["ts_code", "end_date", "ann_date"])
    out = out.drop_duplicates(["ts_code", "end_date"], keep="last")
    return out.reset_index(drop=True)


def _add_flow_metrics(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    """为累计流量科目补单季（_q）、TTM（_ttm）、年度（_y）。"""
    df = df.sort_values(["ts_code", "end_date"]).reset_index(drop=True).copy()
    df["_year"] = df["end_date"].dt.year
    df["_quarter"] = df["end_date"].dt.quarter
    for col in cols:
        if col not in df.columns:
            continue
        v = pd.to_numeric(df[col], errors="coerce")
        df[col] = v
        # 单季：Q1=累计；其余=本期累计-同年上一季累计
        prev = df.groupby(["ts_code", "_year"])[col].shift(1)
        df[f"{col}_q"] = v.where(df["_quarter"] == 1, v - prev)
        # 年度：12-31 的值，按股票前向填充
        annual = v.where(df["_quarter"] == 4)
        df[f"{col}_y"] = annual.groupby(df["ts_code"]).ffill()
        # TTM：年报=年度；中报/季报=本期+上年年报-上年同期
        annual_map = df.loc[df["_quarter"] == 4, ["ts_code", "_year", col]].copy()
        annual_map = annual_map.rename(columns={col: "_annual"})
        annual_map["_year"] = annual_map["_year"] + 1
        df = df.merge(annual_map[["ts_code", "_year", "_annual"]],
                      on=["ts_code", "_year"], how="left")
        prior = df[["ts_code", "_year", "_quarter", col]].copy()
        prior["_year"] = prior["_year"] + 1
        df = df.merge(prior, on=["ts_code", "_year", "_quarter"],
                      how="left", suffixes=("", "_ps"))
        v2 = pd.to_numeric(df[col], errors="coerce")
        ttm = v2.where(df["_quarter"] == 4, v2 + df["_annual"] - df[f"{col}_ps"])
        df[f"{col}_ttm"] = ttm
        df = df.drop(columns=["_annual", f"{col}_ps"])
        df = df.sort_values(["ts_code", "end_date"]).reset_index(drop=True)
    return df


def _build_derived(df: pd.DataFrame) -> pd.DataFrame:
    """在报表层面构造派生财务指标。"""
    df = df.copy()

    def g(name: str, suffix: str = "") -> pd.Series:
        col = f"{name}{suffix}"
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce")
        return pd.Series(np.nan, index=df.index, dtype=float)

    # 流量：收入/成本/利润
    for col in FLOW_INCOME + FLOW_CASH:
        for suf in ("_q", "_ttm", "_y"):
            if f"{col}{suf}" not in df.columns and col in df.columns:
                df[f"{col}{suf}"] = np.nan
    # 存量
    for col in BALANCE_COLS:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")

    revenue_ttm, revenue_q, revenue_y = g("revenue", "_ttm"), g("revenue", "_q"), g("revenue", "_y")
    cost_ttm, cost_q, cost_y = g("oper_cost", "_ttm"), g("oper_cost", "_q"), g("oper_cost", "_y")
    ni_ttm, ni_q, ni_y = g("n_income", "_ttm"), g("n_income", "_q"), g("n_income", "_y")
    nip_ttm, nip_q, nip_y = (g("n_income_attr_p", "_ttm"), g("n_income_attr_p", "_q"),
                             g("n_income_attr_p", "_y"))
    op_ttm, op_q, op_y = (g("operate_profit", "_ttm"), g("operate_profit", "_q"),
                          g("operate_profit", "_y"))
    tp_ttm, tp_q = g("total_profit", "_ttm"), g("total_profit", "_q")
    ebit_ttm, ebitda_ttm = g("ebit", "_ttm"), g("ebitda", "_ttm")
    interest_ttm = g("fin_exp_int_exp", "_ttm")
    if interest_ttm.isna().all():
        interest_ttm = g("fin_exp", "_ttm").abs()
    tax_ttm = g("income_tax", "_ttm")
    ocf_ttm = g("n_cashflow_act", "_ttm")
    icf_ttm = g("n_cashflow_inv_act", "_ttm")
    fcf_ttm = g("n_cash_flows_fnc_act", "_ttm")
    inv_out_ttm = g("stot_out_inv_act", "_ttm")
    op_in_ttm = g("c_inf_fr_operate_a", "_ttm")
    inv_in_ttm = g("stot_inflows_inv_act", "_ttm")
    staff_ttm = g("c_paid_to_for_empl", "_ttm")
    sell_ttm, admin_ttm, fin_ttm = g("sell_exp", "_ttm"), g("admin_exp", "_ttm"), g("fin_exp", "_ttm")
    sell_q, admin_q, fin_q = g("sell_exp", "_q"), g("admin_exp", "_q"), g("fin_exp", "_q")
    tax_surcharge_ttm = g("biz_tax_surchg", "_ttm")
    eps_ttm, eps_q, eps_y = g("basic_eps", "_ttm"), g("basic_eps", "_q"), g("basic_eps", "_y")
    profit_dedt_ttm = g("profit_dedt", "_ttm")
    if profit_dedt_ttm.isna().all():
        profit_dedt_ttm = nip_ttm

    assets = g("total_assets")
    liab = g("total_liab")
    equity = g("total_hldr_eqy_exc_min_int")
    equity_inc = g("total_hldr_eqy_inc_min_int")
    if equity.isna().all():
        equity = equity_inc
    ca, cl = g("total_cur_assets"), g("total_cur_liab")
    inv, cash, trad = g("inventories"), g("money_cap"), g("trad_asset")
    ar, far, ltr = g("accounts_receiv"), g("fix_assets"), g("lt_rec")
    rear, prepay = g("adv_receipts"), g("prepayment")
    dta, tncl, shares = g("defer_tax_assets"), g("total_ncl"), g("total_share")

    df["revenue_ttm"], df["revenue_q"], df["revenue_y"] = revenue_ttm, revenue_q, revenue_y
    df["gross_margin_ttm"] = _safe_div(revenue_ttm - cost_ttm, revenue_ttm)
    df["gross_margin_q"] = _safe_div(revenue_q - cost_q, revenue_q)
    df["gross_margin_y"] = _safe_div(revenue_y - cost_y, revenue_y)
    df["net_margin_ttm"] = _safe_div(ni_ttm, revenue_ttm)
    df["net_margin_q"] = _safe_div(ni_q, revenue_q)
    df["net_margin_y"] = _safe_div(ni_y, revenue_y)
    df["opm_ttm"] = _safe_div(op_ttm, revenue_ttm)
    df["opm_y"] = _safe_div(op_y, revenue_y)
    df["roa_ttm"] = _safe_div(ni_ttm, assets)
    df["roa_q"] = _safe_div(ni_q, assets)
    df["roa_y"] = _safe_div(ni_y, assets)
    df["roe_ttm"] = _safe_div(nip_ttm, equity)
    df["roe_q"] = _safe_div(nip_q, equity)
    df["roe_y"] = _safe_div(nip_y, equity)
    df["current_ratio"] = _safe_div(ca, cl)
    df["quick_ratio"] = _safe_div(ca - inv, cl)
    df["cash_ratio"] = _safe_div(cash + trad, cl)
    df["debt_asset_ratio"] = _safe_div(liab, assets)
    df["de"] = _safe_div(liab, equity)
    df["financial_leverage"] = _safe_div(assets, equity)
    df["asset_turnover_ttm"] = _safe_div(revenue_ttm, assets)
    df["equity_turnover_ttm"] = _safe_div(revenue_ttm, equity)
    df["inventory_turnover_ttm"] = _safe_div(cost_ttm, inv)
    df["receivable_turnover_ttm"] = _safe_div(revenue_ttm, ar)
    df["fixed_asset_turnover_ttm"] = _safe_div(revenue_ttm, far)
    df["ocf_ttm"], df["icf_ttm"], df["fcf_ttm"] = ocf_ttm, icf_ttm, fcf_ttm
    df["invest_outflow_ttm"] = inv_out_ttm
    df["ocf_inflow_ttm"] = op_in_ttm
    df["inv_inflow_ttm"] = inv_in_ttm
    df["staff_ttm"] = staff_ttm
    df["interest_ttm"] = interest_ttm
    df["tax_ttm"] = tax_ttm
    df["ebit_ttm"], df["ebitda_ttm"] = ebit_ttm, ebitda_ttm
    df["tax_surcharge_ttm"] = tax_surcharge_ttm
    df["eps_ttm"], df["eps_q"], df["eps_y"] = eps_ttm, eps_q, eps_y
    df["profit_dedt_ttm"] = profit_dedt_ttm
    # 报表层面的比率（Growth 的 YoY / QoQ 用）
    df["expenses_q"] = sell_q + admin_q + fin_q
    df["expenses_ratio"] = _safe_div(df["expenses_q"], equity)
    df["np_to_deferred_tax"] = _safe_div(ni_q, dta)
    df["np_to_fixed_assets"] = _safe_div(ni_q, far)
    df["np_to_inventory"] = _safe_div(ni_q, inv)
    df["np_to_salary"] = _safe_div(nip_ttm, staff_ttm)
    df["np_to_total_expenses"] = _safe_div(ni_q, df["expenses_q"])
    df["np_to_total_profit"] = _safe_div(op_q, tp_q)
    df["ocf_to_market_num"] = ocf_ttm
    df["ncf_ttm"] = ocf_ttm + icf_ttm + fcf_ttm
    df["fcf_ttm2"] = ocf_ttm - inv_out_ttm
    df["ebitda_ttm"] = ebitda_ttm
    df["quality_1"] = _safe_div(op_ttm, assets)
    df["quality_2"] = _safe_div(ni_ttm, equity)
    df["quality_3"] = _safe_div(ni_ttm, assets)
    df["quality_4"] = _safe_div(op_in_ttm + inv_in_ttm, assets)
    df["quality_5"] = _safe_div(op_ttm, revenue_ttm)
    df["quality_6"] = _safe_div(ni_ttm, op_in_ttm + inv_in_ttm)
    df["quality_composite"] = df[["quality_1", "quality_2", "quality_3",
                                 "quality_4", "quality_5", "quality_6"]].sum(axis=1, min_count=1)
    df["ar_ap_to_revenue"] = _safe_div(g("adv_receipts") - g("prepayment"), revenue_q)
    df["cash_profit_ratio"] = _safe_div(ocf_ttm - ni_ttm, ni_ttm)
    df["cfcr"] = _safe_div(ocf_ttm, interest_ttm)
    df["icr"] = _safe_div(ebit_ttm, interest_ttm)
    df["npm_tsh"] = _safe_div(nip_ttm, shares)
    df["market_value_leverage"] = _safe_div(g("total_assets") - tncl, g("total_assets"))
    df["sales_to_market_num"] = revenue_q
    df["earnings_to_price_num"] = nip_ttm
    df["earnings_cut_to_market_num"] = profit_dedt_ttm
    df["ebitda_to_market_num"] = ebitda_ttm
    df["fcf_to_market_num"] = df["fcf_ttm2"]
    df["ncf_to_market_num"] = df["ncf_ttm"]
    df["ocf_to_market_num"] = ocf_ttm
    df["book_to_market_num"] = equity + dta
    # QoQ（报表层面，之后前向填充）
    for col in ("total_assets", "gross_margin_ttm", "n_income_ttm", "net_margin_q",
                "net_margin_ttm"):
        if col in df.columns:
            df[f"{col}_qoq"] = _pct(df[col], df.groupby("ts_code")[col].shift(1))
    # 同比（报表层面，前第 4 个披露日）
    for col in ("expenses_ratio", "np_to_deferred_tax", "np_to_fixed_assets",
                "np_to_inventory", "np_to_salary", "np_to_total_expenses",
                "lt_rec", "tax_surcharge_ttm", "income_tax_ttm"):
        if col in df.columns:
            df[f"{col}_yoy"] = _pct(df[col], df.groupby("ts_code")[col].shift(4))
    return df


# ---------------------------------------------------------------- 数据对象
@dataclass
class FinancialData:
    reports: pd.DataFrame
    root: str = ""
    cache: dict[str, pd.DataFrame] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.reports = self.reports.sort_values(["ts_code", "end_date"]).reset_index(drop=True)
        self._ann = self.reports[["ann_date", "ts_code"]].copy()
        self._ann["ann_date"] = pd.to_datetime(self._ann["ann_date"], errors="coerce")

    @classmethod
    def load(cls, root: str | Path = DEFAULT_ROOT, codes: Iterable[str] | None = None,
             limit: int | None = None) -> "FinancialData":
        root = Path(root)
        code_set = set(codes) if codes is not None else None
        if code_set is not None and limit:
            code_set = set(sorted(code_set)[:limit])
        inc = _read_table(root, "income", INCOME_COLS, code_set)
        bal = _read_table(root, "balancesheet", BALANCE_COLS, code_set)
        cf = _read_table(root, "cashflow", CASHFLOW_COLS, code_set)
        fi = _read_table(root, "fina_indicator", FINA_COLS, code_set)
        if inc.empty and bal.empty and cf.empty:
            return cls(pd.DataFrame(columns=["ts_code", "ann_date", "end_date"]), str(root))
        # 各表公告日的最大值，作为该报告期整体可见日（保守：最晚一张表可见才可见）
        ann = pd.concat([d[["ts_code", "end_date", "ann_date"]]
                         for d in (inc, bal, cf, fi) if not d.empty], ignore_index=True)
        base = ann.groupby(["ts_code", "end_date"], as_index=False)["ann_date"].max()
        # 每个科目表各自取最新公告；合并后 ann_date 取最大值（保守假设同一期报表一起可见）
        def prep(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
            if df.empty:
                return df
            d = df[["ts_code", "ann_date", "end_date"] + [c for c in cols if c in df.columns]].copy()
            d = d.sort_values(["ts_code", "end_date", "ann_date"]).drop_duplicates(
                ["ts_code", "end_date"], keep="last")
            return d
        merged = base.merge(prep(inc, FLOW_INCOME), on=["ts_code", "end_date"], how="left",
                            suffixes=("", "_inc"))
        merged = merged.merge(prep(bal, BALANCE_COLS), on=["ts_code", "end_date"], how="left",
                              suffixes=("", "_bal"))
        merged = merged.merge(prep(cf, FLOW_CASH), on=["ts_code", "end_date"], how="left",
                              suffixes=("", "_cf"))
        merged = merged.merge(prep(fi, FINA_COLS), on=["ts_code", "end_date"], how="left",
                              suffixes=("", "_fi"))
        merged = merged.drop(columns=[c for c in merged.columns if c.endswith(("_inc", "_bal", "_cf", "_fi"))])
        # 合并各表 ann_date：取最大值
        ann_cols = [c for c in ("ann_date",) if c in merged.columns]
        merged["ann_date"] = merged["ann_date"]
        merged = merged[merged["end_date"].notna() & merged["ann_date"].notna()]
        merged = merged[merged["end_date"] >= pd.Timestamp("2009-01-01")]
        merged = _add_flow_metrics(merged, FLOW_INCOME + FLOW_CASH + ("profit_dedt",))
        merged = _build_derived(merged)
        return cls(merged, str(root))

    # -- 日频化
    def daily(self, column: str, panel: Any, agg: str = "last") -> pd.DataFrame:
        if column in self.cache:
            return self.cache[column]
        reports = self.reports
        if column not in reports.columns:
            out = pd.DataFrame(np.nan, index=panel.dates, columns=panel.codes)
            self.cache[column] = out
            return out
        sub = reports[["ann_date", "ts_code", column]].dropna(subset=["ann_date"])
        sub = sub.dropna(subset=[column])
        if sub.empty:
            out = pd.DataFrame(np.nan, index=panel.dates, columns=panel.codes)
            self.cache[column] = out
            return out
        sub = sub.sort_values("ann_date").drop_duplicates(["ann_date", "ts_code"], keep="last")
        wide = sub.pivot_table(index="ann_date", columns="ts_code", values=column, aggfunc=agg)
        trade_dates = pd.to_datetime(pd.Index(panel.dates))
        all_dates = wide.index.union(trade_dates)
        wide = wide.reindex(all_dates).ffill()
        out = wide.reindex(trade_dates)
        out.index = panel.dates
        out = out.reindex(columns=panel.codes)
        self.cache[column] = out.astype(float)
        return self.cache[column]

    def qoq(self, column: str, panel: Any) -> pd.DataFrame:
        return self.daily(column, panel)

    def rank(self, column: str, panel: Any) -> pd.DataFrame:
        return self.daily(column, panel).rank(axis=1, pct=True)


def _empty_financial() -> FinancialData:
    cached = getattr(_empty_financial, "_cache", None)
    if cached is None:
        cached = FinancialData(pd.DataFrame(columns=["ts_code", "ann_date", "end_date"]))
        setattr(_empty_financial, "_cache", cached)
    return cached


def get_financial(panel: Any) -> FinancialData:
    """从 panel.load_meta 注入或按 data_root 懒加载（进程内缓存）。"""
    meta = getattr(panel, "load_meta", None) or {}
    injected = meta.get("financial")
    if isinstance(injected, FinancialData):
        return injected
    root = str(meta.get("data_root") or "").strip()
    if not root:
        return _empty_financial()
    cache = getattr(get_financial, "_cache", None)
    if cache is None:
        cache = {}
        setattr(get_financial, "_cache", cache)
    key = (root, tuple(str(c) for c in getattr(panel, "codes", ())))
    if key not in cache:
        cache[key] = FinancialData.load(root, codes=set(getattr(panel, "codes", ())))
    return cache[key]


def _price(panel: Any, name: str) -> pd.DataFrame:
    value = panel.fields.get(name) if hasattr(panel, "fields") else None
    if value is None:
        return pd.DataFrame(np.nan, index=panel.dates, columns=panel.codes)
    return value.reindex(index=panel.dates, columns=panel.codes).astype(float)


def _daily_rank(df: pd.DataFrame) -> pd.DataFrame:
    return df.rank(axis=1, pct=True)


# ---------------------------------------------------------------- 因子计算辅助
# 每个函数接收 (fd, panel)，返回 DataFrame（尚未 rank）
def _f(fd: FinancialData, panel: Any, col: str) -> pd.DataFrame:
    return fd.daily(col, panel)


def _rank_of(fd: FinancialData, panel: Any, col: str) -> pd.DataFrame:
    return _daily_rank(_f(fd, panel, col))


def _ratio_daily(fd: FinancialData, panel: Any, num: str, den: str) -> pd.DataFrame:
    return _safe_div(_f(fd, panel, num), _f(fd, panel, den))


def _yoy_daily(fd: FinancialData, panel: Any, col: str, window: int = 252) -> pd.DataFrame:
    d = _f(fd, panel, col)
    return _safe_div(d, d.shift(window)) - 1.0


def _delta_daily(fd: FinancialData, panel: Any, col: str, window: int = 252) -> pd.DataFrame:
    d = _f(fd, panel, col)
    return d - d.shift(window)


def _ranked_daily(fd: FinancialData, panel: Any, series_fn: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    return _daily_rank(series_fn())


# ---------------------------------------------------------------- 因子注册表
FactorDef = dict[str, Any]
B3_FACTORS: dict[str, FactorDef] = {}


def _add(name: str, factor_type: str, fn: Callable[[FinancialData, Any], pd.DataFrame],
         direction: int = 1, requires: tuple[str, ...] = (), note: str = "") -> None:
    B3_FACTORS[name] = {
        "name": name, "factor_type": factor_type, "batch": BATCH_B3,
        "direction": direction, "fn": fn, "requires": requires, "note": note,
    }


# -- Growth 15
def _g(fd: FinancialData, panel: Any, col: str) -> pd.DataFrame:
    return _rank_of(fd, panel, col)


_add("asset_growth_qoq", "Growth", lambda fd, p: _rank_of(fd, p, "total_assets_qoq"),
     note="总资产环比增速（报表层面 QoQ，PIT 填充）")
_add("eaa", "Growth", lambda fd, p: _ranked_daily(
    fd, p, lambda: (_safe_div(_f(fd, p, "eps_ttm"), _f(fd, p, "eps_ttm").shift(252)) - 1)
    - (_safe_div(_f(fd, p, "eps_ttm"), _f(fd, p, "eps_ttm").shift(252)) - 1).shift(63)))
_add("eap", "Growth", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(_f(fd, p, "eps_q") - _f(fd, p, "eps_q").shift(252), _price(p, "close"))
    - _safe_div(_f(fd, p, "eps_q") - _f(fd, p, "eps_q").shift(252), _price(p, "close")).shift(63)))
_add("gross_margin_qoq", "Growth", lambda fd, p: _rank_of(fd, p, "gross_margin_ttm_qoq"))
_add("np_ttm_qoq", "Growth", lambda fd, p: _rank_of(fd, p, "n_income_ttm_qoq"))
_add("pa", "Growth", lambda fd, p: _ranked_daily(
    fd, p, lambda: (_f(fd, p, "roa_ttm") - _f(fd, p, "roa_ttm").shift(252))
    - (_f(fd, p, "roa_ttm") - _f(fd, p, "roa_ttm").shift(252)).shift(63)))
_add("peg_252d", "Growth", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(_safe_div(_price(p, "close"), _f(fd, p, "eps_ttm")),
                             (_safe_div(_f(fd, p, "eps_ttm"), _f(fd, p, "eps_ttm").shift(252)) - 1) * 100)))
_add("sa", "Growth", lambda fd, p: _ranked_daily(
    fd, p, lambda: ((_safe_div(_f(fd, p, "revenue_q"), _f(fd, p, "total_share")))
                    / _safe_div(_f(fd, p, "revenue_q"), _f(fd, p, "total_share")).shift(252) - 1)
    - ((_safe_div(_f(fd, p, "revenue_q"), _f(fd, p, "total_share")))
       / _safe_div(_f(fd, p, "revenue_q"), _f(fd, p, "total_share")).shift(252) - 1).shift(63)))
for _name, _col in (
    ("yoy_net_asset", "total_hldr_eqy_exc_min_int"),
    ("yoy_net_profit", "n_income_attr_p_ttm"),
    ("yoy_ocf", "ocf_ttm"),
    ("yoy_revenue", "revenue_ttm"),
    ("yoy_roa", "roa_ttm"),
    ("yoy_roe", "roe_ttm"),
    ("yoy_total_asset", "total_assets"),
):
    _add(_name, "Growth", (lambda c: lambda fd, p: _ranked_daily(fd, p, lambda: _yoy_daily(fd, p, c)))(_col),
         note="Value_t/Value_{t-252}-1，截面排名")

# -- Quality 59
_add("ar_ap_to_revenue", "Quality", lambda fd, p: _rank_of(fd, p, "ar_ap_to_revenue"))
_add("asset_turnover", "Quality", lambda fd, p: _rank_of(fd, p, "asset_turnover_ttm"))
_add("cash_profit_ratio", "Quality", lambda fd, p: _rank_of(fd, p, "cash_profit_ratio"))
_add("cash_ratio", "Quality", lambda fd, p: _rank_of(fd, p, "cash_ratio"))
_add("cfcr", "Quality", lambda fd, p: _rank_of(fd, p, "cfcr"))
_add("current_ratio", "Quality", lambda fd, p: _rank_of(fd, p, "current_ratio"))
_add("de", "Quality", lambda fd, p: _rank_of(fd, p, "de"))
_add("debt_asset_ratio", "Quality", lambda fd, p: _rank_of(fd, p, "debt_asset_ratio"))
for _name, _col in (("delta_asset_turnover", "asset_turnover_ttm"),
                    ("delta_cash_ratio", "cash_ratio"),
                    ("delta_current_ratio", "current_ratio"),
                    ("delta_de", "de"), ("delta_gpm", "gross_margin_ttm"),
                    ("delta_inventory_turnover", "inventory_turnover_ttm"),
                    ("delta_npm", "net_margin_ttm"), ("delta_opm", "opm_ttm"),
                    ("delta_quick_ratio", "quick_ratio"), ("delta_roa", "roa_ttm"),
                    ("delta_roe", "roe_ttm")):
    _add(_name, "Quality", (lambda c: lambda fd, p: _ranked_daily(fd, p, lambda: _delta_daily(fd, p, c)))(_col))
_add("eps_q", "Quality", lambda fd, p: _rank_of(fd, p, "eps_q"))
_add("eps_ttm", "Quality", lambda fd, p: _rank_of(fd, p, "eps_ttm"))
_add("eps_y", "Quality", lambda fd, p: _rank_of(fd, p, "eps_y"))
_add("equity_turnover", "Quality", lambda fd, p: _rank_of(fd, p, "equity_turnover_ttm"))
_add("expenses_to_equity_yoy", "Quality", lambda fd, p: _rank_of(fd, p, "expenses_ratio_yoy"))
_add("financial_leverage", "Quality", lambda fd, p: _rank_of(fd, p, "financial_leverage"))
_add("fixed_asset_turnover", "Quality", lambda fd, p: _rank_of(fd, p, "fixed_asset_turnover_ttm"))
_add("gpm_q", "Quality", lambda fd, p: _rank_of(fd, p, "gross_margin_q"))
_add("gpm_qoq", "Quality", lambda fd, p: _rank_of(fd, p, "gross_margin_ttm_qoq"))
_add("gpm_ttm", "Quality", lambda fd, p: _rank_of(fd, p, "gross_margin_ttm"))
_add("gpm_y", "Quality", lambda fd, p: _rank_of(fd, p, "gross_margin_y"))
_add("icr", "Quality", lambda fd, p: _rank_of(fd, p, "icr"))
_add("income_tax_yoy", "Quality", lambda fd, p: _rank_of(fd, p, "income_tax_ttm_yoy"))
_add("inventory_turnover", "Quality", lambda fd, p: _rank_of(fd, p, "inventory_turnover_ttm"))
_add("lra_yoy", "Quality", lambda fd, p: _rank_of(fd, p, "lt_rec_yoy"))
_add("market_value_leverage", "Quality", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(_price(p, "total_mv") - _f(fd, p, "total_ncl"), _price(p, "total_mv"))))
_add("np_to_deferred_tax_yoy", "Quality", lambda fd, p: _rank_of(fd, p, "np_to_deferred_tax_yoy"))
_add("np_to_fixed_assets_yoy", "Quality", lambda fd, p: _rank_of(fd, p, "np_to_fixed_assets_yoy"))
_add("np_to_inventory_yoy", "Quality", lambda fd, p: _rank_of(fd, p, "np_to_inventory_yoy"))
_add("np_to_salary_yoy", "Quality", lambda fd, p: _rank_of(fd, p, "np_to_salary_yoy"))
_add("np_to_total_expenses_yoy", "Quality", lambda fd, p: _rank_of(fd, p, "np_to_total_expenses_yoy"))
_add("npm_q", "Quality", lambda fd, p: _rank_of(fd, p, "net_margin_q"))
_add("npm_q_qoq", "Quality", lambda fd, p: _rank_of(fd, p, "net_margin_q_qoq"))
_add("npm_tsh", "Quality", lambda fd, p: _rank_of(fd, p, "npm_tsh"))
_add("npm_ttm", "Quality", lambda fd, p: _rank_of(fd, p, "net_margin_ttm"))
_add("npm_ttm_qoq", "Quality", lambda fd, p: _rank_of(fd, p, "net_margin_ttm_qoq"))
_add("npm_y", "Quality", lambda fd, p: _rank_of(fd, p, "net_margin_y"))
_add("opm_ttm", "Quality", lambda fd, p: _rank_of(fd, p, "opm_ttm"))
_add("opm_y", "Quality", lambda fd, p: _rank_of(fd, p, "opm_y"))
_add("opt_tpro", "Quality", lambda fd, p: _rank_of(fd, p, "np_to_total_profit"))
_add("quality_composite", "Quality", lambda fd, p: _rank_of(fd, p, "quality_composite"))
_add("quick_ratio", "Quality", lambda fd, p: _rank_of(fd, p, "quick_ratio"))
_add("receivable_turnover", "Quality", lambda fd, p: _rank_of(fd, p, "receivable_turnover_ttm"))
_add("roa_q", "Quality", lambda fd, p: _rank_of(fd, p, "roa_q"))
_add("roa_ttm", "Quality", lambda fd, p: _rank_of(fd, p, "roa_ttm"))
_add("roa_y", "Quality", lambda fd, p: _rank_of(fd, p, "roa_y"))
_add("roe_ttm", "Quality", lambda fd, p: _rank_of(fd, p, "roe_ttm"))
_add("roe_ttm_lag63d", "Quality", lambda fd, p: _ranked_daily(
    fd, p, lambda: _f(fd, p, "roe_ttm").shift(63)))
_add("roe_y", "Quality", lambda fd, p: _rank_of(fd, p, "roe_y"))
_add("tax_surcharge_yoy", "Quality", lambda fd, p: _rank_of(fd, p, "tax_surcharge_ttm_yoy"))

# -- Value 11
_add("book_to_market", "Value", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(_f(fd, p, "book_to_market_num"), _price(p, "total_mv"))))
_add("dividend_yield_3y_avg", "Value", lambda fd, p: pd.DataFrame(
    np.nan, index=p.dates, columns=p.codes), requires=("dividend",),
    note="缺少 dividend 表，暂不可用（blocked）")
_add("earnings_cut_to_market", "Value", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(_f(fd, p, "earnings_cut_to_market_num"), _price(p, "total_mv"))))
_add("earnings_to_price", "Value", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(_f(fd, p, "earnings_to_price_num"), _price(p, "total_mv"))))
_add("ebitda_to_market", "Value", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(_f(fd, p, "ebitda_to_market_num"), _price(p, "total_mv"))))
_add("etp5", "Value", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(
        _f(fd, p, "n_income_attr_p_y").rolling(1260, min_periods=252).mean(),
        _price(p, "total_mv").rolling(1260, min_periods=252).mean())))
_add("fcf_to_market", "Value", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(_f(fd, p, "fcf_to_market_num"), _price(p, "total_mv"))))
_add("ncf_to_market", "Value", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(_f(fd, p, "ncf_to_market_num"), _price(p, "total_mv"))))
_add("ocf_to_market", "Value", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(_f(fd, p, "ocf_to_market_num"), _price(p, "total_mv"))))
_add("pegh5", "Value", lambda fd, p: _ranked_daily(
    fd, p, lambda: -_safe_div(_price(p, "close"),
                              ((_safe_div(_f(fd, p, "eps_ttm"), _f(fd, p, "eps_ttm").shift(1260)) ** (1 / 5)) - 1)
                              * _f(fd, p, "eps_ttm"))))
_add("sales_to_market", "Value", lambda fd, p: _ranked_daily(
    fd, p, lambda: _safe_div(_f(fd, p, "sales_to_market_num"), _price(p, "total_mv"))))


# ---------------------------------------------------------------- 注册
def register_financial(register: Callable, existing: dict[str, Any] | None = None) -> int:
    """把 B3 财报因子注册进 factor_lib；返回新增数量。"""
    existing = existing or {}
    count = 0
    for name, spec in B3_FACTORS.items():
        if name in existing or "blocked" in (spec["note"] or ""):
            continue
        requires = ("daily_basic", "income", "balancesheet", "cashflow") + tuple(spec["requires"])
        register(name, "Tushare-财务", "continuous", int(spec["direction"]),
                 f"{spec['factor_type']} PIT 财报因子：{spec['note'] or name}",
                 source=SOURCE, requires=requires)(lambda p, n=name: compute_b3(n, p))
        count += 1
    return count


def compute_b3(name: str, panel: Any) -> pd.DataFrame:
    spec = B3_FACTORS.get(name)
    if spec is None:
        raise KeyError(name)
    fd = get_financial(panel)
    out = spec["fn"](fd, panel)
    if not isinstance(out, pd.DataFrame):
        out = pd.DataFrame(out, index=panel.dates, columns=panel.codes)
    return out.reindex(index=panel.dates, columns=panel.codes).replace(
        [np.inf, -np.inf], np.nan)


def summary() -> dict[str, Any]:
    by_type: dict[str, int] = {}
    for spec in B3_FACTORS.values():
        by_type[spec["factor_type"]] = by_type.get(spec["factor_type"], 0) + 1
    blocked = [n for n, s in B3_FACTORS.items() if "blocked" in (s["note"] or "")]
    return {"total": len(B3_FACTORS), "by_type": by_type, "blocked": blocked}
