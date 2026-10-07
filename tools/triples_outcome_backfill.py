# -*- coding: utf-8 -*-
r"""triples_outcome_backfill: triples.jsonl 盈亏标签回填器（UMP 边裁影子原型第一步，K194-C/K195）
用法（Python311 主链）:
  & "C:\Program Files\Python311\python.exe" -X utf8 triples_outcome_backfill.py [days_back]
职责（只读 gate 文件、写独立 outcome 流，零碰产线）:
  1) 读 jev-eval/review_data/triples.jsonl 全量
  2) 过滤 CN + pool_date <= T-days_back（默认 7 自然日兜底 5 交易日）
  3) tushare daily 拉候选日与 T+5 交易日收盘 → outcome_5d
  4) 增量写 review_data/triples_outcome.jsonl（已回填 candidate_id 幂等跳过）
  5) 影子统计：outcome 分布 + gate 判定 × 收益对照（FOCUS/PASS 的收益差）
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')
import tushare as ts


def _get_token():
    """token 获取链：env TUSHARE_TOKEN → 包缓存 → HKEY_USERS Administrator(User-500)
    环境（SYSTEM 任务下包缓存读不到用户级 token，K196）"""
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
HERE = os.path.dirname(os.path.abspath(__file__))
RD = os.path.join(os.path.dirname(HERE), 'jev-eval', 'review_data')
TRIPLES = os.path.join(RD, 'triples.jsonl')
OUTCOME = os.path.join(RD, 'triples_outcome.jsonl')
OUTCOME_DAYS = 5


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
    days_back = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    triples = load_jsonl(TRIPLES)
    done = {r.get('candidate_id') for r in load_jsonl(OUTCOME)}
    cut = (datetime.today() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    todo = [r for r in triples
            if r.get('market') == 'CN' and r.get('status') == 'reviewed'
            and (r.get('pool_date') or '') <= cut
            and r.get('candidate_id') not in done]
    print('[backfill] triples=%d CN_eligible=%d already_done=%d' % (len(triples), len(todo), len(done) - (len(done) - len([r for r in todo if r.get('candidate_id') in done]))))
    if not todo:
        print('[backfill] nothing to backfill')
        return

    # 按合约分组拉日线（一次拉全区间，多候选复用）
    need = defaultdict(lambda: None)  # ts_code -> (min_date, max_date)
    for r in todo:
        code = r['ts_code']
        pd_ = r['pool_date'].replace('-', '')
        if need[code] is None:
            need[code] = [pd_, None]
        else:
            need[code][0] = min(need[code][0], pd_)
    out_rows = []
    fail = 0
    for code, (d0, _) in need.items():
        try:
            df = pro.daily(ts_code=code, start_date=d0,
                           end_date=(datetime.today()).strftime('%Y%m%d'))
            if df is None or df.empty:
                continue
            df = df.sort_values('trade_date').reset_index(drop=True)
            idx_by_date = {d: i for i, d in enumerate(df['trade_date'])}
            for r in [x for x in todo if x['ts_code'] == code]:
                pd_ = r['pool_date'].replace('-', '')
                if pd_ not in idx_by_date:
                    continue
                i0 = idx_by_date[pd_]
                i5 = i0 + OUTCOME_DAYS
                if i5 >= len(df):
                    continue  # 尚不满 5 个交易日
                c0 = float(df.loc[i0, 'close'])
                c5 = float(df.loc[i5, 'close'])
                if c0 <= 0:
                    continue
                out_rows.append({
                    'candidate_id': r['candidate_id'], 'ts_code': code,
                    'pool_date': r['pool_date'], 'decision': r.get('decision'),
                    'quality': r.get('quality'), 'regime_p': r.get('regime_p'),
                    'score': r.get('score'),
                    'outcome_5d': round((c5 / c0 - 1) * 100, 3),
                    'backfilled_at': datetime.now().isoformat(timespec='seconds'),
                })
        except Exception as e:
            fail += 1
            print('  ERR %s: %r' % (code, e)[:100])
    # 幂等合并写回
    old = load_jsonl(OUTCOME)
    old_ids = {r.get('candidate_id') for r in old}
    new = [r for r in out_rows if r['candidate_id'] not in old_ids]
    with open(OUTCOME, 'a', encoding='utf-8') as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print('[backfill] fetched=%d written=%d fail_codes=%d total_outcome=%d'
          % (len(out_rows), len(new), fail, len(old) + len(new)))

    # 影子统计：decision × outcome（gate 判定与 5 日收益对照）
    all_rows = old + new
    by_dec = defaultdict(list)
    for r in all_rows:
        if r.get('outcome_5d') is not None:
            by_dec[r.get('decision')].append(r['outcome_5d'])
    print('-- 影子统计：gate 判定 × 5 日收益 --')
    for dec, vals in sorted(by_dec.items()):
        pos = sum(1 for v in vals if v > 0)
        avg = sum(vals) / len(vals)
        print('  %-6s n=%-4d pos=%4.1f%% avg=%+.2f%% med=%+.2f%%'
              % (dec, len(vals), pos / len(vals) * 100, avg,
                 sorted(vals)[len(vals) // 2]))


if __name__ == '__main__':
    main()
