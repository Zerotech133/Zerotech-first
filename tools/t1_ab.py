#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""T1/T2 输出预算/契约 A/B：同一 prompt 对比 num_predict 与输出顺序。

不改生产；结果落 work/p0/ab/，供 change_gate/人工决策。
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# 回环禁代理：本机服务（Ollama 11434 等）的请求不走系统代理（见 tools/safe_urlopen.py）
import sys as _sys
from pathlib import Path as _Path
for _base in _Path(__file__).resolve().parents:
    for _cand in (_base, _base / "tools"):
        if (_cand / "safe_urlopen.py").exists():
            _sys.path.insert(0, str(_cand))
            break
    else:
        continue
    break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "work" / "p0" / "ab"
OLLAMA = "http://127.0.0.1:11434/api/chat"
JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)

BASE_PROMPT = """你是多头研究员 T1。基于以下数据写一份技术面分析：
标的 002712，近5日 -9.84%，量比 1.15，PE -52.09，换手率 6.62%，
距20日高点回撤 18.66%，MA5/MA10 空头排列，箱体 4.39-5.27。
要求：先写 Markdown 分析（含证据链、风险），最后必须输出一个 ```json 代码块，
含 overallAssessment、keyEvidence、confidence。不要省略 JSON 块。"""

JSON_FIRST = BASE_PROMPT + "\n输出顺序改为：先输出 ```json 代码块，再用不超过 200 字写 Markdown 说明；总长度控制在 350 token 内。"

VARIANTS = [
    {"name": "current-400", "num_predict": 400, "prompt": BASE_PROMPT},
    {"name": "budget-800", "num_predict": 800, "prompt": BASE_PROMPT},
    {"name": "json-first-400", "num_predict": 400, "prompt": JSON_FIRST},
]


def _call(prompt: str, num_predict: int, model: str, timeout: float) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"num_predict": num_predict, "temperature": 0.4, "num_ctx": 16384},
    }
    req = urllib.request.Request(OLLAMA, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    try:
        with safe_urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        elapsed = round((time.time() - t0) * 1000, 1)
        text = ((data.get("message") or {}).get("content") or "")
        return {"ok": True, "elapsed_ms": elapsed,
                "done_reason": data.get("done_reason"),
                "eval_count": data.get("eval_count"),
                "chars": len(text),
                "json_block": bool(JSON_RE.search(text)),
                "text_tail": text[-200:]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "elapsed_ms": round((time.time() - t0) * 1000, 1),
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="T1/T2 输出预算/契约 A/B")
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--out-dir", default=str(OUTDIR))
    args = ap.parse_args(argv)
    rows = []
    for run in range(1, args.runs + 1):
        for v in VARIANTS:
            print("[run %d] %s ..." % (run, v["name"]), flush=True)
            r = _call(v["prompt"], v["num_predict"], args.model, args.timeout)
            r.update({"run": run, "variant": v["name"], "num_predict": v["num_predict"]})
            rows.append(r)
            print("  ok=%s json=%s done=%s chars=%s elapsed_ms=%s" % (
                r.get("ok"), r.get("json_block"), r.get("done_reason"),
                r.get("chars"), r.get("elapsed_ms")), flush=True)
    outdir = Path(args.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / ("t1_ab_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    out.write_text(json.dumps({"model": args.model, "rows": rows}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("wrote", out)
    return 0 if all(r.get("ok") for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
