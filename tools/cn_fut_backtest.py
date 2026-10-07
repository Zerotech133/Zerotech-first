# -*- coding: utf-8 -*-
r"""cn_fut_backtest: 国内期货主力连续 × 放行策略 walk-forward 回测（F3b，K161-B/K163-C）
用法（三家裸 python 之外的 tushare 环境）:
  & "C:\Program Files\Python311\python.exe" -X utf8 cn_fut_backtest.py [years] [max_symbols]
产物: out/cn_fut_backtest_YYYYMMDD.json + 控制台双榜面板
口径: 与 fut_walkforward.py 对齐（3 折 70/30，robustness=test/train，同阈值判定）
局限: 主力连续未复权（换月跳空存在），v1 标注不消除
"""
import datetime
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import tushare as ts

pro = ts.pro_api()

# 国内核心品种池（F3b，对标 fut_scan 核心宏观池）
POOL = [
    ('RB.SHF', '螺纹钢'), ('M.DCE', '豆粕'), ('TA.ZCE', 'PTA'), ('SA.ZCE', '纯碱'),
    ('SC.INE', '原油'), ('AU.SHF', '黄金'), ('CU.SHF', '铜'), ('AG.SHF', '白银'),
    ('T.CFX', '10Y国债'), ('IF.CFX', '沪深300股指'),
]
STRATEGIES = ['ema_cross', 'rsi']  # K164 放行 + 备选
COMMISSION = 0.001  # 0.1% 与 TV 口径对齐
SLIPPAGE = 0.0005


def build_continuous(sym: str, years: int) -> pd.DataFrame:
    """主力映射 → 分段 fut_daily → 拼接连续日线（升序）。"""
    mp = pro.fut_mapping(ts_code=sym)
    if mp is None or len(mp) == 0:
        return pd.DataFrame()
    mp = mp.sort_values('trade_date').reset_index(drop=True)
    cut = (pd.Timestamp.today() - pd.DateOffset(years=years)).strftime('%Y%m%d')
    mp = mp[mp['trade_date'] >= cut]
    if len(mp) == 0:
        return pd.DataFrame()
    frames = []
    # 每段主力区间起止
    seg = mp.copy()
    seg['new_seg'] = seg['mapping_ts_code'] != seg['mapping_ts_code'].shift()
    seg['seg_id'] = seg['new_seg'].cumsum()
    for sid, g in seg.groupby('seg_id'):
        contract = g.iloc[0]['mapping_ts_code']
        s, e = g['trade_date'].min(), g['trade_date'].max()
        try:
            d = pro.fut_daily(ts_code=contract, start_date=s, end_date=e)
            if len(d):
                frames.append(d[['trade_date', 'close', 'vol']])
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).drop_duplicates('trade_date').sort_values('trade_date').reset_index(drop=True)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    return df.dropna(subset=['close'])


def sig_ema_cross(df: pd.DataFrame) -> pd.Series:
    """EMA20/50 金叉持多、死叉离场（1/0 持仓，信号次日生效）。"""
    e20 = df['close'].ewm(span=20, adjust=False).mean()
    e50 = df['close'].ewm(span=50, adjust=False).mean()
    return (e20 > e50).astype(int)


def sig_rsi(df: pd.DataFrame) -> pd.Series:
    """RSI14 超卖(<30)持多、回到>70 离场（简化均值回归口径，Wilder 平滑）。"""
    delta = df['close'].diff()
    up = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, 1e-9))
    pos = pd.Series(0, index=df.index)
    pos[rsi < 30] = 1
    pos[rsi > 70] = 0
    pos[(rsi >= 30) & (rsi <= 70)] = pd.NA
    return pos.ffill().fillna(0).astype(int)


SIG_FN = {'ema_cross': sig_ema_cross, 'rsi': sig_rsi}


def run_backtest(df: pd.DataFrame, strategy: str) -> dict:
    """单段回测：次日生效持仓 + 双边费滑。返回 pct 与 trades。"""
    pos = SIG_FN[strategy](df)
    ret = df['close'].pct_change().fillna(0)
    # 信号次日生效：持仓 shift(1)
    pos_eff = pos.shift(1).fillna(0)
    gross = (ret * pos_eff).sum()
    trades = int((pos.diff().abs() > 0).sum())  # 开平各计一次
    cost = trades * (COMMISSION + SLIPPAGE)
    return {'return_pct': round((gross - cost) * 100, 2), 'trades': trades}


def walk_forward(df: pd.DataFrame, strategy: str, n_splits=3, train_ratio=0.7) -> dict:
    """3 折 70/30 walk-forward，robustness=test/train。"""
    n = len(df)
    fold_n = (n - int(n * train_ratio)) // n_splits
    robustness, folds = [], []
    for k in range(n_splits):
        tr_end = int(n * train_ratio) + k * fold_n
        te_end = tr_end + fold_n if k < n_splits - 1 else n
        tr, te = df.iloc[:tr_end], df.iloc[tr_end:te_end]
        if len(tr) < 60 or len(te) < 15:
            continue
        r_tr = run_backtest(tr, strategy)
        r_te = run_backtest(te, strategy)
        rob = 1.0 if r_tr['return_pct'] <= 0 else max(0.0, r_te['return_pct'] / r_tr['return_pct'])
        if r_tr['return_pct'] <= 0 and r_te['return_pct'] > 0:
            rob = 1.0  # 样本内亏样本外赚=稳健方向
        robustness.append(rob)
        folds.append({'train_pct': r_tr['return_pct'], 'test_pct': r_te['return_pct'],
                      'train_trades': r_tr['trades'], 'test_trades': r_te['trades']})
    if not robustness:
        return {'skipped': True}
    return {'avg_train_pct': round(sum(f['train_pct'] for f in folds) / len(folds), 2),
            'avg_test_pct': round(sum(f['test_pct'] for f in folds) / len(folds), 2),
            'robustness': round(sum(robustness) / len(robustness), 3),
            'oos_trades': sum(f['test_trades'] for f in folds), 'folds': folds}


def verdict_tag(rob):
    if rob >= 0.8:
        return 'ROBUST'
    if rob >= 0.5:
        return 'MODERATE'
    if rob >= 0.2:
        return 'WEAK'
    return 'OVERFIT'


def main():
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    max_n = int(sys.argv[2]) if len(sys.argv) > 2 else len(POOL)
    subset = POOL[:max_n]
    results = []
    print(f"[cn_fut_bt] years={years} symbols={len(subset)} strategies={STRATEGIES}")
    for sym, cn in subset:
        df = build_continuous(sym, years)
        if len(df) < 200:
            print(f"  {sym} {cn}: 连续日线不足（rows={len(df)}），跳过")
            continue
        for st in STRATEGIES:
            wf = walk_forward(df, st)
            if wf.get('skipped'):
                results.append({'symbol': sym, 'name_cn': cn, 'strategy': st, 'skipped': True})
                continue
            wf.update({'symbol': sym, 'name_cn': cn, 'strategy': st, 'rows': len(df),
                       'verdict': verdict_tag(wf['robustness']),
                       'full_return_pct': run_backtest(df, st)['return_pct']})
            results.append(wf)
        r_ok = [r for r in results[-len(STRATEGIES):] if not r.get('skipped')]
        det = ' | '.join(f"{r['strategy']}: rob={r['robustness']} {r['verdict']} oos={r['avg_test_pct']}%" for r in r_ok)
        print(f"  {sym} {cn} rows={len(df)}: {det}")

    ok = [r for r in results if not r.get('skipped')]
    rank = []
    for st in STRATEGIES:
        rs = [r for r in ok if r['strategy'] == st]
        if rs:
            rank.append({'strategy': st,
                         'robust_avg': round(sum(r['robustness'] for r in rs) / len(rs), 3),
                         'R': sum(1 for r in rs if r['verdict'] == 'ROBUST'),
                         'M': sum(1 for r in rs if r['verdict'] == 'MODERATE'),
                         'W': sum(1 for r in rs if r['verdict'] == 'WEAK'),
                         'OF': sum(1 for r in rs if r['verdict'] == 'OVERFIT'),
                         'oos_avg': round(sum(r['avg_test_pct'] for r in rs) / len(rs), 2)})
    rank.sort(key=lambda x: x['robust_avg'], reverse=True)

    out = {'ts': datetime.datetime.now().isoformat(timespec='seconds'), 'years': years,
           'source': 'tushare fut_mapping+fut_daily 主力连续（未复权）',
           'note': '口径对齐 fut_walkforward（3折70/30），策略=K164 放行集',
           'results': results, 'strategy_rank': rank}
    od = Path(__file__).parent / 'out'
    od.mkdir(exist_ok=True)
    f = od / f"cn_fut_backtest_{datetime.date.today():%Y%m%d}.json"
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"[cn_fut_bt] ok={len(ok)}/{len(results)} saved={f.name}")
    print('-- 国内策略稳健性排名 --')
    for x in rank:
        print(f"  {x['strategy']:<10} rob={x['robust_avg']:.2f} R{x['R']}/M{x['M']}/W{x['W']}/OF{x['OF']} oos_avg={x['oos_avg']:+.2f}%")


if __name__ == '__main__':
    main()
