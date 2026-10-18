"""回测服务：基于真实历史数据对 StrategySpec 做简单日频回测。"""
from __future__ import annotations

from datetime import date

import pandas as pd

from zhice_os_v2.domain.strategy.data_loader import load_recent_bars
from zhice_os_v2.domain.strategy.indicators import ma_cross, momentum, rsi
from zhice_os_v2.domain.strategy.spec import StrategySpec


def _factor(name: str, params: dict, closes: list[float]) -> float | None:
    if name == "momentum":
        return momentum(closes, int(params.get("window", 20)))
    if name == "ma_cross":
        return ma_cross(closes, int(params.get("fast", 5)), int(params.get("slow", 20)))
    if name == "rsi":
        return rsi(closes, int(params.get("window", 14)))
    return None


def _rule_satisfied(rules, values: dict[str, float]) -> bool:
    if not rules:
        # 无规则 → 不满足退出条件（持有到期末，由风控止损/止盈或回测结束决定出场）
        # 修复前返回 True，导致 exit_rules=[] 时每日立即平仓，止损/止盈逻辑无法触发，
        # FIXED/TIGHT/LOOSE 三变体结果完全相同。
        return False
    for rule in rules:
        v = values.get(rule.feature)
        if v is None:
            return False
        if rule.op == ">":
            if not v > rule.value:
                return False
        elif rule.op == "<":
            if not v < rule.value:
                return False
        elif rule.op == ">=":
            if not v >= rule.value:
                return False
        elif rule.op == "<=":
            if not v <= rule.value:
                return False
        elif rule.op == "==":
            if not v == rule.value:
                return False
    return True


class BacktestService:
    def run(
        self,
        spec: StrategySpec,
        ts_code: str,
        start: date,
        end: date,
        initial_capital: float = 100000.0,
        fee_rate: float = 0.0,
        slippage_bps: float = 0.0,
    ) -> dict:
        df = load_recent_bars(end, lookback_days=500, market=spec.market)
        if df.empty:
            return {"error": "no data"}
        df = df[df["ts_code"] == ts_code].copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date")
        df = df[(df["trade_date"] >= pd.Timestamp(start)) & (df["trade_date"] <= pd.Timestamp(end))]
        if df.empty:
            return {"error": "no data in range"}

        cash = initial_capital
        qty = 0.0
        entry_price = 0.0
        entry_date = None
        equity_curve: list[float] = []
        trades: list[dict] = []
        closes_so_far: list[float] = []
        pending_entry = False
        pending_exit = False
        pending_exit_reason = "signal_exit"

        def _price(row, name: str, fallback: float) -> float:
            value = row.get(name, fallback)
            try:
                value = float(value)
            except (TypeError, ValueError):
                return fallback
            return value if pd.notna(value) and value > 0 else fallback

        def _fill_price(raw_price: float, side: str) -> float:
            slip = max(0.0, float(slippage_bps)) / 10000.0
            return raw_price * (1.0 + slip if side == "BUY" else 1.0 - slip)

        # 从 spec.risk 读止损/止盈比例（run_exit_lab 按变体设置，使三种变体产生真实差异）
        risk_stop_loss = float(spec.risk.stop_loss) if spec.risk else 0.0
        risk_take_profit = float(spec.risk.take_profit) if spec.risk else 0.0

        for _, row in df.iterrows():
            close = float(row["close"])
            # 先撮合上一根收盘产生的 pending 信号；信号本身不能在同一根 close 成交。
            if pending_entry and qty == 0:
                fill = _fill_price(_price(row, "open", close), "BUY")
                qty = cash / (fill * (1.0 + max(0.0, fee_rate)))
                cash = 0.0
                entry_price = fill
                entry_date = row["trade_date"]
                pending_entry = False
            if pending_exit and qty > 0:
                raw = _price(row, "open", close)
                fill = _fill_price(raw, "SELL")
                proceeds = qty * fill * (1.0 - max(0.0, fee_rate))
                cash = proceeds
                pnl = (fill - entry_price) / entry_price if entry_price else 0.0
                trades.append({"entry_date": str(entry_date.date()),
                               "exit_date": str(row["trade_date"].date()),
                               "pnl_pct": round(pnl, 4),
                               "exit_reason": pending_exit_reason})
                qty = 0.0
                pending_exit = False

            closes_so_far.append(close)
            values = {}
            for f in spec.factors:
                v = _factor(f.name, f.params, closes_so_far)
                if v is not None:
                    values[f.name] = v
            enter_rules = [r for r in spec.rules if r.action == "enter"]
            exit_rules = [r for r in spec.rules if r.action == "exit"]

            if qty > 0 and entry_price > 0:
                assert entry_date is not None
                high = _price(row, "high", close)
                low = _price(row, "low", close)
                stop = entry_price * (1 - risk_stop_loss) if risk_stop_loss > 0 else None
                target = entry_price * (1 + risk_take_profit) if risk_take_profit > 0 else None
                # 同一根同时触发时保守优先止损；实际成交在下一根 open。
                if stop is not None and low <= stop:
                    pending_exit, pending_exit_reason = True, "stop_loss"
                elif target is not None and high >= target:
                    pending_exit, pending_exit_reason = True, "take_profit"
                elif _rule_satisfied(exit_rules, values):
                    pending_exit, pending_exit_reason = True, "signal_exit"

            if qty == 0 and not pending_entry and not pending_exit:
                if _rule_satisfied(enter_rules, values):
                    pending_entry = True

            equity = cash + qty * close
            equity_curve.append(equity)

        if not equity_curve:
            return {"error": "no equity"}

        final = equity_curve[-1]
        total_return = final / initial_capital - 1
        # 回撤只能相对历史运行峰值计算，不能使用全局未来峰值。
        running_peak = equity_curve[0]
        drawdowns = []
        for equity in equity_curve:
            running_peak = max(running_peak, equity)
            drawdowns.append(equity / running_peak - 1 if running_peak else 0.0)
        max_drawdown = min(drawdowns) if drawdowns else 0.0
        returns = [equity_curve[i] / equity_curve[i - 1] - 1 for i in range(1, len(equity_curve)) if equity_curve[i - 1] != 0]
        sharpe = 0.0
        if returns and len(returns) > 1:
            mean = sum(returns) / len(returns)
            var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
            std = var ** 0.5
            if std > 0:
                sharpe = mean / std * (252 ** 0.5)

        return {
            "ts_code": ts_code,
            "market": spec.market,
            "start": str(start),
            "end": str(end),
            "initial_capital": initial_capital,
            "final_equity": round(final, 2),
            "total_return": round(total_return, 4),
            "max_drawdown": round(max_drawdown, 4),
            "sharpe": round(sharpe, 4),
            "trade_count": len(trades),
            "trades": trades[-10:],
        }
