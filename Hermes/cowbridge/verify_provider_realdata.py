# -*- coding: utf-8 -*-
"""provider 读侧真实数据验证——CowAgent 冒烟对话（smoke_v65）应可被 Hermes 侧检索。

与 test_provider_plugin.py 的区别：不 monkeypatch 桥读侧，
read_cow_memory 直打真实 ~/cow 的 index.db（K273 部署后已有数据）。

运行：python verify_provider_realdata.py   # 须在 hermes venv 内执行（HERMES_HOME 默认 ~/.hermes）
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

AGENT_DIR = Path(os.getenv("HERMES_AGENT_DIR") or os.getenv("HERMES_HOME") or str(Path.home() / ".hermes")) / "hermes-agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def main() -> int:
    print("== provider 读侧真实数据验证（~/cow index.db）==")

    from plugins.memory import load_memory_provider

    # 1) 桥读侧直读真实 workspace
    bridge = None
    prov = load_memory_provider("cowbridge")
    check("1 provider 装载", prov is not None)
    if prov is None:
        return 1
    prov.initialize("verify-realdata-001",
                    hermes_home=str(Path.home() / ".hermes"),
                    agent_context="primary", platform="cli")
    bridge = prov._bridge
    check("1b 桥本体加载", bridge is not None)

    items = bridge.read_cow_memory()
    dialogs = [i for i in items if i.get("tier") == "dialog"]
    check("2 真实 index.db 产出 dialog 候选", len(dialogs) >= 1,
          f"items={len(items)} dialogs={len(dialogs)}")
    joined = json.dumps(dialogs, ensure_ascii=False)
    check("2b 命中 smoke_v65 转录", "smoke_v65" in joined and "自我介绍" in joined)

    # 2) prefetch 注入召回（真实查询场景：用户在 Hermes 里问 CowAgent 是谁）
    q = "CowAgent Qwen 自我介绍 交流风格"
    prov.queue_prefetch(q)
    time.sleep(0.6)
    pf = prov.prefetch(q)
    check("3 prefetch 召回真实转录", ("自我介绍" in pf) or ("Qwen" in pf),
          repr(pf[:200]))

    # 3) 工具检索（Hermes agent 的 cow_memory_search 真实调用）
    out = prov.handle_tool_call("cow_memory_search", {"query": "自我介绍"})
    o = json.loads(out)
    check("4 cow_memory_search 命中", o.get("count", 0) >= 1
          and "自我介绍" in json.dumps(o, ensure_ascii=False), out[:300])

    prov.shutdown()
    print(f"== PASS {len(PASS)} / FAIL {len(FAIL)} ==")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
