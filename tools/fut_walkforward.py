# -*- coding: utf-8 -*-
r"""fut_walkforward: 核心宏观池 × 9 策略 walk-forward 样本外验证（tradingview-mcp F3 严格试测）
用法（uv tool 独立环境）:
  & "$env:APPDATA\uv\tools\tradingview-mcp-server\Scripts\python.exe" fut_walkforward.py [period] [max_symbols]
产物: out/fut_walkforward_YYYYMMDD.json + 控制台稳健性排名
判定: robustness=test/train ≥0.8 ROBUST | ≥0.5 MODERATE | ≥0.2 WEAK | <0.2 OVERFIT
"""
import asyncio
import datetime
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import tradingview_mcp.server as tv

STRATEGIES = [
    'rsi', 'bollinger', 'macd', 'ema_cross', 'supertrend',
    'donchian', 'rsi_pullback', 'keltner_breakout', 'triple_ema',
]

SYMBOLS = [
    ('GC=F', '黄金'), ('SI=F', '白银'), ('CL=F', 'WTI原油'), ('BZ=F', '布伦特'),
    ('NG=F', '天然气'), ('HG=F', '铜'), ('ES=F', '标普期货'), ('NQ=F', '纳指期货'),
    ('YM=F', '道指期货'), ('NKD=F', '日经期货'), ('ZN=F', '10Y美债'),
    ('6E=F', '欧元期货'), ('BTC=F', '比特币期货'),
]


def verdict_tag(score):
    if score is None:
        return 'N/A'
    if score >= 0.8:
        return 'ROBUST'
    if score >= 0.5:
        return 'MODERATE'
    if score >= 0.2:
        return 'WEAK'
    return 'OVERFIT'


async def wf_one(symbol, strategy, period):
    try:
        r = tv.walk_forward_backtest(symbol, strategy, period=period, n_splits=3)
        if hasattr(r, '__await__'):
            r = await r
        if r.get('error'):
            return {'symbol': symbol, 'strategy': strategy, 'error': str(r['error'])[:100]}
        return {
            'symbol': symbol, 'strategy': strategy,
            'avg_train_return_pct': r.get('avg_train_return_pct'),
            'avg_test_return_pct': r.get('avg_test_return_pct'),
            'oos_total_return_pct': r.get('oos_total_return_pct'),
            'oos_trades': r.get('oos_total_trades'),
            'oos_win_rate_pct': r.get('oos_win_rate_pct'),
            'oos_sharpe': r.get('oos_sharpe_ratio'),
            'robustness_score': r.get('robustness_score'),
            'verdict_raw': (r.get('verdict') or '')[:40],
            'buy_hold_return_pct': r.get('buy_and_hold_return_pct'),
        }
    except Exception as e:
        return {'symbol': symbol, 'strategy': strategy, 'error': repr(e)[:100]}


async def main():
    period = sys.argv[1] if len(sys.argv) > 1 else '2y'
    max_n = int(sys.argv[2]) if len(sys.argv) > 2 else len(SYMBOLS)
    subset = SYMBOLS[:max_n]
    total = len(subset) * len(STRATEGIES)
    print(f"[fut_wf] period={period} symbols={len(subset)} strategies={len(STRATEGIES)} runs={total}")
    results = []
    for i, (sym, cn) in enumerate(subset, 1):
        for st in STRATEGIES:
            row = await wf_one(sym, st, period)
            row['name_cn'] = cn
            row['verdict'] = verdict_tag(row.get('robustness_score')) if 'error' not in row else 'ERR'
            results.append(row)
        batch = results[-len(STRATEGIES):]
        robust = [r for r in batch if r['verdict'] in ('ROBUST', 'MODERATE')]
        best = max(robust, key=lambda x: x.get('robustness_score') or -1) if robust else None
        print(f"  [{i}/{len(subset)}] {sym} {cn}: R/M={len(robust)}/{len(STRATEGIES)}"
              + (f" best={best['strategy']}({best['verdict']},rob={best['robustness_score']:.2f})" if best else ' all-weak'))

    ok_rows = [r for r in results if 'error' not in r]
    # 汇总 1：verdict 分布 × 策略
    dist = {}
    for r in ok_rows:
        dist.setdefault(r['strategy'], {}).setdefault(r['verdict'], 0)
        dist[r['strategy']][r['verdict']] += 1
    # 汇总 2：按 robustness 均值排名（OOS 有交易的才计入样本外均值）
    rank = []
    for st, d in dist.items():
        rows_st = [r for r in ok_rows if r['strategy'] == st]
        rob_avg = sum(r.get('robustness_score') or 0 for r in rows_st) / len(rows_st)
        oos_avg = sum(r.get('avg_test_return_pct') or 0 for r in rows_st if (r.get('oos_trades') or 0) > 0)
        oos_n = sum(1 for r in rows_st if (r.get('oos_trades') or 0) > 0)
        rank.append({'strategy': st, 'robust_avg': round(rob_avg, 3),
                     'R': d.get('ROBUST', 0), 'M': d.get('MODERATE', 0),
                     'W': d.get('WEAK', 0), 'OF': d.get('OVERFIT', 0),
                     'oos_avg_test_pct': round(oos_avg / oos_n, 2) if oos_n else None,
                     'oos_n': oos_n})
    rank.sort(key=lambda x: x['robust_avg'], reverse=True)

    out = {
        'ts': datetime.datetime.now().isoformat(timespec='seconds'),
        'period': period, 'symbols': len(subset), 'runs': total, 'ok_runs': len(ok_rows),
        'source': 'tradingview-mcp walk_forward_backtest n_splits=3 train_ratio=0.7 (yahoo)',
        'results': results,
        'strategy_robustness_rank': rank,
    }
    od = Path(__file__).parent / 'out'
    od.mkdir(exist_ok=True)
    f = od / f"fut_walkforward_{datetime.date.today():%Y%m%d}.json"
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"[fut_wf] ok={len(ok_rows)}/{total} saved={f.name}")
    print('-- 策略稳健性排名（robustness 均值，R/M/W/OF=计数）--')
    for x in rank:
        oos = f"oos_avg={x['oos_avg_test_pct']:+.2f}%(n={x['oos_n']})" if x['oos_avg_test_pct'] is not None else 'oos=无交易'
        print(f"  {x['strategy']:<18} rob={x['robust_avg']:.2f}  R{x['R']}/M{x['M']}/W{x['W']}/OF{x['OF']}  {oos}")


asyncio.run(main())
