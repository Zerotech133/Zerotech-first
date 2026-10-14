# -*- coding: utf-8 -*-
"""
publish_picks.py — X5 选股公网发布一键链（sync + 多文件线上比对 + 状态留痕）

背景（2026-10-13 事故复盘）：
    16:44 发布后 picks_public 快照 22:53 又刷新过，但 deploy 没有重新执行，
    线上滞留旧版。原 sync_publish.py 只做本地快照刷新（deploy 是"另行触发"
    的第二步），两步之间没有状态衔接，容易漏。

v1.1（2026-10-14，团队审计后修复）：
    - 线上比对从单文件（picks.html）扩到 picks.html + data/todays_picks.json
      （审计 P1-2：数据文件漂移时旧版会误报"线上已是最新"，注入实测坐实）
    - 滞后分支回写 online_hash/online_hashes 到状态文件（审计 P1-1：
      此前告警卡与旗标里线上 hash 恒为 "?"）
    - publish_state.json 改原子写（tmp+os.replace，对齐 sync_publish 纪律，P2-2）
    - 子进程强制 PYTHONIOENCODING/PYTHONUTF8=utf-8（P2-1：管道编码不再依赖环境）
    - 访问码外移到 config/picks.local.json（安全审计 F-001，复用既有 gitignore 规则）
    - 无 --check-online 时保留上次 deploy 确认字段（审计建议级 #2）
    - hash 键统一正斜杠（审计建议级 #3）

链路：
    1. 调 sync_publish.py 刷新 picks_public 快照（子进程，透传输出与退出码）
    2. 校验关键文件存在，计算 sha256（键为正斜杠相对路径）
    3. 原子写 work/publish_state.json（时间、hash、sync 统计、deploy_confirmed）
    4. --check-online 时直连线上逐文件比对，任一不一致即判定"线上滞后"

边界说明：
    WorkBuddy 站点发布（CloudStudio Gateway deploy）是 agent 侧动作，无法从
    本机脚本直接触发。结论为"线上滞后"时，需要让 agent 重新 deploy
    （对 agent 说"重新发布 picks"即可，目录/appId/入口都不变，链接不变）。

用法：
    python publish_picks.py                   # 同步 + 状态记录
    python publish_picks.py --check-online    # 追加线上比对，报告是否需要重新发布
退出码：0 一切正常（含"线上已是最新"）；2 线上滞后需要重新 deploy；非 0 其余异常。
注意：argparse 参数错误也退出 2，调用方（watchdog）以 stdout 含"[滞后]"佐证区分。
零第三方依赖，仅标准库。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

PORTAL = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(PORTAL, "picks_public")
WORK = os.path.join(PORTAL, "work")
STATE_PATH = os.path.join(WORK, "publish_state.json")

SYNC_SCRIPT = os.path.join(PORTAL, "sync_publish.py")

ONLINE_BASE = "https://quant-picks-board.app.workbuddy.host/"
KEY_CONFIG = os.path.join(os.path.dirname(PORTAL), "config", "picks.local.json")

# 关键文件：存在性必须校验；hash 记入状态（键一律正斜杠，跨工具可读）
KEY_FILES = ["picks.html", "static/tokens.css", "data/todays_picks.json"]
# 线上比对文件（数据文件参与比对是 v1.1 关键修复，勿回退）
ONLINE_FILES = ["picks.html", "data/todays_picks.json"]

# 子进程强制 UTF-8 管道，不依赖宿主环境配置（审计 P2-1）
CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path, obj):
    """原子写 JSON：同目录 mkstemp -> 写入 -> os.replace（审计 P2-2）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                               prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def load_access_key():
    """访问码从 config/picks.local.json 读（字段 portal_access_key）。
    缺失返回 None，由调用方报错（安全审计 F-001：不再硬编码进源码）。"""
    try:
        with open(KEY_CONFIG, encoding="utf-8") as f:
            key = (json.load(f) or {}).get("portal_access_key")
        return key or None
    except (OSError, ValueError):
        return None


def run_sync():
    """跑 sync_publish.py，透传输出；返回 (退出码, 统计行列表)。"""
    proc = subprocess.run(
        [sys.executable, SYNC_SCRIPT],
        cwd=PORTAL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=CHILD_ENV,
    )
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    for ln in lines:
        print(ln)
    if proc.stderr:
        for ln in proc.stderr.splitlines():
            if ln.strip():
                print("[stderr] " + ln, file=sys.stderr)
    return proc.returncode, lines


def parse_sync_stats(lines):
    """从 sync_publish 输出里抠统计行，形如 '新增: 0  更新: 3  跳过(hash相同): 950 ...'。"""
    for ln in lines:
        if ln.startswith("新增:"):
            return ln.strip()
    return ""


def load_prev_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def check_online(rel, key):
    """直连（绕系统代理）拉线上 <rel>，返回 (在线hash, 错误信息)。"""
    if not key:
        return None, "访问码配置缺失: %s (字段 portal_access_key)" % KEY_CONFIG
    url = "%s%s?key=%s" % (ONLINE_BASE, rel, key)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        data = opener.open(req, timeout=30).read()
    except Exception as e:  # noqa: BLE001 — 网络错误要归类成结论而不是崩
        return None, "线上拉取失败 %s: %s" % (rel, e)
    return hashlib.sha256(data).hexdigest(), None


def main():
    parser = argparse.ArgumentParser(description="picks 公网发布一键链（sync + 比对 + 留痕）")
    parser.add_argument("--check-online", action="store_true",
                        help="同步后逐文件拉线上比对，报告是否需要重新 deploy")
    args = parser.parse_args()

    t0 = time.time()
    prev = load_prev_state()

    # --- 1. 快照刷新 ---
    print("== 第 1 步：刷新 picks_public 快照 ==")
    sync_rc, sync_lines = run_sync()
    if sync_rc != 0:
        print("[失败] sync_publish.py 退出码 %d，中止后续步骤" % sync_rc, file=sys.stderr)
        return sync_rc

    # --- 2. 校验关键文件 + 算 hash ---
    print("")
    print("== 第 2 步：关键文件校验与 hash ==")
    hashes = {}
    missing = []
    for rel in KEY_FILES:
        path = os.path.join(PUBLIC, *rel.split("/"))
        if not os.path.exists(path):
            missing.append(rel)
            continue
        hashes[rel] = sha256_of(path)
        print("[ok] %-28s %s" % (rel, hashes[rel][:16] + "..."))
    if missing:
        print("[失败] 关键文件缺失: %s" % ", ".join(missing), file=sys.stderr)
        return 1

    # --- 3. 状态留痕（原子写） ---
    print("")
    print("== 第 3 步：状态留痕 ==")
    state = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "epoch": int(t0),
        "sync_stats": parse_sync_stats(sync_lines),
        "hashes": hashes,
        "deploy_confirmed": False,
    }
    if not state["sync_stats"]:
        print("[警告] 未能从 sync 输出解析统计行（管道编码异常？）", file=sys.stderr)
    # 未做线上比对时，若 hash 与上次一致则保留上次的 deploy 确认（审计建议级 #2）
    if not args.check_online and prev and prev.get("hashes") == hashes:
        state["deploy_confirmed"] = bool(prev.get("deploy_confirmed"))
        if prev.get("online_hash"):
            state["online_hash"] = prev["online_hash"]
        if prev.get("online_hashes"):
            state["online_hashes"] = prev["online_hashes"]  # 复验 N2：一并保留多文件结论
        print("[ok] hash 与上次一致，保留上次线上比对结论（未复核）")
    atomic_write_json(STATE_PATH, state)
    print("[ok] %s" % os.path.relpath(STATE_PATH, PORTAL))

    changed = prev is None or prev.get("hashes", {}).get("picks.html") != hashes["picks.html"]
    if changed:
        print("[注意] picks.html 与上次记录不同（首次运行或快照有更新）")

    # --- 4. 线上比对（多文件） ---
    if not args.check_online:
        print("")
        print("==== 完成（未做线上比对；需要时加 --check-online）====")
        print("总耗时: %.2fs" % (time.time() - t0))
        return 0

    print("")
    print("== 第 4 步：线上比对（%d 个文件） ==" % len(ONLINE_FILES))
    key = load_access_key()
    online_hashes = {}
    mismatched = []
    for rel in ONLINE_FILES:
        if rel not in hashes:
            continue  # 本地缺失的文件已在第 2 步中止
        online_hash, err = check_online(rel, key)
        if err:
            print("[警告] %s" % err, file=sys.stderr)
            return 1
        online_hashes[rel] = online_hash
        if online_hash == hashes[rel]:
            print("[ok] %s 与线上一致" % rel)
        else:
            mismatched.append(rel)
            print("[滞后] %s 本地 %s... / 线上 %s..." % (rel, hashes[rel][:16], online_hash[:16]))

    # 比对结果一律回写状态（滞后分支也写——修复审计 P1-1）
    state["online_hashes"] = online_hashes
    state["online_hash"] = online_hashes.get("picks.html", "")
    atomic_write_json(STATE_PATH, state)

    if not mismatched:
        print("[ok] 线上 %d 个比对文件全部与本地快照一致，无需重新 deploy" % len(online_hashes))
        state["deploy_confirmed"] = True
        atomic_write_json(STATE_PATH, state)
        print("总耗时: %.2fs" % (time.time() - t0))
        return 0

    print("[滞后] 线上滞后文件: %s，需要重新 deploy" % ", ".join(mismatched))
    print("       处置：让 agent 重新发布 picks（目录/入口/appId 不变，链接不变）")
    print("总耗时: %.2fs" % (time.time() - t0))
    return 2


if __name__ == "__main__":
    sys.exit(main())
