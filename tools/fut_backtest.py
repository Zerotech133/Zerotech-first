# -*- coding: utf-8 -*-
r"""fut_backtest: 期货量化分析层 F3（K161-B）——核心池 9 策略回测对照。
用法（uv tool 独立环境）:
  & "$env:APPDATA\uv\tools\tradingview-mcp-server\Scripts\python.exe" fut_backtest.py [period]
流程: 每品种 compare_strategies(9 策略排行) → 冠军策略若 WF 支持
  （rsi_pullback/triple_ema 不支持）再跑 walk_forward 防过拟合。
产物: out/fut_strat_YYYYMMDD.json + out/fut_strat_YYYYMMDD.html（矩阵面板）
"""
import datetime
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import tradingview_mcp.server as tv

# 核心宏观池（同 fut_scan.CORE_POOL——不 import fut_scan，其模块级
# asyncio.run(main()) 会在 import 时触发全市场扫描）
CORE_POOL = [
    ('GC=F',  '黄金'),   ('SI=F',  '白银'),   ('CL=F',  'WTI原油'),
    ('BZ=F',  '布伦特'), ('NG=F',  '天然气'), ('HG=F',  '铜'),
    ('ES=F',  '标普期货'), ('NQ=F', '纳指期货'), ('YM=F',  '道指期货'),
    ('NKD=F', '日经期货'), ('ZN=F',  '10Y美债'), ('DX-Y.NYB', '美元指数'),
    ('6E=F',  '欧元期货'), ('BTC=F', '比特币期货'),
]

STRATS = ['rsi', 'bollinger', 'macd', 'ema_cross', 'supertrend',
          'donchian', 'rsi_pullback', 'keltner_breakout', 'triple_ema']
WF_OK = set(STRATS) - {'rsi_pullback', 'triple_ema'}


import asyncio


def _call(fn, *a, **kw):
    """同步/协程双态调用（K161-C 判例：tradingview-mcp 混合 API）。"""
    r = fn(*a, **kw)
    if hasattr(r, '__await__'):
        return asyncio.run(r)
    return r


def run_one(sym, cn, period):
    cmp1 = _call(tv.compare_strategies, sym, period=period, interval='1d')
    row = {'symbol': sym, 'name_cn': cn}
    if isinstance(cmp1, dict) and cmp1.get('error'):
        row['error'] = str(cmp1['error'])[:140]
        return row
    ranking = cmp1.get('ranking') or []
    row['ranking'] = ranking
    row['buy_and_hold_pct'] = cmp1.get('buy_and_hold_return_pct')
    row['winner'] = cmp1.get('winner')
    row['candles'] = cmp1.get('candles_analyzed')
    # 冠军策略 walk-forward（WF 支持的 7 策略才跑）
    w = cmp1.get('winner')
    if w in WF_OK:
        wf = _call(tv.walk_forward_backtest_strategy, sym, w,
                   period=period, interval='1d')
        if isinstance(wf, dict) and not wf.get('error'):
            row['wf'] = {k: wf.get(k) for k in (
                'avg_train_return_pct', 'avg_test_return_pct',
                'robustness_score', 'verdict')}
        else:
            row['wf'] = {'error': str((wf or {}).get('error'))[:100]}
    return row


def render_html(out, path):
    gen = (out.get('ts') or '')[:16]
    strats = STRATS
    head = ("<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
            "<title>期货核心池 · 策略×品种回测对照</title><style>"
            "body{background:#0d1117;color:#e6edf3;font:13.5px/1.7"
            " -apple-system,'Segoe UI','Microsoft YaHei',sans-serif;"
            "margin:0;padding:22px}.wrap{max-width:1180px;margin:0 auto}"
            "h1{font-size:20px;margin:0 0 4px}.meta{color:#8b949e;"
            "font-size:12px;margin-bottom:14px}table{border-collapse:"
            "collapse;font-size:12px}th,td{border:1px solid #30363d;"
            "padding:4px 8px;text-align:center;white-space:nowrap}th"
            "{background:#161b22;color:#8b949e}td.l{text-align:left}"
            ".dim{color:#8b949e;font-size:12px;margin:6px 0 18px}.rw"
            "{color:#f85149;font-weight:600}.gn{color:#3fb950;"
            "font-weight:600}.yl{color:#d29922}</style></head><body>"
            "<div class='wrap'><h1>🌾 期货核心池 · 策略×品种回测对照</h1>"
            "<div class='meta'>tradingview-mcp compare_strategies(9 策略,"
            " 2y, 1d) + 冠军策略 walk-forward · 生成于 " + gen + " · "
            "数字=总收益%（红正/绿负，相对买入持有）· 本报告为研究快照，"
            "不构成投资建议</div><table><tr><th>品种</th><th>冠军</th>"
            + ''.join('<th>%s</th>' % s for s in strats)
            + '<th>B&amp;H</th><th>WF 稳健性</th></tr>')
    body = []
    for r in out['results']:
        if r.get('error'):
            body.append("<tr><td class='l'>%s %s</td><td colspan='11' "
                        "class='dim'>%s</td></tr>"
                        % (r['symbol'], r['name_cn'], r['error']))
            continue
        m = {x['strategy']: (x.get('total_return_pct') or 0)
             for x in r['ranking']}
        bh = r.get('buy_and_hold_pct')
        cells = ''
        for s in strats:
            v = m.get(s)
            if v is None:
                cells += '<td class="dim">—</td>'
                continue
            better = (v - (bh or 0)) > 0
            cells += ("<td class='%s'>%+.1f</td>"
                      % ('rw' if better else 'gn', v))
        wf = r.get('wf') or {}
        if wf.get('verdict'):
            score = wf.get('robustness_score')
            vcolor = ('gn' if score is not None and score >= 0.8
                      else 'yl' if score is not None and score >= 0.5 else 'rw')
            wf_h = ("<span class='%s'>%.2f</span> <span class='dim'>%s</span>"
                    % (vcolor, score,
                       (wf.get('verdict') or '').split('—')[0].strip()))
        else:
            wf_h = "<span class='dim'>%s</span>" % (
                (wf.get('error') or '不适用')[:30])
        body.append("<tr><td class='l'><b>%s</b> %s</td><td class='yl'>%s"
                    "</td>%s<td class='dim'>%s</td><td>%s</td></tr>"
                    % (r['symbol'], r['name_cn'], r.get('winner') or '—',
                       cells, ('%+.1f%%' % bh) if bh is not None else '—',
                       wf_h))
    tail = ('</table><div class="dim">WF 稳健性=样本外/样本内收益比均值'
            '（≥0.8 稳健 / ≥0.5 中等 / ≥0.2 弱 / &lt;0.2 过拟合）；'
            '冠军=compare_strategies 排行第一；rsi_pullback/triple_ema '
            '不参与 WF（SMA200 预热超折长）。</div></div></body></html>')
    path.write_text(head + ''.join(body) + tail, encoding='utf-8')


def main():
    period = sys.argv[1] if len(sys.argv) > 1 else '2y'
    results = []
    for sym, cn in CORE_POOL:
        print('[%s] %s ...' % (sym, cn), flush=True)
        try:
            results.append(run_one(sym, cn, period))
        except Exception as e:
            results.append({'symbol': sym, 'name_cn': cn,
                            'error': repr(e)[:140]})
        r = results[-1]
        if r.get('error'):
            print('   ERR', r['error'][:80])
        else:
            wf = r.get('wf') or {}
            print('   winner=%s bh=%s wf=%s' % (
                r.get('winner'), r.get('buy_and_hold_pct'),
                wf.get('robustness_score', 'n/a')))
    out = {'ts': datetime.datetime.now().isoformat(timespec='seconds'),
           'period': period, 'interval': '1d',
           'source': 'tradingview-mcp compare_strategies + '
                     'walk_forward_backtest_strategy (yahoo 2y daily)',
           'n_pool': len(CORE_POOL), 'results': results}
    od = Path(__file__).parent / 'out'
    od.mkdir(exist_ok=True)
    f = od / ('fut_strat_%s.json'
              % datetime.date.today().strftime('%Y%m%d'))
    f.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                 encoding='utf-8')
    render_html(out, od / ('fut_strat_%s.html'
                           % datetime.date.today().strftime('%Y%m%d')))
    print('[fut_backtest] saved', f.name, 'results=%d' % len(results))
    if results and all(r.get('error') for r in results):
        print('[fut_backtest] ALL %d symbols failed; F3 matrix unavailable'
              % len(results), file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()
