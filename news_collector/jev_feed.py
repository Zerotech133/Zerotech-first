# -*- coding: utf-8 -*-
"""数据中台 news/notice 别名读路径 → JEV 新闻分流接线。

从 quant_backtest 读层 datamart.read('news'/'notice') 按水位增量拉取
（news→tdx stock_news；notice→stock_notice 真源命中、anns_d 降级），
映射为 news.db news 表新行（id=xd:<kind>:md5(url) 幂等，source=xd_news/
xd_notice，stocks=[ts_code]），逐条过 JEV 本地模型判定写回 jev 列——
接入与分流一步完成。下游 pushed/digested/摘要管线无需改动。

水位存 runs 表（kind=jev_feed，detail 记各源日期水位），每轮回退 1 天
重叠拉取 + INSERT OR IGNORE 去重（与 tushare_sync 增量同口径）。
单轮截断（拉满 cap）时水位不动，下轮重叠窗口重拉。

失败可见（2026-10-26 JEV 审计 §四-⑤ 修复：此前 runs.ok 恒 1、失败计成功）：
源失败（dm.read 抛异常/返回 None）或判定阶段整批失败 → runs.ok=0、
detail.errors 带 {source, stage, error} 明细、打印 FEED FAILED、进程 rc=1；
失败源水位不推进（下次从上次成功水位重拉，防丢数被"成功"掩盖）。
真实空数据（空 DataFrame）仍记成功（重拉同窗口幂等，不算失败）。

用法：cd news_collector && python jev_feed.py [--days 2] [--cap 500]
      [--dryrun]
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "quant_backtest"))

import datamart as dm  # noqa: E402
import news_ai  # noqa: E402

DB = os.path.join(HERE, "news.db")
FEED_KIND = "jev_feed"

# 2026-10-26 OS-FIX-JEVFEEDNIGHT：夜批独占窗口守卫。
# 背景：21:00 JevFeed 与 21:50 JevNight 同时本地 judge，抢同一 Ollama
# （NUM_PARALLEL=1）→ 夜批速率从 1,300/h 掉到 612/h（10-26 实锤）。
# 规则：21:45–07:05 或 M3 暂停旗/夜锁存在时，jev_feed 只拉数不判读；
# 待判行留给 JevNight（按 jev IS NULL 取全量）处理，不丢数据。
# 应急开关：JEV_FEED_FORCE_JUDGE=1 强制判读。
NIGHT_GUARD_START_MIN = 21 * 60 + 45  # 21:45
NIGHT_GUARD_END_MIN = 7 * 60 + 5  # 07:05（不含）
NIGHT_PAUSE_FLAG = r"C:\Temp\k266_m3.pause"
NIGHT_LOCK = os.environ.get(
    "JEV_NIGHT_LOCK_PATH",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "locks",
        "jev_night.lock",
    ),
)


def _in_night_window(now=None):
    """是否处于 JevNight 独占窗口（时间窗 + pause/lock 强证据）。"""
    if os.environ.get("JEV_FEED_FORCE_JUDGE") == "1":
        return False
    now = now or datetime.now()
    minutes = now.hour * 60 + now.minute
    if minutes >= NIGHT_GUARD_START_MIN or minutes < NIGHT_GUARD_END_MIN:
        return True
    return os.path.exists(NIGHT_PAUSE_FLAG) or os.path.exists(NIGHT_LOCK)


def _xd_id(kind, url, title):
    """幂等主键：xd:<kind>:md5(url|title) 前 12 位（对齐 sina:5115033 风格）。"""
    h = hashlib.md5((url or title or "").encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    return "xd:%s:%s" % (kind, h)


def _norm_ts(pub_date, pub_time=""):
    """YYYYMMDD(+HH:MM:SS) → 'YYYY-MM-DD HH:MM'（对齐 news.ts 口径）。"""
    d = str(pub_date or "").replace("-", "")[:8]
    if len(d) != 8 or not d.isdigit():
        return None
    t = str(pub_time or "")[:5]
    return "%s-%s-%s %s" % (d[:4], d[4:6], d[6:8], t if len(t) == 5 else "00:00")


def load_watermark():
    """读 runs 表最近一次成功 feed 的各源水位 {kind: YYYYMMDD}。"""
    db = sqlite3.connect(DB)
    try:
        row = db.execute(
            "SELECT detail FROM runs WHERE kind=? AND ok=1 ORDER BY id DESC LIMIT 1",
            (FEED_KIND,),
        ).fetchone()
    finally:
        db.close()
    if not row:
        return {}
    try:
        return json.loads(row[0]).get("wm", {})
    except Exception as e:
        news_ai.log(
            "jev_feed: runs.detail 解析失败，feed 降级为空: %s" % str(e)[:100]
        )  # C2批2
        return {}


def _pick(row, *names):
    """dict 取首个非空字段。"""
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return v
    return ""


def feed_source(alias, kind, wm_date, days, cap, dryrun=False):
    """拉一个别名 → 映射插入 news.db → 返回 (插入数, 拉取数, max_date, error)。

    「真实空数据」与「源失败」严格区分（2026-10-26 JEV 审计 §四-⑤：
    runs.ok 恒 1、失败被计成功）：
    - `datamart.read` 返回空 DataFrame = 真实无新数据 → error=None（成功）；
    - 抛异常 / 返回 None = 源不可用/未就绪 → error=明细（fail-visible，
      调用方据此把 runs.ok 记 0、水位不推进，避免丢数被"成功"掩盖）。
    """
    if wm_date:
        start = (datetime.strptime(wm_date, "%Y%m%d") - timedelta(days=1)).strftime(
            "%Y%m%d"
        )
    else:
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    if dryrun:
        print("  DRY %s <- %s start=%s cap=%d" % (kind, alias, start, cap))
        return 0, 0, None, None
    try:
        df = dm.read(alias, start=start, limit=cap)
    except Exception as e:  # noqa: BLE001
        return 0, 0, None, "dm.read(%r) 异常: %s" % (alias, str(e)[:200])
    if df is None:
        return 0, 0, None, "dm.read(%r) 返回 None（源不可用/未就绪）" % alias
    if df.empty:
        return 0, 0, None, None
    if len(df) >= cap:
        print(
            "  WARN %s 拉满 cap=%d（截断；水位推进策略见主循环 ADV 行）" % (kind, cap)
        )
    db = sqlite3.connect(DB)
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        max_date, n_ins = None, 0
        for row in df.to_dict("records"):
            title = str(_pick(row, "title"))
            if not title:
                continue
            nid = _xd_id(kind, _pick(row, "url"), title)
            ts = _norm_ts(
                _pick(row, "pub_date", "notice_date", "ann_date"),
                _pick(row, "pub_time"),
            )
            if ts is None:
                continue
            # max_date 统计全部可解析行（含已存在重复）：修复活锁——
            # 若仅统计已插入行，截断轮 inserted=0 时 max_date=None，
            # 水位永不前进，同一窗口无限重拉（2026-10-16 实证两轮
            # fetched=500/inserted=0 卡死，日增量无法进窗）。
            d = ts[:10].replace("-", "")
            if max_date is None or d > max_date:
                max_date = d
            cur = db.execute("SELECT 1 FROM news WHERE id=?", (nid,)).fetchone()
            if cur:
                continue
            code = str(_pick(row, "ts_code") or _pick(row, "symbol") or "")
            stocks = [code] if code else []
            db.execute(
                "INSERT INTO news(id, source, ts, title, content, hit, "
                "pushed, digested, created_at, stocks, jev) "
                "VALUES (?,?,?,?,?,0,0,0,?,?,NULL)",
                (
                    nid,
                    "xd_%s" % kind,
                    ts,
                    title,
                    str(_pick(row, "content")),
                    now,
                    json.dumps(stocks, ensure_ascii=False),
                ),
            )
            n_ins += 1
        db.commit()
    finally:
        db.close()
    return n_ins, len(df), max_date, None


def judge_pending(kind, cap, dryrun=False):
    """对本轮 xd_ 来源未判行过 JEV 模型（每批 8 条，内部 0.8s 防限流）。

    返回 {"judged": 实际判成条数, "missing": 判定失败条数, "error": 阶段错误或 None}。
    夜批独占窗口（21:45–07:05 / 暂停旗 / 夜锁）内跳过判读，返回
    {"judged":0,...,"skipped_night":True}——数据留待 JevNight 判，不丢。
    """
    res = {"judged": 0, "missing": 0, "error": None}
    if not dryrun and _in_night_window():
        res["skipped_night"] = True
        print(
            "  SKIP judge %s: JevNight 独占窗口（21:45–07:05 / 暂停旗）；"
            "留待夜批判读" % kind
        )
        return res
    db = sqlite3.connect(DB)
    try:
        rows = db.execute(
            "SELECT id, title, stocks FROM news "
            "WHERE source=? AND (jev IS NULL OR jev='') "
            "ORDER BY ts DESC LIMIT ?",
            ("xd_%s" % kind, cap),
        ).fetchall()
        if not rows:
            return res
        if dryrun:
            print("  DRY judge %s: %d pending" % (kind, len(rows)))
            return res
        for i in range(0, len(rows), 8):
            items = []
            for nid, title, stocks in rows[i : i + 8]:
                try:
                    st = json.loads(stocks) if stocks else []
                except Exception:
                    st = []
                items.append({"id": nid, "title": title, "stocks": st})
            try:
                got = news_ai.jev_judge_batch(items) or {}
            except Exception as e:  # noqa: BLE001
                res["missing"] += len(items)
                res["error"] = res["error"] or (
                    "jev_judge_batch(%s) 异常: %s" % (kind, str(e)[:200])
                )
                continue
            for nid, j in got.items():
                db.execute(
                    "UPDATE news SET jev=? WHERE id=?",
                    (json.dumps(j, ensure_ascii=False), nid),
                )
            db.commit()
            res["judged"] += len(got)
            res["missing"] += len(items) - len(got)
            if items and not got and res["error"] is None:
                res["error"] = "jev_judge_batch(%s) 全部失败（0/%d 条判成）" % (
                    kind,
                    len(items),
                )
            time.sleep(0.5)
        return res
    finally:
        db.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2, help="无水位（首跑）回看天数")
    ap.add_argument("--cap", type=int, default=500, help="每源单轮拉取/判定上限")
    ap.add_argument("--dryrun", action="store_true")
    args = ap.parse_args(argv)

    wm = load_watermark()
    print(
        "=== jev_feed 水位=%s days=%d cap=%d dryrun=%s ==="
        % (wm or "无(首跑)", args.days, args.cap, args.dryrun)
    )
    summary = {"wm": {}, "src": {}, "errors": []}
    errors = summary["errors"]
    t0 = time.time()
    total_ins = 0

    def _fail(kind, stage, msg):
        """阶段失败留痕：detail.errors + stdout/stderr 双写（err log 供告警）。"""
        errors.append({"source": kind, "stage": stage, "error": msg})
        line = "FEED FAILED %s stage=%s: %s" % (kind, stage, msg)
        print(line)
        print(line, file=sys.stderr)

    for alias, kind in [("news", "news"), ("notice", "notice")]:
        n_ins, n_fetched, max_date, err = feed_source(
            alias, kind, wm.get(kind), args.days, args.cap, dryrun=args.dryrun
        )
        # 水位推进策略（2026-10-16 活锁修复 + 2026-10-26 源失败不推进）：
        #   源失败 → 不写 wm（下次从上次成功水位重拉，避免丢数被"成功"掩盖）；
        #   未截断 → 推进到本轮 max_date（原行为）；
        #   截断且有新插入 → 推进（存量积压排水：INSERT OR IGNORE +
        #     1 天重叠兜底回看）；
        #   截断且零插入 → 也推进（活锁破除：整窗全为重复/坏日期行，
        #     不推进则永远卡死，实证 21:24/23:55 两轮 fetched=500
        #     inserted=0 无限重拉）。
        if err:
            _fail(kind, "feed", err)
        elif not args.dryrun and max_date:
            if n_fetched >= args.cap:
                print(
                    "  ADV %s 截断轮推进水位→%s（插入=%d，%s）"
                    % (kind, max_date, n_ins, "排水" if n_ins else "活锁破除")
                )
            summary["wm"][kind] = max_date
        summary["src"][kind] = {"inserted": n_ins, "fetched": n_fetched}
        total_ins += n_ins
        print(
            "  %s <- %s: inserted=%d fetched=%d max=%s"
            % (kind, alias, n_ins, n_fetched, max_date or "-")
        )
        jr = judge_pending(kind, args.cap, dryrun=args.dryrun)
        if jr.get("skipped_night"):
            summary["src"][kind]["judge_skipped_night"] = True
        if jr["judged"]:
            summary["src"][kind]["judged"] = jr["judged"]
            print("  %s judged=%d" % (kind, jr["judged"]))
        if jr["missing"]:
            # 单条判定失败不硬失败（避免个别坏条让整轮红），但留明细可追
            summary["src"][kind]["judge_missing"] = jr["missing"]
            print(
                "  WARN %s judge_missing=%d（下轮仍会被重判）" % (kind, jr["missing"])
            )
        if jr["error"]:
            _fail(kind, "judge", jr["error"])
    if args.dryrun:
        print("DRYRUN 结束，未写库。")
        return 0
    ok = not errors
    db = sqlite3.connect(DB)
    try:
        db.execute(
            "INSERT INTO runs(kind, ts, ok, new_count, push_count, detail) "
            "VALUES (?,?,?,?,0,?)",
            (
                FEED_KIND,
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                1 if ok else 0,
                total_ins,
                json.dumps(summary, ensure_ascii=False),
            ),
        )
        db.commit()
    finally:
        db.close()
    if ok:
        print(
            "FEED done inserted=%d wm=%s %.1fs"
            % (total_ins, summary.get("wm", {}), time.time() - t0)
        )
        return 0
    print(
        "FEED FAILED runs.ok=0 errors=%d inserted=%d wm=%s %.1fs"
        % (len(errors), total_ins, summary.get("wm", {}), time.time() - t0)
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
