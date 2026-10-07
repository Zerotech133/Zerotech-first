# -*- coding: utf-8 -*-
r"""us_scan: 美股全市场市值榜快照（tradingview-mcp P1a 扩池工具）

用法（uv tool 独立环境）:
  & "$env:APPDATA\uv\tools\tradingview-mcp-server\Scripts\python.exe" us_scan.py [limit]
产物: out/us_scan_YYYYMMDD.json + 控制台涨幅榜摘要
"""
import asyncio, datetime, json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import tradingview_mcp.server as tv


async def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    r = tv.stock_screener(country='america', limit=limit)
    if hasattr(r, '__await__'):
        r = await r
    rows = r.get('rows', [])
    out = {
        'ts': datetime.datetime.now().isoformat(timespec='seconds'),
        'total_matches': r.get('total_matches'),
        'returned': len(rows),
        'source': 'tradingview-mcp stock_screener (TradingView public endpoint)',
        'rows': rows,
    }
    od = Path(__file__).parent / 'out'
    od.mkdir(exist_ok=True)
    f = od / f"us_scan_{datetime.date.today():%Y%m%d}.json"
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"[us_scan] total={out['total_matches']} returned={out['returned']} saved={f.name}")
    top = sorted(rows, key=lambda x: x.get('change_percent') or 0, reverse=True)[:8]
    for r2 in top:
        print(f"  {r2.get('ticker','?'):>16} {r2.get('change_percent') or 0:+7.2f}%  cap={((r2.get('market_cap') or 0)/1e9):6.0f}B  {r2.get('description','')[:30]}")


asyncio.run(main())
