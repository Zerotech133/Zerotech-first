# -*- coding: utf-8 -*-
"""K263：东方财富股吧舆论采集器（公开列表页 SSR 内嵌 JSON，只读不登录）。

数据源：https://guba.eastmoney.com/list,{code6},{page}.html
  页面内嵌 var article_list={"re":[…80 帖/页…]}，字段含
  post_id / post_title / post_publish_time / post_click_count /
  post_comment_count / user_nickname / bullish_bearish(看多看空)。
合规：仅拉列表页公开标题与计数（不进帖正文、不登录），1.5s/页限速。

存储：data/guba/{code6}.jsonl（post_id 去重增量合并，原子写）。

P0 修复（2026-11-02 task-61，依据 deliverables/2026-11-02_guba空文件审计_v1.md）：
  1. fetch 状态可分辨：ok / empty / no_marker / http_4xx|http_5xx / exception；
  2. **空结果不落盘**：无新增帖不写文件（不产生 0 字节空占位；已有文件无变化不重写）；
  3. 退避+重试（总尝试 ≤3 次，指数退避，可配）、每轮随机顺序、分批（默认 ≤15 码/轮）；
  4. 失败率 >20% → exit 非 0（默认 rc=2，可配）并调用
     news_collector/alert_fail_wrap.py guba_crawl <rc> <log>（告警失败不反噬 rc）；
  5. 保持 CLI/数据格式兼容：成功输出仍为 data/guba/{code6}.jsonl（一行一帖）。

用法：
  python guba_crawl.py 000338.SZ --pages 2   # 采潍柴动力吧 2 页
  python guba_crawl.py 000338.SZ,600519.SH --pages 1
  python guba_crawl.py --pool                # 日链模式：近30天缠论池活跃股（随机取 1 批 ≤15 码）
  python guba_crawl.py --pool --batch-index 1 --batch-size 15   # 多窗口：取第 2 批

退出码：0 = 未超失败率阈值（含 empty/无新增）；2 = 失败率 > 阈值（可配 --fail-rate）；
  1 = 参数/内部异常。告警链：rc≠0 时调用 alert_fail_wrap（可用 --no-alert 关闭）。
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "data", "guba")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
THROTTLE = 1.5  # 秒/页
MAX_PAGES = 10  # 硬上限（防失控翻页）
ART_RE = re.compile(r"var article_list=(\{.*?\});?\s*(?:var|</script>)", re.S)

# ── P0（task-61）采集器健康度常量 ──
FETCH_OK = "ok"
FETCH_EMPTY = "empty"
FETCH_NO_MARKER = "no_marker"
FETCH_HTTP_4XX = "http_4xx"
FETCH_HTTP_5XX = "http_5xx"
FETCH_EXCEPTION = "exception"
RETRYABLE_STATUSES = (FETCH_NO_MARKER, FETCH_EXCEPTION, FETCH_HTTP_5XX,
                      "http_429", "http_403", "http_5xx")
MAX_ATTEMPTS = 3            # 总尝试次数上限（含首次；任务要求 ≤3）
BACKOFF_BASE = 5.0          # 指数退避基数（秒）：attempt2=5s、attempt3=10s
BATCH_SIZE_DEFAULT = 15     # 每轮最多处理的码数
FAIL_RATE_THRESHOLD = 0.20  # 失败率阈值（> 阈值 → rc≠0/告警）
FAIL_RC = 2
ALERT_WRAP = os.path.abspath(
    os.path.join(os.path.dirname(HERE), "news_collector", "alert_fail_wrap.py")
)


def code6(tc):
    """000338.SZ / sz000338 → 000338。"""
    c = str(tc or "").strip().lower()
    if "." in c:
        c = c.split(".")[0]
    c = re.sub(r"^(sz|sh|bj)", "", c)
    return c if c.isdigit() and len(c) == 6 else ""


def _http_status_bucket(code):
    try:
        code = int(code)
    except (TypeError, ValueError):
        return FETCH_EXCEPTION
    if code >= 500:
        return FETCH_HTTP_5XX
    if code >= 400:
        return "http_%d" % code
    return FETCH_OK


def fetch_page(c6, page, timeout=12):
    """单页 → (rows, status)；status ∈ {ok, empty, no_marker, http_4xx/5xx, exception}。

    - ok：解析到 ≥1 条有效帖；
    - empty：页面有 article_list 但 0 条有效帖；
    - no_marker：无 article_list（限流/验证页/改版），**绝非"该股无帖"**；
    - http_4xx/http_5xx：HTTP 错误（4xx 带具体码，如 http_429）；
    - exception：网络异常。
    旧调用方如需纯 rows，用 fetch_page_rows()。
    """
    u = "https://guba.eastmoney.com/list,%s%s.html" % (
        c6,
        ",%d" % page if page > 1 else "",
    )
    try:
        r = requests.get(u, headers={"User-Agent": UA}, timeout=timeout)
    except Exception:
        return [], FETCH_EXCEPTION
    bucket = _http_status_bucket(getattr(r, "status_code", 200))
    if bucket != FETCH_OK:
        return [], bucket
    text = getattr(r, "text", "") or ""
    m = ART_RE.search(text)
    if not m:
        return [], FETCH_NO_MARKER
    try:
        j = json.loads(m.group(1))
    except Exception:
        return [], FETCH_NO_MARKER
    out = []
    for a in j.get("re") or []:
        if not a.get("post_id") or not a.get("post_title"):
            continue
        # 资讯(CMS)/帖子区分：media_type 非空为资讯，保留但打标记
        is_news = bool(a.get("media_type") or a.get("cms_media_type"))
        out.append(
            {
                "post_id": a["post_id"],
                "title": str(a["post_title"]).strip()[:120],
                "ts": a.get("post_publish_time") or a.get("post_display_time"),
                "click": a.get("post_click_count") or 0,
                "comment": a.get("post_comment_count") or 0,
                "user": a.get("user_nickname") or "",
                "bb": a.get("bullish_bearish"),  # 1 看多 -1 看空 None 无
                "news": 1 if is_news else 0,
            }
        )
    return out, (FETCH_OK if out else FETCH_EMPTY)


def fetch_page_rows(c6, page, timeout=12):
    """兼容旧语义：只返回 rows（失败/空 → []）。新代码请用 fetch_page() 分辨状态。"""
    return fetch_page(c6, page, timeout=timeout)[0]


def _retryable(status):
    return status in RETRYABLE_STATUSES or status.startswith("http_5")


def fetch_page_with_retry(c6, page, timeout=12, max_attempts=MAX_ATTEMPTS,
                          backoff_base=BACKOFF_BASE, sleep=time.sleep):
    """带指数退避重试的单页 fetch → (rows, status, attempts)。

    只对可重试状态（no_marker / 5xx / 429 / 403 / exception）重试，总尝试 ≤ max_attempts；
    等待序列：backoff_base * 2^(n-1)（默认 5s、10s）。ok/empty 立即返回。
    """
    rows, status, attempts = [], FETCH_EXCEPTION, 0
    while attempts < max(1, int(max_attempts)):
        attempts += 1
        rows, status = fetch_page(c6, page, timeout=timeout)
        if status in (FETCH_OK, FETCH_EMPTY) or not _retryable(status):
            return rows, status, attempts
        if attempts < max_attempts:
            sleep(backoff_base * (2 ** (attempts - 1)))
    return rows, status, attempts


def _atomic_jsonl(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _load_posts(path):
    old, seen = [], set()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                        seen.add(row["post_id"])
                        old.append(row)
                    except Exception:
                        continue
        except OSError:
            pass
    return old, seen


def crawl(tc, pages=2, timeout=12, max_attempts=MAX_ATTEMPTS,
          backoff_base=BACKOFF_BASE, throttle=THROTTLE, sleep=time.sleep):
    """单只：采 pages 页 → 增量合并 jsonl。返回结果 dict（含状态，便于监控）。

    {"code","status":"ok|empty|error","n_new","n_total","pages",
     "page_statuses":[{"page","status","attempts"}]}
    - 传输错误/限流（重试后仍失败）→ status=error；
    - 页面可访问但 0 新帖/0 数据 → status=empty（**不落盘**，不创建空文件）；
    - 有数据/有新增 → status=ok。
    **空结果不落盘**：n_new==0 时不写文件（已有文件不重写，保留 mtime 语义）。
    """
    result = {"code": "", "status": "error", "n_new": 0, "n_total": 0,
              "pages": 0, "page_statuses": [], "error": ""}
    c6 = code6(tc)
    if not c6:
        print("%s: 代码不合法，跳过" % tc)
        result["status"] = "invalid"
        result["error"] = "invalid_code"
        return result
    result["code"] = c6
    path = os.path.join(OUT_DIR, c6 + ".jsonl")
    old, seen = _load_posts(path)
    got, page_statuses = [], []
    maxp = max(1, min(int(pages or 1), MAX_PAGES))
    for p in range(1, maxp + 1):
        rows, status, attempts = fetch_page_with_retry(
            c6, p, timeout=timeout, max_attempts=max_attempts,
            backoff_base=backoff_base, sleep=sleep)
        page_statuses.append({"page": p, "status": status, "attempts": attempts})
        if status == FETCH_OK:
            got.extend(rows)
        elif status == FETCH_EMPTY:
            print("  %s 第 %d 页无有效帖（empty），停止翻页" % (c6, p), flush=True)
            break
        else:
            result["status"] = "error"
            result["error"] = status
            result["pages"] = p
            result["page_statuses"] = page_statuses
            result["n_total"] = len(old)
            print("  %s 第 %d 页失败（%s，尝试 %d 次），本只标记 error"
                  % (c6, p, status, attempts), flush=True)
            return result
        if p < maxp:
            sleep(throttle)
    n_new = 0
    for r in got:
        if r["post_id"] not in seen:
            old.append(r)
            seen.add(r["post_id"])
            n_new += 1
    # ★ 空结果/无变化不落盘：零字节占位正是审计中的 P0 缺陷
    if n_new > 0:
        old.sort(key=lambda r: r.get("ts") or "")
        _atomic_jsonl(path, old)
    result.update({
        # empty = 页面可访问但无任何数据且无历史（不落盘）；ok = 有数据/有新增
        "status": "ok" if (n_new > 0 or old) else "empty",
        "n_new": n_new,
        "n_total": len(old),
        "pages": maxp,
        "page_statuses": page_statuses,
        "max_ts": _max_ts(got),
    })
    return result


def pool_codes(pool_days=30, limit=60):
    """近 N 天缠论池有信号的股票（保序去重，上限 limit）。

    活跃股口径：出过 1/2/3 买卖点的股票，舆论采集的价值密度最高；
    池含指数（.SH/.SZ 大盘）与 US/HK 后缀，只收 6 位 A 股码。
    """
    path = os.path.join(HERE, "data", "chanlun_pool.jsonl")
    if not os.path.exists(path):
        return []
    cutoff = (datetime.now() - timedelta(days=pool_days)).strftime("%Y%m%d")
    seen = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            c = str(r.get("code") or "")
            m = re.match(r"^(\d{6})\.(SZ|SH|BJ)$", c, re.I)
            if not m:
                continue  # 指数/US/HK 不采
            if str(r.get("dt") or "").replace("-", "")[:8] < cutoff:
                continue
            seen.setdefault(m.group(1), c)  # 首见序 = 信号日序
    return list(seen.values())[:limit]


def _chunk(seq, size):
    size = max(0, int(size or 0))
    if size <= 0:
        return [list(seq)]
    return [list(seq[i:i + size]) for i in range(0, len(seq), size)]


def _max_ts(posts):
    best = ""
    for p in posts or []:
        ts = str(p.get("ts") or "")
        if ts > best:
            best = ts
    return best


def _alert_fail(rc, log_path, enabled=True):
    """调用既有告警链（alert_fail_wrap.py guba_crawl <rc> <log>）；异常不反噬 rc。"""
    if not enabled:
        return False
    if not os.path.exists(ALERT_WRAP):
        print("告警包装器不存在，跳过：%s" % ALERT_WRAP, flush=True)
        return False
    try:
        log = log_path or os.path.join(HERE, "logs", "guba_crawl.err")
        r = subprocess.run(
            [sys.executable, "-X", "utf8", ALERT_WRAP, "guba_crawl", str(rc), log],
            capture_output=True, text=True, timeout=120,
        )
        print("告警链已调用：alert_fail_wrap guba_crawl %s（rc=%s）"
              % (rc, r.returncode), flush=True)
        return r.returncode == 0
    except Exception as exc:  # noqa: BLE001 — 告警层异常不影响任务 rc
        print("告警调用异常（已忽略）：%s: %s" % (type(exc).__name__, str(exc)[:160]),
              flush=True)
        return False


def _processed_codes(args):
    """按模式取本轮要处理的码：pool → 随机顺序并取第 batch-index 批（默认 ≤15）；显式清单 → 全部分批。"""
    if args.pool:
        codes = pool_codes(args.pool_days, args.limit)
        if not codes:
            return [], "pool_empty"
        if not args.no_shuffle:
            random.shuffle(codes)
        batches = _chunk(codes, args.batch_size)
        idx = max(0, min(int(args.batch_index or 0), len(batches) - 1))
        return batches[idx], "pool batch %d/%d" % (idx + 1, len(batches))
    codes = [c.strip() for c in (args.codes or "").split(",") if c.strip()]
    if not codes:
        return [], "no_codes"
    if not args.no_shuffle:
        random.shuffle(codes)
    return codes, "explicit"


def main(argv=None):
    ap = argparse.ArgumentParser(description="东方财富股吧采集器（P0 修复版）")
    ap.add_argument(
        "codes",
        nargs="?",
        default=None,
        help="代码（逗号分隔）；省略且 --pool 时自动取池",
    )
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--pool", action="store_true", help="日链模式：近30天缠论池活跃股")
    ap.add_argument("--pool-days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=60)
    # P0 新增参数（均有默认值；旧 CLI 不带也兼容）
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT,
                    help="每批码数上限（默认 15；pool 模式每轮只跑 1 批）")
    ap.add_argument("--batch-index", type=int, default=0,
                    help="pool 模式选第几批（0 起，多窗口轮换用）")
    ap.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS,
                    help="单页总尝试次数（≤3，指数退避）")
    ap.add_argument("--backoff-base", type=float, default=BACKOFF_BASE,
                    help="退避基数秒（attempt2=base、attempt3=2*base）")
    ap.add_argument("--fail-rate", type=float, default=FAIL_RATE_THRESHOLD,
                    help="失败率阈值（超过则 rc≠0 并告警，默认 0.20）")
    ap.add_argument("--seed", type=int, default=None, help="随机顺序种子（可复现/测试）")
    ap.add_argument("--no-shuffle", action="store_true", help="关闭随机顺序（调试用）")
    ap.add_argument("--alert-log", default="", help="告警引用的错误日志路径")
    ap.add_argument("--alert-direct", action="store_true",
                    help="失败率超限时由采集器直接调用 alert_fail_wrap（默认由 launcher 的 "
                         "rc≠0 告警链触发，避免重复告警）；env GUBA_ALERT_DIRECT=1 亦可开启")
    ap.add_argument("--no-alert", action="store_true", help="失败率超限也不调用告警链")
    a = ap.parse_args(argv)
    if a.seed is not None:
        random.seed(a.seed)
    os.makedirs(OUT_DIR, exist_ok=True)
    codes, mode = _processed_codes(a)
    print("模式=%s，本轮码数=%d（batch-size=%d）" % (mode, len(codes), a.batch_size),
          flush=True)
    if not codes:
        summary = {"attempted": 0, "succeeded": 0, "empty": 0, "failed": 0,
                   "invalid": 0, "new_posts": 0, "max_ts": "", "duration_s": 0.0,
                   "fail_rate": 0.0, "rc": 0, "mode": mode}
        print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
        print("无可采代码（%s），正常退出" % mode)
        return 0
    t0 = time.time()
    counts = {"attempted": 0, "succeeded": 0, "empty": 0, "failed": 0,
              "invalid": 0, "new_posts": 0}
    max_ts = ""
    failed_codes = []
    batches = _chunk(codes, a.batch_size)
    for bi, batch in enumerate(batches, 1):
        for i, tc in enumerate(batch, 1):
            res = crawl(tc, pages=a.pages, max_attempts=a.max_attempts,
                        backoff_base=a.backoff_base, sleep=time.sleep)
            counts["attempted"] += 1
            if res["status"] == "ok":
                counts["succeeded"] += 1
            elif res["status"] == "empty":
                counts["empty"] += 1
            elif res["status"] == "invalid":
                counts["invalid"] += 1
            else:
                counts["failed"] += 1
                failed_codes.append(res.get("code") or tc)
            if res["status"] == "ok":
                counts["new_posts"] += res["n_new"]
                max_ts = max(max_ts, str(res.get("max_ts") or ""))
            print(
                "[批%d/%d %d/%d] %s: %s 新增 %d 累计 %d%s"
                % (bi, len(batches), i, len(batch), tc, res["status"],
                   res["n_new"], res["n_total"],
                   (" 原因=%s" % res["error"]) if res.get("error") else ""),
                flush=True,
            )
        if bi < len(batches):
            time.sleep(THROTTLE)
    attempted = counts["attempted"]
    fail_rate = (counts["failed"] / attempted) if attempted else 0.0
    rc = FAIL_RC if (attempted > 0 and fail_rate > a.fail_rate) else 0
    summary = {
        "attempted": attempted,
        "succeeded": counts["succeeded"],
        "empty": counts["empty"],
        "failed": counts["failed"],
        "invalid": counts["invalid"],
        "new_posts": counts["new_posts"],
        "max_ts": max_ts,
        "duration_s": round(time.time() - t0, 1),
        "fail_rate": round(fail_rate, 3),
        "failed_codes": failed_codes[:20],
        "batch_size": a.batch_size,
        "mode": mode,
        "rc": rc,
    }
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
    print("完成：尝试 %d（成功 %d / 空 %d / 失败 %d / 非法 %d），新增 %d 帖，失败率 %.1f%%，rc=%d"
          % (attempted, counts["succeeded"], counts["empty"], counts["failed"],
             counts["invalid"], counts["new_posts"], fail_rate * 100, rc),
          flush=True)
    if rc != 0:
        print("失败率 %.1f%% > 阈值 %.1f%% → rc=%d，触发告警链"
              % (fail_rate * 100, a.fail_rate * 100, rc), flush=True)
        direct = a.alert_direct or os.environ.get("GUBA_ALERT_DIRECT", "") == "1"
        if not direct and not a.alert_log:
            print("告警：由 launcher 的 rc≠0 链调用 alert_fail_wrap（--alert-direct 可直调）",
                  flush=True)
        elif not a.no_alert:
            _alert_fail(rc, a.alert_log, enabled=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
