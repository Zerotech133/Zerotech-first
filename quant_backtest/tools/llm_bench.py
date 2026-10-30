# -*- coding: utf-8 -*-
"""LLM 推理速率基准（rate-opt 工位 2026-10-18）。

目的：分类追平瓶颈评估——同一批真实帖子在
  配置A（现状：num_ctx=2048 / num_predict=256，取自 xq_classify 代码常量）
  配置B（候选：num_ctx=1024 / num_predict=128）
下各跑一遍，记录双口径耗时（墙钟含队列等待 / eval 纯推理）与判定一致性。

纪律（任务书铁律）：
  - 抽样只读 DB（mode=ro + busy_timeout），优先已分类行，短/中/长混合 8 条；
  - 一次跑完 A8+B8=16 次调用，不做重试循环；单条护栏 240s（队列被
    automation 占用时墙钟会含等待，不用短超时误杀）；
  - Ollama 不可达 → fail-visible 退出码 2，不硬重试；
  - 判定 agreement 与测速解耦：纯推理提速 ≥30% 且 agreement=100% 才建议切换，
    拍板权在用户，本脚本只出数。

用法：
  python tools/llm_bench.py                 # 全量 16 次
  python tools/llm_bench.py --out <path>    # 自定义输出
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))

import xq_classify as xq  # noqa: E402

try:
    from tools.safe_urlopen import safe_urlopen
except ImportError:  # 作为脚本直接运行时
    from safe_urlopen import safe_urlopen

DB_PATH = xq.DB_PATH
DEFAULT_OUT = Path(r"C:\aios\automation_runs") / "llm_bench" / "bench_2026-10-18.json"
BENCH_TIMEOUT = 240  # 单条护栏：墙钟含 automation 队列等待，不能短
SAMPLE_N = 8
CONFIG_B_NUM_CTX = 1024
CONFIG_B_NUM_PREDICT = 128


def probe_ollama(
    host: str = "127.0.0.1", port: int = 11434, timeout: float = 3.0
) -> bool:
    """TCP 可达性探测（沙箱 curl.exe 被拦，判例：用 python socket）。"""
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
    rows = con.execute(  # nosec B608 - 值参数化/内部标识符（已抽查）
        "SELECT s.status_id, s.title, s.desc FROM xq_status_classified c "  # nosec B608 - 值参数化/内部标识符（已抽查）
        "JOIN xq_status s ON s.status_id = c.status_id "
        "WHERE c.parse_ok = 1 AND %s" % nonempty
    ).fetchall()
    pool = [_mk_sample(r) for r in rows]
    if len(pool) < n:
        rows2 = con.execute(  # nosec B608 - 值参数化/内部标识符（已抽查）
            "SELECT s.status_id, s.title, s.desc FROM xq_status s "  # nosec B608 - 值参数化/内部标识符（已抽查）
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
    """真 sender：POST /api/chat（stream:false），返回解析后的响应 JSON。"""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with safe_urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def bench_one(
    sender,
    url: str,
    model: str,
    system: str,
    body: str,
    options: dict,
    timeout: float = BENCH_TIMEOUT,
) -> dict:
    """单次调用 + 指标提取。异常不抛（记 error 字段），不重试。"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": body or "（无文字内容）"},
        ],
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
        content = (resp.get("message") or {}).get("content", "")
        rec["content_head"] = (content or "")[:120]
        rec["verdict"] = parse_verdict(content)
        rec["parse_ok"] = rec["verdict"]["parse_ok"]
    except Exception as e:  # noqa: BLE001
        rec["wall_s"] = round(time.time() - t0, 3)
        rec["error"] = str(e)[:200]
    return rec


def run_bench(
    samples: list,
    sender=http_sender,
    url: str = xq.OLLAMA_URL,
    model: str = xq.MODEL,
    system: str = xq.SYSTEM_PROMPT,
    timeout: float = BENCH_TIMEOUT,
) -> list:
    """同批样本 × {A 现状, B 候选} 逐条交错跑完，返回 16 条记录。

    配置A 硬取代码常量（不吃环境变量，避免测量对象被 env 漂移污染）。
    """
    config_a = {
        "temperature": 0.1,
        "num_ctx": xq.DEFAULT_NUM_CTX,
        "num_predict": xq.DEFAULT_NUM_PREDICT,
    }
    config_b = {
        "temperature": 0.1,
        "num_ctx": CONFIG_B_NUM_CTX,
        "num_predict": CONFIG_B_NUM_PREDICT,
    }
    records = []
    for i, s in enumerate(samples):
        for tag, cfg in (("A", config_a), ("B", config_b)):
            rec = bench_one(sender, url, model, system, s["body"], cfg, timeout)
            rec.update(
                {
                    "sample_id": i,
                    "status_id": s["status_id"],
                    "chars": s["chars"],
                    "config": tag,
                }
            )
            records.append(rec)
            print(
                "  [%s] sample=%d chars=%d wall=%s eval=%s err=%s"
                % (
                    tag,
                    i,
                    s["chars"],
                    rec.get("wall_s"),
                    rec.get("eval_duration_s"),
                    rec["error"] is not None and rec["error"][:60] or "-",
                ),
                flush=True,
            )
    return records


def compute_agreement(records: list) -> dict:
    """同一样本 A/B 判定一致性：双方都 parse_ok=1 且三维全等才算一致。"""
    by_sample: dict = {}
    for r in records:
        by_sample.setdefault(r["sample_id"], {})[r["config"]] = r
    n = agree = 0
    for sid, pair in sorted(by_sample.items()):
        if "A" not in pair or "B" not in pair:
            continue
        n += 1
        va, vb = pair["A"].get("verdict"), pair["B"].get("verdict")
        if (
            va
            and vb
            and va.get("parse_ok") == 1
            and vb.get("parse_ok") == 1
            and va["event_type"] == vb["event_type"]
            and va["sentiment"] == vb["sentiment"]
            and va["content_type"] == vb["content_type"]
        ):
            agree += 1
    return {
        "n": n,
        "agree": agree,
        "agreement_pct": round(100.0 * agree / n, 1) if n else None,
    }


def _config_stats(records: list, tag: str) -> dict:
    rs = [
        r
        for r in records
        if r["config"] == tag
        and not r["error"]
        and r.get("eval_duration_s") is not None
    ]
    walls = sorted(r["wall_s"] for r in rs)
    evals = sorted(r["eval_duration_s"] for r in rs)
    toks = sorted(
        r["eval_count"] / r["eval_duration_s"]
        for r in rs
        if r.get("eval_count") and r["eval_duration_s"]
    )
    ok = sum(1 for r in rs if r.get("parse_ok") == 1)
    total = sum(1 for r in records if r["config"] == tag)

    def stats(v):
        if not v:
            return None
        mid = len(v) // 2
        med = v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2
        return {
            "mean": round(sum(v) / len(v), 3),
            "median": round(med, 3),
            "min": round(v[0], 3),
            "max": round(v[-1], 3),
        }

    return {
        "calls_total": total,
        "calls_ok": len(rs),
        "parse_ok": ok,
        "wall_s": stats(walls),
        "eval_s": stats(evals),
        "eval_tok_per_s": stats(toks),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 推理速率基准（16 次调用）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args()

    if not probe_ollama():
        sys.stderr.write(
            "[llm_bench] FAIL Ollama 127.0.0.1:11434 不可达，退出"
            "（fail-visible，不硬重试）\n"
        )
        return 2

    samples = fetch_sample_rows(DB_PATH, SAMPLE_N)
    print("samples=%d (A/B 各 %d 次调用)" % (len(samples), len(samples)), flush=True)
    for i, s in enumerate(samples):
        print(
            "  #%d status_id=%s chars=%d" % (i, s["status_id"], s["chars"]), flush=True
        )
    if not samples:
        sys.stderr.write("[llm_bench] FAIL 抽样为空，退出\n")
        return 3

    records = run_bench(samples)
    agg = compute_agreement(records)
    summary = {
        "config_A": _config_stats(records, "A"),
        "config_B": _config_stats(records, "B"),
        "agreement": agg,
    }

    # 切换建议条件（拍板权在用户）：纯推理提速 ≥30% 且 agreement=100%
    ev_a = (summary["config_A"] or {}).get("eval_s") or {}
    ev_b = (summary["config_B"] or {}).get("eval_s") or {}
    speedup = None
    if ev_a.get("median") and ev_b.get("median"):
        speedup = round(100.0 * (1 - ev_b["median"] / ev_a["median"]), 1)
    summary["eval_speedup_pct"] = speedup
    summary["switch_recommendation"] = (
        "switch-ok"
        if (
            speedup is not None
            and speedup >= 30.0
            and agg.get("agreement_pct") == 100.0
        )
        else "keep-current"
    )

    out = {
        "date": "2026-10-18",
        "model": xq.MODEL,
        "config_A": {
            "num_ctx": xq.DEFAULT_NUM_CTX,
            "num_predict": xq.DEFAULT_NUM_PREDICT,
            "source": "xq_classify 代码常量（现状）",
        },
        "config_B": {
            "num_ctx": CONFIG_B_NUM_CTX,
            "num_predict": CONFIG_B_NUM_PREDICT,
            "source": "候选（任务书）",
        },
        "timeout_per_call_s": BENCH_TIMEOUT,
        "samples": [{k: v for k, v in s.items() if k != "body"} for s in samples],
        "summary": summary,
        "records": records,
    }
    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("== summary ==")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("result written: %s" % out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
