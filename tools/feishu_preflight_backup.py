# -*- coding: utf-8 -*-
r"""飞书中台开闸/重启前备份工具（checklist 32 缺口补齐）。

背景：检查清单第 32 项「备份与回滚预案」长期停在「预案文字在、实际未执行」
——`C:\Temp\feishu_pre_restart_20261020` 从未创建。本工具把备份变成一条命令，
并在 manifest 里留哈希，便于「备份是否完整」自证。

设计原则
--------
1. **只读复制**：不删、不改、不移动任何源文件；只往目标目录写。
2. **不备份凭据**：`push_secrets.json`、`audit_ai_config.json`、`*.key`、
   `*token*` 一律排除——备份包外泄时不能连带泄露密钥（判例：凭据不落地外传）。
3. **可自证**：manifest.json 记每个文件的 size + mtime + sha256；
   `--verify` 逐文件重算哈希，能证明「备份与源一致」。
4. **清单可读**：`--list` 只打印将要备份什么，不写盘（干跑）。

用法
----
    python tools/feishu_preflight_backup.py --list
    python tools/feishu_preflight_backup.py                  # 备份到 C:\\Temp\\feishu_backup_<ts>
    python tools/feishu_preflight_backup.py --dest D:\\bak
    python tools/feishu_preflight_backup.py --verify C:\\Temp\\feishu_backup_20261021_160000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 备份对象：按「丢失后不可再生」排序（账本 > 画像/状态 > 配置 > 注册表）
INCLUDE_PATTERNS = [
    ("portal/work/*.jsonl", "留痕与账本流水（含画像/记忆/路由留痕）"),
    ("portal/work/*.sqlite3", "订阅账本等 sqlite"),
    ("portal/work/*.json", "状态文件（renewal_state / card_actions 等）"),
    ("portal/data/*.jsonl", "数据面流水"),
    ("portal/nginx.conf", "反代配置（改坏需回滚）"),
    ("portal/static/sub_admin.html", "商务运营面（今日改动）"),
    ("portal/static/my_subscription.html", "客户订阅面"),
    ("agent_platform/registry/org.json", "军团编制注册表"),
    ("agent_platform/registry/team_comm.json", "通信部建册"),
]

# 绝不备份（凭据/密钥/含 key 的配置）
EXCLUDE_KEYWORDS = (
    "secret", "secrets", "key", "token", "password", "credential",
    "auth.json", "audit_ai_config.json", ".env",
)


def _is_excluded(path: Path) -> bool:
    name = path.name.lower()
    return any(k in name for k in EXCLUDE_KEYWORDS)


def collect(root: Path = None):
    """→ [(相对路径, 绝对路径, 说明)]。排除凭据类。"""
    base = Path(root) if root else ROOT
    out = []
    seen = set()
    for pattern, desc in INCLUDE_PATTERNS:
        for p in sorted(base.glob(pattern)):
            if not p.is_file() or _is_excluded(p):
                continue
            rel = p.relative_to(base).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            out.append((rel, p, desc))
    return out


def _sha256(p: Path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def backup(dest=None, root=None):
    """→ manifest dict。任何一步失败抛 OSError（由 CLI 打印后 exit 2）。"""
    base = Path(root) if root else ROOT
    items = collect(base)
    if not items:
        raise OSError("没有可备份的文件（检查路径）")
    ts = time.strftime("%Y%m%d_%H%M%S")
    target = Path(dest) if dest else (Path("C:/Temp") / ("feishu_backup_" + ts))
    target.mkdir(parents=True, exist_ok=True)

    entries = []
    for rel, src, desc in items:
        dst = target / rel.replace("/", os.sep)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        st = src.stat()
        entries.append({
            "path": rel, "desc": desc,
            "size": st.st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                   time.localtime(st.st_mtime)),
            "sha256": _sha256(src),
        })
    manifest = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_root": str(base),
        "file_count": len(entries),
        "total_bytes": sum(e["size"] for e in entries),
        "excluded_credentials": list(EXCLUDE_KEYWORDS),
        "files": entries,
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"target": str(target), "manifest": manifest}


def verify(backup_dir):
    """→ (ok, problems)。逐文件重算 sha256 与 manifest 比对。"""
    d = Path(backup_dir)
    mf = d / "manifest.json"
    if not mf.exists():
        return False, ["manifest_missing: %s" % mf]
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    problems = []
    for e in manifest.get("files", []):
        f = d / e["path"].replace("/", os.sep)
        if not f.exists():
            problems.append("missing: %s" % e["path"])
            continue
        if _sha256(f) != e["sha256"]:
            problems.append("hash_mismatch: %s" % e["path"])
    return (not problems), problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列出将备份的文件")
    ap.add_argument("--dest", default=None, help="目标目录")
    ap.add_argument("--root", default=None, help="源根（默认仓库根）")
    ap.add_argument("--verify", default=None, help="校验既有备份目录")
    args = ap.parse_args()

    if args.verify:
        ok, problems = verify(args.verify)
        print(("VERIFY PASS: %s" % args.verify) if ok
              else ("VERIFY FAIL:\n  " + "\n  ".join(problems)))
        return 0 if ok else 1

    if args.list:
        items = collect(args.root)
        total = sum(p.stat().st_size for _, p, _ in items)
        print("将备份 %d 个文件，合计 %.1f KB：" % (len(items), total / 1024))
        for rel, p, desc in items:
            print("  %-46s %8.1f KB  %s" % (rel, p.stat().st_size / 1024, desc))
        print("\n排除（凭据类）：%s" % ", ".join(EXCLUDE_KEYWORDS))
        return 0

    try:
        r = backup(args.dest, args.root)
    except OSError as ex:
        print("BACKUP FAILED: %s" % ex, file=sys.stderr)
        return 2
    m = r["manifest"]
    print("备份完成：%s" % r["target"])
    print("  文件数 %d / 合计 %.1f KB" % (m["file_count"],
                                       m["total_bytes"] / 1024))
    print("  清单：%s" % (Path(r["target"]) / "manifest.json"))
    print("  校验：python tools/feishu_preflight_backup.py --verify \"%s\""
          % r["target"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
