"""门 N1 决策守卫实证：把 universe=market（A股-only）套到真实研究池。

只读分析脚本（不写、不删、不发网络、不触生产）。
目的：验证「老田已拍板的 N1 universe=market 决策」对 STRAT1 硬阻塞
（真实研究池 T2 混入非 A 股代码）的实际解阻塞效力。

口径：
- A 股 = 6 位数字 + .SH / .SZ（与 backtest_engine_v2.py:1178 market 域一致，
  全市场 5000+ 剔 ST / 北交所）。
- 北交所（8xxxxx/4xxxxx 8 位）在 market 域属「剔北交所」范畴，单独计数警示，
  本真实池未见，仅做防御性分类。
- 非 A 股（字母 ticker，如 ON/RVSB/TXN/NNBR = 美/外股）即污染，应被过滤。

输出：按 tier 统计过滤前后数量、被剔除的污染符号清单、还原后分布，
并给出「若决策在真实链强制生效，STRAT1 污染是否清零」的结论。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PY / "strategy_layer"))

from liudu_universe import SELECTED_UNIVERSE, resolve_universe  # noqa: E402

ASHRE = re.compile(r"^\d{6}\.(SH|SZ)$", re.IGNORECASE)
# 北交所：以 .BJ 后缀标识（含 920190.BJ 这类 6 位+后缀写法），market 域同样剔除。
BJRE = re.compile(r"\.BJ$", re.IGNORECASE)

REAL_POOL = PY / "reports" / "xunyou_pool_20261020.json"


def classify(symbol: str) -> str:
    """分类单只符号：ashare / bj / foreign。"""
    if ASHRE.match(symbol):
        return "ashare"
    if BJRE.search(symbol):
        return "bj"
    return "foreign"


def main(trade_date: str = "20261020") -> int:
    pool_path = REAL_POOL
    if not pool_path.exists():
        print(f"[ERR] 真实研究池不存在: {pool_path}", file=sys.stderr)
        return 2

    data = json.loads(pool_path.read_text(encoding="utf-8"))
    pool = data.get("pool", [])
    print(f"=== N1 守卫实证：universe={SELECTED_UNIVERSE}（resolve→{resolve_universe(SELECTED_UNIVERSE)}）===")
    print(f"真实研究池 {pool_path.name}：总 {len(pool)} 只")

    # 全量分类
    cats = {"ashare": 0, "bj": 0, "foreign": 0}
    foreign_by_tier: dict[str, list[str]] = {}
    for r in pool:
        sym = str(r.get("symbol", ""))
        c = classify(sym)
        cats[c] += 1
        if c in ("foreign", "bj"):
            t = r.get("tier") or "UNTIERED"
            foreign_by_tier.setdefault(t, []).append(sym)

    print(f"  分类：A股={cats['ashare']}  北交所={cats['bj']}  非A股(污染)={cats['foreign']}")

    # 按 tier 过滤前后对照
    from collections import Counter
    before = Counter(r.get("tier") or "UNTIERED" for r in pool)
    kept = [r for r in pool if classify(str(r.get("symbol", ""))) == "ashare"]
    after = Counter(r.get("tier") or "UNTIERED" for r in kept)
    removed = [r for r in pool if classify(str(r.get("symbol", ""))) != "ashare"]

    print("\n-- 按 tier 过滤前后 --")
    all_tiers = sorted(set(before) | set(after))
    for t in all_tiers:
        print(f"  {t:10s}: 前 {before.get(t,0):3d}  ->  后 {after.get(t,0):3d}  (剔除 {before.get(t,0)-after.get(t,0)})")

    print(f"\n-- 被剔除的污染/北交符号（共 {len(removed)}）--")
    for r in removed:
        sym = str(r.get("symbol", ""))
        print(f"  {sym:10s}  tier={r.get('tier') or 'UNTIERED':8s}  action={r.get('action')}  分类={classify(sym)}")

    # 结论
    print("\n=== 结论 ===")
    if cats["foreign"] == 0 and cats["bj"] == 0:
        print("  ✅ 真实池无非 A 股污染；N1=market 守卫无额外剔除需求。")
    else:
        print(f"  ⚠️ 真实池存在 {cats['foreign']} 个非 A 股 + {cats['bj']} 个北交所符号。")
        print("  ✅ 若 N1=market 在真实链强制生效（仅放 A 股），上述污染将被剔除，")
        print("     STRAT1 硬阻塞（非 A 股污染）在样本层面清零——但仍需 N5 源治理")
        print("     从源头消除外源 ticker 泄漏，否则下一轮研究池会再次污染。")

    print("\n注：本脚本仅做决策守卫实证，不修改任何文件、不触生产、不写 signal_pool。")
    return 0


if __name__ == "__main__":
    td = sys.argv[1] if len(sys.argv) > 1 else "20261020"
    raise SystemExit(main(td))
