# -*- coding: utf-8 -*-
"""席位层 —— 龙虎榜席位分层与匿名化（J-02，2026-11-13）

**只读本地快照** `data/hm_detail/*.jsonl` 与 `data/top_list/*.jsonl`
（由 `tools/top_list_fetch.py` 产出），**不联网、不走 xtcloud**。
这是与 `portal/jev_chain.py` 的分工：chain 是「接力」视角（联网直查、回答
「这个板块有没有接力链」），seat 是「分层」视角（快照只读、回答「谁在买」）。
两者判定同一套四类，但**输出对象不同**，不要互相替代。

## 三层结构

| 层 | 取值 | 说明 |
|---|---|---|
| L0 身份 | `inst` 机构 / `north` 北向 / `quant` 量化 / `hot` 游资大户 | 样本充足，可直接出统计 |
| L1 系别 | `geo` 地域系 / `behavior` 行为派 / `persona` 自然人 | 地域系与行为派可用；persona 只计数 |
| L2 营业部 | `hm_orgs` 原始名 | **下钻底线，不再往下** |

## 匿名化纪律（2026-11-13 裁定，判例 #40）

`hm_name` 里有一类称号是指向**自然人**的（章盟主 / T王 / 赵老哥 / 方新侠…，
其中还有实名）。按「席位边界只到营业部/席位、不下钻自然人」的裁定，
这类称号一律映射成 `PSN_<hash6>`：**参与计数、不展示原名**。

分类词表在 `config/seat_alias.json`，该文件**刻意不含任何真实人名**——
靠规则判定，拿不准一律按 `persona` 处理（多藏不泄漏）。
映射是单向哈希，输出里无法反推原名。

## 口径注意

- **称号 ≠ 营业部**：同一个 `hm_name` 可能挂多个 `hm_orgs`，反过来同一营业部
  名下既有量化也有游资（`国泰海通证券总部`）。所以按称号分类，**不能**按营业部分类。
- **披露席位净额 ≠ 龙虎榜净额**：席位明细只披露部分营业部（平均 2.8 席/只），
  总量只认 `ts_top_list`。本模块两个都给，但字段名分开（`seat_net` vs `list_net`）。
- **金额单位统一万元**（源表是元，落盘时已 /1e4 过的也用同一单位）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEAT_DIR = os.path.join(STATION, "data", "hm_detail")
TOP_DIR = os.path.join(STATION, "data", "top_list")
CFG_PATH = os.path.join(STATION, "config", "seat_alias.json")
CACHE_TTL_S = 300
_CACHE: dict = {}
_CFG: dict | None = None

KIND_LABEL = {"inst": "机构", "north": "北向", "quant": "量化", "hot": "游资/大户"}
GROUP_LABEL = {"inst": "机构", "north": "北向", "quant": "量化",
               "geo": "地域系", "behavior": "行为派", "persona": "自然人(匿名)"}


def _cfg() -> dict:
    """读分类配置。缺文件时用内置兜底，保证模块不会因为配置丢了就整层哑掉。"""
    global _CFG
    if _CFG is not None:
        return _CFG
    fallback = {"policy": "persona_hash", "hash_salt": "jev-seat-v1", "hash_len": 6,
                "l0": {"inst": ["机构专用"], "north": ["股通专用"],
                       "quant_words": ["量化"]},
                "l1": {"behavior_words": ["炒新", "敢死队", "低位挖掘", "打板", "超短"],
                       "geo_suffix": ["帮", "系", "路", "街", "道", "村", "口"],
                       "geo_words": []}}
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            j = json.load(f)
        fallback.update({k: v for k, v in j.items() if not k.startswith("_")})
    except Exception:  # noqa: BLE001
        pass
    _CFG = fallback
    return _CFG


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def classify(name) -> tuple:
    """称号 → (kind, group)。

    kind 是 L0 四类（用于「谁在买」的粗统计），group 是 L1 六组（更细）。
    判定顺序见 `config/seat_alias.json::_rule_order`，其中 behavior 先于 geo：
    「上海超短帮」同时含地名与打法，归 behavior 更贴语义。
    """
    n = str(name or "").strip()
    if not n:
        return "hot", "persona"
    c = _cfg()
    l0, l1 = c.get("l0") or {}, c.get("l1") or {}
    for w in (l0.get("inst") or []):
        if w and w in n:
            return "inst", "inst"
    for w in (l0.get("north") or []):
        if w and w in n:
            return "north", "north"
    if n.endswith("股通"):
        return "north", "north"
    for w in (l0.get("quant_words") or []):
        if w and w in n:
            return "quant", "quant"
    for w in (l1.get("behavior_words") or []):
        if w and w in n:
            return "hot", "behavior"
    for suf in (l1.get("geo_suffix") or []):
        if suf and n.endswith(suf):
            return "hot", "geo"
    for w in (l1.get("geo_words") or []):
        if w and w in n:
            return "hot", "geo"
    return "hot", "persona"


def alias_of(name, group=None) -> str:
    """称号 → 展示名。persona 一律 `PSN_<hash6>`，其余原样。"""
    n = str(name or "").strip()
    g = group or classify(n)[1]
    if g != "persona":
        return n
    c = _cfg()
    raw = ("%s|%s" % (c.get("hash_salt") or "", n)).encode("utf-8")
    h = hashlib.sha256(raw).hexdigest()
    return "PSN_%s" % h[:int(c.get("hash_len") or 6)]


def _cache_get(key):
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < CACHE_TTL_S:
        return hit[1]
    return None


def _cache_put(key, val):
    _CACHE[key] = (time.time(), val)
    return val


def _d8(day) -> str:
    return "".join(ch for ch in str(day or "") if ch.isdigit())[:8]


def _read_jsonl(path) -> list:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return out


def has_snapshot(day) -> dict:
    """快照体检（供页面/交叉卡判断「有没有数据」而不是「有没有钱」）。"""
    d = _d8(day)
    seat = os.path.join(SEAT_DIR, "%s.jsonl" % d)
    top = os.path.join(TOP_DIR, "%s.jsonl" % d)
    return {"day": d,
            "seat": os.path.exists(seat), "top": os.path.exists(top),
            "seat_bytes": os.path.getsize(seat) if os.path.exists(seat) else 0,
            "top_bytes": os.path.getsize(top) if os.path.exists(top) else 0}


def load_seats(day: str) -> dict:
    """读当日席位明细并按票聚合。

    → {has, day, by_code, n_codes, n_rows, n_persona_rows, note, errors}

    缺快照不算错：`has=False` + `note` 写补法，调用方照常出图（与 jev_flow /
    jev_social 同构）。
    """
    d = _d8(day)
    key = ("seats", d)
    hit = _cache_get(key)
    if hit is not None:
        return hit

    out = {"has": False, "day": d, "by_code": {}, "n_codes": 0, "n_rows": 0,
           "n_persona_rows": 0, "anon_policy": (_cfg().get("policy") or "persona_hash"),
           "note": None, "errors": []}
    path = os.path.join(SEAT_DIR, "%s.jsonl" % d)
    if not os.path.exists(path):
        out["note"] = ("当日无席位快照；补法：python tools/top_list_fetch.py --day %s"
                       "（龙虎榜 T+1 早间才全，当日查不到是正常现象）" % d)
        return _cache_put(key, out)

    try:
        rows = _read_jsonl(path)
    except Exception as exc:  # noqa: BLE001
        out["note"] = "席位快照解析失败: %s" % exc
        return _cache_put(key, out)

    by: dict = {}
    n_persona = 0
    for x in rows:
        code = str(x.get("ts_code") or "").strip()
        if not code:
            continue
        raw_name = x.get("hm_name")
        kind, group = classify(raw_name)
        disp = alias_of(raw_name, group)
        if group == "persona":
            n_persona += 1
        buy = _f(x.get("buy_amount")) / 1e4      # 元 → 万元
        sell = _f(x.get("sell_amount")) / 1e4
        s = by.setdefault(code, {"code": code, "c6": code.split(".")[0],
                                 "name": x.get("ts_name"),
                                 "buy": 0.0, "sell": 0.0,
                                 "by_kind": {}, "by_group": {}, "seats": []})
        s["buy"] += buy
        s["sell"] += sell
        for bucket, k in ((s["by_kind"], kind), (s["by_group"], group)):
            e = bucket.setdefault(k, {"buy": 0.0, "sell": 0.0, "n": 0})
            e["buy"] += buy
            e["sell"] += sell
            e["n"] += 1
        s["seats"].append({"display": disp, "kind": kind, "group": group,
                           "org": x.get("hm_orgs"),
                           "buy": round(buy, 1), "sell": round(sell, 1),
                           "net": round(buy - sell, 1)})

    for s in by.values():
        s["buy"] = round(s["buy"], 1)
        s["sell"] = round(s["sell"], 1)
        s["seat_net"] = round(s["buy"] - s["sell"], 1)
        s["n_seats"] = len(s["seats"])
        for bucket in (s["by_kind"], s["by_group"]):
            for e in bucket.values():
                e["buy"] = round(e["buy"], 1)
                e["sell"] = round(e["sell"], 1)
                e["net"] = round(e["buy"] - e["sell"], 1)
        s["seats"].sort(key=lambda z: -z["buy"])
        # 买方榜首按**买入额**排：净额最大的人可能是「买很多也卖很多」的
        # 换手席位，那不是「谁在接」的答案（jev_chain 同口径）。
        s["top_buyer"] = dict(s["seats"][0]) if s["seats"] and s["seats"][0]["buy"] > 0 else None
        sellers = sorted([z for z in s["seats"] if z["sell"] > 0], key=lambda z: -z["sell"])
        s["top_seller"] = dict(sellers[0]) if sellers else None

    out.update(has=True, by_code=by, n_codes=len(by), n_rows=len(rows),
               n_persona_rows=n_persona)
    out["note"] = ("席位按称号分类（不是营业部）；persona 类称号已匿名化为 "
                   "PSN_hash，只计数不展示原名；seats 里的 org 是营业部，下钻到此为止。")
    return _cache_put(key, out)


def seat_of(seats: dict, code6) -> dict:
    """单票席位（没有就空 dict，调用方可以直接 .get）。"""
    c6 = str(code6 or "")[:6]
    for code, v in (seats.get("by_code") or {}).items():
        if v.get("c6") == c6:
            return v
    return {}


def load_top(day: str) -> dict:
    """读当日龙虎榜汇总（只读快照）。

    → {has, day, by_code, n_codes, note}
    `reason` 原文保留在 `reason_raw`，归一留给 `jev_chain.reason_kind`（单一真相源），
    这里是**纯读取**，不做二次解释。
    """
    d = _d8(day)
    key = ("top", d)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    out = {"has": False, "day": d, "by_code": {}, "n_codes": 0, "note": None}
    path = os.path.join(TOP_DIR, "%s.jsonl" % d)
    if not os.path.exists(path):
        out["note"] = "当日无龙虎榜快照；补法：python tools/top_list_fetch.py --day %s" % d
        return _cache_put(key, out)
    try:
        rows = _read_jsonl(path)
    except Exception as exc:  # noqa: BLE001
        out["note"] = "龙虎榜快照解析失败: %s" % exc
        return _cache_put(key, out)
    by = {}
    for x in rows:
        code = str(x.get("ts_code") or "").strip()
        if not code:
            continue
        cur = by.get(code)
        if cur:      # 一票多规则上榜：净额只取一次（同一份榜单，不是两笔钱）
            if x.get("reason") not in cur["reasons_raw"]:
                cur["reasons_raw"].append(x.get("reason"))
            continue
        by[code] = {"code": code, "c6": code.split(".")[0], "name": x.get("name"),
                    "pct": round(_f(x.get("pct_change")), 2),
                    "list_net": round(_f(x.get("net_amount")) / 1e4, 1),
                    "l_buy": round(_f(x.get("l_buy")) / 1e4, 1),
                    "l_sell": round(_f(x.get("l_sell")) / 1e4, 1),
                    "amount": round(_f(x.get("amount")) / 1e4, 1),
                    "reasons_raw": [x.get("reason")]}
    for t in by.values():
        t["net_pct_amount"] = (round(t["list_net"] / t["amount"] * 100, 2)
                               if t["amount"] else None)
        t["reasons"] = list(t["reasons_raw"])
    out.update(has=True, by_code=by, n_codes=len(by))
    out["note"] = "总量只认龙虎榜（全量）；席位明细只披露部分营业部，两者可以方向相反。"
    return _cache_put(key, out)


def snapshot_days() -> list:
    """本地已落快照的交易日（升序）。用文件列表而不是猜交易日历——
    没有快照的日子本来就算不出来，猜了也没用。"""
    try:
        names = os.listdir(SEAT_DIR)
    except OSError:
        return []
    return sorted(n[:8] for n in names if n.endswith(".jsonl") and len(n) >= 13)


def seat_summary(day: str, window: int | None = None) -> dict:
    """席位全景 → L0/L1 汇总 + 营业部频次分布。

    `window=None` 单日；`window=N` 取截至 `day` 的最近 N 个**有快照**的交易日。

    为什么需要 window：单日只有约 50 个营业部、中位出现 1 次，只看单日会把
    「席位全是长尾」当成结论（11-11 单日 51% 只出现一次）；8 日累计后降到
    **36.7%**，才看得出哪些是真活跃席位。频次分布是判例 #41 的**证伪探针**：
    若绝大多数营业部只出现一次，则「席位族群」不成立，只保留 L0/L1 分层。
    """
    d = _d8(day)
    days = [d]
    if window and int(window) > 1:
        days = [x for x in snapshot_days() if x <= d][-int(window):] or [d]

    out = {"has": False, "day": d, "days": days, "window": len(days),
           "note": None, "n_codes": 0, "n_rows": 0, "n_persona_rows": 0,
           "anon_policy": _cfg().get("policy") or "persona_hash",
           "by_kind": {}, "by_group": {}, "org_freq": {}}

    freq: dict = {}
    seen_codes: dict = {}
    for dd in days:
        seats = load_seats(dd)
        if not seats.get("has"):
            continue
        out["has"] = True
        out["n_rows"] += seats.get("n_rows") or 0
        out["n_persona_rows"] += seats.get("n_persona_rows") or 0
        # 票数按 (票, 日) 去重：同一只票连着三天上榜是三天的事，不是一只票
        for code in seats["by_code"]:
            seen_codes[(code, dd)] = 1
        for s in seats["by_code"].values():
            for k, e in s["by_kind"].items():
                a = out["by_kind"].setdefault(k, {"buy": 0.0, "sell": 0.0, "n": 0,
                                                 "label": KIND_LABEL.get(k, k)})
                a["buy"] += e["buy"]
                a["sell"] += e["sell"]
                a["n"] += e["n"]
            for k, e in s["by_group"].items():
                a = out["by_group"].setdefault(k, {"buy": 0.0, "sell": 0.0, "n": 0,
                                                  "label": GROUP_LABEL.get(k, k)})
                a["buy"] += e["buy"]
                a["sell"] += e["sell"]
                a["n"] += e["n"]
            for z in s["seats"]:
                org = str(z.get("org") or "").strip()
                if org:
                    freq[org] = freq.get(org, 0) + 1

    if not out["has"]:
        out["note"] = "窗口内无任何席位快照；补法：python tools/top_list_fetch.py --day YYYYMMDD"
        return out

    out["n_codes"] = len(seen_codes)
    for bucket in (out["by_kind"], out["by_group"]):
        for a in bucket.values():
            a["buy"] = round(a["buy"], 1)
            a["sell"] = round(a["sell"], 1)
            a["net"] = round(a["buy"] - a["sell"], 1)

    n_orgs = len(freq)
    n_once = sum(1 for v in freq.values() if v == 1)
    n_ge5 = sum(1 for v in freq.values() if v >= 5)
    vals = sorted(freq.values())
    out["org_freq"] = {
        "n_orgs": n_orgs,
        "n_once": n_once,
        "pct_once": round(n_once / n_orgs * 100, 1) if n_orgs else None,
        "n_ge5": n_ge5,
        "pct_ge5": round(n_ge5 / n_orgs * 100, 1) if n_orgs else None,
        "median": vals[len(vals) // 2] if vals else None,
        "per_day": round(out["n_rows"] / len(days) / n_orgs, 2) if n_orgs else None,
        "top": sorted(freq.items(), key=lambda kv: -kv[1])[:10],
    }
    out["note"] = ("席位按称号分类（不是营业部）；persona 类称号已匿名化为 PSN_hash，"
                   "只计数不展示原名；票数按（票,日）去重。")
    return out
