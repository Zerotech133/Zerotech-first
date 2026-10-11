# -*- coding: utf-8 -*-
"""K-STOCK-101 M2（前半）：LLM 成本计量独立模块。

独立 jsonl 账本（data/llm_cost/YYYYMMDD.jsonl），不碰 portal.db。
定价为锚点示意（人民币/百万 token）；超 daily_cost_cap 触发降级建议。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# 模块级可覆写（测试 monkeypatch 用）
DATA_DIR = _HERE.parent / "data"
COST_DIR = DATA_DIR / "llm_cost"
TIERS_PATH = _HERE / "tiers.json"

# 每百万 token 单价表（人民币，prompt/completion 同价锚点示意）
MODEL_UNIT_PRICE: dict[str, float] = {
    "ollama/qwen3.5:4b": 0.0,
    "ollama/qwen3.5:9b": 0.0,
    "deepseek/deepseek-chat": 2.0,
    "deepseek/deepseek-reasoner": 4.0,
    "qwen/qwen-max": 20.0,
    "qwen/qwen-plus": 8.0,
    "zhipu/glm-4-flash": 1.0,   # 077w M2：kstock/llm.py primary 实际模型（低价锚点）
    "zhipu/glm-4-plus": 50.0,
    "anthropic/claude-sonnet-4": 60.0,
    "anthropic/claude-opus-4": 150.0,
    "openai/gpt-4o": 60.0,
    "openai/gpt-5": 150.0,
    "google/gemini-pro": 30.0,
    "xai/grok-4": 100.0,
}
UNKNOWN_FALLBACK_PRICE = 50.0  # 未知模型保守价
DEFAULT_TIER_CAP = 20.0        # 取不到 tier 档案时按 T2 上限


def _price(binding: str) -> tuple[float, bool]:
    """返回 (单价, 是否估算)。匹配规则：全匹配 > provider 前缀 > 保守 50。"""
    p = MODEL_UNIT_PRICE.get(binding)
    if p is not None:
        return p, False
    provider = binding.split("/", 1)[0]
    for k, v in MODEL_UNIT_PRICE.items():
        if k.startswith(provider + "/"):
            return v, True
    return UNKNOWN_FALLBACK_PRICE, True


def _load_tiers() -> dict:
    try:
        return json.loads(TIERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _tier_of(account: str) -> dict | None:
    """从 trainee.load_profile(account) 取 tier 信息；失败返回 None。"""
    try:
        from k_stock.paper import trainee
        prof = trainee.load_profile(account)
    except Exception:
        return None
    if not isinstance(prof, dict):
        return None
    cap = prof.get("daily_cost_cap")
    return {
        "tier": prof.get("tier") or "T2",
        "daily_cost_cap": float(cap) if cap is not None else DEFAULT_TIER_CAP,
    }


def _day_file(date: str | None = None) -> Path:
    d = date or time.strftime("%Y%m%d")
    return COST_DIR / f"{d}.jsonl"


def record_cost(account: str, binding: str, prompt_tokens: int,
                completion_tokens: int, ts: float | None = None) -> dict:
    """估算单次调用成本并追加写当日 jsonl。返回该行记录。"""
    ts = ts if ts is not None else time.time()
    unit, estimated = _price(binding)
    total = prompt_tokens + completion_tokens
    cost = round(total * unit / 1_000_000, 4)
    rec = {
        "ts": ts,
        "account": account,
        "binding": binding,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost": cost,
    }
    if estimated:
        rec["estimated"] = True
    COST_DIR.mkdir(parents=True, exist_ok=True)
    with open(_day_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def daily_cost(account: str | None = None, date: str | None = None) -> dict:
    """某账本（account=None 为全部）当日累计成本、距 cap 余量、触线布尔。"""
    f = _day_file(date)
    total = 0.0
    n = 0
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if account is not None and rec.get("account") != account:
                continue
            total += float(rec.get("cost") or 0.0)
            n += 1
    total = round(total, 4)
    out: dict = {"date": f.stem, "account": account or "*", "cost": total, "calls": n}
    if account is not None:
        t = _tier_of(account)
        cap = t["daily_cost_cap"] if t else DEFAULT_TIER_CAP
        out["daily_cost_cap"] = cap
        out["remaining"] = round(max(0.0, cap - total), 4)
        out["breached"] = total >= cap
    return out


def check_and_flag(account: str) -> dict:
    """超 cap 时返回降级建议（binding 读 tiers.json 的 degrade_binding）。"""
    d = daily_cost(account)
    if not d.get("breached"):
        return {"breached": False}
    return {
        "breached": True,
        "cost": d["cost"],
        "daily_cost_cap": d.get("daily_cost_cap"),
        "degrade_to": _load_tiers().get("degrade_binding") or "ollama/qwen3.5:9b",
    }


def _cli() -> int:
    ap = argparse.ArgumentParser(description="LLM 成本计量（K-STOCK-101 M2）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record")
    p.add_argument("--account", required=True)
    p.add_argument("--binding", required=True)
    p.add_argument("--pt", type=int, required=True, help="prompt tokens")
    p.add_argument("--ct", type=int, required=True, help="completion tokens")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("daily")
    p.add_argument("--account")
    p.add_argument("--date", help="YYYYMMDD")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("check")
    p.add_argument("--account", required=True)
    p.add_argument("--json", action="store_true")

    a = ap.parse_args()
    if a.cmd == "record":
        rec = record_cost(a.account, a.binding, a.pt, a.ct)
        if a.json:
            print(json.dumps(rec, ensure_ascii=False))
        else:
            est = " (estimated)" if rec.get("estimated") else ""
            print(f"recorded: {rec['account']} {rec['binding']}{est} "
                  f"cost={rec['cost']} 元")
    elif a.cmd == "daily":
        d = daily_cost(a.account, a.date)
        print(json.dumps(d, ensure_ascii=False, indent=2)
              if a.json else json.dumps(d, ensure_ascii=False))
    elif a.cmd == "check":
        r = check_and_flag(a.account)
        print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
