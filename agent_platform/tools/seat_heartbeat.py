# -*- coding: utf-8 -*-
"""席位心跳（B2-e）：对指定 LLM 席每日做一次**真实最小模型调用**，补齐触发/审计维。

做的是真事：用 DeepSeek 引擎按各席人设/职责发一条最小请求，拿到真实回复；
结果写 `agent_platform/data/seat_heartbeat/<席>/status.json` + history.jsonl，
并落 `dev_events`（patrol_verdict，task=agent=席码）。不写业务、不改配置、不调用工具。

环境/回滚：`SEAT_HEARTBEAT=off` 立即跳过；删除计划任务 + 本工具即回滚。
退出码：0 全部心跳成功；1 有失败；2 自身错误（客户端/配置）。
用法：python agent_platform/tools/seat_heartbeat.py --quiet
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
CONFIG = PLATFORM / "config" / "seat_heartbeat_seats.json"
KILL_ENV = "SEAT_HEARTBEAT"
KILL_VALUES = ("off", "0", "false", "no")
MAX_HISTORY = 2000


def kill_switch_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(KILL_ENV) or "").strip().lower() in KILL_VALUES


def load_config(path=CONFIG) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _client():
    sys.path.insert(0, str(PLATFORM))
    from engine import deepseek  # noqa: WPS433

    return deepseek.DeepSeekClient()


def seat_brief(seat: str, bundle: dict) -> dict:
    card = (bundle.get("agents") or {}).get(seat) or {}
    return {
        "seat": seat,
        "name": card.get("cn_name") or seat,
        "role": card.get("role_cn") or card.get("role_profile") or "",
        "charter": (card.get("charter") or "")[:300],
    }


def heartbeat_one(client, seat: str, brief: dict, model: str, max_tokens: int) -> dict:
    system = (
        "你是数字军团席位自检器。只用一句话确认在岗，并复述你的岗位职责；"
        "不调用工具、不编造数据、不执行动作。"
    )
    user = "席位：%s（%s）。岗位：%s。职责摘要：%s" % (
        brief["seat"],
        brief["name"],
        brief["role"],
        brief["charter"],
    )
    t0 = time.time()
    try:
        reply = client.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
            temperature=0.0,
            max_tokens=max_tokens,
        )
        text = str(reply or "").strip()
        return {
            "seat": seat,
            "ok": bool(text),
            "model": model,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "reply_len": len(text),
            "reply_sha8": hashlib.sha256(text.encode("utf-8")).hexdigest()[:8],
            "reply_head": text[:120],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "seat": seat,
            "ok": False,
            "model": model,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
        }


def record(results: list, status_root=None) -> Path:
    root = Path(status_root) if status_root else (PLATFORM / "data" / "seat_heartbeat")
    root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    for r in results:
        d = root / r["seat"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text(
            json.dumps({**r, "generated_at": ts}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    hist = root / "history.jsonl"
    with open(hist, "a", encoding="utf-8") as f:
        f.write(
            json.dumps({"generated_at": ts, "results": results}, ensure_ascii=False)
            + "\n"
        )
    lines = hist.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_HISTORY:
        hist.write_text("\n".join(lines[-MAX_HISTORY:]) + "\n", encoding="utf-8")
    return root


def emit_audit(result: dict) -> dict:
    try:
        tools = str(REPO / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import events  # noqa: WPS433

        return events.emit(
            kind="patrol_verdict",
            task=result["seat"],
            agent=result["seat"],
            detail=(
                "seat_heartbeat ok=%s model=%s reply_len=%s"
                % (result.get("ok"), result.get("model"), result.get("reply_len"))
            )[:300],
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="席位真实心跳（DeepSeek 最小调用）")
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--status-dir", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if kill_switch_on():
        if not args.quiet:
            print(
                json.dumps({"ok": False, "skipped": "kill_switch"}, ensure_ascii=False)
            )
        return 0
    try:
        cfg = load_config(args.config)
        seats = [str(s).upper() for s in (cfg.get("seats") or [])]
        model = str(cfg.get("model") or "deepseek-flash")
        max_tokens = int(cfg.get("max_tokens") or 64)
        bundle = json.loads(
            (REPO / "portal" / "agent_ide_bundle.json").read_text(encoding="utf-8")
        )
        client = _client()
        results = [
            heartbeat_one(client, s, seat_brief(s, bundle), model, max_tokens)
            for s in seats
        ]
        audited = [emit_audit(r) for r in results]
        record(results, args.status_dir or None)
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:200]},
                ensure_ascii=False,
            )
        )
        return 2
    ok_count = sum(1 for r in results if r.get("ok"))
    summary = {
        "ok": ok_count == len(results),
        "total": len(results),
        "ok_count": ok_count,
        "model": model,
        "results": results,
        "audits": audited,
    }
    if not args.quiet:
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=1))
        else:
            for r in results:
                print(
                    "%-9s ok=%s len=%s %s"
                    % (r["seat"], r.get("ok"), r.get("reply_len"), r.get("error") or "")
                )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
