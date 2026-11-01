# -*- coding: utf-8 -*-
"""② 解锁：把 pending_sources.json 的 5 源登记进原型 Registry。

前提：老田 2026-11-01 显式 override 田丰归口登记；owner 归情报口(INTEL0，军情司上行受理席)，曹操属工程线(dev/ARCH1)非情报owner，仅保留跨部门协调角色且≤10%工时。
仅写原型自带 registry/sources.json，不碰生产数据根 / 现役来源注册。
权利按 pending 提案设 internal_retention/cleaning/controlled_query=true（均为非高风险，
不需 explicit_approval meta）；training/redistribution/commercial 默认 false（不开放）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from registry.source_registry import SourceRegistry, SourceSpec


HERE = os.path.dirname(os.path.abspath(__file__))
PENDING = os.path.join(HERE, "pending_sources.json")
OUT = os.path.join(HERE, "sources.json")


def main() -> None:
    with open(PENDING, encoding="utf-8") as f:
        data = json.load(f)

    reg = SourceRegistry(OUT)
    registered = []
    for s in data["pending_sources"]:
        spec = SourceSpec(
            source_id=s["source_id"],
            name=s["name"],
            category=s["category"],
            owner=s["proposed_owner"],  # 老田 override 确认，非 UNOWNED
            content_types=s.get("content_types", []),
            rights=dict(s.get("rights", {})),
            sla=s.get("sla", {}),
            enabled=True,
            note="老田 2026-11-01 override 田丰归口登记",
        )
        reg.register(spec)  # 内部硬拒 UNOWNED，owner 合法才过
        registered.append(s["source_id"])

    # 验证：数量一致、无 UNOWNED
    assert len(reg.list()) == len(data["pending_sources"]), "登记数量不符"
    for sp in reg.list():
        assert sp.owner and sp.owner.upper() != "UNOWNED", f"{sp.source_id} owner 非法"

    print(f"✅ 已登记 {len(registered)} 源：{registered}")
    print(f"✅ 输出文件：{OUT}")
    print("✅ 全部 owner 合法，无 UNOWNED")


if __name__ == "__main__":
    main()
