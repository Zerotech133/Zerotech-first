# -*- coding: utf-8 -*-
"""LLM-as-judge（带每日预算 + 确定性降级，不伪造分数）。

用途：对治理建议/判例综述等**主观文本**做 1-5 分评分（rubric 明确），用于 eval 补充。
边界：
  · 每日预算（默认 10 次，env ZHAOYUN_JUDGE_DAILY_LIMIT 可调），超预算直接拒绝；
  · 模型调用走 model_router（自动降级/留证）；无 key/全失败 → 返回 score=None + source=unavailable（**不编分**）；
  · 输出必须 JSON：{score:1-5, rationale, flags}；解析失败同样不伪造；
  · 结果落 data/evals/judge_latest.json，可审计。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
OUT = PLATFORM / "data" / "evals"
BUDGET = OUT / "judge_budget.json"
LATEST = OUT / "judge_latest.json"
DEFAULT_LIMIT = 10

RUBRIC = """你是严格的治理质量评审。按以下 rubric 给 1-5 整数分：
5=结论有证据、边界清晰、可执行；4=基本可靠，小瑕疵；3=方向对但证据不足；
2=泛泛而谈或含未验证断言；1=错误/空话。只输出 JSON：
{"score": <1-5>, "rationale": "<一句话理由>", "flags": ["..."]}"""


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def budget(limit: int | None = None) -> dict:
    try:
        b = json.loads(BUDGET.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        b = {"date": _today(), "count": 0}
    if b.get("date") != _today():
        b = {"date": _today(), "count": 0}
    try:
        lim = int(limit or os.environ.get("ZHAOYUN_JUDGE_DAILY_LIMIT") or DEFAULT_LIMIT)
    except ValueError:
        lim = DEFAULT_LIMIT
    b["limit"] = lim
    b["remaining"] = max(0, lim - int(b.get("count") or 0))
    return b


def _consume() -> bool:
    b = budget()
    if b["remaining"] <= 0:
        return False
    b["count"] = int(b.get("count") or 0) + 1
    b["date"] = _today()
    OUT.mkdir(parents=True, exist_ok=True)
    BUDGET.write_text(
        json.dumps({"date": b["date"], "count": b["count"]}), encoding="utf-8"
    )
    return True


def _parse(text: str) -> dict:
    raw = str(text or "")
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        score_m = re.search(r'"score"\s*:\s*([1-5])', raw)
        if not score_m:
            return {"ok": False, "error": "no_json_in_reply"}
        rationale_m = re.search(r'"rationale"\s*:\s*"([^"]*)', raw)
        return {
            "ok": True,
            "score": int(score_m.group(1)),
            "rationale": (rationale_m.group(1) if rationale_m else "")[:300],
            "flags": [],
            "partial": True,
            "note": "模型输出截断，仅提取已生成的 score/rationale",
        }
    try:
        data = json.loads(m.group(0))
    except ValueError:
        # 模型可能在 flags 数组中途被 max_tokens 截断；只从文本安全提取已生成的
        # score/rationale，不补造 flags，也不把无 score 的文本当有效评审。
        score_m = re.search(r'"score"\s*:\s*([1-5])', raw)
        if not score_m:
            return {"ok": False, "error": "bad_json_in_reply"}
        rationale_m = re.search(r'"rationale"\s*:\s*"([^"]*)', raw)
        return {
            "ok": True,
            "score": int(score_m.group(1)),
            "rationale": (rationale_m.group(1) if rationale_m else "")[:300],
            "flags": [],
            "partial": True,
            "note": "模型输出截断，仅提取已生成的 score/rationale",
        }
    try:
        score = int(data.get("score"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad_score"}
    score = max(1, min(5, score))
    return {
        "ok": True,
        "score": score,
        "rationale": str(data.get("rationale") or "")[:300],
        "flags": [str(x)[:80] for x in (data.get("flags") or [])][:8],
    }


def judge_text(
    text: str,
    purpose: str = "governance",
    rubric: str | None = None,
    adapters: dict | None = None,
) -> dict:
    """对一段治理文本打分；预算/解析失败不伪造分数。"""
    b = budget()
    if b["remaining"] <= 0:
        return {
            "ok": False,
            "error": "budget_exceeded",
            "budget": b,
            "source": "unavailable",
        }
    if not _consume():
        return {
            "ok": False,
            "error": "budget_exceeded",
            "budget": budget(),
            "source": "unavailable",
        }
    sys.path.insert(0, str(PLATFORM / "tools"))
    from model_router import invoke  # noqa: WPS433

    messages = [
        {"role": "system", "content": rubric or RUBRIC},
        {"role": "user", "content": str(text or "")[:8000]},
    ]
    try:
        res = invoke(
            messages,
            purpose=purpose,
            prefer=["deepseek/deepseek-flash"],
            max_tokens=300,
            adapters=adapters,
        )
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": "invoke_failed",
            "detail": "%s: %s" % (type(e).__name__, str(e)[:160]),
            "source": "unavailable",
            "budget": budget(),
        }
    if not res.get("ok"):
        return {
            "ok": False,
            "error": "no_model_available",
            "source": "unavailable",
            "attempts": res.get("attempts"),
            "budget": budget(),
        }
    parsed = _parse(res.get("text") or "")
    if not parsed.get("ok"):
        return {
            "ok": False,
            "error": parsed.get("error"),
            "source": "unparseable",
            "raw": str(res.get("text") or "")[:300],
            "budget": budget(),
        }
    result = {
        "ok": True,
        "score": parsed["score"],
        "rationale": parsed["rationale"],
        "flags": parsed["flags"],
        "model": res.get("model"),
        "usage": res.get("usage"),
        "source": "judge",
        "budget": budget(),
        "judged_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if parsed.get("partial"):
        result["partial"] = True
        result["note"] = parsed.get("note")
    return result


def judge_latest(limit: int = 3, adapters: dict | None = None) -> dict:
    """评最近治理 AI draft（data/governance_ai/*.json），最多 limit 条。"""
    d = PLATFORM / "data" / "governance_ai"
    items = []
    files = (
        sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[
            : max(1, min(int(limit), 5))
        ]
        if d.is_dir()
        else []
    )
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        text = json.dumps(data, ensure_ascii=False)[:6000]
        rep = judge_text(text, adapters=adapters)
        items.append({"file": f.name, "result": rep})
    rep = {
        "ok": all(i["result"].get("ok") for i in items) if items else False,
        "count": len(items),
        "items": items,
        "budget": budget(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": "judge 分数仅用于质量参考，不替代人工评审",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep


def latest() -> dict:
    try:
        return json.loads(LATEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": "no_judge_report", "budget": budget()}


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="LLM-as-judge")
    ap.add_argument("--text", default="")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.report:
        rep = judge_latest(args.limit)
    elif args.text:
        rep = judge_text(args.text)
    else:
        rep = latest()
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
