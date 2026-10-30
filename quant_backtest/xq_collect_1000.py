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

用法：python xq_collect_1000.py [--target 1000] [--clean-junk] [--cursor-file PATH]
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
MAX_PAGES_PER_CAT = 120  # 单类目每轮步数上限（续翻后每轮全部花在新鲜区）
MAX_REQUESTS = 640
CURSOR_PATH = os.path.join(
    r"C:\aios\automation_runs", "xq_backfill", "backfill_cursor.json"
)  # 定时任务产物统一根（2026-10-18）
SEED_CURSOR_PATH = os.path.join(
    r"C:\aios\automation_runs", "xq_backfill", "seed_cursor.json"
)  # 播种轮独立断点档（与常规轮隔离）

STATUS_FIELDS = (
    "view_count",
    "comment_count",
    "retweet_count",
    "like_count",
    "reward_count",
)


def db_count(table: str) -> int:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % X.INFO_DB, uri=True)
        n = con.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]  # nosec B608 - 值参数化/内部标识符（已抽查）
        con.close()
        return n
    except Exception:
        return 0


def load_seen(table: str, pk: str) -> set:
    try:
        con = sqlite3.connect("file:%s?mode=ro" % X.INFO_DB, uri=True)
        ids = {str(r[0]) for r in con.execute("SELECT %s FROM %s" % (pk, table))}  # nosec B608 - 值参数化/内部标识符（已抽查）
        con.close()
        return ids
    except Exception:
        return set()


def load_seed_cursors(seed: int) -> dict:
    """读播种断点：{"<cat>": {"max_id": ..., "exhausted": bool}}（该 seed 的）。"""
    try:
        with open(SEED_CURSOR_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get(str(seed), {}) or {}
    except Exception:
        return {}


def save_seed_cursor(seed: int, cat: int, state: dict) -> None:
    """播种断点原子落盘：seed_cursor.json 顶层 key=<seed>，内层 key=<cat>。

    双实例并行必须经 --cursor-file 分片传参（seed_cursor_A/B.json）防互踩
    ——本函数是整文件读改写，两实例共写同一档必相互覆盖丢断点。
    """
    try:
        data = {}
        if os.path.exists(SEED_CURSOR_PATH):
            with open(SEED_CURSOR_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        bucket = data.get(str(seed)) or {}
        bucket[str(cat)] = {
            "max_id": state.get("max_id"),
            "exhausted": bool(state.get("exhausted")),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        data[str(seed)] = bucket
        tmp = SEED_CURSOR_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SEED_CURSOR_PATH)
    except Exception as e:
        print("[seed-cursor] 落档异常（不阻断采集）：%s" % str(e)[:60])


def load_cursors() -> dict:
    """读每类目深翻断点：{cat: {"max_id": str|None, "exhausted": bool}}。

    max_id 为状态 ID 型游标（按 ID 翻页永不过期），持久化后每轮从断点
    续翻，重叠区重走成本归零；exhausted=true 的类目直接跳过（已到底）。
    """
    try:
        with open(CURSOR_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        out = {}
        for k, v in (d or {}).items():
            if isinstance(v, dict):
                out[str(k)] = {
                    "max_id": v.get("max_id"),
                    "exhausted": bool(v.get("exhausted")),
                }
        return out
    except Exception:
        return {}


def save_cursors(d: dict) -> None:
    tmp = CURSOR_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, CURSOR_PATH)


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
    if len(rows) == total:  # 全是空壳才清，防误伤好行
        bak = "C:/Temp/xq_status_junk_backup_%s.json" % datetime.now().strftime(
            "%H%M%S"
        )
        with open(bak, "w", encoding="utf-8") as f:
            json.dump([r[0] for r in rows], f)
        con.execute("DELETE FROM xq_status WHERE created_at='' OR created_at IS NULL")
        con.commit()
        print("[clean] 空壳行 %d 条已清（备份 %s）" % (len(rows), bak))
    else:
        print("[clean] 空壳 %d/%d 条（非全空壳，跳过清理防误伤）" % (len(rows), total))
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
    def __init__(
        self, target: int, fresh: bool = False, seed: int | None = None
    ) -> None:
        self.c = XqCrawler()
        self.target = target
        self.new = 0
        self.requests = 0
        self.rotations = 0
        self.block_streak = 0
        self.seen = load_seen("xq_status", "status_id")
        self.cursors = {} if fresh else load_cursors()
        self.fresh = fresh
        # 播种轮（号段跳挖）：feed 序号空间任意起点，4 类目同区段扫；
        # 断点写独立档 seed_cursor.json（key="<seed>_<cat>"），与常规轮
        # 游标存档完全隔离——同 seed 重跑自动续挖，exhausted 后整 cat 跳过。
        # 实证锚点：30000→2015-03 / 45000→2015末-2016 / 60000→2017-05 /
        # 150000→2018-04 / 250000→2020-03（探针 v0.2）
        self.seed = seed
        if seed:
            saved = load_seed_cursors(seed)
            self.cursors = {}
            for cat in CATS:
                st = saved.get(str(cat))
                self.cursors[str(cat)] = st or {"max_id": str(seed), "exhausted": False}
        print(
            "[collect] 已有 status_id %d 个%s"
            % (len(self.seen), "，播种起点 max_id=%s" % seed if seed else "")
        )

    def _save(self) -> None:
        if self.seed:
            for cat in CATS:
                st = self.cursors.get(str(cat))
                if st:
                    save_seed_cursor(self.seed, cat, st)
            return
        save_cursors(self.cursors)

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
        print("[rotate] %s → 出口已换 %s（第 %d 次）" % (reason, name, self.rotations))
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
                        "连续 %d 次换新出口仍被封，放弃" % MAX_BLOCK_STREAK
                    )
                self.rotate("ip_blocked#%d" % self.block_streak)
            except Exception as e:
                print("[get_json] 请求异常：%s" % str(e)[:70])
                return None
        return None

    def run(self) -> None:
        for cat in CATS:
            key = str(cat)
            state = self.cursors.get(key) or {}
            if state.get("exhausted"):
                print("[cat %d] 已到底（存档 exhausted），跳过" % cat)
                continue
            cursor = state.get("max_id")  # None=首页
            resumed = cursor is not None
            if resumed:
                print("[cat %d] 从存档游标续翻（%s...）" % (cat, str(cursor)[:16]))
            for step in range(MAX_PAGES_PER_CAT):
                if self.new >= self.target or self.requests >= MAX_REQUESTS:
                    return
                params = {"category": cat, "count": COUNT, "page": 1, "source": "user"}
                if cursor:
                    params["max_id"] = cursor
                data = self.get_json(params)
                if data is None:
                    print("[cat %d] step %d 取页失败，跳过类目" % (cat, step))
                    break
                items = (data or {}).get("list") or (data or {}).get("items") or []
                if not items:
                    if resumed:
                        # 续翻到空页 = 走到既往已达的底部 → 类目挖尽
                        self.cursors[key] = {"max_id": None, "exhausted": True}
                        self._save()
                        print("[cat %d] step %d 空页（续翻），类目已到底" % (cat, step))
                    else:
                        print(
                            "[cat %d] step %d 空页（非续翻不停档，如周末真空）"
                            % (cat, step)
                        )
                    break  # 到底/暂空
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
                print(
                    "[cat %d] step %2d: %2d 条（新 %2d）| 累计新增 %d/%d"
                    % (cat, step, len(items), len(rows), self.new, self.target)
                )
                if self.new >= self.target:
                    if nxt and nxt != cursor:  # 断点存档后停机
                        self.cursors[key] = {"max_id": nxt, "exhausted": False}
                        self._save()
                    return
                if not nxt or nxt == cursor:
                    # 游标耗尽 = 到底。仅续翻或深走（step>=10）才判 exhausted，
                    # 防周末短 feed 被误标永久跳过
                    if resumed or step >= 10:
                        self.cursors[key] = {"max_id": None, "exhausted": True}
                        self._save()
                        print("[cat %d] 游标耗尽，类目已到底" % cat)
                    break
                cursor = nxt
                self.cursors[key] = {"max_id": nxt, "exhausted": False}
                self._save()
                X.jitter_sleep()


def main() -> int:
    ap = argparse.ArgumentParser(description="目标采集 v2（游标深翻）")
    ap.add_argument("--target", type=int, default=1000)
    ap.add_argument(
        "--clean-junk", action="store_true", help="先清理 v1 空壳行（created_at=''）"
    )
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="忽略深翻断点存档，从顶部全量重走（seen 去重防重）",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="号段播种起点（feed 序号，如 30000=2015-03 区段）；"
        "播种轮断点不落档，与常规轮隔离",
    )
    ap.add_argument(
        "--cursor-file",
        default=None,
        help="播种断点档路径覆盖（双实例并行必须分片传参，如 "
        "seed_cursor_A.json/_B.json，防整文件读改写互踩）；"
        "缺省用默认 seed_cursor.json，行为不变",
    )
    a = ap.parse_args()
    if a.cursor_file:  # 双实例分片：覆盖模块级断点档路径
        global SEED_CURSOR_PATH
        SEED_CURSOR_PATH = a.cursor_file
    t0 = time.time()
    if a.clean_junk:
        clean_junk()
    base = db_count("xq_status")
    col = Collector(a.target, fresh=a.fresh, seed=a.seed)
    print("[collect] 基线 xq_status=%d，目标新增 %d" % (base, a.target))
    try:
        col.run()
    except RuntimeError as e:
        print("[ABORT] %s" % e)
    except Exception as e:
        print("[ABORT] 意外异常：%s" % str(e)[:120])
    after = db_count("xq_status")
    print(
        "[collect] 完成：新增 %d/%d | xq_status %d→%d | 请求 %d 次, "
        "换出口 %d 次, 用时 %.1f 分钟"
        % (
            col.new,
            a.target,
            base,
            after,
            col.requests,
            col.rotations,
            (time.time() - t0) / 60,
        )
    )
    return 0 if col.new >= a.target else 1


if __name__ == "__main__":
    sys.exit(main())
