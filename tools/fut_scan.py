# -*- coding: utf-8 -*-
r"""fut_scan: 国际期货扫描快照 + 核心宏观池行情（tradingview-mcp F1+F2，K161-C）
用法（uv tool 独立环境）:
  & "$env:APPDATA\uv\tools\tradingview-mcp-server\Scripts\python.exe" fut_scan.py [overview_limit]
产物: out/fut_scan_YYYYMMDD.json + 控制台核心池摘要（涨跌 + 52 周位置）
"""
import asyncio
import datetime
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import tradingview_mcp.server as tv

# 核心宏观池（F2，K161-B）：贵金属/能源/基本金属/股指/债汇/加密
CORE_POOL = [
    ('GC=F',  '黄金'),   ('SI=F',  '白银'),   ('CL=F',  'WTI原油'),
    ('BZ=F',  '布伦特'), ('NG=F',  '天然气'), ('HG=F',  '铜'),
    ('ES=F',  '标普期货'), ('NQ=F', '纳指期货'), ('YM=F',  '道指期货'),
    ('NKD=F', '日经期货'), ('ZN=F',  '10Y美债'), ('DX-Y.NYB', '美元指数'),
    ('6E=F',  '欧元期货'), ('BTC=F', '比特币期货'),
]


async def core_pool_rows():
    rows = []
    for sym, cn in CORE_POOL:
        try:
            p = tv.yahoo_price(sym)
            if hasattr(p, '__await__'):
                p = await p
            price = p.get('price')
            chg = p.get('change_pct') or 0
            hi, lo = p.get('52w_high') or 0, p.get('52w_low') or 0
            # 52 周位置：0=贴地板 1=创新高
            pos = ((price - lo) / (hi - lo)) if (price and hi and hi > lo) else None
            rows.append({
                'symbol': sym, 'name_cn': cn, 'price': price,
                'change_pct': chg, 'previous_close': p.get('previous_close'),
                '52w_high': hi, '52w_low': lo, '52w_pos': round(pos, 3) if pos is not None else None,
                'exchange': p.get('exchange'),
            })
        except Exception as e:
            rows.append({'symbol': sym, 'name_cn': cn, 'error': repr(e)[:120]})
    return rows


async def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    # F1: 全类别扫描快照（TV 公开接口，9648 合约池）
    ov = tv.futures_market_overview(category='all', exchanges='us', limit=limit, volume_min=0)
    if hasattr(ov, '__await__'):
        ov = await ov
    contracts = ov.get('contracts', [])
    sorted(
        [c for c in contracts if c.get('change') is not None],
        key=lambda x: x.get('change') or 0, reverse=True)[:8]
    # F2: 核心池行情
    pool = await core_pool_rows()

    out = {
        'ts': datetime.datetime.now().isoformat(timespec='seconds'),
        'overview_total': ov.get('total_available'),
        'overview_returned': ov.get('returned'),
        'source': 'tradingview-mcp futures_market_overview (TV public) + yahoo_price (futures continuous)',
        'contracts': contracts,
        'core_pool': pool,
    }
    od = Path(__file__).parent / 'out'
    od.mkdir(exist_ok=True)
    f = od / f"fut_scan_{datetime.date.today():%Y%m%d}.json"
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"[fut_scan] overview_total={out['overview_total']} contracts_returned={len(contracts)} pool={len(pool)} saved={f.name}")
    print('-- 核心池（按涨跌排序）--')
    for r in sorted(pool, key=lambda x: x.get('change_pct') or 0, reverse=True):
        if 'error' in r:
            print(f"  {r['symbol']:>6} {r['name_cn']:<5} ERR {r['error'][:60]}")
            continue
        pos = r['52w_pos']
        pos_s = f"{pos * 100:4.0f}%" if pos is not None else '  n/a'
        price_s = f"{r['price']:>10.2f}" if r.get('price') is not None else '       n/a'
        chg = r.get('change_pct') or 0
        print(f"  {r['symbol']:>9} {r['name_cn']:<5} {price_s} {chg:+6.2f}%  52周位={pos_s}")


asyncio.run(main())
