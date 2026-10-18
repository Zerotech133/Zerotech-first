import { useEffect, useMemo, useState } from 'react';
import { api } from '../services/api';
import { useMarket } from '../contexts/MarketContext';

/** /accounts/stats/by-strategy 返回的单策略账户指标 */
export interface StrategyAccount {
  strategy_version_id: string;
  market?: string;
  initial_capital?: number;
  available_cash: number;
  market_value?: number;
  total_equity: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  position_ratio: number;
  position_count?: number;
}

/**
 * 当前市场的策略列表 + 选中策略（'' = 全部）。
 * 切换市场时在渲染期同步清空选中与选项，保证页面数据请求不会带上旧市场的策略参数。
 */
export function useStrategies() {
  const { market } = useMarket();
  const [accounts, setAccounts] = useState<StrategyAccount[]>([]);
  const [strategy, setStrategy] = useState('');
  const [prevMarket, setPrevMarket] = useState(market);

  if (prevMarket !== market) {
    setPrevMarket(market);
    setStrategy('');
    setAccounts([]);
  }

  useEffect(() => {
    let cancelled = false;
    api<StrategyAccount[]>(`/accounts/stats/by-strategy?market=${market}`)
      .then((list) => {
        if (!cancelled) setAccounts(Array.isArray(list) ? list : []);
      })
      .catch(() => {
        if (!cancelled) setAccounts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [market]);

  const strategies = useMemo(
    () => accounts.map((a) => a.strategy_version_id).filter(Boolean),
    [accounts],
  );

  return { strategies, accounts, strategy, setStrategy };
}
