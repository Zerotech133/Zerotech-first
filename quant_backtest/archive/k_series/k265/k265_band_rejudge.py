# -*- coding: utf-8 -*-
"""K265 回捞：0.8-0.95 带 pv1 正向判定 → pv2 重判（pv1 行保留，追加 pv2 行）。

用法：python -X utf8 k265_band_rejudge.py [--lo 0.8] [--hi 0.95] [--limit N]
断点续跑：shadow 中已有 pv2 行的对自动跳过。
"""
import json
import sys
import time

sys.path.insert(0, r"C:\项目\老田的AI助手\quant_backtest")
import k265_phase1 as P
from kg_client import KgClient

SHADOW = P.OUT
LO = 0.80
HI = 0.95
LIMIT = 0  # 0=不限

args = sys.argv[1:]


def _opt(name, default):
    return type(default)(args[args.index(name) + 1]) if name in args else default


LO = _opt("--lo", LO)
HI = _opt("--hi", HI)
LIMIT = _opt("--limit", LIMIT)

# 1) 影子现状：pv1 行（保留判断）+ pv2 已判对
pv1_rows = {}
pv2_done = set()
with open(SHADOW, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = (r["a"], r["b"])
        if r.get("pv") == 2:
            pv2_done.add(key)
        else:
            pv1_rows[key] = r

# 2) band 选择：pv1 正向判定且 conf∈[LO,HI)
band = []
for (a, b), r in pv1_rows.items():
    if (a, b) in pv2_done:
        continue
    ext = r.get("ext") or {}
    if ext.get("ok") is not True:
        continue
    c = ext.get("conf")
    if c is None or not (LO <= c < HI):
        continue
    band.append((a, b, r.get("overlap", 0), c))
band.sort(key=lambda x: -(x[3] or 0))
if LIMIT:
    band = band[:LIMIT]
print(f"[1] pv1 rows={len(pv1_rows)} pv2_done={len(pv2_done)} "
      f"band[{LO},{HI}) todo={len(band)}", flush=True)
if not band:
    print("[1b] nothing to do")
    sys.exit(0)

# 3) 概念成员（重判 prompt 需要）
cli = KgClient(retry=1, retry_wait=1)
cy = ("MATCH (c:Concept)<-[:MAPS_TO]-(s:Security) "
      "RETURN c.name AS name, collect(s.name) AS members, count(s) AS n")
rows = [dict(r) for r in cli._session().run(cy)]
keep = {r["name"]: set(r["members"]) for r in rows
        if P.MIN_OV <= r["n"] <= P.MAX_MEMBERS
        and not P.NOISE_PAT.search(r["name"])}
print(f"[2] concepts usable: {len(keep)}", flush=True)

missing = [(a, b, ov, c) for a, b, ov, c in band
           if a not in keep or b not in keep]
if missing:
    print(f"[2b] WARN concept-miss pairs skipped: {len(missing)}")
    band = [(a, b, ov, c) for a, b, ov, c in band
            if a in keep and b in keep]

# 4) 重判循环
t0 = time.time()
n_ok = 0
with open(SHADOW, "a", encoding="utf-8") as f:
    for i, (a, b, ov, oldc) in enumerate(band, 1):
        ext = P.llm_pair(a, ", ".join(sorted(keep[a])[:5]),
                         b, ", ".join(sorted(keep[b])[:5]))
        rec = {"a": a, "b": b, "overlap": ov, "ext": ext, "pv": 2,
               "rj": 1, "old_conf": oldc}
        if ext.get("ok"):
            n_ok += 1
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        if i % 30 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(band)} elapsed={el:.0f}s avg={el/i:.1f}s/对",
                  flush=True)

print(f"[3] rejudge done: {len(band)} pairs, ok={n_ok} "
      f"({time.time()-t0:.0f}s)", flush=True)
