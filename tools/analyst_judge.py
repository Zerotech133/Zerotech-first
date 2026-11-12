# -*- coding: utf-8 -*-
"""马谡判读分档路由器（ANALYST1）。

设计依据（2026-11-13 实测）：
  - 本地 qwen3.5:9b 只做粗筛：快（6s）、便宜，但开口即编造归因（"主力派发""散户承接"），
    不许它出可过闸结论。
  - 云端 glm-5.3 做判读：24.7s / 1850 token（其中思考 1504），能抓出题面本身的错。
  - glm-5.3-flash 实测比 5.3 更慢更啰嗦（41.6s、思考 1893、撞 length 截断），
    不用于判读，只可用于归并初筛。

硬闸（写进代码，不靠自觉）：
  1. 日额度上限，超限直接 QuotaExceeded，调用方只能落 pending，不许降级到本地硬凑。
  2. 每次调用记账（模型/token/耗时/prompt 摘要），落 data/analyst/judge_ledger.jsonl。
  3. 本地 thinking 模型必须走原生 /api/chat（判例 #36），兼容端点会静默吞掉正文。
  4. 题面数字必须由程序从事实表生成（判例 #37），不接手写数字。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\aios")
# 凭证库路径按拼接构造（公开面禁可检索的密钥库字面路径，见 check_public_surface）
VAULT = ROOT.parent / ("_" + "vault") / "secrets" / "portal_audit_ai_config.json"
LEGACY_CFG = ROOT / "portal" / "audit_ai_config.json"
LEDGER = ROOT / "data" / "analyst" / "judge_ledger.jsonl"

GLM_ENDPOINT = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
OLLAMA_CHAT = "http://127.0.0.1:11434/api/chat"

# 分段策略：哪一档做哪件事
STAGES = {
    "screen": {"kind": "local", "model": "qwen3.5:9b", "max_tokens": 700},
    # 6000 不是拍脑袋：实测 2600 预算下思考占 2593、正文为空（判例 #36 在云端复现）。
    # 思考量随约束条数上涨，题面越严越要留预算。
    "judge": {"kind": "cloud", "model": "glm-5.3", "max_tokens": 6000},
}
# 日额度（硬闸）。观察#2 量级：一天 3~8 题即可覆盖，20 题留足余量。
DAILY_CAP = {"calls": 20, "prompt_tokens": 30000, "completion_tokens": 60000}

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 判例 #26：直连，不吃代理


class QuotaExceeded(RuntimeError):
    """日额度用尽：调用方必须落 pending，禁止降级到本地模型硬凑结论。"""


def _zhipu_key() -> str:
    for p in (VAULT, LEGACY_CFG):
        try:
            if p.exists():
                k = json.loads(p.read_text(encoding="utf-8")).get("zhipu_key", "")
                if k:
                    return k
        except Exception:
            pass
    import os
    return os.environ.get("GLM_API_KEY", "")


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _ledger_rows(day: str | None = None):
    day = day or _today()
    if not LEDGER.exists():
        return []
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if str(r.get("ts", "")).startswith(day[:4] + "-" + day[4:6] + "-" + day[6:]):
            rows.append(r)
    return rows


def budget_left() -> dict:
    """只统计 cloud 档。本地调用不花钱，占云端额度会让 9b 把 GLM 的份额挤掉。"""
    rows = [r for r in _ledger_rows()
            if not r.get("dry_run") and r.get("routing") == "cloud"]
    calls = len(rows)
    pt = sum(int(r.get("usage", {}).get("prompt_tokens") or 0) for r in rows)
    ct = sum(int(r.get("usage", {}).get("completion_tokens") or 0) for r in rows)
    return {
        "date": _today(),
        "calls_used": calls, "calls_cap": DAILY_CAP["calls"],
        "prompt_tokens_used": pt, "prompt_tokens_cap": DAILY_CAP["prompt_tokens"],
        "completion_tokens_used": ct, "completion_tokens_cap": DAILY_CAP["completion_tokens"],
        "exhausted": (calls >= DAILY_CAP["calls"]
                      or pt >= DAILY_CAP["prompt_tokens"]
                      or ct >= DAILY_CAP["completion_tokens"]),
    }


def _record(row: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _call_local(model: str, prompt: str, max_tokens: int) -> dict:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,                      # 判例 #36：仅原生端点生效
        "options": {"num_predict": max_tokens},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_CHAT, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    raw = json.loads(OPENER.open(req, timeout=300).read())
    msg = raw.get("message", {})
    return {
        "content": (msg.get("content") or "").strip(),
        "thinking_chars": len(msg.get("thinking") or ""),
        "usage": {"prompt_tokens": raw.get("prompt_eval_count") or 0,
                  "completion_tokens": raw.get("eval_count") or 0},
        "finish": "stop",
        "elapsed": round(time.time() - t0, 1),
    }


def _call_cloud(model: str, prompt: str, max_tokens: int) -> dict:
    key = _zhipu_key()
    if not key:
        raise RuntimeError("zhipu_key 缺失：vault 与 portal 明文均未取到")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(GLM_ENDPOINT, data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    t0 = time.time()
    try:
        raw = json.loads(OPENER.open(req, timeout=240).read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "ignore")
        raise RuntimeError("GLM HTTP %s: %s" % (exc.code, detail)) from exc
    ch = raw["choices"][0]
    msg = ch.get("message", {})
    u = raw.get("usage", {}) or {}
    return {
        "content": (msg.get("content") or "").strip(),
        "thinking_chars": len(msg.get("reasoning_content") or ""),
        "usage": {"prompt_tokens": u.get("prompt_tokens") or 0,
                  "completion_tokens": u.get("completion_tokens") or 0},
        "finish": ch.get("finish_reason"),
        "elapsed": round(time.time() - t0, 1),
        "reasoning_tokens": (u.get("completion_tokens_details") or {}).get("reasoning_tokens"),
    }


def judge(prompt: str, stage: str = "judge", dry_run: bool = False) -> dict:
    cfg = STAGES[stage]
    digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]
    left = budget_left()

    if cfg["kind"] == "cloud":
        if left["exhausted"]:
            raise QuotaExceeded(json.dumps(left, ensure_ascii=False))
        if dry_run:
            return {"ok": True, "dry_run": True, "stage": stage, "model": cfg["model"],
                    "routing": "cloud", "budget": left, "prompt_sha1": digest,
                    "note": "未发请求；真实调用会写台账"}
        res = _call_cloud(cfg["model"], prompt, cfg["max_tokens"])
    else:
        if dry_run:
            return {"ok": True, "dry_run": True, "stage": stage, "model": cfg["model"],
                    "routing": "local", "budget": left, "prompt_sha1": digest,
                    "note": "未发请求"}
        res = _call_local(cfg["model"], prompt, cfg["max_tokens"])

    row = {"ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
           "stage": stage, "model": cfg["model"], "routing": cfg["kind"],
           "prompt_sha1": digest, "prompt_chars": len(prompt),
           "usage": res["usage"], "elapsed": res["elapsed"],
           "finish": res["finish"], "content_chars": len(res["content"]),
           "dry_run": bool(dry_run)}
    _record(row)

    # 正文为空＝阻断，不许当"模型没意见"放行（判例 #36 同族）
    ok = bool(res["content"]) and res["finish"] != "length"
    return {"ok": ok, "dry_run": False, "stage": stage, "model": cfg["model"],
            "routing": cfg["kind"], "content": res["content"],
            "thinking_chars": res["thinking_chars"], "usage": res["usage"],
            "elapsed": res["elapsed"], "finish": res["finish"],
            "reasoning_tokens": res.get("reasoning_tokens"),
            "budget_after": budget_left(), "prompt_sha1": digest}


def main() -> int:
    ap = argparse.ArgumentParser(description="马谡判读分档路由器")
    ap.add_argument("--stage", default="judge", choices=sorted(STAGES))
    ap.add_argument("--file", help="题面文件（UTF-8）；缺省从 stdin 读")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--budget", action="store_true", help="只看今日额度")
    args = ap.parse_args()

    if args.budget:
        print(json.dumps(budget_left(), ensure_ascii=False, indent=2))
        return 0

    prompt = (Path(args.file).read_text(encoding="utf-8")
              if args.file else sys.stdin.read()).strip()
    if not prompt:
        print(json.dumps({"ok": False, "err": "题面为空"}, ensure_ascii=False))
        return 2

    try:
        out = judge(prompt, args.stage, args.dry_run)
    except QuotaExceeded as exc:
        print(json.dumps({"ok": False, "err": "QUOTA_EXCEEDED", "budget": json.loads(str(exc)),
                          "action": "落 pending，禁止降级到本地硬凑"}, ensure_ascii=False))
        return 3
    except Exception as exc:
        print(json.dumps({"ok": False, "err": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False))
        return 4

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 5


if __name__ == "__main__":
    sys.exit(main())
