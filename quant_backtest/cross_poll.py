# -*- coding: utf-8 -*-
"""多策略共振聚合器（策略集市基础设施层 · 只读聚合，不改任何策略口径）。

设计原则（对齐项目纪律）：
· 声明式策略注册表 STRATEGIES——新增策略=加一条注册（产物路径/取数/
  方向语义/徽标/详情页 URL），聚合与页面自动跟随，零改框架；
· 展示层聚合：不修改任何策略的入池/推送/拦截规则，影子策略保持影子；
· 方向语义三分：long（看多）/ short（看空或回避）/ neutral（中性观察），
  冲突=同票同时存在 long 与 short；
· 代码归一化：以 tushare 格式为基准（600519.SH / PLTR / 00700 / BTC/USDT），
  watchlist 前缀（sh/sz/bj/rt_hk/gb_）在入口归一。

产物消费（全部只读）：
· RPS 共振    reports/rps_latest.json     res[].code/name/market/score/action/sigs
· 四策略信号  同上 res[].sigs（trend_follow/ma_cross/rsi_reversal/boll_breakout）
· 缠论预警池  data/chanlun_pool.jsonl     近 90 天最新信号/票 + 侧面标签
· 九维评分    data/nine_dim_scores.json   rows[].code/name/score/cov（观察工具）
"""
import json
import os
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
REPORTS = os.path.join(HERE, "reports")

CHANLUN_LOOKBACK_DAYS = 90      # 缠论信号有效窗口（检出日起）
NINE_DIM_LONG = 80              # 九维展示阈值（观察工具口径，非买入建议）
SIG4_LONG = 3                   # 四策略持仓数 ≥3 记 long
_CACHE = {"t": 0, "d": None}
CACHE_TTL = 60                  # 门户请求级缓存（秒）


def norm_code(raw):
    """watchlist/tushare 代码 → 统一格式（sh600519→600519.SH、
    gb_pltr→PLTR、rt_hk00700→00700；其余原样）。"""
    c = str(raw or "").strip()
    if c.startswith("sh"):
        return c[2:] + ".SH"
    if c.startswith("sz") or c.startswith("bj"):
        return c[2:] + "." + c[:2].upper()
    if c.startswith("rt_hk"):
        try:
            return "%05d" % int(c[5:])
        except ValueError:
            return c
    if c.startswith("gb_"):
        return c[3:].upper().replace(".", "_")
    return c


def _load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _load_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        pass
    return out


# ── 策略取数器：各自返回 {code: hit}，hit={dir,label,extra} ──
def _src_qpzm():
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json"))
    out = {}
    for r in snap.get("res", []):
        act = str(r.get("action", ""))
        base = act.replace("（环境闸门）", "")
        d = ("long" if base == "买入" else
             "short" if base == "回避" else "neutral")
        out[norm_code(r.get("code"))] = {
            "dir": d,
            "label": "共振%d·%s" % (r.get("score") or 0, base),
            "name": r.get("name") or "",
            "market": r.get("market") or ""}
    return out


def _src_sig4():
    snap = _load_json(os.path.join(REPORTS, "rps_latest.json"))
    out = {}
    for r in snap.get("res", []):
        n = int(r.get("longs") or 0)
        out[norm_code(r.get("code"))] = {
            "dir": "long" if n >= SIG4_LONG else "neutral",
            "label": "4策略%d/4" % n,
            "name": r.get("name") or "",
            "market": r.get("market") or ""}
    return out


def _src_chanlun():
    # 数据源：优先 SQLite，fallback JSONL（双写期过渡）
    pool = _src_chanlun_load()
    cutoff = (datetime.now()
              - timedelta(days=CHANLUN_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    latest = {}
    for r in pool:
        dd = str(r.get("detect_date") or r.get("dt") or "")
        if dd < cutoff:
            continue
        code = norm_code(r.get("code"))
        if code not in latest or dd > latest[code][0]:
            latest[code] = (dd, r)
    out = {}
    for code, (_, r) in latest.items():
        t = str(r.get("type") or "")
        d = "long" if "买" in t else "short" if "卖" in t else "neutral"
        tags = "".join(lb for lb, k in
                       (("M", "mbei"), ("盘", "bei2"), ("量", "vv"),
                        ("趋", "tbe")) if r.get(k) is True)
        out[code] = {"dir": d,
                     "label": "缠论%s%s" % (t, ("·" + tags) if tags else ""),
                     "name": r.get("name") or "",
                     "market": r.get("market") or "CN",
                     "dt": str(r.get("dt") or "")}
    return out


def _src_chanlun_load():
    """优先从 SQLite 读取近 CHANLUN_LOOKBACK_DAYS 天信号，失败回退 JSONL。

    SQLite 路径走 db.pool_query_recent（已按 code 保留最新）；JSONL 路径
    全量加载后由 _src_chanlun 下游过滤。两条路径输出结构一致。
    """
    try:
        import db as _db
        conn = _db.get_conn()
        try:
            rows = _db.pool_query_recent(conn, days=CHANLUN_LOOKBACK_DAYS)
            if rows:
                return rows
        finally:
            conn.close()
    except Exception:
        pass
    return _load_jsonl(os.path.join(DATA, "chanlun_pool.jsonl"))


def _src_9dim():
    snap = _load_json(os.path.join(DATA, "nine_dim_scores.json"))
    out = {}
    for r in snap.get("rows", []):
        sc = r.get("score")
        if sc is None or sc < NINE_DIM_LONG:
            continue
        out[norm_code(r.get("code"))] = {
            "dir": "long",
            "label": "九维%.0f" % sc,
            "name": r.get("name") or "",
            "market": "CN"}
    return out


# ── 策略注册表：新增策略在此加一条即可 ─────────────────────
STRATEGIES = [
    {"id": "qpzm", "name": "RPS共振", "badge": "共振", "color": "#3fb950",
     "icon": "🌊", "url": "/bt-qpzm", "markets": "CN/US/HK/CRYPTO",
     "loader": _src_qpzm},
    {"id": "sig4", "name": "四策略信号", "badge": "4策略", "color": "#58a6ff",
     "icon": "📊", "url": "/bt-signals", "markets": "自选池",
     "loader": _src_sig4},
    {"id": "chanlun", "name": "缠论结构", "badge": "缠论", "color": "#f778ba",
     "icon": "🧭", "url": "/bt-chanlun-pool", "markets": "CN",
     "loader": _src_chanlun},
    {"id": "9dim", "name": "九维评分", "badge": "九维", "color": "#d29922",
     "icon": "🧮", "url": "/bt-9dim", "markets": "CN", "loader": _src_9dim},
]


def build_cross(force=False):
    """聚合全部注册策略 → {generated_at, strategies, stocks, matrix}。
    stocks[]: code/name/market/hits[{sid,dir,label,url,badge,color}]/n_hit/
    n_long/n_short/state(long=全多 short=全空 mixed=冲突 solo=单策略)"""
    if not force and _CACHE["d"] and time.time() - _CACHE["t"] < CACHE_TTL:
        return _CACHE["d"]
    per = {}
    for s in STRATEGIES:
        try:
            per[s["id"]] = s["loader"]()
        except Exception:
            per[s["id"]] = {}
    stocks = {}
    for s in STRATEGIES:
        sid = s["id"]
        for code, hit in per[sid].items():
            st = stocks.setdefault(code, {
                "code": code, "name": hit.get("name") or "",
                "market": hit.get("market") or "", "hits": []})
            if not st["name"] and hit.get("name"):
                st["name"] = hit["name"]
            st["hits"].append({"sid": sid, "dir": hit["dir"],
                               "label": hit["label"], "url": s["url"],
                               "badge": s["badge"], "color": s["color"],
                               "dt": hit.get("dt", "")})
    for st in stocks.values():
        dirs = [h["dir"] for h in st["hits"]]
        st["n_hit"] = len(st["hits"])
        st["n_long"] = dirs.count("long")
        st["n_short"] = dirs.count("short")
        st["state"] = ("mixed" if st["n_long"] and st["n_short"] else
                       "long" if st["n_long"] else
                       "short" if st["n_short"] else "neutral")
        st["hits"].sort(key=lambda h: h["sid"])
    # 策略两两交集矩阵
    matrix = {a["id"]: {b["id"]: 0 for b in STRATEGIES} for a in STRATEGIES}
    for st in stocks.values():
        sids = {h["sid"] for h in st["hits"]}
        for a in sids:
            for b in sids:
                matrix[a][b] += 1
    out = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "strategies": [{k: v for k, v in s.items() if k != "loader"}
                          for s in STRATEGIES],
           "n_per": {sid: len(v) for sid, v in per.items()},
           "stocks": sorted(stocks.values(),
                            key=lambda x: (-x["n_hit"], -x["n_long"],
                                           x["code"])),
           "matrix": matrix}
    _CACHE["t"], _CACHE["d"] = time.time(), out
    return out


if __name__ == "__main__":
    d = build_cross(force=True)
    multi = [s for s in d["stocks"] if s["n_hit"] >= 2]
    mixed = [s for s in d["stocks"] if s["state"] == "mixed"]
    print("stocks=%d multi(>=2)=%d mixed=%d" % (len(d["stocks"]),
                                                len(multi), len(mixed)))
    for s in multi[:15]:
        print(" ", s["code"], s["name"], s["state"],
              " | ".join(h["label"] for h in s["hits"]))
