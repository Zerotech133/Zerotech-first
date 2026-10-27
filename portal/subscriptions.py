# -*- coding: utf-8 -*-
"""K-STOCK-122 M3 订阅与组合数据层（单用户版，纯标准库）。

订阅=扩展 hires 雇佣语义（方案拍板 ①）：订阅策略集生成组合，
组合维度可聘交易员（M4）或手动模拟。价格字段现在埋，商业化只改值。

存储：portal/data/strategy_subscriptions.json
  {"portfolios":[{"id":"pf_001","name":"我的组合","strategies":[...],
                  "price":0,"paid":false,"trader":null,"created_at":...}]}

CLI 自测：python portal/subscriptions.py
"""

import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REG = os.path.join(HERE, "strategies.json")
PATH = os.path.join(HERE, "data", "strategy_subscriptions.json")
DEFAULT_PF = "pf_001"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _reg_ids():
    with open(REG, encoding="utf-8") as f:
        return [s["id"] for s in (json.load(f).get("strategies") or [])]


def _load():
    if os.path.exists(PATH):
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"portfolios": []}


def _save(d):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


def _pf(d, pid=DEFAULT_PF):
    for p in d["portfolios"]:
        if p["id"] == pid:
            return p
    p = {
        "id": pid,
        "name": "我的组合",
        "strategies": [],
        "price": 0,
        "paid": False,
        "trader": None,
        "note": "免费体验 · 未来商业化收费",
        "created_at": _now(),
    }
    d["portfolios"].append(p)
    return p


def portfolios():
    """组合列表（附每策略名/tagline 便于前端渲染）。"""
    reg = {}
    with open(REG, encoding="utf-8") as f:
        for s in json.load(f).get("strategies") or []:
            reg[s["id"]] = {
                "name": s.get("name"),
                "tagline": s.get("tagline"),
                "status": s.get("status"),
            }
    d = _load()
    for p in d["portfolios"]:
        p["_detail"] = [reg[sid] for sid in p["strategies"] if sid in reg]
        p["_shadow"] = [
            sid for sid in p["strategies"] if reg.get(sid, {}).get("status") == "shadow"
        ]
    return {"ok": True, "portfolios": d["portfolios"]}


def subscribe(strategy_id: str, action: str = "add", pid: str = DEFAULT_PF) -> dict:
    """订阅/退订（action: add|remove|toggle）。非法策略 id 拒绝。"""
    ids = _reg_ids()
    if strategy_id not in ids:
        return {"ok": False, "error": "strategy not found: %s" % strategy_id}
    d = _load()
    p = _pf(d, pid)
    has = strategy_id in p["strategies"]
    if action == "toggle":
        action = "remove" if has else "add"
    if action == "add" and not has:
        p["strategies"].append(strategy_id)
    elif action == "remove" and has:
        p["strategies"].remove(strategy_id)
    _save(d)
    return {
        "ok": True,
        "action": action,
        "portfolio": p["id"],
        "strategies": p["strategies"],
        "at": _now(),
    }


def bind_trader(trader: str, pid: str = DEFAULT_PF) -> dict:
    """M4 分支①：组合聘交易员（记 portfolio.trader；实际 hire 由
    trainee.hire 落 hires.json，本函数只做组合侧绑定）。"""
    d = _load()
    p = _pf(d, pid)
    p["trader"] = trader
    p["hired_at"] = _now()
    _save(d)
    return {
        "ok": True,
        "portfolio": p["id"],
        "trader": trader,
        "strategies": p["strategies"],
    }


def unbind_trader(pid: str = DEFAULT_PF) -> dict:
    d = _load()
    p = _pf(d, pid)
    p["trader"] = None
    _save(d)
    return {"ok": True, "portfolio": p["id"], "trader": None}


if __name__ == "__main__":
    print(json.dumps(portfolios(), ensure_ascii=False, indent=1)[:400])
