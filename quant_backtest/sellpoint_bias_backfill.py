# -*- coding: utf-8 -*-
"""sellpoint_bias_backfill: SellPoint 多 Agent 卖点研判 · 真实收益回填器（训练数据地基 / 锦标赛对照）

用途：对 data/sellpoint_cards.jsonl 已生成的多 agent 研判卡，回填信号日之后
N 个交易日的真实收益，产出 data/sellpoint_bias.jsonl —— (agent_name, code, type,
dt, action, strength, real_ret) 四元组流。供后续「哪个 agent / 哪个 regime 最优」
统计与模型训练。只读 cards 文件、写独立 bias 流，零碰产线（影子信息层铁律）。

口径：卖点为方向统计（非做空损益）——卖出后下跌视为「卖对了」。real_ret =
close[T+OUTCOME_DAYS]/close[dt] - 1 表示信号后 5 交易日累计涨跌；负值=卖得对。

用法（Python311 主链，tushare token 三级链需满足）:
  & "C:\\Program Files\\Python311\\python.exe" -X utf8 sellpoint_bias_backfill.py [--limit N] [--days 7] [--status]
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')
import tushare as ts

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
CARDS = os.path.join(DATA, 'sellpoint_cards.jsonl')
BIAS = os.path.join(DATA, 'sellpoint_bias.jsonl')
OUTCOME_DAYS = 5            # T+5 交易日收益窗口（同 triples 口径，可扩展 HOLDS）
FALLBACK_DAYS = 7           # 待回填信号需距今 ≥ 此自然日（兜底 5 交易日）


def _get_token():
    """token 三级链：env TUSHARE_TOKEN → 包缓存 → HKEY_USERS User-500（SYSTEM 任务下兜底）。"""
    tok = ts.get_token()
    if tok:
        return tok
    import winreg
    try:
        root = winreg.HKEY_USERS
        for sid in range(0, winreg.QueryInfoKey(root)[0]):
            name = winreg.EnumKey(root, sid)
            if name.endswith('-500'):
                try:
                    k = winreg.OpenKey(root, name + r'\Environment')
                    v, _ = winreg.QueryValueEx(k, 'TUSHARE_TOKEN')
                    winreg.CloseKey(k)
                    if v:
                        return v
                except OSError:
                    continue
    except OSError:
        pass
    return None


pro = ts.pro_api(_get_token())


def load_jsonl(path):
    rows = []
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    return rows


def main():
    ap = argparse.ArgumentParser(description='SellPoint multi-agent bias backfill')
    ap.add_argument('--limit', type=int, default=0, help='本次最多回填几条（0=全部 eligible）')
    ap.add_argument('--days', type=int, default=FALLBACK_DAYS,
                    help='信号日距今需 ≥ 此自然日才回填（默认 7）')
    ap.add_argument('--status', action='store_true', help='只看影子统计，不回填')
    a = ap.parse_args()

    cards = load_jsonl(CARDS)
    bias = load_jsonl(BIAS)
    done = {(r.get('agent_name') or 'legacy_cons', r.get('code'),
             r.get('type'), r.get('dt')) for r in bias}
    cut = (datetime.today() - timedelta(days=a.days)).strftime('%Y-%m-%d')
    todo = [c for c in cards if (c.get('dt') or '') <= cut]
    todo = [c for c in todo if (c.get('agent_name') or 'legacy_cons',
                                c.get('code'), c.get('type'), c.get('dt')) not in done]
    todo = todo[0:a.limit] if a.limit else todo
    print('[bias] cards=%d bias=%d eligible_pending=%d days=%d' % (len(cards), len(bias), len(todo), a.days))

    # ── 影子统计（不回填也显示现有对照） ──
    def _show_stats(rows):
        by_agent = defaultdict(list)
        for r in rows:
            if r.get('real_ret') is not None:
                by_agent[r.get('agent_name') or 'legacy_cons'].append(r['real_ret'])
        print('-- 锦标赛对照：agent × 信号后 %d 日收益（负=卖得对） --' % OUTCOME_DAYS)
        if not by_agent:
            print('  (暂无已回填样本)')
            return
        for an, vals in sorted(by_agent.items()):
            pos = sum(1 for v in vals if v > 0)
            avg = sum(vals) / len(vals)
            med = sorted(vals)[len(vals) // 2]
            print('  %-12s n=%-4d 下跌胜率(负收益)%.0f%% avg=%+.2f%% med=%+.2f%%'
                  % (an, len(vals), (len(vals) - pos) / len(vals) * 100,
                     avg, med))
    if a.status or not todo:
        _show_stats(bias)
        if not a.status:
            print('[bias] nothing new to backfill')
        return

    # 按合约分组建索引（一次拉区间，多 agent 卡复用）
    need = {}
    for c in todo:
        code = c.get('code', '')
        d0 = (c.get('dt') or '').replace('-', '')
        if not code or len(d0) != 8:
            continue
        prev = need.get(code)
        need[code] = d0 if prev is None else min(prev, d0)

    out_rows, fail = [], 0
    for code, d0 in need.items():
        try:
            df = pro.daily(ts_code=code, start_date=d0,
                           end_date=datetime.today().strftime('%Y%m%d'))
            if df is None or df.empty:
                continue
            df = df.sort_values('trade_date').reset_index(drop=True)
            idx = {d: i for i, d in enumerate(df['trade_date'])}
            for c in [x for x in todo if x.get('code') == code]:
                key = ((c.get('agent_name') or 'legacy_cons'),
                       c.get('code'), c.get('type'), c.get('dt'))
                if key in done:
                    continue
                d0s = (c.get('dt') or '').replace('-', '')
                if d0s not in idx:
                    continue
                i0 = idx[d0s]
                i5 = i0 + OUTCOME_DAYS
                if i5 >= len(df):
                    continue  # 尚不满 5 交易日
                c0 = float(df.loc[i0, 'close'])
                c5 = float(df.loc[i5, 'close'])
                if c0 <= 0:
                    continue
                out_rows.append({
                    'agent_name': c.get('agent_name') or 'legacy_cons',
                    'method': c.get('method'), 'focus': c.get('focus'),
                    'timing': c.get('timing'),
                    'code': c.get('code'), 'name': c.get('name'),
                    'type': c.get('type'), 'dt': c.get('dt'),
                    'tbe': c.get('tbe'), 'npv': c.get('npv'),
                    'action': c.get('action'), 'strength': c.get('strength'),
                    'held': c.get('held'), 'paper_pnl': c.get('paper_pnl'),
                    'real_ret': round((c5 / c0 - 1) * 100, 3),
                    'backfilled_at': datetime.now().isoformat(timespec='seconds'),
                })
        except Exception as e:
            fail += 1
            print('  ERR %s: %r' % (code, str(e)[:100]))

    # 幂等合并写回（append；done 集合已在内存，重复进程安全）
    old_ids = {(r.get('agent_name'), r.get('code'), r.get('type'), r.get('dt'))
               for r in bias}
    new = [r for r in out_rows if (r['agent_name'], r['code'], r['type'], r['dt']) not in old_ids]
    with open(BIAS, 'a', encoding='utf-8') as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    total = load_jsonl(BIAS)
    print('[bias] written=%d fail_codes=%d total_bias=%d' % (len(new), fail, len(total)))
    _show_stats(total)


if __name__ == '__main__':
    main()