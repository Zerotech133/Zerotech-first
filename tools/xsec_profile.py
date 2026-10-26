# -*- coding: utf-8 -*-
"""三线交叉标准件 xsec_profile v1.0（总方案 B1 · K-ORG-119 路由注册工具）

五虎/风控组的公共事实底座：一键生成个股「事件传导 + K线审美 + 资金主体」三线报告。
数据源全部走数据册登记位置（data_catalog.yaml），零臆造——分析师只做判断不做检索。

用法：
  python tools/xsec_profile.py 600337              # 终端摘要
  python tools/xsec_profile.py 600337 --json       # 结构化（喂辩论管线）
"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from chart_hypothesis import load_kline, operators, primary_hypothesis

ROOT = Path(__file__).resolve().parent.parent   # 主仓根
sys.path.insert(0, str(ROOT.parent / 'zerobound-kg'))  # kg_schema


def _load_env():
    for line in (ROOT / 'quant_backtest' / '.env.local').read_text(encoding='utf-8').splitlines():
        if line.startswith('KG_'):
            k, v = line.split('=', 1)
            os.environ[k] = v.strip()


# ── 线1：事件传导（KG Event + news.db）──
def line1_events(code: str) -> dict:
    _load_env()
    from neo4j import GraphDatabase
    from kg_schema import neo4j_conn
    uri, auth, db = neo4j_conn()
    drv = GraphDatabase.driver(uri, auth=auth)
    out = {"events": [], "market_events": []}
    with drv.session() as s:
        r = s.run("""
MATCH (:Security {symbol:$c})-[:INVOLVES]-(e:Event)
RETURN e.text AS t, e.date AS d ORDER BY d DESC LIMIT 8""", c=code)
        out["events"] = [{"date": x["d"], "title": (x["t"] or "")[:60]} for x in r]
        r2 = s.run("""
MATCH (:Security {symbol:$c})-[:HAS_PATTERN|HAS_TOPGAP|HAS_VOL_EVENT]->(me)
RETURN me.date AS d, me.kind AS k, me.type AS t, me.vol_ratio AS vr
ORDER BY d DESC LIMIT 10""", c=code)
        out["market_events"] = [{"date": x["d"], "kind": x["k"], "type": x["t"], "vol_ratio": x["vr"]} for x in r2]
    drv.close()
    # news
    try:
        db_ = sqlite3.connect(ROOT / 'news_collector' / 'news.db')
        name_hint = ''
        rows = db_.execute("SELECT substr(ts,1,10), substr(title,1,56) FROM news WHERE title LIKE ? ORDER BY ts DESC LIMIT 5",
                           (f'%{code}%',)).fetchall()
        out["news"] = [{"ts": a, "title": b} for a, b in rows]
    except Exception:
        out["news"] = []
    return out


# ── 线2：K线审美（缠论视角量化判据）──
def line2_kline(code: str) -> dict:
    import pandas as pd
    mkt = 'SH' if code.startswith('6') else ('SZ' if code[0] in '03' else 'SH')
    df = pd.read_parquet(ROOT / f'quant_backtest/data/ashare/{code}_{mkt}.parquet').sort_values('trade_date')
    last = df.iloc[-1]
    w20 = df.tail(20)
    w60 = df.tail(60)
    # 审美判据（可解释规则）
    v_ma20_now = df['volume'].tail(20).mean()
    v_ma20_prev = df['volume'].iloc[-40:-20].mean()
    shrink = v_ma20_now < v_ma20_prev * 0.8            # 量能萎缩
    pos_250 = (df['close'].tail(250) < last['close']).mean()
    range_hi, range_lo = w60['high'].max(), w60['low'].min()
    mid = (range_hi + range_lo) / 2
    verdict = []
    if shrink: verdict.append("量能萎缩(退潮)")
    if last['close'] > mid: verdict.append("中枢中轨上方")
    else: verdict.append("中枢中轨下方")
    verdict.append(f"250日分位{pos_250:.0%}")
    aesthetic = "NOT_BUY" if shrink else "WATCH"
    return {
        "close": float(last['close']), "pct_chg": float(last['pct_chg']),
        "hi_60": float(range_hi), "lo_60": float(range_lo),
        "vol_shrink_20v20": bool(shrink), "pos_in_60d_range": float((last['close']-range_lo)/(range_hi-range_lo)),
        "pct_rank_250d": round(float(pos_250), 2),
        "signals": verdict, "aesthetic": aesthetic,
        "aesthetic_rule": "放量启动/三买=PASS; 缩量中枢震荡=NOT_BUY; 临界=WATCH",
    }


# ── 线3：资金主体（席位/游资）──
def line3_seats(code: str) -> dict:
    _load_env()
    from neo4j import GraphDatabase
    from kg_schema import neo4j_conn
    uri, auth, db = neo4j_conn()
    drv = GraphDatabase.driver(uri, auth=auth)
    hot_kw = ('开源', '益田路', '东亚前海', '拉萨', '北一环', '量化', '中信上海溧阳', '华鑫')
    with drv.session() as s:
        r = s.run("""
MATCH (seat:Seat)-[t:TRADED_ON]->(:Security {symbol:$c})
WITH seat, sum(t.buy) AS buy, sum(t.sell) AS sell, sum(t.net_buy) AS net
ORDER BY abs(net) DESC LIMIT 12
RETURN seat.name AS seat, buy, sell, net""", c=code)
        seats = []
        for x in r:
            nm = x["seat"] or ''
            seats.append({"seat": nm, "net_wan": round((x["net"] or 0)/1e4, 0),
                          "hotmoney_flag": any(k in nm for k in hot_kw) or '拉萨' in nm})
        # 拉萨散户团
        lasa = sum(1 for s_ in seats if '拉萨' in s_['seat'])
    drv.close()
    hm_cnt = sum(1 for s_ in seats if s_["hotmoney_flag"] and '拉萨' not in s_['seat'])
    return {"seats": seats[:8], "hotmoney_seat_count": hm_cnt, "lasa_retail_count": lasa,
            "verdict": "游资主导" if hm_cnt >= 3 else ("游资参与" if hm_cnt else "无显著游资")}


def funnel_corroborate(code: str) -> dict:
    """K-ORG-120 四层漏斗：图形假设 → 事件/资金佐证三态。"""
    ops = operators(load_kline(code))
    prim = primary_hypothesis(ops)
    l1 = line1_events(code)
    l3 = line3_seats(code)
    # 层2：抢跑日 vs 后续公告耦合（14 日窗口）
    ev = {}
    for e in l1["events"]:
        d = str(e.get("date") or "")
        if d and d != "None":
            ev[d] = (e["title"] or "")[:40]
    corroborated, refuted = [], []
    for rd in ops["hypothesis_runup"].get("runup_days", []):
        coupled = [t for d, t in ev.items() if rd < d <= str(int(rd) + 30)]  # 简化：数字序即时间序
        if coupled:
            corroborated.append({"runup_day": rd, "followed_by": coupled[:2]})
    # 层3：席位方向 vs 假设
    seats = l3["seats"]
    net_sum = sum(s["net_wan"] for s in seats)
    if prim["primary"] in ("runup", "accumulate"):
        fund_state = "佐证" if net_sum > 0 else "证伪"
    elif prim["primary"] == "distribute":
        fund_state = "佐证" if net_sum < 0 else "证伪"
    else:
        fund_state = "无关"
    verdict = "定性成立" if (fund_state == "佐证" and (corroborated or prim["primary"] != "runup")) else ("存疑观察" if prim["primary"] != "idle" else "散手票")
    return {"primary_hypothesis": prim, "ops": ops,
            "layer2_event_couple": corroborated, "layer2_state": "佐证" if corroborated else ("待核" if prim["primary"]=="runup" else "无关"),
            "layer3_fund_state": f"{fund_state}(席位净额{net_sum:+.0f}万)",
            "verdict": verdict}


def profile(code: str) -> dict:
    out = {"code": code, "generated": __import__('time').strftime('%Y-%m-%d %H:%M'),
           "funnel": funnel_corroborate(code),
           "line1_events": line1_events(code),
           "line2_kline": line2_kline(code),
           "line3_seats": line3_seats(code)}
    try:
        from legion_pulse import heartbeat
        heartbeat("XSEC_PROFILE", "profile", f"{code} 漏斗报告 {out['funnel'].get('verdict','')}")
    except Exception:
        pass
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('code')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    p = profile(a.code)
    if a.json:
        print(json.dumps(p, ensure_ascii=False, indent=2))
    else:
        l1, l2, l3 = p['line1_events'], p['line2_kline'], p['line3_seats']
        print(f"═══ {a.code} 漏斗·三线标准件 v2 ═══")
        fu = p.get('funnel', {})
        pr = fu.get('primary_hypothesis', {})
        print(f"[漏斗] 主假设={pr.get('name','?')} | 事件{fu.get('layer2_state','?')} | 资金{fu.get('layer3_fund_state','?')} | 结论={fu.get('verdict','?')}")
        for c in fu.get('layer2_event_couple', [])[:2]:
            print(f"   · 抢跑{c['runup_day']} → 公告耦合: {c['followed_by'][0][:36] if c['followed_by'] else ''}")
        print(f"[线1 事件] 公告{len(l1['events'])}条 | 市场事件{len(l1['market_events'])}个 | 新闻{len(l1.get('news', []))}条")
        for e in l1['events'][:3]: print(f"   · {e['date']} {e['title'][:46]}")
        print(f"[线2 K线] 收{l2['close']} ({l2['pct_chg']:+.1f}%) | {l2['aesthetic']} | {'; '.join(l2['signals'])}")
        print(f"[线3 资金] {l3['verdict']} | 游资席位{l3['hotmoney_seat_count']} 拉萨{l3['lasa_retail_count']}")
        for s_ in l3['seats'][:4]:
            flag = '🔥' if s_['hotmoney_flag'] else '  '
            print(f"   {flag} {s_['seat'][:28]} 净{s_['net_wan']:+.0f}万")
