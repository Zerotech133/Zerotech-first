# -*- coding: utf-8 -*-
"""T1 近期线 headed 探测：Playwright 持久化会话访问雪球用户 timeline。

结局：
  a) 拿到帖子 -> 写 probe_result.json，可 --insert 入库 xq_status（INSERT OR IGNORE）
  b) 滑块 -> 截图 captcha.png，浏览器保持打开等 10 分钟人工过滑块
  c) 其他错误 -> 错误全文写 probe_result.json

用法：
  python tools/xq_user_timeline_probe.py [--uids uid1,uid2] [--insert]
"""

import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright
import logging

REPO = Path(__file__).resolve().parents[1]
PROFILE_DIR = REPO / "data" / "xueqiu" / "_headed_profile"
OUT_DIR = Path(r"C:/aios/automation_runs/xq_probe_headed")
DB_PATH = REPO / "data" / "xueqiu" / "xueqiu_info.db"
DEFAULT_UIDS = ["5679199459", "9411213320", "9600110938", "6403447880", "1340904670"]

HOME_URL = "https://xueqiu.com"
USER_URL = "https://xueqiu.com/u/{uid}"
API_JS = """
async (uid) => {
  try {
    const r = await fetch('/v4/statuses/user_timeline.json?user_id=' + uid + '&page=1&type=0',
                          {credentials: 'include', headers: {'Accept': 'application/json'}});
    if (r.status !== 200) {
      return {err: 'HTTP ' + r.status, body: (await r.text()).slice(0, 300)};
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


def detect_slider(page):
    """检测 WAF 滑块/验证页。返回原因字符串或 None。"""
    url = (page.url or "").lower()
    if any(k in url for k in ("captcha", "geetest", "seccode", "passport.")):
        return "URL 跳转: " + page.url
    try:
        hits = page.locator(
            ".gt_box, .geetest_box, .geetest_slider, .gt_slider, .captcha-container, "
            'iframe[src*="captcha"], iframe[src*="geetest"], #captcha'
        )
        if hits.count() > 0:
            return "captcha 元素存在 (%d 个)" % hits.count()
        body = page.locator("body").inner_text(timeout=3000)
        for kw in ("安全验证", "拖动滑块", "请输入验证码", "请完成安全验证"):
            if kw in body:
                return "页面含「%s」" % kw
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_user_timeline_probe.py", exc)
    return None


def ms_to_iso(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def fetch_api_timeline(page, uid):
    """在页面上下文内带 cookie 调雪球内部 API。"""
    data = page.evaluate(API_JS, uid)
    if not data.get("ok"):
        return None, json.dumps(data, ensure_ascii=False)
    posts = []
    for s in data["statuses"]:
        created = s.get("created_at_ms")
        iso = (
            ms_to_iso(created)
            if isinstance(created, (int, float))
            else str(created or "")
        )
        posts.append(
            {
                "status_id": s["status_id"],
                "created_at": iso,
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
    return posts, None


def parse_dom_timeline(page):
    """API 不可用时的 DOM 兜底解析。"""
    items = page.locator("div.timeline__item")
    n = items.count()
    posts = []
    for i in range(n):
        item = items.nth(i)
        try:
            date_el = item.locator(".timeline__item__info--date").first
            date_txt = date_el.get_attribute("title") or date_el.inner_text(
                timeout=1500
            )
        except Exception:
            date_txt = ""
        try:
            body_el = item.locator(
                ".timeline__item__content--txt, .timeline__item__content"
            ).first
            body_txt = body_el.inner_text(timeout=1500).strip()[:300]
        except Exception:
            body_txt = ""
        sid = ""
        try:
            link = item.locator("a[href]").first
            href = link.get_attribute("href") or ""
            m = [p for p in href.split("/") if p.isdigit()]
            if m:
                sid = m[-1]
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_user_timeline_probe.py", exc)
        posts.append(
            {
                "status_id": sid,
                "created_at": date_txt.strip(),
                "title": "",
                "desc": "",
                "text": body_txt,
                "target": "",
                "view_count": 0,
                "comment_count": 0,
                "like_count": 0,
                "retweet_count": 0,
                "source": "dom",
            }
        )
    return posts


def wait_slider_or_die(page, ctx, out_dir, wait_sec=600, poll=5):
    """结局 b：保持浏览器打开等人工过滑块。返回是否解除。"""
    print("[%s] 检测到滑块，浏览器保持打开 %d 秒等人工处理..." % (now_str(), wait_sec))
    deadline = time.time() + wait_sec
    shot = out_dir / "captcha.png"
    while time.time() < deadline:
        time.sleep(poll)
        try:
            page.bring_to_front()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_user_timeline_probe.py", exc)
        if detect_slider(page) is None:
            try:
                page.screenshot(path=str(shot))
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_user_timeline_probe.py", exc)
            print("[%s] 滑块已解除（人工通过）。" % now_str())
            return True
    try:
        page.screenshot(path=str(shot))
        print("截图已存: %s" % shot)
    except Exception as e:
        print("截图失败: %s" % e)
    return False


def probe_user(ctx, uid, out_dir):
    """探一个用户。返回 (outcome, posts, error)。"""
    page = ctx.new_page()
    page.set_default_timeout(30000)
    try:
        # 1. 首页预热 cookie
        print("[%s] 打开首页预热 cookie..." % now_str())
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(4000)
        reason = detect_slider(page)
        if reason:
            solved = wait_slider_or_die(page, ctx, out_dir)
            if not solved:
                return "b", None, reason
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            reason = detect_slider(page)
            if reason:
                return "b", None, "预热后仍滑块: " + reason

        # 2. 用户页
        print("[%s] 进入用户页 %s" % (now_str(), uid))
        page.goto(
            USER_URL.format(uid=uid), wait_until="domcontentloaded", timeout=45000
        )
        page.wait_for_timeout(5000)
        reason = detect_slider(page)
        if reason:
            solved = wait_slider_or_die(page, ctx, out_dir)
            if not solved:
                return "b", None, reason

        # 3. API 优先
        posts, api_err = fetch_api_timeline(page, uid)
        if posts is not None:
            return "a", posts, None
        print("[%s] API 不可用(%s)，尝试 DOM 解析..." % (now_str(), api_err))
        posts = parse_dom_timeline(page)
        if posts:
            return "a", posts, None
        return (
            "c",
            None,
            "无帖子且无滑块：API=%s；DOM timeline__item=0。页面 title=%s url=%s"
            % (api_err, page.title(), page.url),
        )
    finally:
        try:
            page.close()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/tools/xq_user_timeline_probe.py", exc)


def insert_db(posts, uid):
    """结局 a 收尾：新帖入库 xq_status（INSERT OR IGNORE）。"""
    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    inserted = 0
    for p in posts:
        cur = con.execute(
            "INSERT OR IGNORE INTO xq_status "
            "(status_id, created_at, title, desc, user_id, target, view_count, "
            " comment_count, retweet_count, like_count, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                p.get("status_id"),
                p.get("created_at"),
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
    return inserted


def main():
    uids = DEFAULT_UIDS
    do_insert = "--insert" in sys.argv
    # --headless（2026-10-19 老田令）：无窗口模式，桌面零干扰。
    # 依据：首探实证页内 API 对持久化 profile 直接 200，不依赖可见窗口；
    # headless 下显式 viewport（viewport=None 依赖窗口尺寸，无头态不稳）。
    do_headless = "--headless" in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--uids" and i + 1 < len(sys.argv):
            uids = [u.strip() for u in sys.argv[i + 1].split(",") if u.strip()]

    out_dir = OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    report = {
        "run_at": now_str(),
        "outcome": None,
        "uids_requested": uids,
        "results": {},
        "posts_2024_all": [],
        "error": None,
    }

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=do_headless,
            viewport=None if not do_headless else {"width": 1440, "height": 900},
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            overall_outcome = "a"
            for uid in uids:
                try:
                    outcome, posts, err = probe_user(ctx, uid, out_dir)
                except Exception as e:
                    outcome, posts, err = "c", None, "%s: %s" % (type(e).__name__, e)
                entry = {"outcome": outcome}
                posts_2024 = []
                if posts is not None:
                    entry["n_posts"] = len(posts)
                    entry["posts"] = posts
                    posts_2024 = [
                        x for x in posts if (x.get("created_at") or "") >= "2024-01-01"
                    ]
                    entry["n_posts_2024"] = len(posts_2024)
                    report["posts_2024_all"].extend(posts_2024)
                    if do_insert and posts:
                        try:
                            n = insert_db(posts, uid)
                            entry["db_inserted"] = n
                            print("[%s] uid=%s 入库 %d 条新帖" % (now_str(), uid, n))
                        except Exception as e:
                            entry["db_error"] = str(e)
                if err:
                    entry["error"] = err
                report["results"][uid] = entry
                print(
                    "[%s] uid=%s outcome=%s posts=%s err=%s"
                    % (
                        now_str(),
                        uid,
                        outcome,
                        len(posts) if posts is not None else None,
                        err,
                    )
                )
                if outcome == "b":
                    # 滑块：红线停批（等人工），批量回填保守语义保留
                    overall_outcome = outcome
                    report["error"] = err
                    break
                if outcome != "a":
                    # c 类（页面正常但 timeline 空/API 变体）：2026-10-19 判例
                    # ——页面 title 正常≠封禁，中止整批会浪费其余号段；
                    # 记录后继续下一个 uid（首探语义用 --uids 单人即等价）
                    if overall_outcome != "b":
                        overall_outcome = outcome
                    report["error"] = report.get("error") or err
                    continue
            report["outcome"] = overall_outcome
        finally:
            ctx.close()

    out_json = out_dir / "probe_result.json"
    out_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("结果已写: %s" % out_json)
    print("OUTCOME=%s" % report["outcome"])
    return 0 if report["outcome"] == "a" else (2 if report["outcome"] == "b" else 3)


if __name__ == "__main__":
    sys.exit(main())
