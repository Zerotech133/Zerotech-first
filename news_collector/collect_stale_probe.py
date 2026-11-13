# -*- coding: utf-8 -*-
"""单票陈旧成因探针（只读、不写盘）：回答「票本身没新帖」还是「采集漏取」。

用法：python collect_stale_probe.py irm 000543 002044 002381
      python collect_stale_probe.py sse 600422 600851 605003

对每只票做两件事：
  1) 现场重拉一次该票的问答接口，取「接口返回条数 + 最新时间戳」；
  2) 读本地已落盘 jsonl，取「本地条数 + 最新时间戳」。
两者最新时间一致 → 采集没丢；接口有更新而本地没有 → 漏取。

不写任何数据文件、不动队列、不重建 uid 映射（只读缓存）。
"""
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)


def _local_latest(kind, code):
    """本地 jsonl 的条数与最新 ts（读不到返回 (0, "")）。"""
    p = os.path.join(ROOT, "data", kind, "%s.jsonl" % code)
    if not os.path.exists(p):
        return 0, ""
    n, latest = 0, ""
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            n += 1
            ts = str(d.get("ts") or "")
            if ts > latest:
                latest = ts
    return n, latest


def _probe_irm(codes):
    import irm_collector as m
    cache = {}
    if os.path.exists(m.ORG_CACHE):
        cache = json.load(open(m.ORG_CACHE, encoding="utf-8"))
    s = m._session()
    out = []
    for c in codes:
        secid = m._resolve_secid(s, c, cache)
        if not secid:
            out.append({"code": c, "verdict": "SECID_FAIL"})
            continue
        try:
            rows, total, dropped = m.fetch_company(s, c, secid, 180, set())
        except Exception as e:
            out.append({"code": c, "verdict": "FETCH_ERR", "err": str(e)[:60]})
            continue
        api_n = len(rows)
        api_latest = max([str(r.get("ts") or "") for r in rows] or [""])
        loc_n, loc_latest = _local_latest("irm", c)
        newer = sum(1 for r in rows if str(r.get("ts") or "") > loc_latest)
        out.append({"code": c, "api_n": api_n, "api_latest": api_latest,
                    "local_n": loc_n, "local_latest": loc_latest,
                    "newer": newer, "total": total, "dropped": dropped})
    return out


def _probe_sse(codes):
    import sse_collector as m
    s = m._session()
    umap = m._load_uid_map(s)
    out = []
    for c in codes:
        info = umap.get(c)
        if not info:
            out.append({"code": c, "verdict": "NO_UID"})
            continue
        try:
            rows = m.fetch_company(s, c, info["uid"])
        except Exception as e:
            out.append({"code": c, "verdict": "FETCH_ERR", "err": str(e)[:60]})
            continue
        api_n = len(rows)
        api_latest = max([str(r.get("ts") or "") for r in rows] or [""])
        loc_n, loc_latest = _local_latest("sse", c)
        newer = sum(1 for r in rows if str(r.get("ts") or "") > loc_latest)
        out.append({"code": c, "api_n": api_n, "api_latest": api_latest,
                    "local_n": loc_n, "local_latest": loc_latest, "newer": newer})
    return out


def main():
    kind = sys.argv[1]
    codes = sys.argv[2:]
    fn = {"irm": _probe_irm, "sse": _probe_sse}[kind]
    res = fn(codes)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=== %s 陈旧探针 %s ===" % (kind, now))
    for r in res:
        if "verdict" in r and "api_n" not in r:
            print("%s: %s %s" % (r["code"], r["verdict"], r.get("err", "")))
            continue
        # 判定：接口最新 == 本地最新 → 采集未丢；接口新于本地 → 漏取
        same = (r["api_latest"] == r["local_latest"])
        verdict = "SAME(未漏取)" if same else "DIFF(看差值)"
        print("%s | 接口 %3d 条 最新 %s | 本地 %3d 条 最新 %s | 接口晚于本地 %d 条 | %s"
              % (r["code"], r["api_n"], r["api_latest"] or "-",
                 r["local_n"], r["local_latest"] or "-", r.get("newer", -1), verdict))


if __name__ == "__main__":
    main()
