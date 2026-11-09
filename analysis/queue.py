# -*- coding: utf-8 -*-
"""分析任务队列（文件队列）：门户提交 -> worker/batch 消费。

目录结构（默认 WORK/analysis/queue）：
  pending/<ts>-<code>.json   待执行
  running/<job>.json         执行中（rename 原子占位）
  done/<job>.json            结果
"""
import json
import os
import re
import time

from . import config

QUEUE = os.path.join(config.WORK, "queue")
CODE_RE = re.compile(r"^(60[0-9]{4}|68[0-9]{4}|00[0-9]{4}|30[0-9]{4}|8[0-9]{6})\.(SH|SZ|BJ)$")


def dirs(base=None):
    base = base or QUEUE
    return {k: os.path.join(base, k) for k in ("pending", "running", "done")}


def _ensure(base=None):
    base = base or QUEUE
    for p in dirs(base).values():
        os.makedirs(p, exist_ok=True)
    return base


def submit(code, llm=False, push=False, base=None):
    base = _ensure(base)
    if not CODE_RE.match(code or ""):
        return {"ok": False, "error": "非法代码"}
    job_id = "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), code.replace(".", "_"))
    payload = {"job_id": job_id, "code": code, "llm": bool(llm), "push": bool(push),
               "submitted_at": time.strftime("%F %T")}
    p = os.path.join(dirs(base)["pending"], job_id + ".json")
    tmp = p + ".tmp"
    json.dump(payload, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, p)
    return {"ok": True, "job_id": job_id, "code": code, "llm": bool(llm)}


def claim(base=None):
    base = _ensure(base)
    pend = dirs(base)["pending"]
    run = dirs(base)["running"]
    for fn in sorted(os.listdir(pend)):
        if not fn.endswith(".json"):
            continue
        src = os.path.join(pend, fn)
        dst = os.path.join(run, fn)
        try:
            os.replace(src, dst)
        except OSError:
            continue
        try:
            return json.load(open(dst, encoding="utf-8"))
        except Exception:
            continue
    return None


def finish(job, result, base=None):
    base = _ensure(base)
    job_id = job.get("job_id") if isinstance(job, dict) else str(job)
    out = {"job_id": job_id, "code": (job or {}).get("code"), "finished_at": time.strftime("%F %T"),
           "result": result}
    p = os.path.join(dirs(base)["done"], job_id + ".json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, default=str)
    rp = os.path.join(dirs(base)["running"], job_id + ".json")
    if os.path.exists(rp):
        os.remove(rp)
    return p


def stats(base=None):
    base = _ensure(base)
    return {k: len([f for f in os.listdir(v) if f.endswith(".json")]) for k, v in dirs(base).items()}
