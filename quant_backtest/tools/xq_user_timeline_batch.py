# -*- coding: utf-8 -*-
"""T1+ 近期线批量回填：headed + 持久化 profile 批量拉取雪球用户 timeline。

纪律：
- 每人之间 sleep 15-25s 随机；每 10 人（一批）后批间休息 90-120s
- 单人最多 200 条 / 10 页 API 分页，翻到 2024-01-01 之前即停
- 遇滑块/验证/连续 API 失败立即停，截图存 batch/，打印断点
- 仅 2024-01-01 后的帖子 INSERT OR IGNORE 入 xq_status
- 支持断点续传：--start-idx / --end-idx / --limit-per-user

用法：
  python tools/xq_user_timeline_batch.py --start-idx 0 --end-idx 20
  python tools/xq_user_timeline_batch.py --start-idx 20 --end-idx 77
"""

import argparse
import json
import os
import random
import sqlite3
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from xq_user_timeline_probe import detect_slider, ms_to_iso  # noqa: E402
import logging

PROFILE_DIR = REPO / "data" / "xueqiu" / "_headed_profile"
POOL_PATH = REPO / "data" / "xueqiu" / "user_pool_sample.json"
DB_PATH = REPO / "data" / "xueqiu" / "xueqiu_info.db"
BATCH_DIR = Path(r"C:/aios/automation_runs/xq_probe_headed/batch")
PROGRESS_LOG = BATCH_DIR / "batch_progress.log"

USER_URL = "https://xueqiu.com/u/{uid}"
HOME_URL = "https://xueqiu.com"
CUTOFF = "2024-01-01"
MAX_PAGES = 10  # 每人最多 API 翻页数（约 200 条，对应 8 屏滚动上限）
PAGESIZE = 20

API_JS = """
async (arg) => {
  try {
    const r = await fetch('/v4/statuses/user_timeline.json?user_id=' + arg.uid +
                          '&page=' + arg.page + '&type=0',
                          {credentials: 'include', headers: {'Accept': 'application/json'}});
    if (r.status !== 200) {
      return {err: 'HTTP ' + r.status, body: (await r.text()).slice(0, 200)};
    }
    const j = await r.json();
    const list = j.statuses || j.list || [];
    return {
      ok: true,
      total: j.total,
      statuses: list.map(s => ({
        status_id: String(s.id),
        created_at_ms: s.created_at,
        title: s.title || '',
        desc: s.description || '',
        text: (s.text || '').slice(0, 300),
        target: s.target || '',
        view_count: s.view_count || 0,
        comment_count: s.comment_count || 0,
        like_count: s.like_count || 0,
        retweet_count: s.retweet_count || 0,
      })),
    };
  } catch (e) {
    return {err: String(e)};
  }
}
"""


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    line = "[%s] %s" % (now_str(), msg)
    try:
        print(line, flush=True)
        BATCH_DIR.mkdir(parents=True, exist_ok=True)
        with open(PROGRESS_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        print("进度日志写入失败（不影响报告状态）: %s" % e, file=sys.stderr, flush=True)


def insert_2024(posts, uid):
    """仅 2024+ 帖子入库。返回 (新增数, 2024+总数)。"""
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    inserted = p2024 = 0
    for p in posts:
        iso = p.get("created_at") or ""
        if iso < CUTOFF:
            continue
        p2024 += 1
        cur = con.execute(
            "INSERT OR IGNORE INTO xq_status "
            "(status_id, created_at, title, desc, user_id, target, view_count, "
            " comment_count, retweet_count, like_count, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                p["status_id"],
                iso,
                p.get("title") or "",
                p.get("desc") or p.get("text") or "",
                str(uid),
                p.get("target") or "",
                p.get("view_count") or 0,
                p.get("comment_count") or 0,
                p.get("retweet_count") or 0,
                p.get("like_count") or 0,
                now_str(),
            ),
        )
        inserted += cur.rowcount
    con.commit()
    con.close()
    return inserted, p2024


def fetch_user_timeline(page, uid, limit_per_user):
    """分页拉取单人 timeline，翻到 cutoff 前即停。返回 (posts, stop_reason)。
    出错时返回已拿到的部分帖子，不丢失。"""
    posts = []
    for pg in range(1, MAX_PAGES + 1):
        if pg == 1:
            time.sleep(random.uniform(2.0, 4.0))  # 进页后稍候再打 API
        data = page.evaluate(API_JS, {"uid": uid, "page": pg})
        if not data.get("ok"):
            return posts, "api_error:" + json.dumps(data, ensure_ascii=False)
        raw = data["statuses"]
        if not raw:
            return posts, "exhausted"
        page_posts = []
        for s in raw:
            page_posts.append(
                {
                    "status_id": s["status_id"],
                    "created_at": ms_to_iso(s["created_at_ms"])
                    if isinstance(s["created_at_ms"], (int, float))
                    else str(s["created_at_ms"] or ""),
                    "title": s["title"],
                    "desc": s["desc"],
                    "text": s["text"],
                    "target": s["target"],
                    "view_count": s["view_count"],
                    "comment_count": s["comment_count"],
                    "like_count": s["like_count"],
                    "retweet_count": s["retweet_count"],
                }
            )
        posts.extend(page_posts)
        # 翻到 2024 前即停（按本页最旧一条判断）
        oldest = min(
            (p["created_at"] for p in page_posts if p["created_at"]), default=""
        )
        if oldest and oldest < CUTOFF:
            return posts, "reached_2024"
        if len(posts) >= limit_per_user:
            return posts, "limit_reached"
        time.sleep(random.uniform(2.0, 4.0))  # 翻页放慢
    return posts, "max_pages"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-idx", type=int, default=0)
    ap.add_argument("--end-idx", type=int, default=77)
    ap.add_argument("--limit-per-user", type=int, default=200)
    args = ap.parse_args()

    try:
        pool = json.loads(POOL_PATH.read_text(encoding="utf-8"))["pool"]
        if not isinstance(pool, list) or not 0 <= args.start_idx < args.end_idx <= len(
            pool
        ):
            raise ValueError("批次范围无效")
        ids = [u.get("user_id") if isinstance(u, dict) else None for u in pool]
        if any(
            not isinstance(uid, str)
            or not uid.isascii()
            or not uid.isdecimal()
            or int(uid) == 0
            for uid in ids
        ) or len(ids) != len(set(ids)):
            raise ValueError("候选池 UID 必须是全局唯一的 ASCII 正整数")
    except (OSError, ValueError, KeyError, TypeError) as e:
        print("候选池或批次参数无效: %s" % e, file=sys.stderr, flush=True)
        return 1
    targets = pool[args.start_idx : args.end_idx]
    try:
        BATCH_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print("批次目录不可写: %s" % e, file=sys.stderr, flush=True)
        return 1
    log(
        "=== 批量回填启动: idx[%d:%d) 共 %d 人 ==="
        % (args.start_idx, args.end_idx, len(targets))
    )

    report = {
        "run_at": now_str(),
        "run_id": uuid.uuid4().hex,
        "start_idx": args.start_idx,
        "end_idx": args.end_idx,
        "target_count": len(targets),
        "completed_count": 0,
        "outcome": None,
        "users": {},
        "totals": {"posts": 0, "posts_2024": 0, "db_inserted": 0},
        "stop_reason": None,
        "next_start_idx": None,
    }

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            viewport=None,
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()
        page.set_default_timeout(30000)
        try:
            # 首页预热一次
            log("首页预热 cookie...")
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            reason = detect_slider(page)
            if reason:
                page.screenshot(path=str(BATCH_DIR / "captcha.png"))
                report.update(
                    outcome="b",
                    stop_reason="预热即滑块: " + reason,
                    next_start_idx=args.start_idx,
                )
                log("预热即触发滑块: %s，已停。" % reason)
                return finish(report)

            for i, u in enumerate(targets):
                idx = args.start_idx + i
                uid = u["user_id"]
                try:
                    page.goto(
                        USER_URL.format(uid=uid),
                        wait_until="domcontentloaded",
                        timeout=45000,
                    )
                    page.wait_for_timeout(5000)
                    reason = detect_slider(page)
                    if reason:
                        page.screenshot(path=str(BATCH_DIR / "captcha.png"))
                        report.update(
                            outcome="b",
                            stop_reason="第 %d 人 uid=%s 触发滑块: %s"
                            % (idx, uid, reason),
                            next_start_idx=idx,
                        )
                        log("STOP 在第 %d 人(uid=%s)触发滑块: %s" % (idx, uid, reason))
                        return finish(report)
                    posts, stop_reason = fetch_user_timeline(
                        page, uid, args.limit_per_user
                    )
                    if stop_reason.startswith("api_error"):
                        # 取证 + 退避后重试一次（HTML 软拦截可能是瞬时限流）
                        try:
                            page.screenshot(
                                path=str(BATCH_DIR / ("api_err_idx%d.png" % idx))
                            )
                        except Exception as exc:
                            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_user_timeline_batch.py", exc)
                        reason2 = detect_slider(page)
                        if reason2:
                            page.screenshot(path=str(BATCH_DIR / "captcha.png"))
                            report.update(
                                outcome="b",
                                stop_reason="第 %d 人 uid=%s API异常后确认滑块: %s"
                                % (idx, uid, reason2),
                                next_start_idx=idx,
                            )
                            log(
                                "STOP 第 %d 人(uid=%s) API异常后确认滑块: %s"
                                % (idx, uid, reason2)
                            )
                            return finish(report)
                        backoff = random.uniform(30, 60)
                        log(
                            "idx=%d uid=%s API 异常，退避 %.0fs 后重试一次..."
                            % (idx, uid, backoff)
                        )
                        time.sleep(backoff)
                        page.goto(
                            USER_URL.format(uid=uid),
                            wait_until="domcontentloaded",
                            timeout=45000,
                        )
                        page.wait_for_timeout(5000)
                        if detect_slider(page):
                            page.screenshot(path=str(BATCH_DIR / "captcha.png"))
                            report.update(
                                outcome="b",
                                stop_reason="第 %d 人 uid=%s 重试后出现滑块"
                                % (idx, uid),
                                next_start_idx=idx,
                            )
                            log("STOP 第 %d 人(uid=%s) 重试后出现滑块" % (idx, uid))
                            return finish(report)
                        posts, stop_reason = fetch_user_timeline(
                            page, uid, args.limit_per_user
                        )
                except Exception as e:
                    posts, stop_reason = [], "exception:%s: %s" % (type(e).__name__, e)

                if stop_reason.startswith("api_error") or stop_reason.startswith(
                    "exception"
                ):
                    entry = {
                        "outcome": "c",
                        "n_posts": 0,
                        "n_posts_2024": 0,
                        "db_inserted": 0,
                        "error": stop_reason,
                    }
                    report["users"][uid] = entry
                    report.update(
                        outcome="c",
                        stop_reason="第 %d 人 uid=%s 失败: %s"
                        % (idx, uid, stop_reason),
                        next_start_idx=idx,
                    )
                    log("STOP 第 %d 人(uid=%s) 最终失败: %s" % (idx, uid, stop_reason))
                    return finish(report)
                else:
                    inserted, n2024 = insert_2024(posts, uid) if posts else (0, 0)
                    entry = {
                        "outcome": "a",
                        "n_posts": len(posts),
                        "n_posts_2024": n2024,
                        "db_inserted": inserted,
                        "stop_reason": stop_reason,
                    }
                    report["totals"]["posts"] += len(posts)
                    report["totals"]["posts_2024"] += n2024
                    report["totals"]["db_inserted"] += inserted

                report["users"][uid] = entry
                report["completed_count"] += 1
                log(
                    "idx=%d uid=%s posts=%d 2024+=%d 新增=%d stop=%s"
                    % (
                        idx,
                        uid,
                        entry["n_posts"],
                        entry["n_posts_2024"],
                        entry["db_inserted"],
                        stop_reason,
                    )
                )
                if report["outcome"] is None:
                    report["outcome"] = "a"

                # 批间休息（每 10 人，最后一人后不休息）
                if (idx + 1) % 10 == 0 and i + 1 < len(targets):
                    rest = random.uniform(90, 120)
                    log("--- 批间休息 %.0fs (已完成 %d 人) ---" % (rest, idx + 1))
                    time.sleep(rest)
                elif i + 1 < len(targets):
                    time.sleep(random.uniform(15, 25))  # 人际限速
        finally:
            ctx.close()

    return finish(report)


def finish(report):
    complete = (
        report.get("target_count", 0) > 0
        and report.get("completed_count") == report["target_count"]
        and len(report["users"]) == report["target_count"]
        and all(u["outcome"] == "a" for u in report["users"].values())
    )
    if report["outcome"] == "a" and not complete:
        report["outcome"] = "c"
        report["stop_reason"] = report.get("stop_reason") or "批次未全部成功完成"
    elif report["outcome"] is None:
        report["outcome"] = "c"
        report["stop_reason"] = report.get("stop_reason") or "批次未处理任何目标"
    name = BATCH_DIR / (
        "batch_result_%d_%d_%s.json"
        % (report["start_idx"], report["end_idx"], report["run_id"])
    )
    temp = name.with_name(name.name + ".%s.tmp" % uuid.uuid4().hex)
    created = False
    try:
        with temp.open("x", encoding="utf-8") as f:
            created = True
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.link(temp, name)
    except (OSError, ValueError, TypeError) as e:
        print("结果写入失败: %s: %s" % (name, e), file=sys.stderr, flush=True)
        return 1
    finally:
        if created:
            try:
                temp.unlink()
            except OSError as e:
                print(
                    "临时结果清理失败: %s: %s" % (temp, e), file=sys.stderr, flush=True
                )
    result = 0 if report["outcome"] == "a" and complete else 1
    try:
        log("结果已写: %s" % name)
        log("OUTCOME=%s totals=%s" % (report["outcome"], json.dumps(report["totals"])))
        if report.get("next_start_idx") is not None:
            log("断点: 下次用 --start-idx %d" % report["next_start_idx"])
    except OSError as e:
        print(
            "进度日志失败（不影响已发布报告及退出码）: %s" % e,
            file=sys.stderr,
            flush=True,
        )
    return result


if __name__ == "__main__":
    sys.exit(main())
