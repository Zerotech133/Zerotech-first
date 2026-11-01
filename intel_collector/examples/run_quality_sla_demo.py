# -*- coding: utf-8 -*-
"""原型态演示：质量 SLA 四态 + PII 脱敏（不触网、虚拟时钟）。

目的：用一组虚拟时间戳把 no_data / 达标 / 滞后 / 违约 四态与 PII 命中直观打印出来，
证明 quality_sla + pii 引擎行为正确；真实面板由 dashboard_build.py 用真实 store+now 计算。

用法：python examples/run_quality_sla_demo.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts import Observation, SourceClock, Status
from core.quality_sla import compute_sla
from core.pii import redact_text, scan_store_pii

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _spec(sid, fresh, max_fail=3):
    return {sid: {"sla": {"freshness_minutes": fresh, "max_failures": max_fail}}}


# 构造覆盖四态的合成注册表
SOURCES = {}
SOURCES.update(_spec("s_ok", 60))        # 达标：采集于 11:55（age=5m）
SOURCES.update(_spec("s_stale", 30))     # 滞后：采集于 11:20（age=40m，30<40<=60）
SOURCES.update(_spec("s_breach", 30))    # 违约：采集于 10:50（age=70m，>60）
# s_no_data：注册表里有但 store 无观测


def _obs(sid, ts, status=Status.COMPLETE.value):
    return Observation(
        source_id=sid, snapshot_id=f"{sid}_{ts}", entity_type="t", entity_id="e",
        clock=SourceClock(collected_at=ts), status=status, completeness="ok",
        payload={}, allowed_use_cases=(), source_field="f", raw_sha256="x",
    )


# 含 PII 的样本（演示脱敏计数）
_PII_OBS = Observation(
    source_id="s_ok", snapshot_id="pii_1", entity_type="t", entity_id="e",
    clock=SourceClock(collected_at="2026-11-01T11:55:00Z"),
    status=Status.COMPLETE.value, completeness="ok",
    payload={"items": [{"title": "联系 13800138000 或 a@b.com"}]},
    allowed_use_cases=(), source_field="f", raw_sha256="x",
)

OBS = [
    _obs("s_ok", "2026-11-01T11:55:00Z"),
    _obs("s_stale", "2026-11-01T11:20:00Z"),
    _obs("s_breach", "2026-11-01T10:50:00Z"),
    _PII_OBS,
]

# 演示 redact_text
sample = "手机 13912345678 邮箱 x@y.com 身份证 11010119900307123X"
redacted, n = redact_text(sample)

print("=" * 64)
print(f"质量 SLA 演示（虚拟 now = {NOW.isoformat()}）")
print("=" * 64)
reports = compute_sla(SOURCES, OBS, NOW)
for sid, r in reports.items():
    d = r.to_dict()
    age = "—" if d["age_minutes"] is None else f'{d["age_minutes"]:.0f}m'
    print(f"  {sid:10s} 态={d['state_label']:4s} age={age:>5s}/{d['freshness_minutes']}m "
          f"空比={d['empty_ratio']*100:.0f}% 连续失败={d['consecutive_failures']}")

print("-" * 64)
print("PII 脱敏演示：")
print(f"  脱敏后：{redacted}")
print(f"  命中数：{n}")
print(f"  入湖前对 store 的 PII 统计：{scan_store_pii(OBS)}")
print("=" * 64)
print("说明：真实面板由 dashboard_build.py 以真实 store + 真实 now 计算；")
print("seed 时间戳为 2026-10-21/22，相对真实 now 已超 freshness，故面板多源显示违约——告警引擎有效。")
