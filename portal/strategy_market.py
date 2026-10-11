# -*- coding: utf-8 -*-
"""K-STOCK-122 策略市场数据层（纯只读，零第三方依赖）。

M1：market()    → 12 策略卡全量（strategies.json 直读 + 方法论标签 + 订阅数）
M2：detail(sid) → 单策略下钻（静态档案 + 动态信号样本，来自 todays_picks.json）

CLI 自测：python portal/strategy_market.py [strategy_id]
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REG = os.path.join(HERE, "strategies.json")
PICKS = os.path.join(HERE, "data", "todays_picks.json")
SUBS = os.path.join(HERE, "data", "strategy_subscriptions.json")

# 方法论标签（K-STOCK-122 方案 §五，老田口述口径固化）
METHOD_TAGS = {
    "double_knife": ["尾盘买入", "十字星+MACD", "Regime 分层"],
    "chanlun": ["缠论三类买卖点"],
    "qpzm": ["微动前置信号"],
    "ninedim": ["RPS50 强势池", "多维评分"],
    "weak": ["板块强弱两只眼", "弱于识别"],
    "strong": ["板块强弱两只眼", "领涨识别"],
    "linkage": ["个股×板块×大盘联动"],
    "cross": ["多策略共振"],
    "jev-gate": ["AI 复核门", "五原子判断"],
    "backtest": ["回测/寻优底座"],
    "quantdinger": ["开源量化平台"],
    "zhice": ["四市场调度"],
}
# todays_picks sources.tag → strategy_id（动态战绩映射）
TAG_MAP = {
    "1买": "chanlun", "2买": "chanlun", "3买": "chanlun",
    "1卖": "chanlun", "2卖": "chanlun", "3卖": "chanlun",
    "九维出分": "ninedim", "九维候选(影子未出分)": "ninedim",
    "板块领涨": "strong", "强失速": "strong",
    "强失速+深度垫底": "strong", "逆板块强势": "strong",
    "深度垫底": "weak", "回避": "jev-gate",
}


def _read(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _subs():
    d = _read(SUBS) or {}
    return d.get("portfolios") or []


def _dynamic_hits():
    """按 strategy_id 聚合今日选股命中：{sid: {"n": x, "samples":[...]}}"""
    d = _read(PICKS) or {}
    out = {}
    for s in d.get("stocks") or []:
        for src in s.get("sources") or []:
            sid = TAG_MAP.get(src.get("tag") or "")
            if not sid:
                continue
            e = out.setdefault(sid, {"n": 0, "samples": []})
            e["n"] += 1
            if len(e["samples"]) < 6:
                e["samples"].append({
                    "code": (s.get("code") or "").split(".")[0],
                    "name": s.get("name") or "",
                    "industry": s.get("industry") or "-",
                    "tag": src.get("tag") or "",
                    "tier": s.get("tier") or "",
                })
    return out


def _card(s, hits, subs_n):
    return {
        "id": s.get("id"),
        "name": s.get("name"),
        "tagline": s.get("tagline"),
        "status": s.get("status"),
        "status_note": s.get("status_note"),
        "badge": s.get("badge"),
        "href": s.get("href"),
        "method_tags": METHOD_TAGS.get(s.get("id"), []),
        "hits_today": (hits.get(s.get("id")) or {}).get("n", 0),
        "subscribers": subs_n,
    }


def market():
    """M1：策略市场全量卡片（数量必须=12，方案验收硬校验）。"""
    reg = _read(REG) or {}
    hits = _dynamic_hits()
    subs = _subs()
    cards = [_card(s, hits, sum(
        1 for pf in subs if s.get("id") in (pf.get("strategies") or [])))
        for s in reg.get("strategies") or []]
    return {"ok": True, "count": len(cards),
            "live": sum(1 for c in cards if c["status"] == "live"),
            "shadow": sum(1 for c in cards if c["status"] == "shadow"),
            "strategies": cards,
            "note": "shadow 态策略为研究验证中，不构成选股依据"}


def detail(sid):
    """M2：单策略下钻——静态档案 + 今日命中样本。"""
    reg = _read(REG) or {}
    s = next((x for x in reg.get("strategies") or []
              if x.get("id") == sid), None)
    if not s:
        return {"ok": False, "error": "strategy not found: %s" % sid}
    hits = _dynamic_hits().get(sid) or {"n": 0, "samples": []}
    return {"ok": True, "strategy": s,
            "method_tags": METHOD_TAGS.get(sid, []),
            "dynamic": {"source": "todays_picks.json", **hits}}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(json.dumps(detail(sys.argv[1]), ensure_ascii=False, indent=1))
    else:
        m = market()
        print("count=%d live=%d shadow=%d" % (
            m["count"], m["live"], m["shadow"]))
        for c in m["strategies"]:
            print("%-12s %-10s %s | 今日命中 %s" % (
                c["id"], c["status"], c["name"], c["hits_today"]))
