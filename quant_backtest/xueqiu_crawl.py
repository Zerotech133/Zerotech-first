# -*- coding: utf-8 -*-
"""雪球 L3 直爬兜底层：舆情三表 + L1 失效时行情接管。

设计依据：xueqiu-collector-design-2026-10-16.md §2.4。
数据源（GET only，未登录匿名 cookie，限 15 次/分钟）：
  /v4/statuses/public_timeline_by_category.json  分类帖流（102 沪深）
  /v4/statuses/hot/listV2.json                   全站热帖
  /v4/statuses/search.json                       个股讨论帖
  /v5/stock/quote.json                           行情接管（batch/quote.json）

存储：SQLite data/xueqiu/xueqiu_info.db 三表
  xq_status（pk=status_id）/ xq_hot_post（pk=status_id）/
  xq_comment（pk=comment_id，复合索引 symbol+created_at）

合规：只读 GET、≥2s+抖动限速、不分发、不登录他人隐私数据（四红线）。

用法：
  python xueqiu_crawl.py --mode all                       # 日链 timeline+hot
  python xueqiu_crawl.py --mode comments --symbols SH600519 --pages 3
  python xueqiu_crawl.py --mode takeover                  # L1 失效手动接管
"""
import argparse
import json
import os
import sys
from datetime import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xueqiu_common as X

BASE = "https://stock.xueqiu.com"
SOCIAL = "https://xueqiu.com"                # 舆情接口在主站域
STATUS_FIELDS = ("view_count", "comment_count", "retweet_count",
                 "like_count", "reward_count")


class XqCrawler:
    """直爬兜底层。未登录限 15 次/分钟→THROTTLE_BASE=2.0s 强制。"""

    def __init__(self, mf: X.XqManifest | None = None) -> None:
        self.mf = mf or X.XqManifest()
        # P0-1 修复（403 熔断冷却暴露的潜伏 Bug）：XqManifest.__init__
        # 只建空骨架不读盘——此前 crawl 层从未调 load()，断点/health
        # 恒为空：冷却检查永远读不到 ip_blocked_until，且 mark+save
        # 每轮用本轮表覆写整份 manifest（其他表状态丢失）。单点在此
        # load，三个流程函数全部受益。
        self.mf.load()
        self.sess = requests.Session()
        # 护栏：禁宿主代理注入（trust_env），判例 2026-10-18 假403/假502
        # （WorkBuddy automation 曾注入代理 env 致「假快+假403/假502」；
        #   P0-3 出口代理池为显式 proxies.update，不受 trust_env 影响）
        self.sess.trust_env = False
        # P0-3：出口代理池注入（空配置=直连，行为与之前一致）；
        # fetch_* 与首页 warm 全部经 self.sess，自动走当前出口。
        # P2-7 出口-指纹粘性：UA 随出口绑定（同出口恒同 UA，换出口
        # 即换 UA）；直连用默认 UA（与 X.UA 同串）。sync 层 pysnowball
        # UA 硬编码不可注入（见 xq_proxy.UA_VARIANTS 注释边界）。
        self._ua = X.UA
        try:
            from xq_proxy import get_pool, ua_for
            pool = get_pool()
            pr = pool.proxies()
            if pr:
                self.sess.proxies.update(pr)
                print("[XqCrawler] 出口代理: %s" % pool.current().name)
            self._ua = ua_for(pool.current())
            if pr:
                print("[XqCrawler] UA 粘性: 出口专属（%s）"
                      % pool.current().name)
        except Exception:
            pass
        self.sess.headers.update({"User-Agent": self._ua})
        self.token = self._warm()

    def _warm(self) -> str:
        """三级链取 cookie（Agent-Reach 判例）：env → 浏览器登录态 → 首页。

        失败不阻塞，交 xq_get 退避兜底；token 来源仅打印"哪一级"，
        绝不打印值。
        """
        tok = X.ensure_session_cookies(self.sess, X.load_token())
        if tok:
            src = ("env/browser" if self.sess.cookies.get("xq_a_token")
                   else "homepage-warm")
            print("[XqCrawler] cookie 就绪（来源: %s）" % src)
        else:
            print("[XqCrawler] 三级链均未取到 cookie"
                  "（请在 Edge 登录 xueqiu.com 后重跑；继续裸请求降级）")
        return tok

    # ── 原始帖行归一 ────────────────────────────────────────
    @staticmethod
    def _row(it: dict, category: int, extra: dict | None = None) -> dict:
        """雪球 status 原始 JSON → 统一行（时间 ms→本地串）。"""
        ts = X.ms_to_dt(it.get("created_at"))
        row = {
            "status_id": str(it.get("id") or it.get("target") or ""),
            "created_at": ts,
            "title": str(it.get("title") or it.get("desc") or "")[:120],
            "desc": str(it.get("description") or it.get("text") or "")[:2000],
            "user_id": str((it.get("user_id") or
                            (it.get("user") or {}).get("user_id")
                            or (it.get("user") or {}).get("id") or "")),
            "target": str(it.get("target") or ""),
            "source": str(it.get("source") or it.get("client") or "")[:60],
            "category": category,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        for f in STATUS_FIELDS:
            row[f] = it.get(f) or 0
        if extra:
            row.update(extra)
        return row

    @staticmethod
    def _parse_feed_item(it: dict, category: int) -> dict | None:
        """feed 项 → 真实帖行（P3 嵌套结构，2026-10-17 实测判例）。

        实测响应为嵌套：外层 {id, data:"{…}"}——外层 id 是 feed 壳，
        真实帖（id/title/description/text/user/created_at/计数）在内层
        data JSON 串。旧 _row 取外层 id → 落库全空壳行（created_at=''）。
        data 存在但解析失败 → None（宁缺毋滥，不落空壳）；外层扁平结构
        （无 data 键）由调用方回退 _row 兼容。
        """
        d = it.get("data")
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                return None
        if not isinstance(d, dict) or not d.get("id"):
            return None
        row = {
            "status_id": str(d["id"]),
            "created_at": X.ms_to_dt(d.get("created_at")),
            "title": str(d.get("title") or "")[:120],
            "desc": str(d.get("description") or d.get("text") or "")[:2000],
            "user_id": str(d.get("user_id") or
                           (d.get("user") or {}).get("id") or ""),
            "target": str(d.get("target") or ""),
            "source": str(d.get("source") or d.get("client") or "")[:60],
            "category": category,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        row["comment_count"] = (d.get("comment_count")
                                or d.get("reply_count") or 0)
        for f in ("view_count", "retweet_count", "like_count",
                  "reward_count"):
            row[f] = d.get(f) or 0
        return row

    # ── 三类抓取 ────────────────────────────────────────────
    def fetch_timeline(self, category: int = 102, count: int = 20,
                       max_pages: int = 1) -> list[dict] | None:
        """v4 分类帖流（102 沪深/104 港股/105 美股/111 基金）→ 帖行列表。

        P3 升级（2026-10-17 实测判例）：匿名态 `page` 参数无效（每页
        返回同批 16-17 条）——改走官方 `next_max_id` 游标（max_id 实测
        两页重叠 0，可深翻历史）；max_pages 语义=游标步数（硬上限
        X.MAX_PAGES）。响应嵌套：真实帖在内层 data JSON，经
        _parse_feed_item 解析；外层扁平结构（无 data 键）回退 _row。

        首步请求失败（429 到顶/风控/400016）返回 None（错误信号，区别
        于真空空页 []）；后续步失败保留已采行（部分成功）。游标耗尽
        （next_max_id 缺失）或卡死（与前次相同）自然终止；跨步按
        status_id 去重。
        """
        rows: list[dict] = []
        seen: set = set()
        url = SOCIAL + "/v4/statuses/public_timeline_by_category.json"
        cursor = None
        for step in range(1, min(max_pages, X.MAX_PAGES) + 1):
            params = {"category": category, "count": count, "page": 1,
                      "source": "user"}
            if cursor:
                params["max_id"] = cursor
            data = X.xq_get(self.sess, url, params=params)
            if data is None:
                # P1 修复（审计）：xq_get None=请求失败（重试到顶/风控/
                # 400016 token 失效），不可与真空空页混同——首步失败返回
                # None 信号，调用方记 manifest failed（fail 而非伪成功）
                if step == 1:
                    return None
                break                   # 后续步失败：保留已采行（部分成功）
            items = (data or {}).get("list") or \
                (data or {}).get("items") or []
            if not items:
                break                       # 空页：自然终止
            for it in items:
                if "data" in it:
                    row = self._parse_feed_item(it, category)
                else:
                    row = self._row(it, category)   # 旧扁平结构兼容
                if row and row["status_id"] and \
                        row["status_id"] not in seen:
                    seen.add(row["status_id"])
                    rows.append(row)
            if step < min(max_pages, X.MAX_PAGES):
                nxt = (data or {}).get("next_max_id")
                if not nxt or nxt == cursor:
                    break                   # 游标耗尽/卡死：自然终止
                cursor = nxt
                X.jitter_sleep()            # 步间强制限速
        return rows

    def fetch_hot(self, count: int = 30) -> list[dict] | None:
        """v4/statuses/hot/listV2.json → 热帖（含 hot_rank）；请求失败 → None。"""
        url = SOCIAL + "/v4/statuses/hot/listV2.json"
        data = X.xq_get(self.sess, url, params={
            "count": count, "page": 1, "source": "user"})
        if data is None:
            return None          # 请求失败信号（区别于真空空页，P1 修复）
        items = (data or {}).get("items") or []
        rows = []
        for rank, it in enumerate(items[:count], 1):
            row = self._row(it, 0, extra={"hot_rank": rank})
            if row["status_id"]:
                rows.append(row)
        return rows

    def fetch_comments(self, symbol: str, max_pages: int = 3) -> list[dict] | None:
        """v4/statuses/search.json?symbol=&source=user → 个股讨论帖。

        MAX_PAGES 硬上限；帖 id 去重（跨页去重 set）；首页请求失败返回
        None（错误信号，区别于真空空页）。
        """
        sym = X.norm_symbol(symbol)
        if not sym:
            return []
        url = SOCIAL + "/v4/statuses/search.json"
        rows, seen = [], set()
        for page in range(1, min(max_pages, X.MAX_PAGES) + 1):
            data = X.xq_get(self.sess, url, params={
                "symbol": sym, "count": 20, "page": page,
                "source": "user", "sort": "time"})
            if data is None:
                # P1 修复（审计）：首页失败=错误信号；后续页失败保留已采行
                if page == 1:
                    return None
                break
            items = (data or {}).get("items") or \
                (data or {}).get("list") or []
            if not items:
                break
            fresh = 0
            for it in items:
                row = self._row(it, 0, extra={"symbol": sym})
                if row["status_id"] and row["status_id"] not in seen:
                    seen.add(row["status_id"])
                    # P1-NEW 修复（QA Round-2）：xq_comment DDL 主键是
                    # comment_id（无 status_id 列），键不齐 upsert 必抛
                    # OperationalError → comments 模式全码假失败。
                    # timeline/hot 两表 DDL 主键本就是 status_id，故只在此改键。
                    row["comment_id"] = row.pop("status_id")
                    rows.append(row)
                    fresh += 1
            if fresh == 0:
                break                       # 全是旧帖：提前收手
            if page < min(max_pages, X.MAX_PAGES):
                X.jitter_sleep()
        return rows

    # ── 落库 ────────────────────────────────────────────────
    def save(self, table: str, rows: list[dict]) -> int:
        """upsert_sqlite(xueqiu_info.db, table, rows, pk) 幂等落库。"""
        pk = X.SQLITE_TABLES.get(table, {}).get("pk", ["status_id"])
        return X.upsert_sqlite(X.INFO_DB, table, rows, pk)


# ── 采集流程 ─────────────────────────────────────────────────
def crawl_all(categories: tuple[int, ...] = (102,), hot: bool = True,
              pages: int = 1) -> dict:
    """日链主流程：timeline+hot→xq_status/xq_hot_post。返回各表新增计数。

    P0-1 熔断：入口冷却检查生效→全表 ip_blocked 零请求返回（rc=1 告警）；
    运行中任一 403 即 ip_block_trip 熔断停轮，剩余表直接记 ip_blocked
    不再发请求（继续撞只会延长拉黑期）。
    """
    c = XqCrawler()
    out: dict = {"xq_status": 0, "xq_hot_post": 0, "failed_tables": []}
    blocked = X.ip_blocked_active(c.mf)
    if blocked:
        print("[ALERT][ip_blocked] 冷却中 %s，本轮全表跳过"
              "（勿手动连触采集）" % blocked)
        for tbl in ("xq_status", "xq_hot_post"):
            c.mf.mark(tbl, done=False, rows=0, status="ip_blocked",
                      note="cooldown %s" % blocked)
            out["failed_tables"].append(tbl)
        c.mf.save()
        return out
    tripped = ""
    for cat in categories:
        if tripped:
            break
        try:
            rows = c.fetch_timeline(cat, count=20, max_pages=pages)
            if rows is None:            # P1 修复：请求失败≠真空，升 failed
                raise RuntimeError(
                    "timeline 请求失败（重试到顶/风控/400016 token 失效）")
            out["xq_status"] += c.save("xq_status", rows)
            c.mf.mark("xq_status", done=out["xq_status"] > 0,
                      rows=out["xq_status"], status="ok")
            X.jitter_sleep()
        except X.XqIpBlocked as e:
            tripped = X.ip_block_trip(c.mf)
            out["failed_tables"].append("xq_status")
            c.mf.mark("xq_status", done=False, rows=0, status="ip_blocked",
                      note=str(e)[:100])
            print("[ALERT][ip_blocked] 403 熔断：%s → 冷却至 %s"
                  % (str(e)[:80], tripped))
        except Exception as e:
            out["failed_tables"].append("xq_status")
            c.mf.mark("xq_status", done=False, rows=0, status="failed",
                      note=str(e)[:100])
            print("[crawl_all] timeline 失败：%s" % str(e)[:80])
    if hot:
        if tripped:                     # 熔断停轮：hot 不再发请求
            out["failed_tables"].append("xq_hot_post")
            c.mf.mark("xq_hot_post", done=False, rows=0,
                      status="ip_blocked",
                      note="熔断停轮（冷却至 %s）" % tripped)
        else:
            try:
                rows = c.fetch_hot(count=30)
                if rows is None:        # P1 修复：请求失败≠真空，升 failed
                    raise RuntimeError(
                        "hot 请求失败（重试到顶/风控/400016 token 失效）")
                out["xq_hot_post"] = c.save("xq_hot_post", rows)
                c.mf.mark("xq_hot_post", done=True,
                          rows=out["xq_hot_post"], status="ok")
            except X.XqIpBlocked as e:
                tripped = X.ip_block_trip(c.mf)
                out["failed_tables"].append("xq_hot_post")
                c.mf.mark("xq_hot_post", done=False, rows=0,
                          status="ip_blocked", note=str(e)[:100])
                print("[ALERT][ip_blocked] 403 熔断：%s → 冷却至 %s"
                      % (str(e)[:80], tripped))
            except Exception as e:
                out["failed_tables"].append("xq_hot_post")
                c.mf.mark("xq_hot_post", done=False, rows=0,
                          status="failed", note=str(e)[:100])
                print("[crawl_all] hot 失败：%s" % str(e)[:80])
    c.mf.save()
    print("[crawl_all] %s" % out)
    return out


def crawl_symbols(symbols: list[str], pages: int = 3,
                  max_symbols: int = 60) -> dict:
    """池模式：逐码讨论帖→xq_comment（MAX_SYMBOLS 上限，默认 60 码）。

    P0-1 熔断：入口冷却检查生效→整轮 skip（fail_codes 非空→rc=1 告警）；
    运行中任一码 403 即 trip 熔断并 break 停轮，剩余码不再请求。
    """
    c = XqCrawler()
    syms = [s for s in (X.norm_symbol(s) for s in symbols) if s]
    if len(syms) > max_symbols:
        print("[crawl_symbols] %d 码超上限 %d，截断"
              % (len(syms), max_symbols))
        syms = syms[:max_symbols]
    out: dict = {"xq_comment": 0, "codes": 0, "fail_codes": []}
    fail_syms: list[str] = []
    blocked = X.ip_blocked_active(c.mf)
    if blocked:
        print("[ALERT][ip_blocked] 冷却中 %s，本轮 %d 码全部跳过"
              % (blocked, len(syms)))
        c.mf.mark("xq_comment", done=False, rows=0, status="ip_blocked",
                  note="cooldown %s" % blocked)
        c.mf.save()
        out["fail_codes"] = ["<cooldown>"]
        return out
    tripped = False
    for i, sym in enumerate(syms, 1):
        try:
            rows = c.fetch_comments(sym, max_pages=pages)
            if rows is None:            # P1 修复：首页请求失败信号
                fail_syms.append(sym)
                print("  [%d/%d] %s 请求失败（重试到顶/风控/token）"
                      % (i, len(syms), sym))
                continue
            n = c.save("xq_comment", rows)
            out["xq_comment"] += n
            out["codes"] += 1
            print("  [%d/%d] %s: %d 帖" % (i, len(syms), sym, n))
        except X.XqIpBlocked as e:
            tripped = True
            until = X.ip_block_trip(c.mf)
            fail_syms.append(sym)
            print("[ALERT][ip_blocked] 403 熔断：%s → 冷却至 %s，"
                  "剩余 %d 码跳过" % (str(e)[:80], until, len(syms) - i))
            break
        except Exception as e:
            fail_syms.append(sym)
            print("  [%d/%d] %s 失败：%s" % (i, len(syms), sym, str(e)[:70]))
        if i < len(syms):
            X.jitter_sleep()
    out["fail_codes"] = fail_syms
    c.mf.mark("xq_comment", done=out["codes"] > 0,
              rows=out["xq_comment"],
              status="ip_blocked" if (tripped or blocked)
              else ("failed" if fail_syms and not out["codes"]
                    else ("ok" if out["codes"] else "empty")),
              note=("%d codes failed: %s"
                    % (len(fail_syms), ",".join(fail_syms[:8])))
              if fail_syms else "")
    c.mf.save()
    print("[crawl_symbols] %s" % out)
    return out


def takeover_quote(symbols: list[str]) -> int:
    """L1 失效接管（手动触发）：quotec.json 直爬→xq_quote_snap 兜底片。

    端点与 pysnowball.quotec 同族（v5/stock/realtime/quotec.json），
    免登录 token；落当日 d_YYYYMMDD.parquet，列口径与 L1 快照一致
    （消费端无感切换）。P0-1：入口冷却检查 + 403 trip 熔断停轮。
    """
    import pandas as pd
    c = XqCrawler()
    syms = [s for s in (X.norm_symbol(s) for s in symbols) if s]
    blocked = X.ip_blocked_active(c.mf)
    if blocked:
        print("[ALERT][ip_blocked] 冷却中 %s，takeover 跳过" % blocked)
        c.mf.mark("xq_quote_snap", done=False, rows=0,
                  status="ip_blocked", note="cooldown %s" % blocked)
        c.mf.save()
        return 0
    rows: list[dict] = []
    url = BASE + "/v5/stock/realtime/quotec.json"
    tripped = False
    for i in range(0, len(syms), X.QUOTEC_BATCH):
        batch = syms[i:i + X.QUOTEC_BATCH]
        try:
            data = X.xq_get(c.sess, url, params={"symbol": ",".join(batch)})
            items = (data or {}).get("items") or []
            for q in items:
                sym = str(q.get("symbol") or "").upper()
                ts_code = X.to_ts_code(sym)
                if not ts_code:
                    continue                 # 指数等无 ts_code：跳过（兜底片仅个股）
                ts = X.ms_to_dt(q.get("timestamp"))
                rows.append({
                    "symbol": sym, "ts_code": ts_code,
                    "current": q.get("current"), "percent": q.get("percent"),
                    "chg": q.get("chg"), "volume": q.get("volume"),
                    "amount": q.get("amount"), "pe": q.get("pe"),
                    "pb": q.get("pb"),
                    "market_capital": q.get("market_capital"),
                    "float_market_capital": q.get("float_market_capital"),
                    "trade_date": X.ms_to_ymd(q.get("timestamp")) or
                    datetime.now().strftime("%Y%m%d"),
                    "timestamp": ts,
                })
        except X.XqIpBlocked as e:
            tripped = True
            until = X.ip_block_trip(c.mf)
            print("[ALERT][ip_blocked] 403 熔断：%s → 冷却至 %s"
                  % (str(e)[:80], until))
            break
        except Exception as e:
            print("  takeover 批 %d 失败：%s" % (i, str(e)[:70]))
        if i + X.QUOTEC_BATCH < len(syms):
            X.jitter_sleep()
    if not rows:
        c.mf.mark("xq_quote_snap", done=False, rows=0,
                  status="ip_blocked" if tripped else "failed",
                  note=("takeover 403 熔断停轮" if tripped
                        else "takeover 直爬全批失败"))
        c.mf.save()
        return 0
    d = os.path.join(X.OUT_DIR, "xq_quote_snap")
    os.makedirs(d, exist_ok=True)
    ymd = datetime.now().strftime("%Y%m%d")
    df = pd.DataFrame(rows).drop_duplicates(
        subset=["symbol", "timestamp"]).sort_values("symbol")
    X.atomic_parquet(df, os.path.join(d, "d_%s.parquet" % ymd))
    c.mf.mark("xq_quote_snap", done=True, rows=len(df),
              file="d_%s.parquet" % ymd, status="ok",
              note="L3 takeover 兜底片")
    c.mf.save()
    print("[takeover_quote] %d 行落兜底片 d_%s.parquet" % (len(df), ymd))
    return len(df)


# ── CLI ──────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    """argparse CLI。rc：全失败=1；单表 failed 继续（部分失败=0）。"""
    ap = argparse.ArgumentParser(description="雪球 L3 直爬器")
    ap.add_argument("--mode", default="all",
                    choices=["timeline", "hot", "comments", "all",
                             "takeover"],
                    help="all=timeline+hot；takeover=L1 失效行情接管")
    ap.add_argument("--symbols", default="",
                    help="逗号分隔雪球符号（comments/takeover 用；"
                         "缺省 comments 取缠论池）")
    ap.add_argument("--categories", default="102",
                    help="帖流分类（102 沪深/104 港股/105 美股）")
    ap.add_argument("--pages", type=int, default=3,
                    help="翻页数（默认 3，硬上限 10）")
    ap.add_argument("--max-symbols", type=int, default=60,
                    help="comments 池模式码上限（匿名 15 次/分钟约束）")
    ap.add_argument("--pool-days", type=int, default=30)
    a = ap.parse_args(argv)
    cats = tuple(int(x) for x in str(a.categories).split(",")
                 if x.strip().isdigit())
    syms = [s.strip() for s in a.symbols.split(",") if s.strip()]
    pages = max(1, min(a.pages, X.MAX_PAGES))
    if a.mode == "takeover":
        # P1 修复（审计）：接管全败（0 行落片）不再伪成功 rc=0——
        # takeover 是 L1 失效的兜底，静默失败=当天行情缺口无告警。
        # P2-9 单任务容错：构造/意外异常兜底 rc=1，不裸崩。
        try:
            n = takeover_quote(syms or X.pool_codes(a.pool_days,
                                                    a.max_symbols))
        except Exception as e:
            print("[main] takeover 异常：%s" % str(e)[:120])
            return 1
        return 0 if n > 0 else 1
    if a.mode == "comments":
        if not syms:
            syms = X.pool_codes(a.pool_days, a.max_symbols)
            print("池模式：近 %d 天活跃股 %d 只（上限 %d）"
                  % (a.pool_days, len(syms), a.max_symbols))
        if not syms:
            print("池为空，正常退出")
            return 0
        try:
            res = crawl_symbols(syms, pages=pages, max_symbols=a.max_symbols)
        except Exception as e:               # P2-9：意外异常不裸崩
            print("[main] comments 异常：%s" % str(e)[:120])
            return 1
        # P1 修复：全部码请求失败 → rc=1（有失败有成功的部分失败仍 rc=0）
        return 1 if not res.get("codes") and res.get("fail_codes") else 0
    # timeline / hot / all（空采=非交易日常态；部分失败单表 manifest
    # failed 继续跑 rc=0；P1 修复：全表 failed 才升 rc=1，兑现注释承诺）
    try:
        res = crawl_all(categories=cats or (102,),
                        hot=a.mode in ("hot", "all"), pages=pages)
    except Exception as e:                   # P2-9：意外异常不裸崩
        print("[main] crawl 异常：%s" % str(e)[:120])
        return 1
    expect = 2 if a.mode in ("all", "hot") else 1
    return 1 if len(res.get("failed_tables") or []) >= expect else 0


if __name__ == "__main__":
    sys.exit(main())
