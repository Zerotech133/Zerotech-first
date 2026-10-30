# -*- coding: utf-8 -*-
"""P0 · YouTube 量化语料交付包完整性核验。

- 逐 ZIP 校验 .sha256（单行 `<hash>  <filename>` 格式）；
- 解析 .manifest.json 汇总 counts（snapshots / batch_unique_videos / batch_unique_comments）；
- 与交付报告口径比对（103,033 快照 / 45,881 视频 / 948 ZIP）；
- 输出 90_报告/p0_verify_report.{json,md}。

用法：python tools/kb_corpus/verify.py [--no-hash]（--no-hash 只对账不校验哈希）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

KB = Path(r"C:\项目\知识库")
SUB = KB / "01_数据分包"
OUT = KB / "90_报告"


def sha256_file(p: Path, chunk=8 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-hash", action="store_true")
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    groups = {"主包": sorted(SUB.glob("*.zip")), "补充更正_v2": sorted((SUB / "补充更正_v2").glob("*.zip"))}
    report = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "groups": {}, "hash_failures": [], "manifest_missing": [], "counts": {}}
    totals = {"zips": 0, "hash_ok": 0, "hash_bad": 0, "manifests": 0,
              "snapshots": 0, "videos": 0, "comments": 0, "bytes": 0}
    for gname, zips in groups.items():
        g = {"zips": len(zips), "hash_ok": 0, "hash_bad": 0, "manifests": 0,
             "snapshots": 0, "videos": 0, "comments": 0, "bytes": 0}
        for z in zips:
            g["bytes"] += z.stat().st_size
            if not args.no_hash:
                sha_file = z.with_name(z.name + ".sha256")
                if sha_file.exists():
                    line = sha_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()[0]
                    expect = line.split()[0].strip().lower()
                    actual = sha256_file(z)
                    if expect == actual:
                        g["hash_ok"] += 1
                    else:
                        g["hash_bad"] += 1
                        report["hash_failures"].append({"zip": z.name, "expect": expect, "actual": actual})
                else:
                    g["hash_bad"] += 1
                    report["hash_failures"].append({"zip": z.name, "error": "missing .sha256"})
            mf = z.with_name(z.name + ".manifest.json")
            if mf.exists():
                try:
                    m = json.loads(mf.read_text(encoding="utf-8"))
                    c = m.get("counts") or {}
                    g["manifests"] += 1
                    g["snapshots"] += int(c.get("snapshots") or 0)
                    g["videos"] += int(c.get("batch_unique_videos") or 0)
                    g["comments"] += int(c.get("batch_unique_comments") or 0)
                except Exception as e:  # noqa: BLE001
                    report["manifest_missing"].append({"zip": z.name, "error": str(e)[:120]})
            else:
                report["manifest_missing"].append({"zip": z.name, "error": "missing manifest"})
        report["groups"][gname] = g
        for k in ("zips", "hash_ok", "hash_bad", "manifests", "snapshots", "videos", "comments", "bytes"):
            totals[k] += g[k]
    report["counts"] = totals
    report["delivery_report_expected"] = {"zips": 948, "snapshots": 103033,
                                          "unique_videos": 45881, "unique_comments": 1400876}
    report["reconcile"] = {
        "zips_match": totals["zips"] == 948,
        "snapshots_match": abs(totals["snapshots"] - 103033) <= 2,
        "snapshots_delta": totals["snapshots"] - 103033,
    }
    (OUT / "p0_verify_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    L = ["# P0 · 交付包完整性核验报告", "",
         f"- 时间：{report['generated_at']}", "",
         "| 组 | ZIP | hash OK | hash 失败 | manifest | snapshots | batch videos | batch comments | 体积 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for gname, g in report["groups"].items():
        L.append("| %s | %d | %d | %d | %d | %d | %d | %d | %.2f GB |" % (
            gname, g["zips"], g["hash_ok"], g["hash_bad"], g["manifests"],
            g["snapshots"], g["videos"], g["comments"], g["bytes"] / 1e9))
    t = totals
    L += ["", "**合计**：ZIP **%d** / hash OK **%d** / 失败 **%d**；manifest %d 份；快照 **%d**（交付报告 103,033，差 %+d）；体积 **%.2f GB**" % (
        t["zips"], t["hash_ok"], t["hash_bad"], t["manifests"], t["snapshots"],
        report["reconcile"]["snapshots_delta"], t["bytes"] / 1e9), ""]
    if report["hash_failures"]:
        L += ["## hash 失败清单（前 30）", ""]
        L += ["- %s" % x for x in report["hash_failures"][:30]]
    if report["manifest_missing"]:
        L += ["", "## manifest 缺失/异常（前 30）", ""]
        L += ["- %s" % x for x in report["manifest_missing"][:30]]
    L += ["", "## 说明", "- 交付报告口径 103,033 快照；主包+补充包逐包 manifest counts 汇总对账；差异 ≤2 视为边界。",
          "- hash 失败需重新下载对应包；本报告只如实列出，不修改原始数据。"]
    (OUT / "p0_verify_report.md").write_text("\n".join(L), encoding="utf-8")
    print(json.dumps({"totals": t, "reconcile": report["reconcile"],
                      "hash_failures": len(report["hash_failures"]),
                      "md": str(OUT / "p0_verify_report.md")}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
