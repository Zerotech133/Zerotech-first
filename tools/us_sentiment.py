# -*- coding: utf-8 -*-
r"""us_sentiment: tradingview-mcp P1b 美股情绪试点（K178，K147-C P1b）。

用法（uv tool 独立环境）:
  & "$env:APPDATA\uv\tools\tradingview-mcp-server\Scripts\python.exe" us_sentiment.py [--n 8] [--symbols AAPL,NVDA] [--token XXX]

前提: MARKETAUX_API_TOKEN（marketaux.com 免费注册，100 调用/天）——
  优先 --token 一次性注入，其次读环境变量；两者皆缺时 API 优雅降级
  （sentiment_label=Unavailable，不抛错），管道照跑便于验收接线。

流程: us_scan 池头部取 N 只（GOOG/GOOGL 等同司双上市不合并）
  → market_sentiment 每只 1 调用（8 只=8 调用/天，免费额度内）
  → out/us_sentiment_YYYYMMDD.json + 控制台摘要。

边界: 试点口径只存快照不推送不入池；对照 ai_reviews 待积累后另弹。
"""
import argparse
import asyncio
import datetime
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import tradingview_mcp.server as tv


def _call(fn, *a, **kw):
    """同步/协程双态调用（K161-C 判例）。"""
    r = fn(*a, **kw)
    if hasattr(r, '__await__'):
        return asyncio.run(r)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=8)
    ap.add_argument('--symbols', default='')
    ap.add_argument('--token', default='')
    a = ap.parse_args()
    if a.token:
        os.environ['MARKETAUX_API_TOKEN'] = a.token

    syms = [s.strip().upper() for s in a.symbols.split(',') if s.strip()]
    if not syms:
        sc = sorted((Path(__file__).parent / 'out').glob('us_scan_*.json'))
        if not sc:
            print('[us_sentiment] 无 us_scan 池——先跑 us_scan.py')
            sys.exit(1)
        rows = json.loads(sc[-1].read_text(encoding='utf-8'))['rows']
        seen = set()
        for r in rows:
            s = r.get('symbol')
            if s and s not in seen:
                seen.add(s)
                syms.append(s)
            if len(syms) >= a.n:
                break

    results = []
    for s in syms:
        try:
            r = _call(tv.market_sentiment, s, limit=10)
        except Exception as e:
            r = {'symbol': s, 'error': repr(e)[:140]}
        results.append(r)
        err = r.get('error')
        print('%-6s %-12s posts=%s bull=%s bear=%s %s' % (
            s, (r.get('sentiment_label') or '')[:12],
            r.get('posts_analyzed'), r.get('bullish_count'),
            r.get('bearish_count'),
            ('ERR:' + str(err)[:60]) if err else ''), flush=True)

    out = {'ts': datetime.datetime.now().isoformat(timespec='seconds'),
           'source': 'tradingview-mcp market_sentiment (Marketaux licensed)',
           'token_configured': bool(os.environ.get('MARKETAUX_API_TOKEN')),
           'symbols': syms, 'results': results}
    od = Path(__file__).parent / 'out'
    od.mkdir(exist_ok=True)
    f = od / ('us_sentiment_%s.json'
              % datetime.date.today().strftime('%Y%m%d'))
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                 encoding='utf-8')
    print('[us_sentiment] saved', f.name, 'n=%d' % len(results))


if __name__ == '__main__':
    main()
