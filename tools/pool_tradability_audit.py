# -*- coding: utf-8 -*-
r"""pool_tradability_audit: 候选池可成交性审计（mask-first 思想落地，arXiv 2507.07107 对照）
用法（Python311 主链）:
  & "C:\Program Files\Python311\python.exe" -X utf8 pool_tradability_audit.py
审计逻辑（论文 upstream contamination 的本仓对照）:
  预警池模拟交易用「检出日收盘入场」——若检出日涨停封板收盘，entry_px 实际不可成交，
  模拟 pnl 虚高（论文口径：污染虚增 IC 18% / Sharpe -0.44）。
  本审计对每条候选拉 detect_date 的 tushare daily（pct_chg/close）判定：
    主板 pct_chg>=9.8 或 创业/科创 >=19.8 → 涨停收盘 → entry_px 不可成交
  输出污染面统计（按市场板块分桶）+ 落盘 data\pool_tradability_audit.json
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
import tushare as ts

pro = ts.pro_api()
HERE = os.path.dirname(os.path.abspath(__file__))
POOL = os.path.join(os.path.dirname(HERE), 'quant_backtest', 'data', 'chanlun_pool.jsonl')
OUT = os.path.join(os.path.dirname(HERE), 'quant_backtest', 'data', 'pool_tradability_audit.json')


def main():
    rows = []
    with open(POOL, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    # CN 市场才有涨跌停语义；按 detect_date 分组批量拉
    cn = [r for r in rows if r.get('market') == 'CN']
    by_date = defaultdict(list)
    for r in cn:
        d = (r.get('detect_date') or '').replace('-', '')
        if d:
            by_date[d].append(r)
    print('[audit] pool=%d CN=%d detect_dates=%d' % (len(rows), len(cn), len(by_date)))

    daily_cache = {}
    stats = defaultdict(lambda: {'n': 0, 'limit_up': 0, 'near_limit': 0})
    skipped = 0
    for d, rs in sorted(by_date.items()):
        codes = list({r['code'] for r in rs})
        for code in codes:
            if code not in daily_cache:
                try:
                    df = pro.daily(ts_code=code, start_date=d, end_date=d)
                    daily_cache[code] = df
                except Exception:
                    daily_cache[code] = None
            df = daily_cache[code]
            if df is None or df.empty:
                skipped += len([x for x in rs if x['code'] == code])
                continue
            row = df.iloc[0]
            pct = float(row['pct_chg'])
            limit_th = 19.8 if code[0] in ('3', '688') or code[:3] == '688' else 9.8
            for r in [x for x in rs if x['code'] == code]:
                s = stats[r.get('type', '?')]
                s['n'] += 1
                if pct >= limit_th:
                    s['limit_up'] += 1
                elif pct >= limit_th - 0.5:
                    s['near_limit'] += 1

    total_n = sum(s['n'] for s in stats.values())
    total_lu = sum(s['limit_up'] for s in stats.values())
    total_near = sum(s['near_limit'] for s in stats.values())
    print('\n-- 可成交性审计（检出日收盘入场 vs 涨停封板）--')
    print('  样本=%d 涨停收盘=%d (%.1f%%) 临界(±0.5%%)=%d (%.1f%%) 数据缺失跳过=%d'
          % (total_n, total_lu, total_lu / max(total_n, 1) * 100,
             total_near, total_near / max(total_n, 1) * 100, skipped))
    print('  按信号类型:')
    for t, s in sorted(stats.items(), key=lambda x: -x[1]['n']):
        if s['n']:
            print('    %-6s n=%-4d 涨停=%-3d (%.1f%%) 临界=%d'
                  % (t, s['n'], s['limit_up'], s['limit_up'] / s['n'] * 100, s['near_limit']))

    # 明细行落盘（消费端 join 用：模拟 pnl 统计按 candidate 剔除不可成交样本）
    # candidate_id 无原生键 → 用 (code,type,dt) 复合键，与 _open_positions 幂等键一致
    detail_path = os.path.join(os.path.dirname(HERE), 'quant_backtest', 'data',
                               'pool_tradability_detail.jsonl')
    with open(detail_path, 'w', encoding='utf-8') as f:
        for d, rs in sorted(by_date.items()):
            pct_by_code = {}
            for code in {r['code'] for r in rs}:
                df = daily_cache.get(code)
                if df is not None and not df.empty:
                    pct_by_code[code] = float(df.iloc[0]['pct_chg'])
            for r in rs:
                pct = pct_by_code.get(r['code'])
                if pct is None:
                    continue
                limit_th = 19.8 if r['code'][0] in ('3', '688') else 9.8
                f.write(json.dumps({
                    'code': r['code'], 'type': r['type'], 'dt': r['dt'],
                    'detect_date': r['detect_date'], 'pct_chg': pct,
                    'tradable': pct < limit_th,
                    'limit_up': pct >= limit_th,
                }, ensure_ascii=False) + '\n')
    print('[audit] detail saved=%s' % detail_path)


    result = {
        'ts': datetime.now().isoformat(timespec='seconds'),
        'method': 'mask-first 对照审计（arXiv 2507.07107 upstream contamination）',
        'total_n': total_n, 'limit_up': total_lu, 'near_limit': total_near,
        'skipped': skipped, 'by_type': {k: dict(v) for k, v in stats.items()},
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=1))
    print('[audit] saved=%s' % OUT)


if __name__ == '__main__':
    main()
