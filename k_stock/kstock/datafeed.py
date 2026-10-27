# -*- coding: utf-8 -*-
"""K-STOCK S1 PoC · D0 正式数据接入（三路 context 自动生成）

数据源（全部工作站现役资产，零新增依赖）：
  行情/技术面 → quant_backtest/data/ashare/{code}.parquet（5596 只全市场，K254 后复权，日更至当日）
  新闻/情绪   → news_collector/news.db news 表（stocks 字段 JSON 数组匹配，9 源）
  基本面      → 日线衍生素（波动率/量能稳定性/回撤）——真财务字段等 Tushare token（挂账 K-STOCK-F1）
产出：三路 context 文本（喂给 N1/F1/T1/T2 节点），全部数值来自真实数据。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # C:\项目\老田的AI助手
ASHARE_DIR = ROOT / "quant_backtest" / "data" / "ashare"
NEWS_DB = ROOT / "news_collector" / "news.db"


def _code_to_file(code: str) -> Path:
    """'600519.SH' -> 600519_SH.parquet"""
    return ASHARE_DIR / (code.replace(".", "_") + ".parquet")


def load_bars(code: str, days: int = 60) -> pd.DataFrame:
    """读单股全量 bar 再取尾部 N 交易日（K254 后复权口径）。"""
    f = _code_to_file(code)
    if not f.exists():
        raise FileNotFoundError(f"特征湖无该股: {code}")
    df = pd.read_parquet(f)
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df.tail(days)


def tech_context(code: str) -> str:
    """T1/T2 技术面底稿：均线结构/动量/量能/波动，全部真实数值。"""
    df = load_bars(code, days=60)
    c = df["close"].astype(float)
    v = df["volume"].astype(float)
    last = df.iloc[-1]
    ma5, ma10, ma20 = c.tail(5).mean(), c.tail(10).mean(), c.tail(20).mean()
    chg20 = (c.iloc[-1] / c.iloc[-21] - 1) * 100 if len(c) > 21 else float("nan")
    chg5 = (c.iloc[-1] / c.iloc[-6] - 1) * 100 if len(c) > 6 else float("nan")
    vol_ratio = v.tail(5).mean() / max(v.tail(20).mean(), 1)
    ret_std = c.pct_change().tail(20).std() * 100
    d = last["trade_date"]
    return (
        f"行情截至 {d}：收盘 {last['close']}（当日涨跌 {last['pct_chg']}%）。"
        f"MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f}"
        f"（收盘价相对 MA5 {'上方' if last['close'] > ma5 else '下方'}、"
        f"相对 MA20 {'上方' if last['close'] > ma20 else '下方'}）。"
        f"近 5 日涨跌 {chg5:+.1f}%、近 20 日涨跌 {chg20:+.1f}%。"
        f"量能：近 5 日均量为 20 日均量的 {vol_ratio:.2f} 倍"
        f"（{'放量' if vol_ratio > 1.3 else '缩量' if vol_ratio < 0.7 else '平量'}）。"
        f"近 20 日日收益率波动 {ret_std:.2f}%。"
    )


def news_context(code: str, days: int = 3) -> str:
    """N1 情绪面底稿：近 N 日该股相关新闻标题（news.stocks JSON 数组匹配）。"""
    if not NEWS_DB.exists():
        return "（新闻库不可用，情绪面输入缺失）"
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    con = sqlite3.connect(str(NEWS_DB))
    try:
        rows = con.execute(
            "SELECT ts, source, title FROM news "
            "WHERE stocks LIKE ? AND ts >= ? ORDER BY id DESC LIMIT 12",
            (f'%"{code}"%', since),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return f"近 {days} 日无该股相关新闻（监控源 9 路，匹配 {code}）。"
    lines = [f"- {ts[:16]} [{source}] {title}" for ts, source, title in rows]
    return f"近 {days} 日相关新闻 {len(rows)} 条：\n" + "\n".join(lines)


def funda_context(code: str) -> str:
    """F1 基本面底稿：日线衍生素（真财务字段等 Tushare token 接入）。"""
    df = load_bars(code, days=120)
    c = df["close"].astype(float)
    v = df["volume"].astype(float)
    hi60, lo60 = c.tail(60).max(), c.tail(60).min()
    drawdown = (c.iloc[-1] / hi60 - 1) * 100
    off_low = (c.iloc[-1] / lo60 - 1) * 100
    vol_cv = v.tail(30).std() / max(v.tail(30).mean(), 1)
    amount_mean = (
        df["amount"].astype(float).tail(20).mean() / 1e5
    )  # tushare amount=千元, /1e5 → 亿元
    return (
        f"中长期结构（近 120 交易日）：现价距 60 日高点 {drawdown:+.1f}%、"
        f"距 60 日低点 {off_low:+.1f}%。近 30 日量能变异系数 {vol_cv:.2f}"
        f"（{'稳定' if vol_cv < 0.5 else '波动大'}）。"
        f"近 20 日日均成交额约 {amount_mean:.2f} 亿元"
        f"（{'流动性充足' if amount_mean > 5 else '流动性一般' if amount_mean > 1 else '流动性偏弱'}）。"
        "（注：财务报表字段尚未接入，本路输入以量价衍生素代理，待 Tushare token 接入后补全）"
    )


def build_context(code: str) -> dict[str, str]:
    """三路 context 一次生成。单源失败不阻塞（返回缺失声明）。"""
    out: dict[str, str] = {}
    for key, fn in (
        ("tech", tech_context),
        ("news", news_context),
        ("funda", funda_context),
    ):
        try:
            out[key] = fn(code)
        except Exception as e:
            out[key] = f"（{key} 数据源失败: {type(e).__name__}: {e}）"
    return out


def _lake_code(code: str) -> str | None:
    """quotes 代码归一化为湖点分式；不可归一化（美股 gb_* 等）返回 None。

    2026-10-15 修复（BUG 审计）：quotes 表存小写前缀式 'sz300750'，而湖
    文件是 '300750_SZ.parquet'（_code_to_file 期望 '300750.SZ'）——格式
    错位导致 exists() 恒 False、watchlist 恒空、07:05 主 PoC 聚合连续
    空跑（10-12/13/15 三日实证）。北交所 'bj920028' 湖内有
    '920028_BJ.parquet'，同样归一化；'gb_*' 美股非 A 股湖语义，跳过。
    """
    c = (code or "").strip().lower()
    for pfx, suf in (("sh", "SH"), ("sz", "SZ"), ("bj", "BJ")):
        if c.startswith(pfx) and len(c) == 8 and c[2:].isdigit():
            return "%s.%s" % (c[2:], suf)
    if code and "." in code and len(code.split(".", 1)[0]) == 6:
        return code.upper()  # 已是点分式（兼容旧数据）
    return None


def watchlist_from_quotes(limit: int = 10) -> list[str]:
    """默认 watchlist：news.db quotes 监控池（80 只）∩ 特征湖覆盖，按成交额取前 N。"""
    con = sqlite3.connect(str(NEWS_DB))
    try:
        # 取额扩到 limit*6：gb_* 等不可归一化代码会占名额（2026-10-15 实测
        # TOP30 全被占），过滤后余量不足会再次空跑
        rows = con.execute(
            "SELECT code, MAX(amount) AS amt FROM quotes GROUP BY code "
            "ORDER BY amt DESC LIMIT ?",
            (limit * 6,),
        ).fetchall()
    finally:
        con.close()
    out = []
    for code, _ in rows:
        lake = _lake_code(code)
        if lake is None:
            continue
        if _code_to_file(lake).exists():
            out.append(lake)
        if len(out) >= limit:
            break
    return out


if __name__ == "__main__":
    import sys

    code = sys.argv[1] if len(sys.argv) > 1 else "000001.SZ"
    ctx = build_context(code)
    for k, v in ctx.items():
        print(f"== {k} ==\n{v}\n")
