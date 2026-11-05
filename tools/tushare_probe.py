#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tushare 令牌体检：一次问清「认证通不通 / 权限够不够」。

为什么需要它（2026-11-05）：
  · JEV 的 review 门依赖 Tushare `anns_d` 拿公告标题，缺权限时会**静默降级**
    到「公告源=none」，日志里只是一行 info，长期空转没人察觉。
  · 40101（token 不对）与 40203（有 token 但无该接口权限）是两种完全不同的
    故障：前者换令牌能解，后者只能提积分/换数据源。用 tushare SDK 时两者会
    被混报成「您的token不对」（见 signal_review_gate.py:204 的 R5 修复），
    所以这里一律裸 HTTP，拿原始错误码。

用法：
    # 体检生产 .enc 里的当前令牌（默认）
    python tools/tushare_probe.py

    # 体检某份备份里的令牌（例如回切用）
    python tools/tushare_probe.py --from-enc jev-eval/jev-gate.env.enc.bak-20261105-052431

    # 体检别的 dotenv（不落盘、只读）
    python tools/tushare_probe.py --from-dotenv "C:\\项目\\zhice-os-v2-windows-20260920\\code\\.env.local"

    # 给计划任务/CI 用：anns_d 必须可用，否则 rc=4
    python tools/tushare_probe.py --require anns_d --json

退出码：0 全部可用 / 3 令牌无效 / 4 必需接口无权限 / 5 网络异常 / 6 未取到令牌
安全：全程不打印令牌，只输出 len 与 sha256 前 8 位。
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENC_FILE = os.path.join(ROOT, "jev-eval", "jev-gate.env.enc")
API = "https://api.tushare.pro"

# 探测清单：name -> (params, fields, 说明, 是否必需)
# 全部挑低消耗接口；anns_d 是 review 门唯一依赖，标 required。
PROBES = [
    ("trade_cal", {"exchange": "SSE", "start_date": "20260101", "end_date": "20260105"},
     "cal_date,is_open", "认证基线（任何有效令牌都能过）", True),
    ("anns_d", {"ts_code": "000001.SZ", "start_date": "20261001", "end_date": "20261105",
                "src": "沪深京"},
     "ts_code,ann_date,title", "公告标题（review 门数据源，需 2000+ 积分）", True),
    ("stock_basic", {"exchange": "SSE", "list_status": "L"},
     "ts_code,name", "A 股列表（低成本，验证通用额度）", False),
    ("daily", {"ts_code": "000001.SZ", "start_date": "20261020", "end_date": "20261105"},
     "ts_code,trade_date,close", "日线（回填/后验可能用）", False),
]

# 错误码语义（Tushare 官方口径）
CODE_MEANING = {
    0: "OK",
    2002: "参数错误",
    40001: "接口不存在或字段错误",
    40002: "请求过于频繁",
    40101: "令牌无效（token 不对/已失效）",
    40102: "令牌过期",
    40203: "无该接口访问权限（积分不足）",
    40204: "积分不足或权限已到期",
}


def _no_proxy_opener():
    """显式不走系统代理：本机 HTTP_PROXY 指向沙箱代理，会拦外部 API。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sha8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def dpapi_unprotect(blob: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    buf = ctypes.create_string_buffer(blob, len(blob))
    fin = _Blob(len(blob), ctypes.cast(buf, ctypes.c_void_p))
    fout = _Blob()
    ok = 0
    for flags in (0x1, 0x5):  # CurrentUser / LocalMachine
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


def token_from_enc(path: str, key: str = "TUSHARE_TOKEN") -> str:
    with open(path, encoding="utf-8") as f:
        enc = json.load(f)
    if key not in enc:
        raise KeyError("键 %s 不在 %s 中" % (key, path))
    return dpapi_unprotect(base64.b64decode(enc[key])).decode("utf-8").strip()


def token_from_dotenv(path: str, key: str = "TUSHARE_TOKEN") -> str:
    pat = re.compile(r"^\s*" + re.escape(key) + r"\s*=\s*(.*)$")
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = pat.match(line)
            if m:
                return m.group(1).strip().strip('"').strip("'")
    raise KeyError("在 %s 中未找到键 %s" % (path, key))


def call(token: str, api_name: str, params: dict, fields: str, timeout: int = 20) -> dict:
    body = json.dumps({
        "api_name": api_name,
        "token": token,
        "params": params,
        "fields": fields,
    }).encode("utf-8")
    req = urllib.request.Request(
        API, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "jev-tushare-probe/1.0"},
    )
    with _no_proxy_opener().open(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    data = d.get("data") or {}
    return {
        "code": d.get("code"),
        "msg": str(d.get("msg") or "")[:160],
        "rows": len(data.get("items") or []),
    }


def status_of(code) -> str:
    if code == 0:
        return "OK"
    if code in (40101, 40102):
        return "TOKEN_INVALID"
    if code in (40203, 40204):
        return "NO_PERMISSION"
    if code == 40002:
        return "RATE_LIMIT"
    return "ERROR"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Tushare 令牌体检（认证 + 分接口权限）")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--from-enc", help="从该 .enc 文件读令牌（默认生产路径）")
    src.add_argument("--from-dotenv", help="从该 dotenv 文件读 TUSHARE_TOKEN")
    src.add_argument("--token", help="直接给令牌（不建议，会进 shell 历史）")
    ap.add_argument("--key", default="TUSHARE_TOKEN", help="键名，默认 TUSHARE_TOKEN")
    ap.add_argument("--require", action="append", default=[],
                    help="必须可用的接口名，可重复；不可用则 rc=4")
    ap.add_argument("--json", action="store_true", help="输出单行 JSON（计划任务用）")
    ap.add_argument("--timeout", type=int, default=20, help="单接口超时秒数")
    a = ap.parse_args(argv)

    # 1) 取令牌
    try:
        if a.token:
            token, source = a.token.strip(), "arg"
        elif a.from_dotenv:
            token, source = token_from_dotenv(a.from_dotenv, a.key), "dotenv:" + os.path.basename(a.from_dotenv)
        else:
            path = a.from_enc or ENC_FILE
            token, source = token_from_enc(path, a.key), "enc:" + os.path.basename(path)
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "stage": "load_token", "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
        print(json.dumps(out, ensure_ascii=False) if a.json else "ABORT 取令牌失败 -> %s" % out["error"])
        return 6

    if not token:
        print("ABORT 令牌为空")
        return 6

    meta = {"source": source, "len": len(token), "sha8": sha8(token)}

    # 2) 逐接口探测
    results = []
    net_error = None
    for name, params, fields, desc, required in PROBES:
        row = {"api": name, "desc": desc, "required": required,
               "code": None, "msg": "", "rows": 0, "status": "SKIP"}
        try:
            r = call(token, name, params, fields, timeout=a.timeout)
            row.update(code=r["code"], msg=r["msg"], rows=r["rows"],
                       status=status_of(r["code"]))
        except Exception as e:  # noqa: BLE001
            row.update(status="NET_ERROR", msg="%s: %s" % (type(e).__name__, str(e)[:100]))
            net_error = row["msg"]
        results.append(row)

    # 3) 汇总判定
    by_api = {r["api"]: r for r in results}
    auth = by_api.get("trade_cal", {})
    required = a.require or [n for n, _, _, _, req in PROBES if req]
    missing = [n for n in required if (by_api.get(n) or {}).get("status") != "OK"]

    if auth.get("status") == "TOKEN_INVALID":
        verdict, rc = "TOKEN_INVALID", 3
    elif all(r["status"] == "NET_ERROR" for r in results):
        verdict, rc = "NET_ERROR", 5
    elif missing:
        verdict, rc = "PERMISSION_MISSING", 4
    elif auth.get("status") != "OK":
        verdict, rc = "AUTH_UNKNOWN", 5
    else:
        verdict, rc = "OK", 0

    payload = {"ok": rc == 0, "verdict": verdict, "required": required,
               "missing": missing, "token": meta, "results": results}

    if a.json:
        print(json.dumps(payload, ensure_ascii=False))
        return rc

    # 人读输出
    print("=" * 74)
    print("Tushare 令牌体检  source=%s  len=%d  sha8=%s" % (source, meta["len"], meta["sha8"]))
    print("=" * 74)
    print("%-12s %-7s %-16s %5s  %s" % ("接口", "code", "状态", "行数", "说明"))
    print("-" * 74)
    for r in results:
        mark = "*" if r["required"] else " "
        print("%s%-11s %-7s %-16s %5s  %s" % (
            mark, r["api"], r["code"] if r["code"] is not None else "-",
            r["status"], r["rows"], r["desc"]))
        if r["status"] not in ("OK",):
            print("    └ %s" % (CODE_MEANING.get(r["code"], r["msg"]) if r["msg"] == "" else r["msg"]))
    print("-" * 74)
    print("必需接口（* 标注）：%s" % ",".join(required))
    print("结论：%s" % verdict)

    if verdict == "TOKEN_INVALID":
        print("下一步：令牌本身失效 → 用 tools/set_env_secret.py 换新令牌，再重启 JevRestart")
    elif verdict == "PERMISSION_MISSING":
        print("下一步：认证已通，但 %s 无权限 → Tushare 账号提积分/开接口，或换数据源"
              % ",".join(missing))
    elif verdict == "NET_ERROR":
        print("下一步：网络不可达（检查代理/防火墙）；这不是令牌问题。err=%s" % net_error)
    elif verdict == "OK":
        print("下一步：无需动作。review 门下一轮起应打印「公告源=tushare」")
    return rc


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
