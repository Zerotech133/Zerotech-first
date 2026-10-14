# -*- coding: utf-8 -*-
"""plain 更新后重加密 .enc（DPAPI 迁移后置轮换路径，K-STOCK-368 收编）。

背景：migrate_env_dpapi.do_migrate 入口先解析 SRC=jev-gate.env.ps1——迁移
完成后该文件已是 stub（无 $env: 赋值），直接重跑必报「未解析到任何 $env:
赋值」。本工具复用其底层函数，只做 do_migrate 的第 3 步
（plain → DPAPI → .enc 原子写），随后 --verify 同款解密回读比对。

用法：改完 jev-gate.env.plain.ps1 后执行
    python jev-eval/reencrypt_env.py
"""
import base64
import sys

from migrate_env_dpapi import (PLAIN_FILE, ENC_FILE, parse_plain,
                               dpapi_protect, atomic_write_json, do_verify)

secrets, _ = parse_plain(PLAIN_FILE)
enc = {k: base64.b64encode(dpapi_protect(v.encode("utf-8"))).decode("ascii")
       for k, v in secrets.items()}
atomic_write_json(ENC_FILE, enc)
print("re-encrypt done: %d keys -> %s" % (len(enc), ENC_FILE))
sys.exit(do_verify())
