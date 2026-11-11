# -*- coding: utf-8 -*-
"""2026-11-11 性能剖析 + 故障演练（只读，不写生产产物、不改系统配置）。

① 热点计时：today_actions / zen_market 各自的墙钟耗时与产物大小；
② 故障演练：三源缺失、pool_state 损坏 JSON、sector_state 字段缺失，
   验证盘前「今日行动卡」全 soft 降级（不抛异常、不阻断 08:20 出报）；
③ 恢复验证：还原后 today_actions 能重新读到真实三源。
"""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8")
import premarket as pm  # noqa: E402

print("=== ① 热点计时 ===")
for label, fn in (("today_actions", pm.today_actions), ("zen_market", pm.zen_market)):
    t0 = time.perf_counter()
    out = fn()
    dt = time.perf_counter() - t0
    print("%-14s %7.3f s | json %7d B" % (label, dt, len(json.dumps(out, ensure_ascii=False, default=str))))

print()
print("=== ② 故障演练 ===")
orig = pm._latest_glob

# 场景 A：pool/sector 两源缺失（这两源走 _latest_glob 抽象）
pm._latest_glob = lambda *a, **k: None
r = pm.today_actions()
assert r["pool"] is None and r["sector"] is None, r
print("A pool/sector 全缺 -> 降级 OK（两者 None，不抛）")

# 场景 A2：shadow 源缺失（该源不走 _latest_glob，直接 glob data/shadow，
#   注入点不统一 = 本次演练发现的设计事实，记录为改进项，不改产线逻辑）
real_glob = pm.glob.glob


def _shadowless_glob(pattern, *a, **k):
    if "shadow" in pattern and "2*.json" in pattern:
        return []
    return real_glob(pattern, *a, **k)


pm.glob.glob = _shadowless_glob
try:
    r = pm.today_actions()
    assert r["shadow"] is None, r
    print("A2 shadow 目录全缺 -> 降级 OK（shadow=None，不抛）")
finally:
    pm.glob.glob = real_glob

# 场景 B：pool_state 文件存在但 JSON 损坏
with tempfile.TemporaryDirectory() as td:
    bad = os.path.join(td, "pool_state_20261111.json")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("{ broken json")
    pm._latest_glob = lambda prefix, *a, **k: (
        bad if prefix == "pool_state" else None
    )
    r = pm.today_actions()
    assert r["pool"] is None and r["sector"] is None, r
    print("B pool JSON 损坏  -> 降级 OK（pool=None，其余不连带失败）")

# 场景 C：sector_state 缺 r5 字段（真实字段口径坑：只有 asof、无 sectors）
with tempfile.TemporaryDirectory() as td:
    thin = os.path.join(td, "sector_state_20261111.json")
    with open(thin, "w", encoding="utf-8") as f:
        json.dump({"asof": "20261109"}, f)
    pm._latest_glob = lambda prefix, *a, **k: (
        thin if prefix == "sector_state" else None
    )
    r = pm.today_actions()
    assert r["sector"] is not None and r["sector"].get("top") == [], r
    print("C sector 字段缺失 -> 降级 OK（top=[]，asof 保留）")

pm._latest_glob = orig
print()
print("=== ③ 恢复验证 ===")
r = pm.today_actions()
print("pool=%s shadow=%s sector=%s" % (
    bool(r["pool"]), bool(r["shadow"]), bool(r["sector"])))
assert r["pool"] and r["shadow"] and r["sector"], "还原后三源未恢复"
print("演练通过：全链路无异常、还原后数据完整。")
