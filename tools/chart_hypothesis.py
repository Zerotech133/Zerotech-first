# -*- coding: utf-8 -*-
"""漏斗层1：图形推演引擎（K-ORG-120 图先行·事佐证）

从 K 线硬数据推演「运作假设」：吸筹/洗盘/出货/抢跑/无为 五算子。
纯 pandas 计算，可复算，零外部依赖。输出假设供层2(事件)/层3(资金)佐证。
"""
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_kline(code: str) -> pd.DataFrame:
    mkt = 'SH' if code.startswith(('6', '5')) else 'SZ'
    return pd.read_parquet(ROOT / f'quant_backtest/data/ashare/{code}_{mkt}.parquet').sort_values('trade_date').reset_index(drop=True)


def operators(df: pd.DataFrame) -> dict:
    """五算子：全部基于可复算的量价几何。"""
    n = len(df)
    close, vol, high, low = df['close'], df['volume'], df['high'], df['low']
    vol_ma20 = vol.rolling(20).mean()

    # ── 算子1：放量吸筹嫌疑 ──
    # 低位箱体（60日振幅<25% 且 处250日下40%分位）+ 近60日出现量比>=3 放量日>=3 次 + 箱底不破
    box = df.tail(60)
    amp60 = (box['high'].max() - box['low'].min()) / box['low'].min()
    pos250 = (close.tail(250) < close.iloc[-1]).mean()
    ratio = vol / vol_ma20
    surge_days = (df.tail(60)['volume'] / df.tail(60)['volume'].rolling(20).mean() >= 3).sum()
    box_low_hold = box['close'].min() > box['low'].min() * 0.97
    accum = amp60 < 0.25 and pos250 < 0.4 and surge_days >= 3 and box_low_hold

    # ── 算子2：缩量洗盘嫌疑 ──
    # 近20日量缩(<前20日80%) + 回撤<15% + 60日内曾有放量拉升(量比>=2.5 的涨幅日)
    v_now, v_prev = vol.tail(20).mean(), vol.iloc[-40:-20].mean()
    shrink = v_now < v_prev * 0.8
    drawdown = (close.tail(60).max() - close.iloc[-1]) / close.tail(60).max()
    had_surge_up = ((df.tail(60)['pct_chg'] > 4) & (df.tail(60)['volume'] / vol_ma20.tail(60) >= 2.5)).any()
    wash = shrink and drawdown < 0.15 and had_surge_up

    # ── 算子3：对倒出货嫌疑 ──
    # 处250日上80%分位 + 近20日有天量长阴(量比>=3 & 跌幅>4%) 或 长上影群
    dist = df.tail(20)
    big_down = ((dist['pct_chg'] < -4) & (dist['volume'] / vol_ma20.iloc[-20:].values >= 3)).any()
    upper_shadow = ((dist['high'] - dist[['open', 'close']].max(axis=1)) / (dist['high'] - dist['low'] + 1e-9) > 0.5).sum() >= 5
    distribute = pos250 > 0.8 and (big_down or upper_shadow)

    # ── 算子4：抢跑异动嫌疑 ──
    # 近90日存在「无公告放量拉抬日」：涨幅>=5% 且（量比>=2.2 或 从地量区(前5日均量<60日均量60%)骤放）
    # 阈值校准依据：600337 9-22 实测（+6.6%/量比2.5/前期地量）——原 4x 阈值漏检，判例修正
    w90 = df.tail(90).reset_index(drop=True)
    ma20_90 = vol_ma20.tail(90).values
    vol_ma5 = w90['volume'].rolling(5).mean().shift(1).values   # 异动前5日均量
    vol_ma60 = w90['volume'].rolling(60).mean().shift(1).values
    runup_days = []
    for i in range(len(w90)):
        chg = w90['pct_chg'].iloc[i]
        if chg >= 5:
            vr = w90['volume'].iloc[i] / (ma20_90[i] if ma20_90[i] and not np.isnan(ma20_90[i]) else 1)
            from_quiet = (vol_ma5[i] < vol_ma60[i] * 0.6) if not (np.isnan(vol_ma5[i]) or np.isnan(vol_ma60[i])) else False
            if vr >= 2.2 or from_quiet:
                runup_days.append(str(w90['trade_date'].iloc[i]))
    runup_suspect = len(runup_days) > 0

    # ── 算子5：无为自然 ──
    # 量价与大盘同律（简化判据：近60日无独立异动=无算子1-4命中）
    idle = not (accum or wash or distribute or runup_suspect)

    return {
        "hypothesis_accumulate": {"hit": bool(accum), "evidence": f"60日振幅{amp60:.0%}/250日分位{pos250:.0%}/放量日{surge_days}"},
        "hypothesis_wash": {"hit": bool(wash), "evidence": f"量缩{v_now/v_prev:.0%}/回撤{drawdown:.0%}/先有拉升={had_surge_up}"},
        "hypothesis_distribute": {"hit": bool(distribute), "evidence": f"250日分位{pos250:.0%}/天量长阴={big_down}/上影群={upper_shadow}"},
        "hypothesis_runup": {"hit": bool(runup_suspect), "runup_days": runup_days[:4], "note": "待层2事件耦合核验"},
        "hypothesis_idle": {"hit": bool(idle), "evidence": "无独立异动结构"},
        "context": {"close": float(close.iloc[-1]), "pos250": round(float(pos250), 2),
                    "vol20_vs_prev20": round(float(v_now / v_prev), 2), "amp60": round(float(amp60), 2)},
    }


def primary_hypothesis(ops: dict) -> dict:
    """主假设排序：抢跑>出货>吸筹>洗盘>无为（证据强度序）"""
    order = [("runup", "抢跑异动", "内幕特征：无公告放量拉抬，待事件耦合核验"),
             ("distribute", "对倒出货", "高位派发嫌疑，待席位卖方核验"),
             ("accumulate", "放量吸筹", "低位建仓嫌疑，待席位买方核验"),
             ("wash", "缩量洗盘", "控盘洗筹嫌疑，待无量回踩核验"),
             ("idle", "无为自然", "散手票，不值得事件/资金查证成本")]
    for key, name, action in order:
        if ops[f"hypothesis_{key}"]["hit"]:
            return {"primary": key, "name": name, "next_verification": action}
    return {"primary": "idle", "name": "无为自然", "next_verification": action}


if __name__ == '__main__':
    import sys, json
    code = sys.argv[1] if len(sys.argv) > 1 else '600337'
    df = load_kline(code)
    ops = operators(df)
    print(json.dumps({"code": code, "ops": ops, "primary": primary_hypothesis(ops)}, ensure_ascii=False, indent=2))
