# -*- coding: utf-8 -*-
"""FEEDBACK-L0 干跑：分析→采集反馈环 · 纯计算演示（零写入/零触网/零 apply）。

FEEDBACK-20261107（B2-3）L0：
  - 读 registry/sources.json + store/observations.db（只读）；
  - compute_sla + scan_store_pii + compute_sla_feedback 纯计算；
  - 打印 SLA 状态表 + 反馈动作清单 + "若 apply 将发生什么"说明；
  - **不 apply_actions、不写任何文件、不触网、不注册调度**。

已知事实（预研 2026-11-07）：生产采集器不写 observations.db（当前仅 8 条
11-01 演示观测）——本脚本输出即反馈环在真实数据上的现状，属于 L1
（生产观测回写，依赖 ⑤ 唯一真源裁定）之前的地基，**不代表反馈环已闭环**。

运行：python examples/feedback_dry.py   （或 import feedback_dry 后调 run()）
"""
import hashlib
import io as _io
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.feedback import compute_sla_feedback  # noqa: E402
from core.pii import scan_store_pii  # noqa: E402
from core.quality_sla import compute_sla  # noqa: E402
from store.obs_store import ObservationStore  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_PATH = os.path.join(ROOT, "registry", "sources.json")
OBS_DB = os.path.join(ROOT, "store", "observations.db")


def _sha256_file(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def run(sources_path=SOURCES_PATH, obs_db=OBS_DB, now=None):
    """干跑：返回 dict（含状态表/动作/哈希），并打印。纯只读。"""
    before = {"sources": _sha256_file(sources_path), "obs_db": _sha256_file(obs_db)}
    raw = json.loads(_io.open(sources_path, encoding="utf-8").read())
    store = ObservationStore(obs_db) if os.path.exists(obs_db) else ObservationStore(":memory:")
    obs = store.all()
    now = now or datetime.now(timezone.utc)
    sla = compute_sla(raw, obs, now, None)
    pii = scan_store_pii(obs)
    actions, _strikes = compute_sla_feedback(sla, {}, None)
    after = {"sources": _sha256_file(sources_path), "obs_db": _sha256_file(obs_db)}
    assert before == after, "零写入被破坏：%r -> %r" % (before, after)

    lines = []
    lines.append("反馈环 L0 干跑 · %s · %d 源 / %d 观测" % (
        now.strftime("%Y-%m-%d %H:%M UTC"), len(raw), len(obs)))
    lines.append("-" * 64)
    lines.append("%-18s %-8s %10s  %s" % ("source", "state", "age_min", "note"))
    for sid in sorted(sla):
        r = sla[sid]
        age = "-" if r.age_minutes is None else "%.0f" % r.age_minutes
        lines.append("%-18s %-8s %10s  %s" % (sid, r.state, age, r.note[:30]))
    lines.append("-" * 64)
    lines.append("PII 命中：%s" % (pii or "无"))
    lines.append("反馈动作（纯计算，未 apply）：%d 条" % len(actions))
    for a in actions:
        lines.append("  - %-16s %-18s %s" % (a.action.value, a.source_id, a.reason[:34]))
    lines.append("-" * 64)
    lines.append("若 apply 将发生（仅说明，不执行）：")
    if not actions:
        lines.append("  无动作。")
    kinds = {}
    for a in actions:
        kinds[a.action.value] = kinds.get(a.action.value, 0) + 1
    for k, n in kinds.items():
        if k == "expedite_retry":
            lines.append("  expedite_retry ×%d → Scheduler.force_due（下次 tick 立即补采）" % n)
        elif k == "open_circuit":
            lines.append("  open_circuit ×%d → Registry.disable（禁用源，需人工复核恢复）" % n)
        elif k == "downgrade_rights":
            lines.append("  downgrade_rights ×%d → Registry.revoke_rights（撤高风险用途）" % n)
        elif k == "flag_review":
            lines.append("  flag_review ×%d → Registry.set_review_flag（人工复核标记）" % n)
    lines.append("零写入实证：sources.json %s / observations.db %s（前后一致）" % (
        before["sources"] or "N/A", before["obs_db"] or "N/A"))
    lines.append("注意：生产观测回写缺失（L1，依赖 ⑤）——本输出不代表反馈环已闭环。")
    text = "\n".join(lines)
    print(text)
    return {
        "sla": {sid: r.to_dict() for sid, r in sla.items()},
        "pii": pii,
        "actions": [
            {"action": a.action.value, "source_id": a.source_id, "reason": a.reason}
            for a in actions
        ],
        "hashes": after,
        "text": text,
        "n_sources": len(raw),
        "n_obs": len(obs),
    }


if __name__ == "__main__":
    run()
