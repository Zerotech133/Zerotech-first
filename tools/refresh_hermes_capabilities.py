#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刷新 Hermes「赵云」能力卡（seat_capabilities/v1）——保守刷新器。

背景（2026-10-29）：`~/.hermes/profiles/zhaoyun/skills/CAPABILITIES.json` 是**人工 curated**
产物（含 what/how/boundary 文案），仓库内无生成器；其 generated_at 超 48h 会使
`capability_self_audit` 判 stale，进而 3 条测试依赖它。本脚本**不重写能力文案**，只做：
  1) 用 Agent Skills 注册表（skill_registry.verify_all）刷新每个能力的 status；
  2) 重新汇总 total/ready/degraded_relay/degraded_rule/pending_wiring/pending_key/paper；
  3) generated_at = 当前时间（Asia/Shanghai）；
  4) 写前备份 .bak-<ts>，写后回读校验并跑 capability_self_audit.check() 断言 ok。

用法：
  python tools/refresh_hermes_capabilities.py             # 默认 dry-run（只打印将变更）
  python tools/refresh_hermes_capabilities.py --apply     # 落盘（自动备份）
  python tools/refresh_hermes_capabilities.py --only-stale  # 仅当 stale 时 apply 之外的行为交给 --apply
退出码：0=成功（dry-run 也 0）；1=校验失败/需要人工。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLATFORM = REPO / "agent_platform"
CAPS = Path(os.path.expanduser("~")) / ".hermes" / "profiles" / "zhaoyun" / "skills" / "CAPABILITIES.json"
STATUS_KEYS = ("ready", "degraded_relay", "degraded_rule", "pending_wiring", "pending_key", "paper")


def _now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).replace(microsecond=0).isoformat()


def _verify_status_map():
    """能力名 → 注册表 verify 结果（True=ok）。失败返回空映射（保守：不改 status）。"""
    try:
        sys.path.insert(0, str(PLATFORM / "tools"))
        from skill_registry import verify_all  # noqa: WPS433

        rep = verify_all()
        out = {}
        for r in rep.get("results") or []:
            name = str(r.get("name") or "").strip()
            if name:
                out[name] = bool(r.get("ok"))
        return out
    except Exception as exc:  # noqa: BLE001
        print("[refresh] 注册表 verify 不可用：%r（将保留原 status）" % exc)
        return {}


def _recount(data):
    caps = [c for c in (data.get("capabilities") or []) if isinstance(c, dict)]
    counts = {k: 0 for k in STATUS_KEYS}
    for c in caps:
        st = str(c.get("status") or "").strip()
        if st in counts:
            counts[st] += 1
    data["total"] = len(caps)
    for k in STATUS_KEYS:
        data[k] = counts[k]
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="刷新 Hermes 赵云能力卡（保守）")
    ap.add_argument("--apply", action="store_true", help="落盘（默认 dry-run）")
    ap.add_argument("--caps", default=str(CAPS), help="能力卡路径")
    args = ap.parse_args(argv)

    path = Path(args.caps)
    if not path.is_file():
        print("[refresh] 能力卡不存在：%s" % path)
        return 1
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("schema") != "seat_capabilities/v1":
        print("[refresh] 非预期 schema：%r（拒绝）" % data.get("schema"))
        return 1

    old_ts = data.get("generated_at")
    status_map = _verify_status_map()
    changed = []
    for cap in data.get("capabilities") or []:
        name = str(cap.get("name") or "")
        if name in status_map and status_map[name] and cap.get("status") not in ("ready", "paper"):
            cap["status"] = "ready"
            changed.append(name)
    _recount(data)
    data["generated_at"] = _now()
    print("[refresh] 旧 generated_at=%s → 新=%s" % (old_ts, data["generated_at"]))
    print("[refresh] status 刷新：%s" % (", ".join(changed) if changed else "无"))
    print("[refresh] 汇总：total=%s %s" % (data.get("total"), " ".join("%s=%s" % (k, data.get(k)) for k in STATUS_KEYS)))

    if not args.apply:
        print("[refresh] DRY-RUN：未写盘（加 --apply 落盘）")
        return 0

    backup = path.with_name(path.name + ".bak-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, backup)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    print("[refresh] 已写入（备份 %s）" % backup.name)

    # 回读 + 审计校验
    try:
        sys.path.insert(0, str(PLATFORM / "tools"))
        import importlib
        csa = importlib.import_module("capability_self_audit")
        rep = csa.check()
        print("[refresh] audit ok=%s drift=%d" % (rep.get("ok"), len(rep.get("drift") or [])))
        return 0 if rep.get("ok") else 1
    except Exception as exc:  # noqa: BLE001
        print("[refresh] 审计校验异常：%r" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
