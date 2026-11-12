# -*- coding: utf-8 -*-
"""缺口成因探针（只读、不写盘）：对缺口代码做单点抓取，回答「无页面」还是「漏取」。

用法：python collect_gap_probe.py sse 600050 600085
      python collect_gap_probe.py irm 300984
不写任何数据文件、不动队列、不重建 uid 映射（只读缓存）。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def probe_sse(codes):
    import sse_collector as m
    umap = m._load_uid_map(m._session())
    s = m._session()
    for c in codes:
        info = umap.get(c)
        if not info:
            print("sse %s: uid_map 无收录 → 页面不存在/未开通" % c)
            continue
        rows = m.fetch_company(s, c, info["uid"])
        print("sse %s %-10s uid=%-8s 拉 %d 条（答 %d）"
              % (c, info.get("name", ""), info["uid"], len(rows),
                 sum(1 for r in rows if r.get("has_reply"))))


def probe_irm(codes):
    import irm_collector as m
    cache = {}
    if os.path.exists(m.ORG_CACHE):
        cache = json.load(open(m.ORG_CACHE, encoding="utf-8"))
    s = m._session()
    for c in codes:
        secid = m._resolve_secid(s, c, cache)
        if not secid:
            print("irm %s: secid 解析失败 → 未收录/接口变更" % c)
            continue
        rows, total, dropped = m.fetch_company(s, c, secid, 180, set())
        print("irm %s secid=%s total=%s 抓 %d 条 丢弃 %d"
              % (c, secid, total, len(rows), dropped))


if __name__ == "__main__":
    kind = sys.argv[1]
    codes = sys.argv[2:]
    {"sse": probe_sse, "irm": probe_irm}[kind](codes)
