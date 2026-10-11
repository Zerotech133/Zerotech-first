# -*- coding: utf-8 -*-
"""K-STOCK-122 M4.3 策略产物适配器：todays_picks.json → poc run 格式。

chanlun/ninedim/strong/weak/jev-gate 等策略不走 poc_* 产物链，其信号已按
策略 id 聚合在 portal/data/todays_picks.json（sources[].s 字段）。本适配器
把单策略命中转成 exec_run 可消费的 poc list（{stock, asof, outputs.D1}），
使组合（portfolio.py）能吃到全部已聚合策略的信号。

口径：
- 动作：tag 含「卖」或=「回避」→ SELL；其余（1买/2买/3买/九维出分/板块领涨
  /逆板块强势等）→ BUY；
- 置信度按 tier：T1=0.9 / T2=0.7 / CROWD=0.5（conf_floor 门槛照常过滤）；
- 缓存：当日一份 DATA_DIR/poc_adapter_<sid>_<YYYYMMDD>.json，同日重生成覆盖
  （todays_picks 每日刷新，exec_run 的 replan 语义照常接管旧 pending）。

CLI 自测：python picks_adapter.py chanlun
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import trainee                                  # noqa: E402
from engine import _norm_stock                  # noqa: E402

PICKS = HERE.parent.parent / "portal" / "data" / "todays_picks.json"
SELL_TAGS = ("1卖", "2卖", "3卖", "回避")
TIER_CONF = {"T1": 0.9, "T2": 0.7, "CROWD": 0.5}
ADAPTER_STRATS = ("chanlun", "ninedim", "strong", "weak", "jev-gate")


def build_run(sid: str) -> Path:
    """生成（或取当日缓存）sid 策略的 poc run 文件，返回路径。"""
    if sid not in ADAPTER_STRATS:
        raise trainee.Reject("picks_adapter 不覆盖策略: %s（可覆盖 %s）"
                             % (sid, "/".join(ADAPTER_STRATS)))
    if not PICKS.exists():
        raise trainee.Reject("todays_picks.json 不存在")
    d = json.loads(PICKS.read_text(encoding="utf-8"))
    asof = d.get("generated_at") or datetime.now().isoformat(timespec="seconds")
    items = []
    for s in d.get("stocks") or []:
        srcs = [x for x in (s.get("sources") or []) if x.get("s") == sid]
        if not srcs:
            continue
        code = _norm_stock(str(s.get("code") or ""))
        if not code:
            continue
        src = srcs[0]
        tag = src.get("tag") or ""
        action = "SELL" if any(t in tag for t in SELL_TAGS) else "BUY"
        items.append({
            "stock": code,
            "asof": asof,
            "outputs": {"D1": {
                "action": action,
                "confidence": TIER_CONF.get(s.get("tier"), 0.5),
                "reason": "%s %s（%s）" % (sid, tag, s.get("tier") or "?"),
            }},
            "_tier": s.get("tier"),
        })
    day = datetime.now().strftime("%Y%m%d")
    fp = trainee.DATA_DIR / ("poc_adapter_%s_%s.json" % (sid, day))
    fp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return fp


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "chanlun"
    fp = build_run(sid)
    items = json.loads(fp.read_text(encoding="utf-8"))
    print("%s: %d items → %s" % (sid, len(items), fp.name))
    for it in items[:3]:
        print(" ", it["stock"], it["outputs"]["D1"]["action"],
              it["outputs"]["D1"]["confidence"],
              it["outputs"]["D1"]["reason"])
