#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev-gate.env.ps1 明文凭据 → Windows DPAPI 加密迁移工具（W1-2 任务A，P0 安全）。

背景（2026-10-14 integration-dev）：jev-gate.env.ps1 含 JEV/Tushare/QD/zhice
四类明文凭据，暴露面过大（研究确认）。本脚本一次性把全部活跃
$env: 键值迁入 DPAPI（CurrentUser 作用域）加密库 jev-gate.env.enc：

  · jev-gate.env.enc     JSON，{键: base64(DPAPI密文)}，原子写
  · jev-gate.env.plain.ps1   原明文文件完整副本（回退目标，轮换完成前保留）
  · jev-gate.effects.ps1     原文件「非 $env: 赋值」副作用段（建日志目录、
                             lark-cli PATH 注入），供 stub 加载 .enc 后执行；
                             机械提取，不含任何明文值（写入前自检）

判例（旋转前车）：rotate_tushare_token.ps1 原子更新明文文件的
TUSHARE_TOKEN 行——迁移后该脚本目标失效，轮换流程见 --rotate-checklist。

安全纪律：
  · 全程不打印任何明文值（错误信息只带键名）
  · CurrentUser 作用域：仅当前 Windows 账户可解密；计划任务若以其他
    账户运行会解密失败 → stub 回退明文并 WARN（可用性保住，安全降级）

用法:
  python migrate_env_dpapi.py                # 执行迁移（幂等：重跑覆盖 .enc）
  python migrate_env_dpapi.py --verify       # 解密回读 vs 明文比对 → MIGRATION_OK
  python migrate_env_dpapi.py --rotate-checklist  # 打印人工轮换清单（只列键名）
"""
import argparse
import base64
import ctypes
import json
import os
import re
import shutil
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_PS1 = os.path.join(HERE, "jev-gate.env.ps1")
ENC_FILE = os.path.join(HERE, "jev-gate.env.enc")
PLAIN_FILE = os.path.join(HERE, "jev-gate.env.plain.ps1")
EFFECTS_FILE = os.path.join(HERE, "jev-gate.effects.ps1")

# DPAPI 描述符（解密方校验用，非机密）
DPAPI_DESC = "jev-gate.env DPAPI(CurrentUser) 20261014"
CRYPTPROTECT_UI_FORBIDDEN = 0x1


# ── DPAPI（ctypes 直调 crypt32，无第三方依赖）──────────────────
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.c_void_p)]


def dpapi_protect(data: bytes) -> bytes:
    """CryptProtectData（CurrentUser 作用域）→ 密文字节。"""
    buf = ctypes.create_string_buffer(data, len(data))
    fin = _Blob(len(data), ctypes.cast(buf, ctypes.c_void_p))
    fout = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(fin), DPAPI_DESC, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(fout))
    if not ok:
        raise OSError("CryptProtectData 失败 err=%d" % ctypes.GetLastError())
    try:
        return ctypes.string_at(fout.pbData, fout.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(fout.pbData))


def dpapi_unprotect(blob: bytes) -> bytes:
    """CryptUnprotectData → 明文字节（仅本账户可解，异地/异账户失败）。"""
    buf = ctypes.create_string_buffer(blob, len(blob))
    fin = _Blob(len(blob), ctypes.cast(buf, ctypes.c_void_p))
    fout = _Blob()
    desc = ctypes.c_wchar_p()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(fin), ctypes.byref(desc), None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(fout))
    if not ok:
        raise OSError("CryptUnprotectData 失败 err=%d（异地/异账户？）"
                      % ctypes.GetLastError())
    try:
        return ctypes.string_at(fout.pbData, fout.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(fout.pbData))
        ctypes.windll.kernel32.LocalFree(desc)


# ── ps1 解析（只输出键名，值不落任何打印）─────────────────────
ASSIGN_RE = re.compile(r"^\s*\$env:([A-Za-z0-9_]+)\s*=")
# 判例：值必带引号（PS 裸词会被当命令）；行尾 # 注释合法（rotate 脚本同款）
VALUE_RE = re.compile(r"""^(["'])(.*)\1(?:\s*#.*)?$""", re.S)


def parse_plain(path=PLAIN_FILE):
    """解析明文 ps1 → (out, lines) 元组（out={键: 值}，lines=原始行）；
    无法解析的键报错（只报键名）。"""
    with open(path, encoding="utf-8-sig") as f:   # 判例：rotate 脚本保留 BOM
        lines = f.read().splitlines()
    out, bad = {}, []
    for ln in lines:
        m = ASSIGN_RE.match(ln)
        if not m:
            continue
        key = m.group(1)
        # rhs 从 '=' 后取（ASSIGN_RE 吃到键名，剩余串再剥引号）
        rhs = ln.split("=", 1)[1].strip()
        vm = VALUE_RE.match(rhs)
        if not vm:
            bad.append(key)
            continue
        v = vm.group(2)
        if vm.group(1) == '"':
            # PS 双引号转义：`" → " ，`` → ` ，`$ → $（凭据常见面窄，够用）
            v = v.replace("``", "\0").replace('`"', '"') \
                 .replace("`$", "$").replace("\0", "`")
        else:
            v = v.replace("''", "'")
        out[key] = v
    if bad:
        raise SystemExit("解析失败键（不打印值）: %s —— 人工检查行格式"
                         % ", ".join(bad))
    if not out:
        raise SystemExit("未解析到任何 $env: 赋值（源文件为空/结构变化？）")
    return out, lines


def extract_effects(lines):
    """提取非 $env: 赋值行（副作用段+注释），供 stub 加载 .enc 后执行。"""
    return [ln for ln in lines if not ASSIGN_RE.match(ln)]


def atomic_write_json(path, obj):
    """原子写（判例：仓库统一 tmp + os.replace，防半截文件）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def atomic_write_text(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def do_migrate():
    if not os.path.exists(SRC_PS1):
        raise SystemExit("源文件不存在: %s" % SRC_PS1)
    secrets, lines = parse_plain(SRC_PS1)

    # 1) plain 副本（回退目标）：首次迁移创建，已存在则不动（幂等，
    #    防重跑迁移把「已轮换的 plain」覆盖回旧值）
    if not os.path.exists(PLAIN_FILE):
        shutil.copy2(SRC_PS1, PLAIN_FILE)
        print("已生成回退副本 jev-gate.env.plain.ps1（%d 键）" % len(secrets))
    else:
        print("plain 副本已存在，跳过覆盖（幂等保护）")
        secrets, lines = parse_plain(PLAIN_FILE)   # 以 plain 为准（真源）

    # 2) 副作用段提取 + 明文值泄漏自检（防续行/here-string 夹带值进新文件）
    effects = extract_effects(lines)
    eff_text = "\n".join(effects) + "\n"
    leaked = [k for k, v in secrets.items() if v and v in eff_text]
    if leaked:
        raise SystemExit("副作用段自检失败：疑似含明文值的键: %s —— 不落盘"
                         % ", ".join(leaked))
    atomic_write_text(EFFECTS_FILE, eff_text)

    # 3) DPAPI 加密 → .enc（原子写）
    enc = {}
    for k, v in secrets.items():
        enc[k] = base64.b64encode(
            dpapi_protect(v.encode("utf-8"))).decode("ascii")
    atomic_write_json(ENC_FILE, enc)
    print("迁移完成：%d 键 → %s（DPAPI CurrentUser）"
          % (len(enc), os.path.basename(ENC_FILE)))
    print("下一步：跑 --verify 确认 MIGRATION_OK，再切换 stub 加载器")


def do_verify():
    if not (os.path.exists(PLAIN_FILE) and os.path.exists(ENC_FILE)):
        raise SystemExit("缺 plain 或 .enc，先跑迁移")
    secrets, _ = parse_plain(PLAIN_FILE)
    with open(ENC_FILE, encoding="utf-8") as f:
        enc = json.load(f)
    miss = sorted(set(secrets) - set(enc))
    extra = sorted(set(enc) - set(secrets))
    bad = []
    for k, b64 in enc.items():
        try:
            if dpapi_unprotect(base64.b64decode(b64)).decode("utf-8") \
                    != secrets.get(k):
                bad.append(k)
        except OSError as e:
            bad.append("%s(%s)" % (k, e))
    if miss or extra or bad:
        print("VERIFY_FAIL 缺失=%s 多余=%s 不一致/解密失败=%s"
              % (miss or "无", extra or "无", bad or "无"))
        return 1
    # 副作用段复检：不得含任何明文值
    with open(EFFECTS_FILE, encoding="utf-8") as f:
        eff = f.read()
    leaked = [k for k, v in secrets.items() if v and v in eff]
    if leaked:
        print("VERIFY_FAIL effects 泄漏键: %s" % leaked)
        return 1
    print("MIGRATION_OK（%d 键解密回读与明文逐键一致；effects 无泄漏）"
          % len(secrets))
    return 0


CHECKLIST = [
    ("typesafe（Jev 网关）", ["JEV_API_KEY"],
     "console.typesafe.ai/keys 生成新 key → 更新 plain 对应行 → 重跑本脚本 → --verify"),
    ("Tushare（公告源）", ["TUSHARE_TOKEN"],
     "platform.tushare.pro 重置 token → 更新 plain → 重跑本脚本 → --verify；"
     "注意 rotate_tushare_token.ps1 仍指 stub 路径，轮换期内勿用其自动更新"),
    ("QuantDinger（QD 桥）", ["QD_USER", "QD_PASSWORD"],
     "QD 后台改密 → 更新 plain → 重跑本脚本 → --verify"),
    ("zhice OS v2 后台", ["ZHICE_ADMIN_USER", "ZHICE_ADMIN_PASSWORD"],
     "zhice 后台改密 → 更新 plain → 重跑本脚本 → --verify"),
    ("飞书（open_id 类，非密钥）", ["LARK_ALERT_OPEN_ID", "LARK_ALLOWED_OPERATORS"],
     "无需轮换；确认暴露后的收件人白名单是否符合预期"),
    ("（清理项）弃用键", ["ALERT_WEBHOOK_URL"],
     "已弃用（注释确认）；确认无消费方后从 plain 删除该行并重跑本脚本"),
    ("（收尾）删除明文", ["jev-gate.env.plain.ps1"],
     "以上全部轮换完成且稳定运行 ≥1 周后，删除 plain 与 .bak_20261014，"
     "stub 去掉回退分支——是否删除由老田拍板"),
]


def do_checklist():
    print("== 轮换清单（只列平台与变量名，不含任何值）==")
    for i, (plat, keys, act) in enumerate(CHECKLIST, 1):
        print("%d. [%s] %s" % (i, plat, ", ".join(keys)))
        print("   动作: %s" % act)


def main():
    ap = argparse.ArgumentParser(description="jev-gate 明文凭据 DPAPI 迁移")
    ap.add_argument("--verify", action="store_true", help="解密回读比对")
    ap.add_argument("--rotate-checklist", action="store_true",
                    help="打印人工轮换清单")
    a = ap.parse_args()
    if a.rotate_checklist:
        do_checklist()
        return 0
    if a.verify:
        return do_verify()
    do_migrate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
