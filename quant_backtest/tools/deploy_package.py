# -*- coding: utf-8 -*-
"""策略部署产物打包（协作协议 v1 · 策略→盯盘方向 · 2026-10-15）

用途：
    python -X utf8 tools/deploy_package.py <策略名>          # 打包部署
    python -X utf8 tools/deploy_package.py <策略名> --list   # 列已部署包

规格依据：《策略底座与多Agent协作架构方案_2026-10-15》§三 协作协议
（部署包单向版本化：策略包版本+判据 sha，盯盘按包执行）。
判据 sha 沿用 voltarget_ab 判例：policy.json 的 config_sha256 是
**规范化 JSON 体哈希**（sort_keys+固定分隔符，剔除 config_sha256 键），
非原始文件字节；PLACEHOLDER=未冻结，**未冻结拒绝部署**（fail-closed，
规格 §4 晋级条件 2）。

产物：deploy_packages/<name>/v<ver>_<sha8>/
    ├── signal.py / policy.json / feeder.yaml / MANIFEST.json  （快照）
    └── PACKAGE.json（打包元信息：sha/时间/源路径）
幂等：同 sha8 已部署则跳过（--force 重打包）。回滚=删该版本目录
+ 手动复位 MANIFEST.deploy（脚本不自动复位，防误删在跑版本）。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # quant_backtest/
STRAT_DIR = os.path.join(HERE, "strategies")
DEPLOY_DIR = os.path.join(HERE, "deploy_packages")
PKG_FILES = ("signal.py", "policy.json", "feeder.yaml", "MANIFEST.json")
PLACEHOLDER = "PLACEHOLDER"


def policy_sha(policy_fp):
    """规范化体哈希（voltarget_ab 判例）：sort_keys+紧凑分隔符，剔除 config_sha256 键。"""
    with open(policy_fp, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.pop("config_sha256", None)
    body = json.dumps(cfg, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def deploy(name, force=False):
    src = os.path.join(STRAT_DIR, name)
    if not os.path.isdir(src):
        print("[FAIL] 策略目录不存在: %s" % src)
        return 2
    missing = [f for f in PKG_FILES if not os.path.exists(os.path.join(src, f))]
    if missing:
        print("[FAIL] 四件套缺 %s——按策略包规格 v1 归位后再部署" % missing)
        return 2
    policy_fp = os.path.join(src, "policy.json")
    with open(policy_fp, encoding="utf-8") as f:
        pol = json.load(f)
    frozen = pol.get("config_sha256") or ""
    if not frozen or frozen.upper().startswith(PLACEHOLDER):
        print("[FAIL] 判据未冻结（config_sha256=%r）——预注册冻结前拒绝部署"
              "（规格 §4 晋级条件 2，fail-closed）" % frozen[:20])
        return 3
    sha = policy_sha(policy_fp)
    if sha != frozen:
        print("[FAIL] sha 不一致：文件体 %s ≠ 冻结值 %s——policy.json 被改过，"
              "升版重新冻结，禁止带病部署" % (sha[:16], frozen[:16]))
        return 3
    ver = str(pol.get("version") or "v1")
    dest = os.path.join(DEPLOY_DIR, name, "%s_%s" % (ver, sha[:8]))
    if os.path.isdir(dest) and not force:
        print("[SKIP] 已部署: %s（--force 重打包）" % dest)
        return 0
    os.makedirs(dest, exist_ok=True)
    for fn in PKG_FILES:
        shutil.copy2(os.path.join(src, fn), os.path.join(dest, fn))
    with open(os.path.join(dest, "PACKAGE.json"), "w", encoding="utf-8") as f:
        json.dump({"strategy": name, "version": ver, "policy_sha256": sha,
                   "packaged_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "source": src, "files": list(PKG_FILES)},
                  f, ensure_ascii=False, indent=1)
    # 回写源 MANIFEST.deploy（版本指针，不自动复位）
    man_fp = os.path.join(src, "MANIFEST.json")
    with open(man_fp, encoding="utf-8") as f:
        man = json.load(f)
    man["deploy"] = {"deployed": True, "sha": sha, "deployed_at":
                     time.strftime("%Y-%m-%d %H:%M:%S")}
    tmp = man_fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2)
    os.replace(tmp, man_fp)
    print("[OK] 已部署 %s %s → %s（sha %s）" % (name, ver, dest, sha[:16]))
    return 0


def list_packages(name):
    if name is None:
        # 无参 --list：列全部已部署包及版本
        if not os.path.isdir(DEPLOY_DIR) or not os.listdir(DEPLOY_DIR):
            print("（无部署记录）")
            return 0
        rc = 0
        for pkg in sorted(os.listdir(DEPLOY_DIR)):
            print("== %s ==" % pkg)
            rc |= list_packages(pkg)
        return rc
    base = os.path.join(DEPLOY_DIR, name)
    if not os.path.isdir(base):
        print("（无部署记录）")
        return 0
    for v in sorted(os.listdir(base)):
        pj = os.path.join(base, v, "PACKAGE.json")
        if os.path.exists(pj):
            with open(pj, encoding="utf-8") as f:
                m = json.load(f)
            print("%s  sha=%s  at=%s" % (v, m["policy_sha256"][:16],
                                         m["packaged_at"]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.list:
        sys.exit(list_packages(a.name))
    sys.exit(deploy(a.name, a.force))


if __name__ == "__main__":
    main()
