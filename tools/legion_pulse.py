# -*- coding: utf-8 -*-
"""军团心跳模块（K-ORG-121 L1 · observe 账本补账）

纪律（K-ORG-116 §三）：全军团一套账本，caller 必填归因。
一切军团工具在关键动作点调用 heartbeat()——30 秒接入，让 42 员的每一天都被账本看见。

用法：
  from legion_pulse import heartbeat
  heartbeat("LEGION_DEBATE", "run", "600337 全链分析完成")
"""
import json
import time
from pathlib import Path

AUDIT = Path(__file__).resolve().parent.parent / "agent_platform" / "data" / "llm_tool_audit.jsonl"


def heartbeat(agent: str, action: str, detail: str = "", extra: dict = None) -> None:
    """军团动作统一落账。fail-open：账本不可写只告警不阻断业务。"""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "caller": agent,
           "action": action, "detail": detail[:200]}
    if extra:
        rec.update(extra)
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[legion_pulse] 账本写入失败(fail-open): {e}")


def recent(minutes: int = 60) -> list:
    """读近 N 分钟活动（大屏数据源）。"""
    try:
        cutoff = time.time() - minutes * 60
        out = []
        for line in reversed(AUDIT.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                ts = r.get("ts", "")
                t = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S")) if ts else 0
                if t >= cutoff:
                    out.append(r)
                else:
                    break
            except Exception:
                continue
        return out
    except FileNotFoundError:
        return []
