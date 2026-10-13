# -*- coding: utf-8 -*-
"""
D1 (K-ORG-030, ARCH1): prompt 版本化登记器
=============================================
扫描 agent_platform/prompts/*.md，从 git log 提取每份人设的提交历史，
生成 agent_platform/registry/prompt_versions.json 版本登记表：
  - version: 提交次数即版本号（v1 = 初版）
  - last_commit / last_date / last_msg: 最近一次变更
  - history: 最近 5 次变更摘要
单一事实源 = git 历史，登记表可随时重跑再生（幂等）。
改动 prompt 后重跑本脚本即完成版本登记，无需手写 CHANGELOG。
"""
import glob
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS = os.path.join(ROOT, "agent_platform", "prompts")
OUT = os.path.join(ROOT, "agent_platform", "registry", "prompt_versions.json")


def git_log(path, limit=5):
    """取单文件的提交历史（新→旧）。"""
    cmd = ["git", "-C", ROOT, "log", "--follow", "-n", str(limit),
           "--date=format:%Y-%m-%d", "--pretty=%h|%ad|%s", "--", path]
    r = subprocess.run(cmd, capture_output=True)
    out = (r.stdout or b"").decode("utf-8", "ignore")
    rows = []
    for line in out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            rows.append({"sha": parts[0], "date": parts[1], "msg": parts[2][:120]})
    return rows


def main():
    files = sorted(glob.glob(os.path.join(PROMPTS, "*.md")))
    registry = {}
    for fp in files:
        rel = os.path.relpath(fp, ROOT).replace("\\", "/")
        name = os.path.splitext(os.path.basename(fp))[0]
        hist = git_log(rel)
        if not hist:
            # 未提交的新文件：登记为 v0 draft
            registry[name] = {
                "file": rel, "version": 0, "status": "draft-uncommitted",
                "last_date": "-", "last_msg": "尚未入库",
                "history": [],
            }
            continue
        registry[name] = {
            "file": rel,
            "version": len(hist),          # 提交次数即版本
            "status": "committed",
            "last_commit": hist[0]["sha"],
            "last_date": hist[0]["date"],
            "last_msg": hist[0]["msg"],
            "history": hist,
        }
    out = {
        "_note": "D1(K-ORG-030,ARCH1) prompt 版本化登记：单一事实源=git 历史，"
                 "改 prompt 后重跑 tools/prompt_version_registry.py 再生。version=提交次数。",
        "generated": __import__("time").strftime("%Y-%m-%d %H:%M"),
        "total": len(registry),
        "prompts": registry,
    }
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    committed = sum(1 for v in registry.values() if v["status"] == "committed")
    print("prompt_versions.json -> %d 份（committed %d / draft %d）"
          % (len(registry), committed, len(registry) - committed))
    # 最高频变更 top3（最活跃人设）
    top = sorted(registry.items(), key=lambda kv: -kv[1]["version"])[:3]
    for name, v in top:
        print("  v%d %s (%s)" % (v["version"], name, v.get("last_date", "-")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
