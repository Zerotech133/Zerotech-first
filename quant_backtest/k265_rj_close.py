# -*- coding: utf-8 -*-
"""K265 回捞收官分析：FLAG 按段分割(rj/非rj) + 0.95带与图内已入图对比 → 待审清单落文件。"""
import json, sys
from pathlib import Path
sys.path.insert(0, r'e:\老田的AI助手\quant_backtest')
from k265_dir_audit import audit_pair
from kg_client import KgClient

SHADOW = Path(r'e:\老田的AI助手\quant_backtest\data\semantica\k265_phase1_shadow.jsonl')

rows = [json.loads(l) for l in open(SHADOW, encoding='utf-8') if l.strip()]
pv2 = [r for r in rows if r.get('pv') == 2 and r.get('ext', {}).get('ok') is True]
rj = [r for r in pv2 if r.get('rj') == 1]
non = [r for r in pv2 if r.get('rj') != 1]
print(f'pv2 正向 = {len(pv2)}  回捞段(rj) = {len(rj)}  非回捞段 = {len(non)}')

def scan(seg, tag):
    flags = []
    for r in seg:
        f, d = audit_pair(r['a'], r['b'], r['ext'])
        if f:
            flags.append((r, d))
    print(f'{tag}: FLAG={len(flags)}/{len(seg)} ({len(flags)/max(1,len(seg))*100:.1f}%)')
    return flags

fl_non = scan(non, '非回捞段')
fl_rj = scan(rj, '回捞段(rj)')
print('--- 回捞段 FLAG 明细 ---')
for r, d in fl_rj:
    e = r['ext']
    print(f"  {e.get('edge')} {r['a']} x {r['b']} src={e.get('src')} conf={e.get('conf')} | {d}")
    print(f"    {r.get('old_conf')} -> {e.get('conf')}  {(e.get('reason') or '')[:80]}")

# 0.95 带 × 图内已入图对比
c = KgClient()
with c._session() as s:
    gedges = {(r['a'], r['b'], r['t']) for r in s.run(
        "MATCH (x:Concept)-[r]->(y:Concept) WHERE r.source='concept_llm_v1' "
        'RETURN x.name AS a, y.name AS b, type(r) AS t').data()}
print(f'图内 LLM 边 = {len(gedges)}')

band = [r for r in pv2 if r['ext'].get('conf', 0) >= 0.95]
def in_graph(r):
    e = r['ext']
    src_name = r['a'] if e.get('src') == 'A' else r['b']
    dst_name = r['b'] if e.get('src') == 'A' else r['a']
    etype = e.get('edge')  # 'SUPPLIES' / 'DRIVES' 直接作边类型
    return (src_name, dst_name, etype) in gedges

done_rows = [r for r in band if in_graph(r)]
todo = [r for r in band if not in_graph(r)]
fl_keys = {id(r[0]) for r in fl_non + fl_rj}
todo_flag = [r for r in todo if id(r) in fl_keys]
print(f'0.95带 = {len(band)}  已入图 = {len(done_rows)}  待入图 = {len(todo)}  (待入图含FLAG {len(todo_flag)})')
print(f'待入图 rj 段 = {sum(1 for r in todo if r.get("rj")==1)}  非rj = {sum(1 for r in todo if r.get("rj")!=1)}')

out = Path(r'e:\老田的AI助手\quant_backtest\data\semantica\k265_ingest_pending.txt')
with open(out, 'w', encoding='utf-8') as f:
    for i, r in enumerate(todo, 1):
        e = r['ext']
        src_name = r['a'] if e.get('src') == 'A' else r['b']
        dst_name = r['b'] if e.get('src') == 'A' else r['a']
        mark = ' [FLAG]' if id(r) in fl_keys else ''
        rjmark = ' rj' if r.get('rj') == 1 else ''
        oc = f" old={r.get('old_conf')}" if r.get('rj') == 1 else ''
        f.write(f"{i:3d}. {e.get('edge')} {src_name} -> {dst_name} conf={e.get('conf')}{oc}{rjmark}{mark}\n")
        f.write(f"     {e.get('reason','')[:150]}\n")
print(f'待审清单 -> {out}')
