# -*- coding: utf-8 -*-
"""J2 预览（只读）：复用 eval_shadow 内部逻辑，min-days 门提前放开出 rho 预览。
不写报告、不碰账本。产出仅打印。K-STOCK-326 后续（10-13 晚）。
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "agent_platform"))
import eval_shadow as es  # noqa: E402

rows = []
with open(es.LEDGER, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))
done = [r for r in rows if r.get("status") == "done"]
failed = [r for r in rows if r.get("status") == "failed"]
days = sorted({r["date"] for r in done})
print("账本：done=%d failed=%d 交易日=%d（%s → %s）" % (
    len(done), len(failed), len(days), days[0] if days else "-", days[-1] if days else "-"))

per_symbol_latest = {}
import time as _t
today = _t.strftime("%Y-%m-%d")
skipped = {}
for r in done:
    if r["date"] >= today:
        continue  # 当日决策无后验收益（quote_snapshot 现价=决策价），取昨日及以前的最近一笔
    per_symbol_latest[r["symbol"]] = r
for r in done:  # 被 skip 的票及原因（含仅当日有决策的票）
    if r["symbol"] not in per_symbol_latest:
        skipped.setdefault(r["symbol"], []).append(r["date"])
if skipped:
    print("[warn] 无昨日前决策的票:", {k: v for k, v in skipped.items()})

scores, confs, rets, detail = [], [], [], []
for sym, r in per_symbol_latest.items():
    if r.get("score") is None or not r.get("price_at_decision"):
        continue
    try:
        now = es.quote_snapshot(sym)["price"]
        ret = (now / r["price_at_decision"] - 1) * 100
        scores.append(r["score"])
        confs.append(r.get("confidence") or 0)
        rets.append(ret)
        detail.append((sym, r.get("name", ""), r["score"], r.get("confidence") or 0,
                       r["price_at_decision"], now, ret, r.get("recommendation", "?")))
    except Exception as e:  # noqa: BLE001
        print("  [warn] %s 后验价获取失败: %s" % (sym, e))

print("\n逐票明细（按后验收益降序，每票=最近决策）：")
print("%-8s %-10s %-5s %-6s %-10s %-10s %+8s %s" % (
    "票", "名称", "score", "conf", "决策价", "现价", "后验%", "决策"))
for sym, name, sc, cf, px0, px1, rt, rec in sorted(detail, key=lambda d: -d[6]):
    print("%-8s %-10s %-5s %-6s %-10s %-10s %+8.2f %s" % (
        sym, name[:10], sc, "%.2f" % cf, px0, px1, rt, rec))

rho_s = es.spearman(scores, rets) if len(scores) >= 3 else None
rho_c = es.spearman(confs, rets) if len(confs) >= 3 else None
print("\n[J2 预览·第%d日口径] score-vs-ret rho=%s | conf-vs-ret rho=%s | n=%d" % (
    len(days), None if rho_s is None else round(rho_s, 3),
    None if rho_c is None else round(rho_c, 3), len(scores)))
print("注：裁决值仍以 10-14 收盘满第 5 交易日后 eval_shadow 正式跑为准；"
      "本预览只读零写入。")
