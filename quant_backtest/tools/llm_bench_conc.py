# -*- coding: utf-8 -*-
"""LLM 队列并发吞吐基准（conc-bench 工位 2026-10-18）。

回答一个决策问题：把 Ollama 并行度（OLLAMA_NUM_PARALLEL）调上去，
能不能成倍提高分类追平吞吐？

方法：分类库只读抽 4 条代表性文本（短/中长混合，已分类行优先），
并发度 {1, 2, 4} 各跑一轮（4×(1+2+4)=28 次调用 ≤ 32 红线），
每级记录聚合墙钟、条/分钟、聚合 tok/s（Ollama eval_duration/eval_count
汇总）、单条墙钟分布、背景负载代理（classified 总数差）；
顺带取证 `ollama ps`（当前设备承载形态，不臆断 CPU/GPU）。

纪律（任务书铁律）：
  - 只读 DB（mode=ro + busy_timeout），不写库；
  - 单条护栏 240s，fail-visible：Ollama 不可达 → 退出码 2，不硬重试；
  - 本脚本只出数与提案，绝不重启 ollama 服务、绝不改其配置；
  - 局限：背景有 automation 单流负载、样本 4 条、单轮无重复。

用法：
  python tools/llm_bench_conc.py                 # 全量 28 次调用
  python tools/llm_bench_conc.py --out <path>    # 自定义输出
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

import xq_classify as xq  # noqa: E402
from tools.safe_urlopen import safe_urlopen

DB_PATH = xq.DB_PATH
DEFAULT_OUT = (
    Path(r"C:\aios\automation_runs") / "llm_bench" / "conc_bench_2026-10-18.json"
)
BENCH_TIMEOUT = 240  # 单条护栏：墙钟含 automation 队列等待，不能短
SAMPLE_N = 4
CONC_LEVELS = [1, 2, 4]
MAX_CALLS = 32  # 红线：本轮全部基准调用 ≤32 次
GENERATE_PATH = "/api/generate"  # 任务书指定直连 /api/generate（stream:false）


def probe_ollama(
    host: str = "127.0.0.1", port: int = 11434, timeout: float = 3.0
) -> bool:
    """TCP 可达性探测（判例同 llm_bench：用 python socket）。"""
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def pick_evenly(items: list, n: int, key=None) -> list:
    """按长度排序后均匀百分位取样（保证短/中/长混合，两端必覆盖）。"""
    items = sorted(items, key=key) if key else sorted(items)
    if len(items) <= n:
        return items
    idx = [round(i * (len(items) - 1) / (n - 1)) for i in range(n)]
    out, seen = [], set()
    for i in idx:
        if i not in seen:
            out.append(items[i])
            seen.add(i)
    return out


def fetch_sample_rows(db_path: Path, n: int = SAMPLE_N) -> list:
    """只读抽样：已分类(parse_ok=1)非空行优先，不足时用未分类非空行补齐。

    返回 [{status_id, body, chars}]，body 口径与 classify_one 一致
    （title\\ndesc 截 BODY_CHARS+60）。
    """
    con = sqlite3.connect(
        "file:%s?mode=ro" % Path(db_path).as_posix(), uri=True, timeout=30
    )
    con.execute("PRAGMA busy_timeout=30000")
    nonempty = "NOT ((s.title IS NULL OR s.title='') AND (s.desc IS NULL OR s.desc=''))"
    rows = con.execute(
        "SELECT s.status_id, s.title, s.desc FROM xq_status_classified c "
        "JOIN xq_status s ON s.status_id = c.status_id "
        "WHERE c.parse_ok = 1 AND %s" % nonempty
    ).fetchall()
    pool = [_mk_sample(r) for r in rows]
    if len(pool) < n:
        rows2 = con.execute(
            "SELECT s.status_id, s.title, s.desc FROM xq_status s "
            "WHERE s.status_id NOT IN (SELECT status_id FROM xq_status_classified) "
            "AND %s" % nonempty
        ).fetchall()
        have = {p["status_id"] for p in pool}
        pool += [_mk_sample(r) for r in rows2 if str(r[0]) not in have]
    con.close()
    return pick_evenly(pool, n, key=lambda p: p["chars"])


def _mk_sample(row: tuple) -> dict:
    status_id, title, desc = row
    body = ((title or "") + "\n" + (desc or "")).strip()[: xq.BODY_CHARS + 60]
    return {"status_id": str(status_id), "body": body, "chars": len(body)}


def count_classified(db_path: Path) -> int:
    """背景负载代理：classified 总数（bench 起止差 = automation 推进量）。"""
    con = sqlite3.connect(
        "file:%s?mode=ro" % Path(db_path).as_posix(), uri=True, timeout=30
    )
    con.execute("PRAGMA busy_timeout=30000")
    n = con.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0]
    con.close()
    return int(n)


def ollama_ps() -> dict:
    """取证 `ollama ps`（模型、大小、CPU/GPU 占用），失败不致命。"""
    for cmd in (
        ["ollama", "ps"],
        [str(Path.home() / "AppData/Local/Programs/Ollama/ollama.exe"), "ps"],
    ):
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15, errors="replace"
            )
            return {
                "cmd": cmd[0],
                "exit_code": r.returncode,
                "stdout": (r.stdout or "").strip(),
                "stderr": (r.stderr or "").strip()[:300],
            }
        except FileNotFoundError:
            continue
        except Exception as e:  # noqa: BLE001
            return {"cmd": cmd[0], "exit_code": None, "error": str(e)[:200]}
    return {
        "cmd": "ollama",
        "exit_code": None,
        "error": "ollama CLI not found in PATH or default install dir",
    }


def parse_verdict(answer: str) -> dict:
    """判定解析（口径同 xq_classify.classify_one 的 JSON 契约）。"""
    v = {"event_type": None, "sentiment": None, "content_type": None, "parse_ok": 0}
    try:
        m = re.search(r"\{.*\}", answer or "", re.S)
        obj = json.loads(m.group(0)) if m else {}
    except Exception:
        return v
    if obj.get("event_type") in xq.EVENT_TYPES:
        v["event_type"] = obj["event_type"]
    if obj.get("content_type") in xq.CONTENT_TYPES:
        v["content_type"] = obj["content_type"]
    if obj.get("sentiment") in xq.SENTIMENTS:
        v["sentiment"] = obj["sentiment"]
    v["parse_ok"] = 1 if (v["event_type"] and v["sentiment"]) else 0
    return v


def http_sender(url: str, payload: dict, timeout: float) -> dict:
    """真 sender：POST /api/generate（stream:false），返回解析后的响应 JSON。"""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with safe_safe_urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def call_one(
    sender,
    url: str,
    model: str,
    system: str,
    body: str,
    options: dict,
    timeout: float = BENCH_TIMEOUT,
) -> dict:
    """单次 /api/generate 调用 + 指标提取。异常不抛（记 error 字段），不重试。"""
    payload = {
        "model": model,
        "prompt": body or "（无文字内容）",
        "system": system,
        "stream": False,
        "think": False,
        "options": dict(options),
    }
    rec: dict = {"options": dict(options), "error": None}
    t0 = time.time()
    try:
        resp = sender(url, payload, timeout)
        rec["wall_s"] = round(time.time() - t0, 3)
        rec["eval_duration_s"] = round(resp.get("eval_duration", 0) / 1e9, 3)
        rec["eval_count"] = resp.get("eval_count")
        rec["prompt_eval_count"] = resp.get("prompt_eval_count")
        content = resp.get("response", "")  # /api/generate 字段是 response
        rec["content_head"] = (content or "")[:120]
        rec["verdict"] = parse_verdict(content)
        rec["parse_ok"] = rec["verdict"]["parse_ok"]
    except Exception as e:  # noqa: BLE001
        rec["wall_s"] = round(time.time() - t0, 3)
        rec["error"] = str(e)[:200]
    return rec


def run_level(
    samples: list,
    workers: int,
    sender=http_sender,
    url: str = "",
    model: str = "",
    system: str = "",
    options: dict | None = None,
    timeout: float = BENCH_TIMEOUT,
) -> dict:
    """一轮并发基准：全部样本提交给 workers 线程池，聚合等待完成。

    返回 {workers, agg_wall_s, records, calls_ok, errors}。
    """
    options = options or {}
    agg0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(
                call_one, sender, url, model, system, s["body"], options, timeout
            ): s
            for s in samples
        }
        results = [(fut, futs[fut]) for fut in list(futs)]
        records = []
        for fut, s in results:
            rec = fut.result()
            rec.update({"status_id": s["status_id"], "chars": s["chars"]})
            records.append(rec)
    agg_wall = round(time.time() - agg0, 3)
    records.sort(key=lambda r: (r["chars"], r["status_id"]))
    for i, r in enumerate(records):
        r["sample_id"] = i
    ok = [r for r in records if not r["error"]]
    print(
        "  [workers=%d] agg_wall=%ss ok=%d/%d err=%d"
        % (workers, agg_wall, len(ok), len(records), len(records) - len(ok)),
        flush=True,
    )
    return {
        "workers": workers,
        "agg_wall_s": agg_wall,
        "records": records,
        "calls_ok": len(ok),
        "errors": len(records) - len(ok),
    }


def _stats(values: list) -> dict | None:
    if not values:
        return None
    v = sorted(values)
    mid = len(v) // 2
    med = v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2
    return {
        "count": len(v),
        "mean": round(sum(v) / len(v), 3),
        "median": round(med, 3),
        "min": round(v[0], 3),
        "max": round(v[-1], 3),
    }


def summarize_level(level_out: dict, agg_wall_s: float | None = None) -> dict:
    """单级汇总：条/分钟、聚合 tok/s（双口径）、单条墙钟分布。"""
    rs = [r for r in level_out["records"] if not r["error"]]
    agg = agg_wall_s if agg_wall_s is not None else level_out["agg_wall_s"]
    items_per_min = round(len(rs) / agg * 60, 2) if agg > 0 else 0.0
    eval_counts = [
        r["eval_count"] for r in rs if r.get("eval_count") and r.get("eval_duration_s")
    ]
    eval_secs = [
        r["eval_duration_s"]
        for r in rs
        if r.get("eval_count") and r.get("eval_duration_s")
    ]
    eval_tok = (
        round(sum(eval_counts) / sum(eval_secs), 2)
        if eval_secs and sum(eval_secs) > 0
        else None
    )
    wall_tok = (
        round(sum(r["eval_count"] for r in rs if r.get("eval_count")) / agg, 2)
        if agg > 0
        else None
    )
    return {
        "calls_total": len(level_out["records"]),
        "calls_ok": len(rs),
        "errors": level_out["errors"],
        "agg_wall_s": agg,
        "items_per_min": items_per_min,
        "eval_tok_per_s": eval_tok,
        "wall_tok_per_s": wall_tok,
        "item_wall_s": _stats([r["wall_s"] for r in rs]),
    }


def run_all_concurrency(
    samples: list,
    sender=http_sender,
    url: str = "",
    model: str = "",
    system: str = "",
    options: dict | None = None,
    timeout: float = BENCH_TIMEOUT,
    levels: list | None = None,
) -> list:
    """三级并发各跑一轮，返回 [{level_out, summary, classified_before/after}]。"""
    options = options or {}
    levels = levels if levels is not None else CONC_LEVELS
    budget = len(samples) * sum(levels)
    if budget > MAX_CALLS:
        raise RuntimeError("调用预算 %d 超红线 %d" % (budget, MAX_CALLS))
    stages = []
    for w in levels:
        before = count_classified(DB_PATH)
        t0 = time.time()
        level_out = run_level(
            samples,
            workers=w,
            sender=sender,
            url=url,
            model=model,
            system=system,
            options=options,
            timeout=timeout,
        )
        after = count_classified(DB_PATH)
        sm = summarize_level(level_out)
        sm["classified_before"] = before
        sm["classified_after"] = after
        sm["classified_delta"] = after - before
        sm["stage_wall_s_incl_dbcheck"] = round(time.time() - t0, 3)
        print(
            "  [workers=%d] items/min=%s eval_tok/s=%s wall_tok/s=%s "
            "classified_delta=%d"
            % (
                w,
                sm["items_per_min"],
                sm["eval_tok_per_s"],
                sm["wall_tok_per_s"],
                sm["classified_delta"],
            ),
            flush=True,
        )
        stages.append({"workers": w, "level_out": level_out, "summary": sm})
    return stages


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 并发吞吐基准（28 次调用）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args()

    if not probe_ollama():
        sys.stderr.write(
            "[llm_bench_conc] FAIL Ollama 127.0.0.1:11434 不可达，"
            "退出（fail-visible，不硬重试）\n"
        )
        return 2

    samples = fetch_sample_rows(DB_PATH, SAMPLE_N)
    if not samples:
        sys.stderr.write("[llm_bench_conc] FAIL 抽样为空，退出\n")
        return 3
    print(
        "samples=%d × levels %s = %d 次调用（红线 %d）"
        % (len(samples), CONC_LEVELS, len(samples) * sum(CONC_LEVELS), MAX_CALLS),
        flush=True,
    )
    for i, s in enumerate(samples):
        print(
            "  #%d status_id=%s chars=%d" % (i, s["status_id"], s["chars"]), flush=True
        )

    ps_before = ollama_ps()
    options = {
        "temperature": 0.1,
        "num_ctx": xq.DEFAULT_NUM_CTX,
        "num_predict": xq.DEFAULT_NUM_PREDICT,
    }  # 现状参数，唯一变量=并发度
    stages = run_all_concurrency(
        samples,
        url=xq.OLLAMA_URL.replace("/api/chat", GENERATE_PATH),
        model=xq.MODEL,
        system=xq.SYSTEM_PROMPT,
        options=options,
    )
    ps_after = ollama_ps()

    base = stages[0]["summary"]["items_per_min"] if stages else None
    comparison = []
    for st in stages:
        sm = st["summary"]
        ratio = (
            round(sm["items_per_min"] / base, 2)
            if base and sm["items_per_min"] is not None
            else None
        )
        comparison.append(
            {
                "workers": st["workers"],
                "items_per_min": sm["items_per_min"],
                "throughput_vs_1x": ratio,
                "eval_tok_per_s": sm["eval_tok_per_s"],
                "item_wall_median_s": (sm["item_wall_s"] or {}).get("median"),
                "classified_delta": sm["classified_delta"],
            }
        )

    out = {
        "date": "2026-10-18",
        "bench": "llm_bench_conc",
        "model": xq.MODEL,
        "endpoint": GENERATE_PATH + " stream:false think:false",
        "options": dict(options),
        "timeout_per_call_s": BENCH_TIMEOUT,
        "call_budget": {"used": len(samples) * sum(CONC_LEVELS), "redline": MAX_CALLS},
        "samples": [{k: v for k, v in s.items() if k != "body"} for s in samples],
        "comparison_vs_1x": comparison,
        "stages": [
            {
                "workers": st["workers"],
                "summary": st["summary"],
                "records": st["level_out"]["records"],
            }
            for st in stages
        ],
        "ollama_ps": {"before": ps_before, "after": ps_after},
        "limitations": [
            "背景有 automation 单流分类负载占用 LLM 队列（classified_delta 为代理指标）",
            "样本仅 4 条（短/中长混合）",
            "单轮无重复，未做方差估计",
            "当前服务进程的 OLLAMA_NUM_PARALLEL 实际值不可直接读，作未知项处理",
        ],
    }
    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("== comparison vs 1x ==")
    print(json.dumps(comparison, ensure_ascii=False, indent=2))
    print("== ollama ps ==")
    print(ps_before.get("stdout") or ps_before.get("error"))
    print("result written: %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
