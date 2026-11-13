# -*- coding: utf-8 -*-
"""探针：deepseek-flash 的推理预算与关闭方式（2026-11-13）。

背景：抢险方案要求结构化长 JSON，但 deepseek-flash 把 max_tokens=3000
全部用在 reasoning_tokens 上、正文为空（finish_reason=length）。
本探针逐个试候选开关，找出一条**正文真的能回来**的路径。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "agent_platform"))
sys.stdout.reconfigure(encoding="utf-8")

from engine.deepseek import DeepSeekClient  # noqa: E402

Q = ("用 JSON 回答：{\"a\": 1, \"b\": \"两句话说明盘前数据检查要查什么\"}")
BIG = ("你是抢险工程师。基于证据给出 JSON："
       "{\"diagnosis\":\"\", \"likely_cause\":\"\", \"why\":\"\", "
       "\"actions\":[{\"step\":1,\"cmd\":\"\"}], \"needs_human\":[], "
       "\"unknowns\":[], \"confidence\":\"high|medium|low\"}。"
       "证据：龙虎榜 ts_top_inst 最新 20261111，目标日 20261112，落后 1 个交易日，"
       "该源不参与选票。只输出 JSON。")


def try_one(tag: str, model: str, msg: str, extra: dict | None, mt: int = 1200) -> dict:
    c = DeepSeekClient(model=model, timeout=180.0)
    payload = {"model": model, "messages": [{"role": "user", "content": msg}],
               "temperature": 0.2, "max_tokens": mt, "stream": False,
               "response_format": {"type": "json_object"}}
    if extra:
        payload.update(extra)
    try:
        d = c._request("POST", "/chat/completions", payload)
        ch = d["choices"][0]
        txt = (ch["message"].get("content") or "").strip()
        u = d.get("usage") or {}
        det = u.get("completion_tokens_details") or {}
        return {"tag": tag, "ok": bool(txt), "len": len(txt),
                "reason_tok": det.get("reasoning_tokens"),
                "comp_tok": u.get("completion_tokens"),
                "finish": ch.get("finish_reason"),
                "head": txt[:120]}
    except Exception as e:  # noqa: BLE001
        return {"tag": tag, "ok": False, "err": "%s: %s" % (type(e).__name__, str(e)[:160])}


CASES = [
    ("A flash/默认/短问", "deepseek-flash", Q, None, 600),
    ("B flash/默认/长问", "deepseek-flash", BIG, None, 1200),
    ("C flash/thinking=disabled", "deepseek-flash", BIG,
     {"thinking": {"type": "disabled"}}, 1200),
    ("D flash/reasoning_effort=none", "deepseek-flash", BIG,
     {"reasoning_effort": "none"}, 1200),
    ("E flash/reasoning_effort=minimal", "deepseek-flash", BIG,
     {"reasoning_effort": "minimal"}, 1200),
    ("F v4-pro/默认", "deepseek-v4-pro", BIG, None, 1200),
    ("G v4-pro/thinking=disabled", "deepseek-v4-pro", BIG,
     {"thinking": {"type": "disabled"}}, 1200),
]

rows = []
for tag, model, msg, extra, mt in CASES:
    r = try_one(tag, model, msg, extra, mt)
    rows.append(r)
    print("%-32s ok=%-5s len=%-4s reason_tok=%-5s comp=%-5s finish=%s"
          % (r["tag"], r["ok"], r.get("len"), r.get("reason_tok"),
             r.get("comp_tok"), r.get("finish")))
    if r.get("err"):
        print("    err: %s" % r["err"])
    elif r.get("head"):
        print("    head: %s" % r["head"].replace("\n", " ")[:110])

Path(ROOT / "var" / "preopen" / "daily_check" / "_probe_ds.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
good = [r["tag"] for r in rows if r["ok"]]
print("\n可用路径: %s" % (good or "无"))
sys.exit(0 if good else 1)
