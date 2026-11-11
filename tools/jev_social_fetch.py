# -*- coding: utf-8 -*-
"""股吧 / 雪球散户热度采集器 —— JEV 事件源第三类（2026-11-08）

## 为什么自建

TDX-Trading 的 181 张表里没有股吧/雪球（最接近的只有 ts_dc_hot 东财热榜、
ts_ths_hot 同花顺热榜、stock_news 个股新闻），所以这两类只能自己采。

## 采什么（全部是公开榜单，不登录、不碰验证码）

| 源 | 接口 | 拿到什么 |
|---|---|---|
| 雪球人气 | stock.xueqiu.com/v5/stock/hot_stock/list.json?type=10 | 综合人气值 + 排名变化 |
| 雪球讨论 | 同上 type=20 | 讨论数（散户在聊多少） |
| 雪球交易 | 同上 type=30 | 交易热度 |
| 雪球关注 | 同上 type=40 | 关注人数 |
| 股吧人气榜 | emappdata.eastmoney.com/stockrank/getAllCurrentList | 排名 + 较上期变化 |
| 股吧帖子 | gbapi.eastmoney.com/webarticlelist/.../Articlelist | 标题/阅读/评论/时间 |

## 四个坑（都实测踩过）

1. **雪球必须先拿 cookie**：直接调 hot_stock 返回 400，先 GET https://xueqiu.com/
   拿到 xq_a_token 再调就 200。
2. **股吧文章接口少一个参数就 count=0**：必须同时带
   `deviceid/version/product=Guba/plat=Web`，少任何一个都返回
   `{"re":[],"count":0}` —— 「没数据」和「参数不对」长得一模一样，
   而 600000 其实有 14 万条。这是本仓判例 #26 第 4 条的同型坑。
3. **`code` 参数只过滤主吧**：返回里会混进「相关吧」的串帖（问 600000 会带
   招商银行吧），以及 300750 会返回 `cfhpl`（财富号评论吧）。一律以响应里的
   `stockbar_code` 为准归属，别信请求参数。
4. **trust_env=False 直连**：本机 HTTP_PROXY 指向 127.0.0.1:56721，
   并发下该代理会 RST（WinError 10054），错误表现是「Expecting value:
   line 1 column 1」——看着像站点挂了，其实是本地代理被打崩。
5. **股吧人气榜 pageSize 上限 100**：传 101/150/200 都静默返回空数组
   （status=0），不是报错。固定 100/页再翻页。

## 纪律

- **三源独立降级**：任一源挂只记 errors，不影响其它源，也不整体失败
- **只对当日要看的票拉帖子**：全市场 5000+ 次请求盘后跑一遍 40 分钟，
  收益不成比例；默认取「涨停 + 涨幅≥5% + 焦点板块前排」
- **产物是快照**：同一天重跑覆盖（不是追加），临时文件 + os.replace 原子替换
- **不参与 Δmin**：帖子/榜单没有「盘中价格反应」口径，进 Δmin 会复现判例
  #25③ 的坑（拿不存在的时间戳硬算滞后）

用法：
    python tools/jev_social_fetch.py --day 20261106 --dry
    python tools/jev_social_fetch.py --day 20261106
    python tools/jev_social_fetch.py --day 20261106 --codes 600519.SH,300750.SZ
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
OUT_DIR = os.path.join(STATION, "portal")
XQ_SIZE = 100          # 雪球每个榜取多少条
GUBA_RANK_SIZE = 100   # 股吧人气榜：接口只给前 100（pageNo=2 起返回空）
GUBA_RANK_PAGE = 100   # 单页上限 100（超过静默返回 0，见坑 5）
GUBA_PS = 20           # 每只票拉多少条帖子
SLEEP = 0.25           # 股吧逐只拉帖的间隔，别把人家接口打疼

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_SESS = None


def _sess():
    """全站共用一个直连会话（见坑 4）。"""
    global _SESS
    if _SESS is None:
        import requests
        s = requests.Session()
        s.trust_env = False
        s.proxies = {"http": None, "https": None}
        s.headers.update({"User-Agent": UA})
        _SESS = s
    return _SESS


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ── 雪球 ──────────────────────────────────────────────────────────────
def xq_hot(kind, size=XQ_SIZE):
    """雪球热股榜。kind: 10 人气 / 20 讨论 / 30 交易 / 40 关注。

    → [{"code","name","value","pct","rank"}]；失败抛异常由调用方记 errors
    """
    s = _sess()
    # 坑 1：没有 xq_a_token 时接口一律 400
    if "xq_a_token" not in {c.name for c in s.cookies}:
        s.get("https://xueqiu.com/", timeout=15)
    r = s.get("https://stock.xueqiu.com/v5/stock/hot_stock/list.json",
              params={"size": size, "_type": kind, "type": kind}, timeout=15)
    r.raise_for_status()
    items = ((r.json().get("data") or {}).get("items")) or []
    out = []
    for i, x in enumerate(items):
        code = str(x.get("code") or "")
        # 雪球给的是 SZ300308 这种，转成 300308.SZ
        if len(code) > 2 and code[:2] in ("SZ", "SH", "BJ"):
            code = "%s.%s" % (code[2:], code[:2])
        out.append({"code": code, "name": x.get("name"),
                    "value": x.get("value"), "pct": x.get("percent"),
                    "rank": i + 1})
    return out


# ── 股吧 ──────────────────────────────────────────────────────────────
def guba_rank(size=GUBA_RANK_SIZE, page=GUBA_RANK_PAGE):
    """股吧人气榜。→ [{"code","rank","chg"}]，chg=较上期排名变化

    坑 5：pageSize **上限 100**，传 101 以上不是报错而是静默返回空数组
    （status 仍是 0）。所以固定 100/页再翻页，别图省事一次要 200 条。
    """
    s = _sess()
    out, page_no = [], 1
    while len(out) < size:
        body = {"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38",
                "marketType": "", "pageNo": page_no,
                "pageSize": min(page, size - len(out))}
        r = s.post("https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
                   json=body, timeout=15)
        r.raise_for_status()
        data = r.json().get("data") or []
        if not data:
            break
        want = min(page, size - len(out))
        for x in data:
            sc = str(x.get("sc") or "")      # SH603366 / SZ002086
            code = ("%s.%s" % (sc[2:], sc[:2])) if len(sc) > 2 else sc
            out.append({"code": code, "rank": x.get("rk"), "chg": x.get("hisRc")})
        if len(data) < want:                 # 本页不满 → 到底了
            break
        page_no += 1
    return out[:size]


def guba_posts(code6, ps=GUBA_PS):
    """个股股吧最新帖子。→ [{"code","bar","title","ts","read","comment"}]

    坑 2：deviceid/version/product/plat 四个参数缺一不可，否则 count=0。
    坑 3：响应里会混进相关吧的串帖，按 stockbar_code 过滤。
    """
    s = _sess()
    r = s.get("https://gbapi.eastmoney.com/webarticlelist/api/Article/Articlelist",
              params={"deviceid": "1", "version": "200", "product": "Guba",
                      "plat": "Web", "code": code6, "type": "0",
                      "sorttype": "1", "ps": str(ps), "p": "1"},
              headers={"Referer": "https://guba.eastmoney.com/list,%s.html" % code6},
              timeout=15)
    r.raise_for_status()
    out = []
    for x in (r.json().get("re") or []):
        if str(x.get("stockbar_code")) != code6:
            continue                      # 坑 3：串帖不计入本票
        out.append({"code": code6,
                    "bar": x.get("stockbar_name"),
                    "title": (x.get("post_title") or "").strip(),
                    "ts": (x.get("post_publish_time") or "").strip(),
                    "read": x.get("post_click_count") or 0,
                    "comment": x.get("post_comment_count") or 0})
    return out


# ── 选票 ──────────────────────────────────────────────────────────────
def pick_codes(day, limit):
    """当日最该看的那批票（复用 jev_mins_fetch 的口径，避免两处不一致）。"""
    sys.path.insert(0, HERE)
    import jev_mins_fetch as F
    picks, focus = F.pick_codes(day, limit)
    return [p[0] for p in picks], focus


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default="", help="YYYYMMDD，缺省=最近交易日")
    ap.add_argument("--codes", default="", help="逗号分隔，指定票（跳过自动选票）")
    ap.add_argument("--limit", type=int, default=60, help="自动选票上限")
    ap.add_argument("--ps", type=int, default=GUBA_PS, help="每只票拉多少条帖子")
    ap.add_argument("--dry", action="store_true", help="只列清单，不请求不写盘")
    ap.add_argument("--force", action="store_true",
                    help="允许给非当日写快照（危险：会把查询时刻榜单错标成历史日，2026-11-10 判例）")
    a = ap.parse_args()

    sys.path.insert(0, os.path.join(STATION, "portal"))
    import jev_graph as G
    day = G.resolve_day(a.day or None)
    # 同日守卫（2026-11-10 判例）：社交榜单是查询时刻实时值，不接日期参数。
    # 给历史日写盘 = 把今天的热度错标成历史日（假数据）。历史快照只能前向累积。
    # 守卫放在取数前，避免拒写还白拉一轮网络。
    if not a.force and not a.dry:
        _today = dt.date.today().strftime("%Y%m%d")
        if day != _today:
            log("拒绝取数/写盘：--day %s 不是今天（%s），榜单是查询时刻实时值，写历史日=假数据。"
                "确需覆盖请加 --force。" % (day, _today))
            return 2

    if a.codes:
        codes = [x.strip().upper() for x in a.codes.split(",") if x.strip()]
        focus = "-"
    else:
        codes, focus = pick_codes(day, a.limit)
    log("=== jev_social_fetch day=%s 焦点板块=%s 票数=%d ===" % (day, focus, len(codes)))
    if a.dry:
        log("dry-run：将采集 雪球4榜 + 股吧人气榜 + %d 只票的帖子" % len(codes))
        return 0

    out = {"day": day,
           "fetched_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "focus_board": focus, "codes": codes,
           "errors": [], "xq": {}, "guba": {"rank": [], "posts": []},
           "by_code": {}}

    # ── 雪球四榜（各自独立降级）──
    for key, kind in (("hot", 10), ("discuss", 20), ("trade", 30), ("follow", 40)):
        try:
            out["xq"][key] = xq_hot(kind)
            log("雪球 %-8s %d 条" % (key, len(out["xq"][key])))
        except Exception as exc:  # noqa: BLE001
            out["xq"][key] = []
            out["errors"].append("xq_%s: %s" % (key, exc))
            log("雪球 %-8s 失败: %s" % (key, exc))

    # ── 股吧人气榜 ──
    try:
        out["guba"]["rank"] = guba_rank()
        log("股吧人气榜 %d 条" % len(out["guba"]["rank"]))
    except Exception as exc:  # noqa: BLE001
        out["errors"].append("guba_rank: %s" % exc)
        log("股吧人气榜 失败: %s" % exc)

    # ── 股吧帖子（逐只，带间隔）──
    n_ok = 0
    for i, code in enumerate(codes, 1):
        code6 = str(code).split(".")[0]
        try:
            ps = guba_posts(code6, a.ps)
            out["guba"]["posts"].extend(ps)
            n_ok += 1
        except Exception as exc:  # noqa: BLE001
            out["errors"].append("guba_posts %s: %s" % (code6, exc))
        if i % 20 == 0:
            log("  帖子 %d/%d" % (i, len(codes)))
        time.sleep(SLEEP)
    log("股吧帖子：%d 只成功，共 %d 条" % (n_ok, len(out["guba"]["posts"])))

    # ── 汇总到 by_code ──
    for rec in out["guba"]["posts"]:
        d = out["by_code"].setdefault(rec["code"], {})
        d["posts"] = d.get("posts", 0) + 1
        d["reads"] = d.get("reads", 0) + int(rec["read"] or 0)
        d["comments"] = d.get("comments", 0) + int(rec["comment"] or 0)
    for k, field in (("hot", "xq_hot"), ("discuss", "xq_discuss"),
                     ("trade", "xq_trade"), ("follow", "xq_follow")):
        for rec in out["xq"].get(k) or []:
            d = out["by_code"].setdefault(str(rec["code"])[:6], {})
            d[field] = rec["value"]
            d[field + "_rank"] = rec["rank"]
    for rec in out["guba"]["rank"]:
        d = out["by_code"].setdefault(str(rec["code"])[:6], {})
        d["guba_rank"] = rec["rank"]
        d["guba_rank_chg"] = rec["chg"]

    path = os.path.join(OUT_DIR, "social_%s.json" % day)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    log("写入 %s（%d 只票有热度数据，errors %d）"
        % (path, len(out["by_code"]), len(out["errors"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
