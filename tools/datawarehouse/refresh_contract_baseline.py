#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""契约产物 sha256 基线维护（R12 D4）。

背景：`dataset_registry.TEMP_CONTRACT_WHITELIST` 里四个产物的 sha256 原是源码常量，
而它们的 `produced_by_job` 全是定时任务——每次重跑字节必变，常量一过期就永久报红。
本脚本把「谁在什么时候认过这个字节」从源码搬到 `contract_baseline.json`：

    python refresh_contract_baseline.py --check
        # 只读：打印每个产物的 expected（基线→出厂）/actual/状态，不写文件
    python refresh_contract_baseline.py --refresh zhice_job_status
        # 人工确认该产物当前字节可信后，写入基线（带 confirmed_at/confirmed_by）
    python refresh_contract_baseline.py --refresh-all --confirmed-by DATA1

口径：
- 只接受 `TEMP_CONTRACT_WHITELIST` 里登记过的 artifact_id（防任意写）
- 产物缺失时拒写（基线不能指向不存在的字节）
- 原子写（临时文件 + replace），并保留其余条目的 confirmed 记录
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataset_registry as dr  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _specs() -> dict:
    return {str(s.get("artifact_id")): s for s in dr.TEMP_CONTRACT_WHITELIST
            if s.get("artifact_id")}


def survey() -> list[dict]:
    """逐条比对：出厂常量 / 基线 / 实际文件。只读。"""
    base = dr.load_contract_baseline()
    rows = []
    for aid, spec in _specs().items():
        p = Path(str(spec.get("path") or ""))
        rec = (base.get("baselines") or {}).get(aid) or {}
        actual = _sha256(p) if p.is_file() else ""
        expected = rec.get("sha256") or spec.get("sha256")
        if not p.is_file():
            state = "missing"
        elif not actual:
            state = "unreadable"
        elif actual == expected:
            state = "active"
        elif spec.get("produced_by_job"):
            state = "drift"           # 定时产物：常态，不告警
        else:
            state = "sha256_mismatch"  # 静态产物：真异常
        rows.append({
            "artifact_id": aid,
            "path": str(p),
            "sha256_factory": spec.get("sha256"),
            "sha256_baseline": rec.get("sha256"),
            "sha256_actual": actual,
            "confirmed_at": rec.get("confirmed_at"),
            "confirmed_by": rec.get("confirmed_by"),
            "produced_by_job": spec.get("produced_by_job"),
            "state": state,
        })
    return rows


def refresh(ids: list[str], confirmed_by: str) -> dict:
    specs = _specs()
    unknown = [i for i in ids if i not in specs]
    if unknown:
        raise SystemExit("未登记的 artifact_id：%s（只允许 %s）"
                         % (", ".join(unknown), ", ".join(sorted(specs))))
    path = Path(os.environ.get(dr.CONTRACT_BASELINE_ENV) or dr.CONTRACT_BASELINE_PATH)
    cur = {"schema": dr.CONTRACT_BASELINE_SCHEMA, "baselines": {}}
    if path.is_file():
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and isinstance(obj.get("baselines"), dict):
                cur = {"schema": obj.get("schema") or dr.CONTRACT_BASELINE_SCHEMA,
                       "baselines": dict(obj["baselines"])}
        except (OSError, ValueError):
            pass  # 坏文件按空基线重建，旧内容在 .bak 里留痕
    now = dt.datetime.now(TZ).replace(microsecond=0).isoformat()
    written = []
    for aid in ids:
        p = Path(str(specs[aid].get("path") or ""))
        if not p.is_file():
            raise SystemExit("产物缺失，拒绝写基线：%s（%s）" % (aid, p))
        digest = _sha256(p)
        cur["baselines"][aid] = {"sha256": digest, "confirmed_at": now,
                                 "confirmed_by": confirmed_by}
        written.append({"artifact_id": aid, "sha256": digest})
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        bak.write_bytes(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {"path": str(path), "confirmed_at": now, "confirmed_by": confirmed_by,
            "written": written}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="契约产物 sha256 基线维护（只读检查 / 人工确认写入）")
    ap.add_argument("--check", action="store_true", help="只读：打印比对结果")
    ap.add_argument("--refresh", action="append", default=[],
                    metavar="ARTIFACT_ID", help="把当前字节写入基线（可重复）")
    ap.add_argument("--refresh-all", action="store_true", help="刷新全部登记产物")
    ap.add_argument("--confirmed-by", default=os.environ.get("USERNAME") or "DATA1")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.refresh or a.refresh_all:
        ids = sorted(_specs()) if a.refresh_all else a.refresh
        out = refresh(ids, a.confirmed_by)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    rows = survey()
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    base = dr.load_contract_baseline()
    print("基线文件：%s（%s）" % (base["path"], "已加载 %d 条" % len(base["baselines"])
                                 if base["loaded"] else "未加载，使用出厂常量"))
    for r in rows:
        print("\n[%s] %s" % (r["state"], r["artifact_id"]))
        print("   path     : %s" % r["path"])
        print("   出厂/基线 : %s / %s" % ((r["sha256_factory"] or "-")[:12],
                                          (r["sha256_baseline"] or "-")[:12]))
        print("   实际     : %s" % ((r["sha256_actual"] or "-")[:12]))
        print("   生产任务 : %s" % (r["produced_by_job"] or "-"))
        if r["confirmed_at"]:
            print("   确认     : %s by %s" % (r["confirmed_at"], r["confirmed_by"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
