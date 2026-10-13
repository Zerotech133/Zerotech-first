# -*- coding: utf-8 -*-
"""K-STOCK-327 · S1 影子证据封存打包（10-15 合流日一键执行）。

合流设计稿 §三 S1：影子证据封存——shadow_ledger.jsonl + checkpoints.db +
判据表 + 两线评估报告打 zip 快照挪 data/archive/。

本脚本原则（326 预检修订）：
  - 只打包复制，**绝不挪动** shadow_ledger.jsonl / checkpoints.db（线上账本）；
  - 评估报告与判据表原件不动（docs/ 与 shadow/ 均在 git 管辖，zip 是封存快照）；
  - zip 内带 MANIFEST.txt（文件清单 + sha256 自证）；
  - 幂等：重跑生成新时刻 zip，不覆盖。

用法：
  python quant_backtest/_s1_seal.py            # 打包 → data/archive/s1_seal_*.zip
  python quant_backtest/_s1_seal.py --list     # 只列待打包文件不动手
"""
import hashlib
import os
import sys
import time
import zipfile

QBT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(QBT)

# ---- 待封存清单（存在才打包）----
ITEMS = [
    # (abs 路径, zip 内相对路径)
    (os.path.join(ROOT, "agent_platform", "data", "shadow", "shadow_ledger.jsonl"),
     "shadow_ledger.jsonl"),
    (os.path.join(ROOT, "agent_platform", "data", "checkpoints.db"),
     "checkpoints.db"),
    (os.path.join(ROOT, "agent_platform", "data", "shadow",
                  "eval_shadow_2026-10-14_official.md"),
     "eval_shadow_2026-10-14_official.md"),
    (os.path.join(ROOT, "docs", "ops",
                  "2026-10-13_合流就绪度检查_1015预检_K-STOCK.md"),
     "docs_ops_预检_326b.md"),
    (os.path.join(ROOT, "docs", "ops",
                  "2026-10-13_K-ORG-043_双通道产出横向对比.md"),
     "docs_ops_产线判据1对比.md"),
]
# 影子区 poc 原料（35 文件·整目录收编）
POC_DIR = os.path.join(ROOT, "k_stock", "data")


def sha256(fp):
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect():
    found, missing = [], []
    for fp, arc in ITEMS:
        if os.path.exists(fp):
            found.append((fp, arc))
        else:
            missing.append(arc)
    for fn in sorted(os.listdir(POC_DIR)):
        if fn.startswith("poc_") and fn.endswith(".json"):
            found.append((os.path.join(POC_DIR, fn), "poc/" + fn))
    return found, missing


def main():
    listing = "--list" in sys.argv
    found, missing = collect()
    print("== S1 封存清单 ==")
    for fp, arc in found:
        print("  %-46s %9d B" % (arc, os.path.getsize(fp)))
    if missing:
        print("  [缺失]（跳过·不阻断）:", ", ".join(missing))
    if listing:
        print("（--list 模式：未打包）")
        return
    if not any(a.startswith("eval_shadow") for _, a in found):
        raise SystemExit("ABORT: 无 eval_shadow 正式评估报告——先跑 10-14 门禁评估再封存")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(ROOT, "agent_platform", "data", "archive")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "s1_seal_%s.zip" % stamp)
    manifest = ["S1 影子证据封存 %s" % time.strftime("%Y-%m-%d %H:%M:%S"), ""]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for fp, arc in found:
            z.write(fp, arc)
            manifest.append("%-46s %s" % (arc, sha256(fp)))
        z.writestr("MANIFEST.txt", "\n".join(manifest) + "\n")
    print("== 封存完成 ==")
    print("zip:", out)
    print("大小: %d B | 条目: %d" % (os.path.getsize(out), len(found) + 1))
    print("MANIFEST.txt 含逐文件 sha256（封存自证）")
    print("注意: shadow_ledger/checkpoints 原件未动（线上账本）")


if __name__ == "__main__":
    main()
