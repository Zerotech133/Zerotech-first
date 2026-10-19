# -*- coding: utf-8 -*-
"""军团心跳桥接器（K-ORG-190 拍板⑤ 立项产物）。

职责：把主仓 hb.v1 账本（agent_platform/data/heartbeat/heartbeat.jsonl，
由 adapters/heartbeat.py 探针写入）的尾端新行转写到军团心跳真总线
llm_tool_audit.jsonl（tools/legion_pulse.heartbeat()），供大屏 recent()
与事件回放消费。

设计约束（拍板①②联动）：
- run-once 非守护进程：周期化由计划任务承载，本脚本绝不自建循环/线程。
- 8790 未上线时 hb.jsonl 不存在或零新行 → exit 0 打印 NOTICE，不造数据、
  不假绿（账本缺失≠心跳异常）。
- 幂等：bridge_state.json 记录已消费行数，重启重跑不重复转写；账本行数
  少于已消费数（账本被轮转/重建）时重置为 0 重新消费。
- 跨仓依赖只锁在主仓侧（军侧零改动，A 案铁律）。

用法：
    python tools/legion_heartbeat_bridge.py --dry-run   # 只打印不写
    python tools/legion_heartbeat_bridge.py             # 实转写
"""
import argparse
import json
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parent.parent / "agent_platform"
HB_LEDGER = PLATFORM / "data" / "heartbeat" / "heartbeat.jsonl"
BRIDGE_STATE = PLATFORM / "data" / "heartbeat" / "bridge_state.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load_state():
    if BRIDGE_STATE.exists():
        try:
            return json.loads(BRIDGE_STATE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass  # 坏状态文件 fail-visible：当作零消费重新对账
    return {"consumed": 0}


def _save_state(state):
    BRIDGE_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = BRIDGE_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(BRIDGE_STATE)  # 原子写


def _read_new_rows(consumed):
    if not HB_LEDGER.exists():
        return None, consumed  # None = 账本未生成
    lines = [ln for ln in HB_LEDGER.read_text(encoding="utf-8")
             .splitlines() if ln.strip()]
    if len(lines) < consumed:
        consumed = 0  # 账本轮转/重建：从头重消费（幂等键在总线侧兜底）
    rows = []
    for ln in lines[consumed:]:
        try:
            rows.append(json.loads(ln))
        except ValueError:
            rows.append({"_bad_line": True, "raw": ln[:120]})
    return rows, len(lines)


def main():
    ap = argparse.ArgumentParser(description="hb.v1 → llm_tool_audit 心跳桥")
    ap.add_argument("--dry-run", action="store_true", help="只打印不转写")
    args = ap.parse_args()

    state = _load_state()
    rows, consumed = _read_new_rows(int(state.get("consumed", 0)))
    if rows is None:
        print("[bridge] NOTICE: %s 不存在（8790 未上线/探针未投产，属预期）"
              % HB_LEDGER)
        return 0
    if not rows:
        print("[bridge] OK: 无新行（consumed=%d）" % consumed)
        return 0

    from legion_pulse import heartbeat  # 主仓侧真总线写入口

    written = 0
    for r in rows:
        if r.get("_bad_line"):
            print("[bridge] SKIP 坏行: %s" % r["raw"])
            continue
        source = r.get("source") or ""
        if not source:
            print("[bridge] SKIP 无 source 行: %s" % json.dumps(r,
                  ensure_ascii=False)[:120])
            continue
        detail = "alive=%s detail=%s" % (r.get("alive"), r.get("detail") or "")
        if args.dry_run:
            print("[bridge][dry] heartbeat(%r, 'heartbeat_bridge', %r)"
                  % (source, detail))
        else:
            heartbeat(source, "heartbeat_bridge", detail,
                      extra={"checked_at": r.get("checked_at"),
                             "alive": r.get("alive")})
        written += 1

    if not args.dry_run:
        _save_state({"consumed": consumed, "last_written": written})
    print("[bridge] %s: 转写 %d 行, consumed=%d"
          % ("DRY" if args.dry_run else "OK", written, consumed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
