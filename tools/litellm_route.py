# -*- coding: utf-8 -*-
"""LiteLLM 路由最小接入：Key 池 → Router（多 Key/多 provider/fallback/成本）。

用法：
  python -X utf8 tools/litellm_route.py list [--json]       # 查看部署（掩码）
  python -X utf8 tools/litellm_route.py route "你好" [--json] # 走 seat-auto，失败 fallback 本地
  python -X utf8 tools/litellm_route.py costs [--json]      # 成本汇总
成本：work/litellm/cost.jsonl
说明：LiteLLM 本地价目表模式（LITELLM_LOCAL_MODEL_COST_MAP=True），不联网拉价格。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
COST = REPO / "work" / "litellm" / "cost.jsonl"
LOCAL_BASE = "http://127.0.0.1:11434/v1"
LOCAL_MODEL = "qwen3.5:9b"


def _keypool():
    sys.path.insert(0, str(REPO / "agent_platform"))
    from legion_command import keypool
    return keypool


def _norm_base(url: str) -> str:
    u = str(url or "").strip().rstrip("/")
    for suf in ("/chat/completions", "/completions"):
        if u.endswith(suf):
            u = u[: -len(suf)]
    return u


def _mask(s: str) -> str:
    s = str(s or "")
    return s[:3] + "***" + s[-3:] if len(s) > 8 else "***"


def load_deployments(entries=None) -> list:
    """从 Key 池 entries 生成 LiteLLM deployments（api_key 只在本机内存/请求用）。"""
    out = []
    if entries is None:
        try:
            kp = _keypool()
            d = kp._load()
            entries = []
            for k in d.get("keys", []):
                if not k.get("enabled", True):
                    continue
                try:
                    secret = kp._decrypt(k.get("secret_enc") or "")
                except Exception:
                    continue
                entries.append({"seat": k.get("seat"), "secret": secret,
                                "base_url": k.get("base_url"), "model": k.get("model"),
                                "label": k.get("label"), "id": k.get("id")})
        except Exception:
            entries = []
    for e in entries or []:
        seat = e.get("seat")
        provider = {"tianfeng": "zhipu", "zhaoyun": "deepseek", "zhuge": "deepseek",
                    "relay": "custom"}.get(seat, "custom")
        model = str(e.get("model") or ("glm-5.3" if provider == "zhipu" else "deepseek-chat"))
        if provider == "custom":
            params = {"model": "openai/" + model,
                      "api_base": _norm_base(e.get("base_url") or ""),
                      "api_key": e.get("secret")}
        elif provider == "zhipu":
            params = {"model": "openai/" + model,
                      "api_base": "https://open.bigmodel.cn/api/paas/v4",
                      "api_key": e.get("secret")}
        else:
            params = {"model": "deepseek/" + model, "api_key": e.get("secret")}
        out.append({"model_name": "seat-auto", "litellm_params": params,
                    "model_info": {"seat": seat, "label": e.get("label"),
                                   "key": _mask(e.get("secret"))}})
    out.append({"model_name": "local-fallback",
                "litellm_params": {"model": "openai/" + LOCAL_MODEL,
                                   "api_base": LOCAL_BASE, "api_key": "local"},
                "model_info": {"seat": "local", "label": "本地9B兜底"}})
    return out


def build_router(entries=None):
    import litellm
    deployments = load_deployments(entries)
    router = litellm.Router(
        model_list=deployments,
        fallbacks=[{"seat-auto": ["local-fallback"]}],
        num_retries=1,
        timeout=120,
        set_verbose=False,
    )
    return router, deployments


def list_deployments(entries=None) -> dict:
    deps = load_deployments(entries)
    return {"ok": True, "count": len(deps),
            "deployments": [{"model_name": d["model_name"],
                             "model": d["litellm_params"].get("model"),
                             "api_base": d["litellm_params"].get("api_base", ""),
                             "seat": d["model_info"].get("seat"),
                             "label": d["model_info"].get("label"),
                             "key": d["model_info"].get("key")} for d in deps]}


def route(prompt: str, model: str = "seat-auto") -> dict:
    import litellm
    router, deployments = build_router()
    t0 = time.time()
    try:
        resp = router.completion(model=model, messages=[{"role": "user", "content": prompt}],
                                 max_tokens=800, temperature=0.3)
        content = ((resp.choices or [{}])[0].message.content) or ""
        used = getattr(resp, "model", "") or ""
        try:
            cost = float(litellm.completion_cost(completion_response=resp) or 0)
        except Exception:
            cost = 0.0
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "model": used,
               "prompt_chars": len(prompt), "reply_chars": len(content),
               "cost_usd": cost, "elapsed_ms": int((time.time() - t0) * 1000)}
        COST.parent.mkdir(parents=True, exist_ok=True)
        with COST.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"ok": True, "reply": content, "used_model": used, "cost_usd": cost,
                "elapsed_ms": rec["elapsed_ms"], "deployments": len(deployments)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:300]),
                "elapsed_ms": int((time.time() - t0) * 1000)}


def costs() -> dict:
    if not COST.exists():
        return {"ok": True, "calls": 0, "total_usd": 0.0}
    rows = []
    for ln in COST.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(ln))
        except ValueError:
            continue
    return {"ok": True, "calls": len(rows),
            "total_usd": round(sum(float(r.get("cost_usd") or 0) for r in rows), 8),
            "models": sorted({r.get("model") for r in rows})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 LiteLLM 路由（Key 池→Router）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("list")
    l.add_argument("--json", action="store_true")
    r = sub.add_parser("route")
    r.add_argument("prompt")
    r.add_argument("--json", action="store_true")
    c = sub.add_parser("costs")
    c.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        res = list_deployments()
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            for d in res["deployments"]:
                print("%-14s %-28s %-8s %s" % (d["model_name"], d["model"],
                                               d["seat"], d["key"]))
        return 0
    if args.cmd == "route":
        res = route(args.prompt)
        print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else
              ("ok %s %.2fs" % (res.get("used_model"), (res.get("elapsed_ms") or 0) / 1000)
               if res.get("ok") else "FAIL: " + str(res.get("error"))))
        return 0 if res.get("ok") else 1
    res = costs()
    print(json.dumps(res, ensure_ascii=False, indent=1) if args.json else
          "calls=%d total_usd=%s" % (res["calls"], res["total_usd"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
