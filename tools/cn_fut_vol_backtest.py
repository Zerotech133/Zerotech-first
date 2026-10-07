# -*- coding: utf-8 -*-
"""cn_fut_vol_backtest: 国内期货 量能/持仓量维度策略回测（K228，用户「量能的方法」点题）。

与 cn_fut_backtest.py 的关系：POOL/COMMISSION/SLIPPAGE 直接 import（权威
单点不复制）；引擎语义逐行对齐（次日生效持仓 + 双边费滑 + 3 折 70/30
walk-forward，robustness 阈值同款）保证结果可比；策略集与取数独立成新文件
（零修改他流文件——并行防撞判例）。

新增两维（tushare fut_daily 在库未用字段）：
  · vol_breakout 放量趋势确认：vol > 2×MA5(vol) 且 close > MA20 持多，
    close < MA20 离场（放量+价格双确认）
  · oi_trend 增仓上行持多：OI 5 日变化率 > 0 且 close > MA20 持多，
    OI 5 日变化率 < 0 离场（增仓=资金进场确认趋势，减仓离场）

用法：python cn_fut_vol_backtest.py [years]   # 默认 2（与 cn_fut_backtest 同窗可比）
产出：stdout 矩阵 + tools/out/cn_fut_vol_YYYYMMDD.json
注意：主力连续未复权；成本按 A 股口径 0.15% 偏保守（期货实际更低）。
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import tushare as ts

_TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _TOOLS)
from cn_fut_backtest import POOL, COMMISSION, SLIPPAGE  # noqa: E402

pro = ts.pro_api()
OUT_DIR = os.path.join(_TOOLS, 'out')
STRATEGIES = ['vol_breakout', 'oi_trend']


def build_continuous_vol(sym: str, years: int) -> pd.DataFrame:
    """主力映射 → 分段 fut_daily → 拼接连续日线（升序，含 vol/OI）。"""
    mp = pro.fut_mapping(ts_code=sym)
    if mp is None or len(mp) == 0:
        return pd.DataFrame()
    mp = mp.sort_values('trade_date').reset_index(drop=True)
    if years:
        cut = (pd.Timestamp.today() - pd.DateOffset(years=years)) \
            .strftime('%Y%m%d')
        mp = mp[mp['trade_date'] >= cut]
    seg = mp.copy()
    seg['new_seg'] = seg['mapping_ts_code'] != seg['mapping_ts_code'].shift()
    seg['seg_id'] = seg['new_seg'].cumsum()
    frames = []
    for _sid, g in seg.groupby('seg_id'):
        contract = g.iloc[0]['mapping_ts_code']
        s, e = g['trade_date'].min(), g['trade_date'].max()
        try:
            d = pro.fut_daily(ts_code=contract, start_date=s, end_date=e)
            if len(d):
                frames.append(d[['trade_date', 'close', 'vol', 'oi']])
        except Exception as ex:
            print(f"[warn] {sym} {contract}: {str(ex)[:60]}")
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).drop_duplicates('trade_date') \
        .sort_values('trade_date').reset_index(drop=True)
    df = df.rename(columns={'oi': 'open_interest'})
    for c in ('close', 'vol', 'open_interest'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.dropna(subset=['close'])


def sig_vol_breakout(df: pd.DataFrame) -> pd.Series:
    """放量趋势确认：vol>2×MA5(vol) 且 close>MA20 持多；close<MA20 离场。"""
    vol2 = df['vol'] > 2 * df['vol'].rolling(5).mean()
    above = df['close'] > df['close'].rolling(20).mean()
    below = df['close'] < df['close'].rolling(20).mean()
    pos = pd.Series(0.0, index=df.index)
    pos[vol2 & above] = 1.0
    pos[below] = 0.0
    return pos.ffill().fillna(0)


def sig_oi_trend(df: pd.DataFrame) -> pd.Series:
    """增仓上行持多：OI 5 日变化率>0 且 close>MA20；OI 5 日变化率<0 离场。"""
    oi_chg = df['open_interest'].pct_change(5)
    above = df['close'] > df['close'].rolling(20).mean()
    pos = pd.Series(0.0, index=df.index)
    pos[(oi_chg > 0) & above] = 1.0
    pos[oi_chg < 0] = 0.0
    return pos.ffill().fillna(0)


SIG_FN = {'vol_breakout': sig_vol_breakout, 'oi_trend': sig_oi_trend}


def run_backtest(df: pd.DataFrame, strategy: str) -> dict:
    """单段回测：次日生效持仓 + 双边费滑（语义与 cn_fut_backtest 逐行对齐）。"""
    pos = SIG_FN[strategy](df)
    ret = df['close'].pct_change().fillna(0)
    pos_eff = pos.shift(1).fillna(0)
    gross = (ret * pos_eff).sum()
    trades = int((pos.diff().abs() > 0).sum())
    cost = trades * (COMMISSION + SLIPPAGE)
    return {'return_pct': round((gross - cost) * 100, 2), 'trades': trades}


def walk_forward(df: pd.DataFrame, strategy: str, n_splits=3,
                 train_ratio=0.7) -> dict:
    """3 折 70/30 walk-forward，robustness=test/train（阈值同款）。"""
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
        rob = 1.0 if r_tr['return_pct'] <= 0 else \
            max(0.0, r_te['return_pct'] / r_tr['return_pct'])
        if r_tr['return_pct'] <= 0 and r_te['return_pct'] > 0:
            rob = 1.0
        robustness.append(rob)
        folds.append({'train_pct': r_tr['return_pct'],
                      'test_pct': r_te['return_pct'],
                      'train_trades': r_tr['trades'],
                      'test_trades': r_te['trades']})
    if not robustness:
        return {'skipped': True}
    return {'avg_train_pct': round(sum(f['train_pct'] for f in folds)
                                   / len(folds), 2),
            'avg_test_pct': round(sum(f['test_pct'] for f in folds)
                                  / len(folds), 2),
            'robustness': round(sum(robustness) / len(robustness), 3),
            'oos_trades': sum(f['test_trades'] for f in folds),
            'folds': folds}


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
    results = []
    print(f"[cn_fut_vol] years={years} symbols={len(POOL)} "
          f"strategies={STRATEGIES}")
    for sym, cn in POOL:
        df = build_continuous_vol(sym, years)
        if len(df) < 100:
            print(f"[skip] {sym} bars={len(df)}")
            results.append({'symbol': sym, 'name_cn': cn, 'skipped': True})
            continue
        for st in STRATEGIES:
            r = run_backtest(df, st)
            wf = walk_forward(df, st)
            tag = ('NO-OP' if r['trades'] == 0 else
                   verdict_tag(wf.get('robustness', 0))) \
                if not wf.get('skipped') else '-'
            results.append({'symbol': sym, 'name_cn': cn, 'strategy': st,
                            'bars': len(df), **r, 'wf': wf, 'verdict': tag})
            print(f"{sym} {cn} {st}: ret={r['return_pct']}% "
                  f"trades={r['trades']} rob={wf.get('robustness')} "
                  f"→ {tag}")
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, 'cn_fut_vol_%s.json'
                       % datetime.now().strftime('%Y%m%d'))
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'generated_at': datetime.now().isoformat(
            timespec='seconds'), 'years': years, 'results': results},
            f, ensure_ascii=False, indent=1)
    print('saved:', out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
