#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev-gate.env.enc（DPAPI）加载器（W1-2 任务A）：py/ps1 双侧消费入口。

py 侧：
    from load_env_dpapi import get_secret
    key = get_secret("JEV_API_KEY")     # 解密失败/缺键返回 ""，不抛异常

ps1 侧（stub 加载模式，供 Invoke-Expression；本仓库 jev-gate.env.ps1 即此用法）：
    $out = & 'C:/Program Files/Python311/python.exe' `
              (Join-Path $PSScriptRoot 'load_env_dpapi.py') --ps1
    if ($LASTEXITCODE -eq 0 -and $out) { ($out -join "`n") | Invoke-Expression }

安全纪律：
  · --ps1 输出的是明文赋值行——仅供 stub 管道捕获注入 $env:，
    不要把该输出落盘或打进日志
  · 解密失败只报键名与异常类型，不打印值
  · 进程内缓存：同一进程重复 get_secret 只解密一次
"""
import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ENC_FILE = os.path.join(HERE, "jev-gate.env.enc")

_CACHE = None          # {键: 值}，首次访问解密后缓存


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.c_void_p)]


def _unprotect(blob: bytes) -> bytes:
    buf = ctypes.create_string_buffer(blob, len(blob))
    fin = _Blob(len(blob), ctypes.cast(buf, ctypes.c_void_p))
    fout = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(fin), None, None, None, None, 0x1, ctypes.byref(fout))
    if not ok:
        raise OSError("CryptUnprotectData 失败 err=%d（CurrentUser 作用域，"
                      "异账户/异机不可解）" % ctypes.GetLastError())
    try:
        return ctypes.string_at(fout.pbData, fout.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(fout.pbData))


def load_all():
    """解密 .enc 全量 → {键: 值}；进程内缓存。失败抛异常（键名不出现在值位）。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not os.path.exists(ENC_FILE):
        raise FileNotFoundError("未找到 %s（迁移未执行？回退明文）" % ENC_FILE)
    with open(ENC_FILE, encoding="utf-8") as f:
        enc = json.load(f)
    _CACHE = {k: _unprotect(base64.b64decode(b64)).decode("utf-8")
              for k, b64 in enc.items()}
    return _CACHE


def get_secret(key: str) -> str:
    """供 py 消费方：取单键明文；.enc 不存在/键缺失/解密失败一律返回 ""。

    判例（C2批2 豁免哲学）：与「未配置」同一出口，消费方按无凭据跳过，
    链路不断只退化——不在库内做告警，避免循环依赖 jev_common。
    """
    try:
        return load_all().get(key, "")
    except Exception as e:
        sys.stderr.write("load_env_dpapi: 取 %s 失败: %s\n"
                         % (key, type(e).__name__))
        return ""


def main():
    sys.stdout.reconfigure(encoding="utf-8")   # 对齐 stub 的 UTF8 管道约定
    if "--ps1" in sys.argv:
        vals = load_all()
        for k, v in vals.items():
            # PS 单引号字面量：' → '' 转义（值不含其他需转义序列）
            print("$env:%s = '%s'" % (k, v.replace("'", "''")))
        return 0
    if "--list" in sys.argv:
        # 只列键名（巡检/验证用，不出值）
        for k in sorted(load_all()):
            print(k)
        return 0
    sys.stderr.write("用法: load_env_dpapi.py --ps1 | --list\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
