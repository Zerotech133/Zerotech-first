# -*- coding: utf-8 -*-
"""K265 rj1 commit 口径清理：删 batch=rj1 且对不在 pv2 白名单(ok+conf>=0.95-剔除) 的边。
背景：rj1 全量切片误将 pv1 行(旧版无自检) 0.95+ 带写入图；pv2 对这些对判负/低conf = 否决。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, r'C:\项目\老田的AI助手\quant_backtest')
from kg_client import KgClient
from k265_phase1_commit import build_brands, is_brand_concept

SHADOW = Path(r'C:\项目\老田的AI助手\quant_backtest\data\semantica\k265_phase1_shadow.jsonl')
EXCL = Path(r'C:\项目\老田的AI助手\quant_backtest\data\semantica\k265_rj_exclude.txt')

excl = set()
for ln in open(EXCL, encoding='utf-8'):
    ln = ln.strip()
    if ln and not ln.startswith('#'):
        excl.add(tuple(p.strip() for p in ln.split('|')))

cli = KgClient()
brands = build_brands(cli)

white = set()   # (src, dst, edge) pv2 背书
pv2_pairs = {}  # (src,dst) -> pv2 最高 conf（诊断用）
for ln in open(SHADOW, encoding='utf-8'):
    ln = ln.strip()
    if not ln:
        continue
    r = json.loads(ln)
    if r.get('pv') != 2:
        continue
    e = r.get('ext', {})
    if e.get('ok') is not True or (e.get('conf') or 0) < 0.95:
        continue
    src = r['a'] if e.get('src') == 'A' else r['b']
    dst = r['b'] if e.get('src') == 'A' else r['a']
    pv2_pairs[(src, dst)] = max(pv2_pairs.get((src, dst), 0), e.get('conf') or 0)
    if (src, dst) in excl:
        continue
    if e.get('edge') == 'SUPPLIES' and is_brand_concept(src, brands):
        continue
    white.add((src, dst, e.get('edge')))
print(f'[1] pv2 白名单 = {len(white)} 对（excl {len(excl)} 已扣）')

sess = cli._session()
edges = sess.run(
    "MATCH (a:Concept)-[r]->(b:Concept) WHERE r.source='concept_llm_v1' "
    "RETURN a.name AS s, b.name AS t, type(r) AS ty, r.batch AS b, r.conf AS c").data()
rj1 = [r for r in edges if r['b'] == 'rj1']
print(f'[2] 图内 LLM 边 = {len(edges)}  batch=rj1 = {len(rj1)}')
kill = [r for r in rj1 if (r['s'], r['t'], r['ty']) not in white]
print(f'[3] 待删（rj1 且非 pv2 白名单）= {len(kill)}')
for r in kill:
    pv2c = pv2_pairs.get((r['s'], r['t']))
    print(f"    {r['ty']} {r['s']} -> {r['t']}  rj1_conf={r['c']}  pv2_max_conf={pv2c}")
if kill and '--go' in sys.argv:
    rows = [{'s': r['s'], 't': r['t'], 'ty': r['ty']} for r in kill]
    sess.run(
        "UNWIND $rows AS k MATCH (a:Concept {name:k.s})-[r]->(b:Concept {name:k.t}) "
        "WHERE type(r) = k.ty AND r.source='concept_llm_v1' DELETE r", rows=rows)
    now = sess.run(
        "MATCH ()-[r]->() WHERE r.source='concept_llm_v1' "
        "RETURN type(r) AS t, count(*) AS n").data()
    print('[4] 删除后图内 =', {d['t']: d['n'] for d in now})
else:
    print('(dry-run，加 --go 执行删除)')
