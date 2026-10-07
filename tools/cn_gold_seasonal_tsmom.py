# -*- coding: utf-8 -*-
"""cn_gold_seasonal_tsmom: 沪金季节性 scout + TSMOM 全史 WF（K229）。

数据：复用 cn_fut_vol_backtest.build_continuous_vol（AU.SHF 主力连续全史）。
产出两段：
  1) 季节性 scout：月度收益按日历月分布（n/win/avg/med，18 年）
  2) TSMOM 时序动量：lookback=252 交易日动量>0 持多（long_flat 1/0 与
     long_short ±1 两变体），全史 3 折 70/30 walk-forward，引擎语义与
     cn_fut_backtest 逐行对齐（次日生效 + 双边费 0.15%）；对照 buy&hold。
纯影子统计，零口径动作。
用法：python cn_gold_seasonal_tsmom.py [years]   # 默认 20（全史）
"""
import json
import os
import sys
import tempfile
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd

_TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _TOOLS)
from cn_fut_vol_backtest import build_continuous_vol, COMMISSION, SLIPPAGE  # noqa: E402

OUT_DIR = os.path.join(_TOOLS, 'out')
SYM = 'AU.SHF'


def _atomic_write(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        fp.write(text)
    os.replace(tmp, path)


def run_backtest(df: pd.DataFrame, pos: pd.Series) -> dict:
    """次日生效持仓 + 双边费滑（语义与 cn_fut_backtest 逐行对齐）。"""
    ret = df['close'].pct_change().fillna(0)
    pos_eff = pos.shift(1).fillna(0)
    gross = (ret * pos_eff).sum()
    trades = int((pos.diff().abs() > 0).sum())
    cost = trades * (COMMISSION + SLIPPAGE)
    return {'return_pct': round((gross - cost) * 100, 2),
            'trades': trades}


def walk_forward(df: pd.DataFrame, pos: pd.Series, n_splits=3,
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
        r_tr = run_backtest(tr, pos.iloc[:tr_end])
        r_te = run_backtest(te, pos.iloc[tr_end:te_end])
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


def seasonality(df: pd.DataFrame) -> dict:
    m = df.set_index(pd.to_datetime(df['trade_date']))['close'] \
        .resample('ME').last().dropna()
    ret = m.pct_change().dropna()
    out = {}
    for mo in range(1, 13):
        rs = ret[ret.index.month == mo]
        if not len(rs):
            continue
        v = sorted(rs)
        n = len(v)
        out['%02d' % mo] = {'n': n,
                            'win': round(sum(1 for x in v if x > 0) / n * 100, 0),
                            'avg': round(sum(v) / n * 100, 2),
                            'med': round((v[n // 2] if n % 2 else
                                          (v[n // 2 - 1] + v[n // 2]) / 2) * 100, 2)}
    return out


def tsmom_pos(df: pd.DataFrame, lookback: int, mode: str) -> pd.Series:
    mom = df['close'] / df['close'].shift(lookback) - 1.0
    pos = pd.Series(0.0, index=df.index)
    pos[mom > 0] = 1.0
    if mode == 'long_short':
        pos[mom < 0] = -1.0
    return pos


def main():
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    df = build_continuous_vol(SYM, years)
    if len(df) < 400:
        print(f"bars insufficient: {len(df)}")
        return 1
    print(f"[gold] bars={len(df)} range={df['trade_date'].iloc[0]}~"
          f"{df['trade_date'].iloc[-1]}")

    # 1) 季节性
    seas = seasonality(df)
    print("-- 季节性（月度收益分布，全史）--")
    for mo in sorted(seas):
        s = seas[mo]
        print(f"  {mo}月: n={s['n']} win={s['win']:.0f}% "
              f"avg={s['avg']:+.2f}% med={s['med']:+.2f}%")

    # 2) TSMOM 全史 WF
    bh = round((df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100, 1)
    results, lines = {}, [f"-- TSMOM 全史 WF（buy&hold={bh}%）--"]
    for lookback in (252, 126):
        for mode in ('long_flat', 'long_short'):
            pos = tsmom_pos(df, lookback, mode)
            r = run_backtest(df, pos)
            wf = walk_forward(df, pos)
            tag = verdict_tag(wf.get('robustness', 0)) \
                if not wf.get('skipped') else '-'
            key = f"tsmom{lookback}_{mode}"
            results[key] = {**r, 'wf': wf, 'verdict': tag}
            lines.append(f"  {key}: ret={r['return_pct']}% "
                         f"trades={r['trades']} "
                         f"rob={wf.get('robustness')} → {tag}")

    out = os.path.join(OUT_DIR, 'cn_gold_seasonal_tsmom_%s.json'
                       % datetime.now().strftime('%Y%m%d'))
    os.makedirs(OUT_DIR, exist_ok=True)
    _atomic_write(out, json.dumps(
        {'generated_at': datetime.now().isoformat(timespec='seconds'),
         'symbol': SYM, 'years': years, 'bars': len(df),
         'buy_hold_pct': bh, 'seasonality': seas, 'tsmom': results},
        ensure_ascii=False, indent=1))
    print("\n".join(lines))
    print('saved:', out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
