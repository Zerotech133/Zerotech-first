# -*- coding: utf-8 -*-
"""盘后批量分析：watchlist -> 逐只运行 analysis.run -> 汇总回执。

用法：
    python -m analysis.batch                       # 读 watchlist.txt，默认不跑 LLM
    python -m analysis.batch --codes 600337.SH --llm
    python -m analysis.batch --queue               # 消费门户队列（一次）
    python -m analysis.batch --max 5 --push

设计：作为计划任务入口（--no-llm 默认，避免与生产 Ollama 抢卡）；
每只股票独立子进程（隔离异常），输出落盘到 analysis_work/batch/<date>/。
"""
import argparse
import json
import os
import subprocess
import sys
import time

from . import config, queue

# ── T2FIX 2026-11-07：盘后批启动迟到告警 ─────────────────────────
# 背景：11-06 计划任务因休眠/阻塞 16:48 才补跑（正常 15:40），当日无任何告警；
# T2 盘后核对发现 11-03/11-06 两次非常规启动。这里在 batch 入口做轻量自检：
# 由 launcher 显式传 --late-alert 启用；超过阈值（默认 8 分钟）经 8097 闸门
# 以 kind=daily:alert 发一条文本告警（个人），失败不阻断批处理。
EXPECTED_START_HHMM = os.environ.get("ANALYSIS_BATCH_EXPECTED_START", "15:40")
LATE_ALERT_THRESHOLD_MIN = int(os.environ.get("ANALYSIS_BATCH_LATE_ALERT_MIN", "8"))


def late_alert_check(now=None, threshold_min=None):
    """返回迟到告警 payload；不需要告警返回 None。"""
    now = now or time.localtime()
    if now.tm_wday >= 5:  # 周末非交易日不告警
        return None
    try:
        eh, em = [int(x) for x in str(EXPECTED_START_HHMM).split(":")[:2]]
    except Exception:
        return None
    expected = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, eh, em, 0, 0, 0, -1))
    lag_min = (time.mktime(now) - expected) / 60.0
    limit = LATE_ALERT_THRESHOLD_MIN if threshold_min is None else int(threshold_min)
    if lag_min <= limit:
        return None
    return {
        "date": time.strftime("%Y-%m-%d", now),
        "expected": EXPECTED_START_HHMM,
        "actual": time.strftime("%H:%M:%S", now),
        "lag_min": round(lag_min, 1),
        "threshold_min": limit,
    }


def _receivers_path():
    return os.path.join(config.MAIN_ROOT, "quant_backtest", "receivers.json")


def _personal_open_id():
    try:
        with open(_receivers_path(), encoding="utf-8") as f:
            data = json.load(f)
        for item in (data if isinstance(data, list) else data.get("receivers") or []):
            name = str(item.get("name") or item.get("alias") or "")
            if name in ("个人", "老田", "personal"):
                return item.get("open_id") or item.get("id")
    except Exception:
        pass
    return os.environ.get("ANALYSIS_ALERT_OPEN_ID")


def send_late_alert(payload, dry_run=False):
    """经 8097 闸门发迟到告警（kind=daily:alert）；fail-safe，不抛异常。"""
    text = ("⚠️ 盘后批启动迟到\n"
            "日期：%s\n计划：%s\n实际：%s\n延迟：%s 分钟（阈值 %s 分钟）\n"
            "说明：计划任务可能被休眠/并发任务阻塞；批处理已继续执行，请关注回执。"
            % (payload["date"], payload["expected"], payload["actual"],
               payload["lag_min"], payload["threshold_min"]))
    if dry_run:
        return {"ok": True, "dry_run": True, "text": text}
    try:
        rid = _personal_open_id()
        if not rid:
            return {"ok": False, "why": "no receiver open_id"}
        from news_collector import feishu_client
        ok, why = feishu_client.send_msg(text, "text", receive_id=rid,
                                         receive_id_type="open_id", kind="daily:alert")
        return {"ok": bool(ok), "why": str(why)[:200]}
    except Exception as e:
        return {"ok": False, "why": str(e)[:200]}



def load_watchlist(path=None):
    p = path or os.path.join(config.PKG, "watchlist.txt")
    if not os.path.exists(p):
        return []
    codes = []
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        codes.append(line.split()[0])
    return codes


def run_one(code, llm=False, push=False, as_of=None, timeout=300):
    cmd = [sys.executable, "-X", "utf8", "-m", "analysis.run", "--code", code]
    if not llm:
        cmd.append("--no-llm")
    if not push:
        pass  # run.py 默认不推送
    else:
        cmd.append("--push")
    if as_of:
        cmd += ["--as-of", as_of]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = config.MAIN_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=config.MAIN_ROOT, capture_output=True, text=True, timeout=timeout, env=env)
        out = (r.stdout or "").strip().splitlines()
        last = out[-1] if out else ""
        try:
            res = json.loads(last) if last.startswith("{") else {"ok": r.returncode == 0}
        except Exception:
            res = {"ok": r.returncode == 0, "stdout_tail": out[-1][:200]}
        res["returncode"] = r.returncode
        res["elapsed_s"] = round(time.time() - t0, 1)
        res["stderr_tail"] = (r.stderr or "")[-300:]
        return res
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "elapsed_s": round(time.time() - t0, 1)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "elapsed_s": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="")
    ap.add_argument("--watchlist", default=None)
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--queue", action="store_true", help="消费门户队列（一次）")
    ap.add_argument("--late-alert", action="store_true",
                    help="启用盘后批启动迟到告警（计划任务 launcher 传；超阈值经 8097 发个人）")
    ap.add_argument("--late-alert-dry-run", action="store_true", help="只计算并打印迟到告警，不实际发送")
    ap.add_argument("--late-threshold-min", type=int, default=None, help="迟到阈值（分钟，默认 8）")
    a = ap.parse_args()

    started = time.strftime("%F %T")
    late_alert = None
    if a.late_alert:
        payload = late_alert_check(threshold_min=a.late_threshold_min)
        if payload:
            late_alert = send_late_alert(payload, dry_run=a.late_alert_dry_run)
            late_alert["payload"] = payload
            print(json.dumps({"late_alert": late_alert}, ensure_ascii=False), flush=True)

    codes = [c.strip() for c in a.codes.split(",") if c.strip()] if a.codes else load_watchlist(a.watchlist)
    if a.max:
        codes = codes[:a.max]

    day = time.strftime("%Y-%m-%d")
    outdir = os.path.join(config.WORK, "batch", day)
    os.makedirs(outdir, exist_ok=True)
    results = []

    def _pending(code):
        try:
            from . import review
            return len(review.pending(code=code, limit=500))
        except Exception:
            return None

    if a.queue:
        job = queue.claim()
        if job:
            res = run_one(job["code"], llm=job.get("llm", False), push=job.get("push", False), as_of=None)
            res["pending_review"] = _pending(job["code"])
            queue.finish(job, res)
            results.append({"job": job, "result": res})
            print(json.dumps({"ok": res.get("ok"), "job": job["job_id"], "result": res}, ensure_ascii=False))
        else:
            print(json.dumps({"ok": True, "queue": "empty"}, ensure_ascii=False))
        return 0

    for code in codes:
        res = run_one(code, llm=a.llm, push=a.push, as_of=a.as_of)
        res["pending_review"] = _pending(code)
        results.append({"code": code, "result": res})
        print("[batch] %s ok=%s rating=%s pending_review=%s %.1fs" % (
            code, res.get("ok"), res.get("rating"), res.get("pending_review"), res.get("elapsed_s", 0)))

    pending_total = sum((r["result"].get("pending_review") or 0) for r in results)
    receipt = {"date": day, "started": started, "llm": a.llm, "push": a.push,
               "codes": codes, "results": results, "pending_review_total": pending_total,
               "late_alert": late_alert,
               "ok_count": sum(1 for r in results if r["result"].get("ok")),
               "fail_count": sum(1 for r in results if not r["result"].get("ok"))}
    rp = os.path.join(outdir, "receipt.json")
    json.dump(receipt, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    md = ["# 盘后批量分析回执 · %s" % day, "",
          "- 标的：%d 只；成功 %d；失败 %d" % (len(codes), receipt["ok_count"], receipt["fail_count"]),
          "- 模式：LLM=%s 推送=%s ｜ 待复核事件合计：%d 条" % (a.llm, a.push, pending_total), "",
          "| 代码 | 状态 | 评级 | 待复核 | 用时(s) | 归档 |", "|---|---|---|---|---|---|"]
    for r in results:
        x = r["result"]
        md.append("| %s | %s | %s | %s | %s | %s |" % (r["code"], "OK" if x.get("ok") else "FAIL", x.get("rating", "—"),
                                                       x.get("pending_review", "—"), x.get("elapsed_s", "—"),
                                                       (x.get("outdir") or "")[-40:]))
    open(os.path.join(outdir, "receipt.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(json.dumps({"ok": receipt["fail_count"] == 0, "receipt": rp, "ok_count": receipt["ok_count"],
                      "fail_count": receipt["fail_count"], "pending_review_total": pending_total},
                     ensure_ascii=False))
    return 0 if receipt["fail_count"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
