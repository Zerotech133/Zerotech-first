#!/usr/bin/env python3
"""Xueqiu token 自动续期：浏览器登录态 → .env.local 缓存层（L2→L1 桥）。

三级链判例（xueqiu_common.load_token）：
  L1 .env.local XQ_A_TOKEN（手填/本脚本刷新）
  L2 浏览器 rookiepy 提取——交互会话可用
  L3 无凭证降级 skip（降级≠失败）

调度链判例（2026-10-17）：XueqiuSync 等计划任务为 S4U 会话，
DPAPI 不可用 → L2 在调度会话必败；而交互会话 L2 实测可用
（token 40 字符）。本脚本在交互会话中把 L2 提取的 token 落盘
为 L1 缓存，S4U 任务即可读取。token 有效期 20-30 天，本任务
随用户登录/定时刷新自动续命，无需手取。

安全约定：绝不打印 token 值（只打印长度）；浏览器未登录时
静默保留旧值退出 0；原子写（tmp + os.replace）防半行损坏。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from xueqiu_common import load_token_from_browser  # noqa: E402

ENV_LOCAL = os.path.join(HERE, ".env.local")
KEY = "XQ_A_TOKEN"


def main() -> int:
    tok = load_token_from_browser()
    if not tok:
        # 静默保留旧值：浏览器未登录/rookiepy 环境异常都不算失败
        print("[refresh] browser token unavailable, keep old value")
        return 0

    lines = []
    if os.path.exists(ENV_LOCAL):
        # utf-8-sig 兼容带/不带 BOM（与 load_token 读侧同款容错）
        with open(ENV_LOCAL, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()

    replaced = False
    out = []
    for ln in lines:
        # 容忍 "XQ_A_TOKEN = 值"（等号两侧空格的手填形态），与读侧同款
        if ln.lstrip("\ufeff").replace(" ", "").startswith(KEY + "="):
            out.append(KEY + "=" + tok)
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(KEY + "=" + tok)

    tmp = ENV_LOCAL + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out).rstrip("\n") + "\n")
    os.replace(tmp, ENV_LOCAL)  # 原子替换
    print("[refresh] XQ_A_TOKEN cached to .env.local, length:", len(tok))
    return 0


if __name__ == "__main__":
    sys.exit(main())
