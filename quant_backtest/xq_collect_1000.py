# -*- coding: utf-8 -*-
"""目标采集 v2：xq_status 新增 >=1000 条（游标深翻 + 内层 data 解析）。

v1 失败复盘（2026-10-17 实测判例）：
  1. timeline 分类流匿名态 `page` 参数无效（每页返回同一批 16-17 条）
     ——但官方响应顶层带 next_max_id 游标，max_id 翻页实测两页重叠 0；
  2. 响应是嵌套结构：外层 {id, data:"{...}"}，真实帖子在内层 data
     JSON 串（id/title/description/text/user/created_at/view_count...）。
     旧采集器 _row 取外层 id → 落库全是 created_at='' 空壳行；
  3. search.json / comments.json 已 404 下线（带 cookie 仍 404）；
     user_timeline.json 匿名被 WAF 挑战页拦。

v2 策略：游标深翻 4 类目（102 沪深/104 港股/105 美股/111 基金），
解析内层 data → 真实行 upsert（pk=真实 status_id），目标达成即停。
403 → xq_proxy.notify_blocked 换出口续爬（连续 5 次放弃）；不触碰
ip_block_trip 全局冷却（动态池时代熔断=换 IP 非停轮）。

合规：只读 GET、页间 2-4s 抖动、官方游标、不登录隐私数据（四红线）。

用法：python xq_collect_1000.py [--target 1000] [--clean-junk]
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xueqiu_common as X
from xueqiu_crawl import XqCrawler
from xq_proxy import get_pool, ua_for, notify_blocked

SOCIAL = "https://xueqiu.com"
URL_TIMELINE = SOCIAL + "/v4/statuses/public_timeline_by_category.json"
CATS = (102, 104, 105, 111)
COUNT = 20
MAX_BLOCK_STREAK = 5
MAX_PAGES_PER_CAT = 60                 # 单类目游标步数上限（1000/4 类目≈13 步，留 4 倍余量）
MAX_REQUESTS = 320

STATUS_FIELDS = ("view_count", "comment_count", "retweet_count",
                 "like_count", "reward_count")


def db_count(table: str) -> int:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % X.INFO_DB, uri=True)
        n = con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        con.close()
        return n
    except Exception:
        return 0


def load_seen(table: str, pk: str) -> set:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % X.INFO_DB, uri=True)
        ids = {str(r[0]) for r in con.execute("SELECT %s FROM %s" % (pk, table))}
        con.close()
        return ids
    except Exception:
        return set()


def clean_junk() -> int:
    """清理 v1 空壳行（created_at=''），删前留 JSON 备份。返回清理数。"""
    con = sqlite3.connect(X.INFO_DB)
    rows = con.execute(
        "SELECT status_id FROM xq_status WHERE created_at='' OR created_at IS NULL"
    ).fetchall()
    total = con.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0]
    if not rows:
        con.close()
        return 0
    if len(rows) == total:              # 全是空壳才清，防误伤好行
        bak = "C:/Temp/xq_status_junk_backup_%s.json" % datetime.now().strftime("%H%M%S")
        with open(bak, "w", encoding="utf-8") as f:
            json.dump([r[0] for r in rows], f)
        con.execute("DELETE FROM xq_status WHERE created_at='' OR created_at IS NULL")
        con.commit()
        print("[clean] 空壳行 %d 条已清（备份 %s）" % (len(rows), bak))
    else:
        print("[clean] 空壳 %d/%d 条（非全空壳，跳过清理防误伤）"
              % (len(rows), total))
    con.close()
    return len(rows)


def parse_item(it: dict, cat: int) -> dict | None:
    """外层 feed 项 → 内层 data JSON → 真实帖行。"""
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
        "user_id": str(d.get("user_id") or (d.get("user") or {}).get("id") or ""),
        "target": str(d.get("target") or ""),
        "source": str(d.get("source") or d.get("client") or "")[:60],
        "category": cat,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    row["comment_count"] = d.get("comment_count") or d.get("reply_count") or 0
    for f in ("view_count", "retweet_count", "like_count", "reward_count"):
        row[f] = d.get(f) or 0
    return row


class Collector:
    def __init__(self, target: int) -> None:
        self.c = XqCrawler()
        self.target = target
        self.new = 0
        self.requests = 0
        self.rotations = 0
        self.block_streak = 0
        self.seen = load_seen("xq_status", "status_id")
        print("[collect] 已有 status_id %d 个" % len(self.seen))

    def rotate(self, reason: str) -> None:
        try:
            notify_blocked()
        except Exception as e:
            print("[rotate] notify_blocked 异常：%s" % str(e)[:60])
        try:
            pool = get_pool()
            self.c.sess.proxies.update(pool.proxies() or {})
            self.c.sess.headers.update({"User-Agent": ua_for(pool.current())})
            name = pool.current().name if pool.current() else "direct"
        except Exception as e:
            name = "? (%s)" % str(e)[:40]
        self.rotations += 1
        print("[rotate] %s → 出口已换 %s（第 %d 次）"
              % (reason, name, self.rotations))
        time.sleep(8)

    def get_json(self, params: dict) -> dict | None:
        """带轮换重试的取页。403 → 换出口重试同页；仍败返回 None。"""
        for attempt in range(2):
            self.requests += 1
            try:
                data = X.xq_get(self.c.sess, URL_TIMELINE, params=params)
                if data is not None:
                    self.block_streak = 0
                return data
            except X.XqIpBlocked as e:
                self.block_streak += 1
                print("[403] %s" % str(e)[:60])
                if self.block_streak >= MAX_BLOCK_STREAK:
                    raise RuntimeError(
                        "连续 %d 次换新出口仍被封，放弃" % MAX_BLOCK_STREAK)
                self.rotate("ip_blocked#%d" % self.block_streak)
            except Exception as e:
                print("[get_json] 请求异常：%s" % str(e)[:70])
                return None
        return None

    def run(self) -> None:
        for cat in CATS:
            cursor = None                 # None=首页
            for step in range(MAX_PAGES_PER_CAT):
                if self.new >= self.target or self.requests >= MAX_REQUESTS:
                    return
                params = {"category": cat, "count": COUNT, "page": 1,
                          "source": "user"}
                if cursor:
                    params["max_id"] = cursor
                data = self.get_json(params)
                if data is None:
                    print("[cat %d] step %d 取页失败，跳过类目" % (cat, step))
                    break
                items = (data or {}).get("list") or (data or {}).get("items") or []
                if not items:
                    break               # 到底
                rows = []
                for it in items:
                    r = parse_item(it, cat)
                    if r and r["status_id"] not in self.seen:
                        self.seen.add(r["status_id"])
                        rows.append(r)
                if rows:
                    self.c.save("xq_status", rows)
                    self.new += len(rows)
                nxt = (data or {}).get("next_max_id")
                print("[cat %d] step %2d: %2d 条（新 %2d）| 累计新增 %d/%d"
                      % (cat, step, len(items), len(rows),
                         self.new, self.target))
                if self.new >= self.target:
                    return
                if not nxt or nxt == cursor:
                    break               # 游标耗尽/卡死
                cursor = nxt
                X.jitter_sleep()


def main() -> int:
    ap = argparse.ArgumentParser(description="目标采集 v2（游标深翻）")
    ap.add_argument("--target", type=int, default=1000)
    ap.add_argument("--clean-junk", action="store_true",
                    help="先清理 v1 空壳行（created_at=''）")
    a = ap.parse_args()
    t0 = time.time()
    if a.clean_junk:
        clean_junk()
    base = db_count("xq_status")
    col = Collector(a.target)
    print("[collect] 基线 xq_status=%d，目标新增 %d" % (base, a.target))
    try:
        col.run()
    except RuntimeError as e:
        print("[ABORT] %s" % e)
    except Exception as e:
        print("[ABORT] 意外异常：%s" % str(e)[:120])
    after = db_count("xq_status")
    print("[collect] 完成：新增 %d/%d | xq_status %d→%d | 请求 %d 次, "
          "换出口 %d 次, 用时 %.1f 分钟"
          % (col.new, a.target, base, after, col.requests,
             col.rotations, (time.time() - t0) / 60))
    return 0 if col.new >= a.target else 1


if __name__ == "__main__":
    sys.exit(main())
