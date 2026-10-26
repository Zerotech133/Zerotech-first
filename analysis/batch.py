# -*- coding: utf-8 -*-
"""盘后批量分析：watchlist -> 逐只运行 analysis.run -> 汇总回执。

用法：
    python -m analysis.batch                       # 读 watchlist.txt，默认不跑 LLM
    python -m analysis.batch --codes 600337.SH --llm
    python -m analysis.batch --queue               # 消费门户队列（一次）
    python -m analysis.batch --max 5 --push

设计：作为计划任务入口（--no-llm 默认，避免与生产 Ollama 抢卡）；
每只股票独立子进程（隔离异常），输出落盘到 work/analysis/batch/<date>/。
"""
import argparse
import json
import os
import subprocess
import sys
import time

from . import config, queue


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
    a = ap.parse_args()

    codes = [c.strip() for c in a.codes.split(",") if c.strip()] if a.codes else load_watchlist(a.watchlist)
    if a.max:
        codes = codes[:a.max]

    day = time.strftime("%Y-%m-%d")
    outdir = os.path.join(config.WORK, "batch", day)
    os.makedirs(outdir, exist_ok=True)
    results = []

    if a.queue:
        job = queue.claim()
        if job:
            res = run_one(job["code"], llm=job.get("llm", False), push=job.get("push", False), as_of=None)
            queue.finish(job, res)
            results.append({"job": job, "result": res})
            print(json.dumps({"ok": res.get("ok"), "job": job["job_id"], "result": res}, ensure_ascii=False))
        else:
            print(json.dumps({"ok": True, "queue": "empty"}, ensure_ascii=False))
        return 0

    for code in codes:
        res = run_one(code, llm=a.llm, push=a.push, as_of=a.as_of)
        results.append({"code": code, "result": res})
        print("[batch] %s ok=%s rating=%s %.1fs" % (code, res.get("ok"), res.get("rating"), res.get("elapsed_s", 0)))

    receipt = {"date": day, "started": time.strftime("%F %T"), "llm": a.llm, "push": a.push,
               "codes": codes, "results": results,
               "ok_count": sum(1 for r in results if r["result"].get("ok")),
               "fail_count": sum(1 for r in results if not r["result"].get("ok"))}
    rp = os.path.join(outdir, "receipt.json")
    json.dump(receipt, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    md = ["# 盘后批量分析回执 · %s" % day, "",
          "- 标的：%d 只；成功 %d；失败 %d" % (len(codes), receipt["ok_count"], receipt["fail_count"]),
          "- 模式：LLM=%s 推送=%s" % (a.llm, a.push), "",
          "| 代码 | 状态 | 评级 | 用时(s) | 归档 |", "|---|---|---|---|---|"]
    for r in results:
        x = r["result"]
        md.append("| %s | %s | %s | %s | %s |" % (r["code"], "OK" if x.get("ok") else "FAIL", x.get("rating", "—"),
                                                   x.get("elapsed_s", "—"), (x.get("outdir") or "")[-40:]))
    open(os.path.join(outdir, "receipt.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(json.dumps({"ok": receipt["fail_count"] == 0, "receipt": rp, "ok_count": receipt["ok_count"],
                      "fail_count": receipt["fail_count"]}, ensure_ascii=False))
    return 0 if receipt["fail_count"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
