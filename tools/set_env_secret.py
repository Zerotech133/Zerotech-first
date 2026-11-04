#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev-gate.env.enc 单键轮换工具（DPAPI CurrentUser 作用域）。

为什么需要它：
  · 旧轮换脚本 jev-eval/rotate_tushare_token.ps1 的更新目标是
    jev-gate.env.plain.ps1（明文副本），而该文件在 W1-2 迁移后被删除，
    现在跑旧脚本会在仓库里重新落一份明文凭据 —— 安全回归。
  · migrate_env_dpapi.py 是「明文 → .enc」的一次性迁移，不能单键改值。

用法：
    # 从另一个 dotenv 文件取新值（最常用：复用 zhice 的有效 token）
    python tools/set_env_secret.py --key TUSHARE_TOKEN \
        --from-dotenv "C:\\项目\\zhice-os-v2-windows-20260920\\code\\.env.local" --probe

    # 交互输入（不回显）
    python tools/set_env_secret.py --key TUSHARE_TOKEN --stdin --probe

    # 只比对不落盘
    python tools/set_env_secret.py --key TUSHARE_TOKEN --from-dotenv ... --dry-run

    # 回切：从历史 .enc 备份取回旧值（改错了/换错了用它退回去）
    python tools/set_env_secret.py --key TUSHARE_TOKEN \
        --from-enc jev-eval/jev-gate.env.enc.bak-20261105-052431 --probe

安全纪律：
  · 全程不打印任何明文；只输出长度与 sha256 前 8 位
  · 原子写（tmp + os.replace）；默认留 .enc.bak-<时间戳>
  · --probe 只做只读校验（tushare trade_cal），失败即中止，不写盘
"""

import argparse
import base64
import ctypes
import getpass
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ENC_FILE = os.path.join(os.path.dirname(HERE), "jev-eval", "jev-gate.env.enc")

CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]


def dpapi_protect(data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    fin = _Blob(len(data), ctypes.cast(buf, ctypes.c_void_p))
    fout = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(fin), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(fout),
    )
    if not ok:
        raise OSError("CryptProtectData 失败 err=%d" % ctypes.GetLastError())
    try:
        return ctypes.string_at(fout.pbData, fout.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(fout.pbData))


def dpapi_unprotect(blob: bytes) -> bytes:
    buf = ctypes.create_string_buffer(blob, len(blob))
    fin = _Blob(len(blob), ctypes.cast(buf, ctypes.c_void_p))
    fout = _Blob()
    ok = 0
    for flags in (0x1, 0x5):  # CurrentUser / LocalMachine（与 load_env_dpapi 一致）
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(fin), None, None, None, None, flags, ctypes.byref(fout)
        )
        if ok:
            break
    if not ok:
        raise OSError("CryptUnprotectData 失败 err=%d" % ctypes.GetLastError())
    try:
        return ctypes.string_at(fout.pbData, fout.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(fout.pbData))


def sha8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def load_enc(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_from_dotenv(path: str, key: str) -> str:
    pat = re.compile(r"^\s*" + re.escape(key) + r"\s*=\s*(.*)$")
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = pat.match(line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    raise KeyError("在 %s 中未找到键 %s" % (path, key))


def probe_tushare(token: str) -> tuple:
    """只读校验：trade_cal 低消耗接口。返回 (ok, code, msg)。"""
    body = json.dumps({
        "api_name": "trade_cal",
        "token": token,
        "params": {"exchange": "SSE", "start_date": "20260101", "end_date": "20260105"},
        "fields": "cal_date,is_open",
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://api.tushare.pro", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8"))
    return (d.get("code") == 0, d.get("code"), str(d.get("msg") or "")[:80])


def probe_permissions(token: str) -> tuple:
    """anns_d 权限体检（复用 tools/tushare_probe.py 的探测清单）。

    为什么单独做：trade_cal 只证明令牌"活着"，不能证明 review 门能用；
    2026-11-05 就踩过"令牌有效但 anns_d 无权限（40203）"仍照写盘的情况。
    本函数只提示、不阻断 —— 有效令牌总比失效令牌好。
    """
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import tushare_probe as tp
    except Exception as e:  # noqa: BLE001
        return "SKIP", str(e)[:80]
    for name, params, fields, _desc, _req in tp.PROBES:
        if name != "anns_d":
            continue
        try:
            r = tp.call(token, name, params, fields, timeout=20)
            return tp.status_of(r["code"]), "code=%s %s" % (r["code"], r["msg"][:60])
        except Exception as e:  # noqa: BLE001
            return "NET_ERROR", "%s: %s" % (type(e).__name__, str(e)[:80])
    return "SKIP", "探测清单里没有 anns_d"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="jev-gate.env.enc 单键轮换")
    ap.add_argument("--key", required=True, help="要更新的键名，如 TUSHARE_TOKEN")
    ap.add_argument("--from-dotenv", help="从该 dotenv 文件读取同名键")
    ap.add_argument("--from-enc",
                    help="从另一份 .enc（如 .bak-时间戳 备份）读取同名键 —— 回切通道")
    ap.add_argument("--stdin", action="store_true", help="从标准输入读新值（不回显）")
    ap.add_argument("--probe", action="store_true", help="写盘前做只读有效性校验")
    ap.add_argument("--dry-run", action="store_true", help="只比对不写盘")
    ap.add_argument("--no-backup", action="store_true", help="不留 .bak 备份")
    ap.add_argument("--enc", default=ENC_FILE, help=".enc 路径（默认生产路径）")
    a = ap.parse_args(argv)

    if not a.from_dotenv and not a.stdin and not a.from_enc:
        ap.error("必须指定 --from-dotenv / --from-enc / --stdin 之一")

    enc = load_enc(a.enc)
    if a.key not in enc:
        print("ABORT 键 %s 不在 .enc 中（现有键：%s）" % (a.key, ",".join(sorted(enc))))
        return 2

    old = dpapi_unprotect(base64.b64decode(enc[a.key])).decode("utf-8")
    if a.stdin:
        new = getpass.getpass("请输入 %s 的新值（不回显）：" % a.key).strip()
    elif a.from_enc:
        try:
            src = load_enc(a.from_enc)
            if a.key not in src:
                raise KeyError("键 %s 不在 %s 中" % (a.key, a.from_enc))
            new = dpapi_unprotect(base64.b64decode(src[a.key])).decode("utf-8").strip()
            print("source  %s（回切/跨库取值的键：%s）" % (os.path.basename(a.from_enc), a.key))
        except Exception as e:  # noqa: BLE001
            print("ABORT 从 %s 取值失败：%s: %s" % (a.from_enc, type(e).__name__, str(e)[:100]))
            return 2
    else:
        try:
            new = read_from_dotenv(a.from_dotenv, a.key).strip()
        except KeyError as e:
            print("ABORT %s" % e)
            return 2
    if not new:
        print("ABORT 新值为空")
        return 2
    if any(c in new for c in "\r\n"):
        print("ABORT 新值含换行")
        return 2

    print("before  %s len=%d sha8=%s" % (a.key, len(old), sha8(old)))
    print("after   %s len=%d sha8=%s" % (a.key, len(new), sha8(new)))
    print("changed %s" % ("YES" if old != new else "NO (值相同，无需写入)"))

    if new == old:
        return 0

    if a.probe and a.key == "TUSHARE_TOKEN":
        try:
            ok, code, msg = probe_tushare(new)
        except Exception as e:
            print("PROBE EXC %s %s -> 中止" % (type(e).__name__, str(e)[:80]))
            return 3
        print("probe   code=%s msg=%s" % (code, msg))
        if not ok:
            print("PROBE FAIL -> 中止，未写盘")
            return 3
        st, detail = probe_permissions(new)
        print("anns_d  %s %s" % (st, detail))
        if st != "OK":
            print("NOTE    令牌有效但公告接口不可用 → review 门仍会降级 none（不阻断写盘）")
    elif a.probe:
        print("probe   SKIP（仅 TUSHARE_TOKEN 支持）")

    if a.dry_run:
        print("DRY-RUN 未写盘")
        return 0

    # 全量重加密（其它键原值不动），保证同一作用域与同一历史语义
    out = {}
    for k, b64 in enc.items():
        val = new if k == a.key else dpapi_unprotect(base64.b64decode(b64)).decode("utf-8")
        out[k] = base64.b64encode(dpapi_protect(val.encode("utf-8"))).decode("ascii")

    if not a.no_backup:
        bak = a.enc + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(a.enc, bak)
        print("backup  %s" % os.path.basename(bak))

    tmp = a.enc + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, a.enc)

    # 回读校验
    back = dpapi_unprotect(base64.b64decode(load_enc(a.enc)[a.key])).decode("utf-8")
    if back != new:
        print("VERIFY FAIL 回读不一致")
        return 4
    print("OK 已写入并回读校验通过（%d 键，%s sha8=%s）" % (len(out), a.key, sha8(back)))
    print("NEXT 重启消费方：Start-ScheduledTask -TaskName JevRestart")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
