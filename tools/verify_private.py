#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""转私有之后的验收：5 项全过才算完成，任一项 FAIL 都说明没关严。

为什么需要一个脚本而不是看网页：
    改可见性的动作在网页上，一眼看去「已经变灰了」就等于成功——但那只是
    UI 反馈。真正的判据是「匿名请求拿不到东西」，而这件事在浏览器里测不
    出来（浏览器带着登录态）。必须在无凭据的情况下打，才知道外面的人看到
    什么。

为什么第 1 项是先测对照仓：
    GitHub 对私有仓返 404 而不是 403，所以「404」同时意味着「已私有」和
    「仓不存在」，也意味着「API 根本不通」。2026-11-11 有过判例：主站 443
    被阻断时 api 子域仍然 200，单端探测什么都证明不了。必须先用一个已知
    存在的公开仓确认探测通道本身是活的，后面的 404 才有意义。

用法：
    python -X utf8 tools/verify_private.py
    python -X utf8 tools/verify_private.py --owner X --repo Y
退出码：0 全过 / 1 有 FAIL / 2 关键前置失败（对照仓不可达，结论不成立）
"""
import argparse
import os
import subprocess
import sys
import urllib.error
import urllib.request

UA = {"User-Agent": "zb-verify-private"}
CONTROL_OWNER, CONTROL_REPO = "git", "git"
DEFAULT_OWNER, DEFAULT_REPO = "Zerotech133", "Zerotech-first"
PROBE_FILE = "AGENTS.md"

results = []


def http(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception as exc:  # 超时、DNS、TLS 都走这里
        return 0, str(exc).encode("utf-8", "replace")


def record(name, ok, detail):
    results.append((name, ok, detail))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", default=DEFAULT_OWNER)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    args = ap.parse_args()
    owner, repo = args.owner, args.repo

    # 1. 探测通道自证
    st, _ = http("https://api.github.com/repos/%s/%s" % (CONTROL_OWNER, CONTROL_REPO))
    record("对照仓 %s/%s 可达" % (CONTROL_OWNER, CONTROL_REPO), st == 200, "HTTP %s" % st)
    if st != 200:
        print("[verify] 探测通道不通，后续 404 不能作为私有证据 —— 先解决网络/API。")
        for name, ok, detail in results:
            print("  %s  %-34s  %s" % ("PASS" if ok else "FAIL", name, detail))
        return 2

    # 2. 匿名 API
    st, _ = http("https://api.github.com/repos/%s/%s" % (owner, repo))
    record("匿名 API 取不到该仓", st == 404, "HTTP %s（私有/不存在均为 404）" % st)

    # 3. 匿名 raw（public 时这条是最容易被忽略的入口：不走 API 也能读文件）
    st, _ = http("https://raw.githubusercontent.com/%s/%s/main/%s" % (owner, repo, PROBE_FILE))
    record("匿名 raw 取不到文件", st in (400, 403, 404), "HTTP %s  %s" % (st, PROBE_FILE))

    # 4. SSH 仍是本人（转私有不该影响推送身份）
    try:
        p = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "-o", "ConnectTimeout=15", "-T", "git@github.com"],
            capture_output=True, timeout=45)
        out = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
    except Exception as exc:
        out = "ssh 调用失败: %s" % exc
    ok = "successfully authenticated" in out and owner.lower() in out.lower()
    record("SSH 认证仍是 %s" % owner, ok, out[:70])

    # 5. 门禁自己也认了（否则推送会被自家门禁按旧判定拦住）
    gate = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "check_public_surface.py")
    if os.path.exists(gate):
        p = subprocess.run(
            [sys.executable, "-X", "utf8", gate, "--status",
             "--remote-url", "git@github.com:%s/%s.git" % (owner, repo)],
            capture_output=True, timeout=90)
        line = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
        record("门禁判定为 private", "-> private" in line, line.splitlines()[-1][:70] if line else "无输出")
    else:
        record("门禁判定为 private", False, "找不到 check_public_surface.py")

    width = max(len(n) for n, _, _ in results)
    failed = 0
    for name, ok, detail in results:
        print("  %s  %-*s  %s" % ("PASS" if ok else "FAIL", width, name, detail))
        if not ok:
            failed += 1
    print("")
    if failed:
        print("[verify] %d/%d 未通过 —— 门还没关严，或关闭动作还没生效。" % (failed, len(results)))
        return 1
    print("[verify] %d/%d 全部通过：匿名侧已取不到内容，SSH 推送不受影响。" % (len(results), len(results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
