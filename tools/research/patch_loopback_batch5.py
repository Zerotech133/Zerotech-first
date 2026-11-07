# -*- coding: utf-8 -*-
"""批 5：回环剩余 14 处 + legion_command safe_http 回环豁免补齐（可复跑）。

背景
----
2026-11-06 `c14ff67a5` 给 6 份 `safe_urlopen.py` 副本加了「回环禁代理」豁免，
但 `agent_platform/legion_command/safe_http.py` 没跟上 —— 它是第 7 份、也是唯一
缺豁免的一份。该文件被 legion_command 全树 7 个模块引用（12 处调用），全部打本机
服务（11434 / 8770 / 8771 / 8787 / 8796）。扫描器按「调的是 safe_urlopen」判
GUARDED，所以这 12 处从未出现在 VICTIM 清单里 —— 属假阴性。

本脚本干两件事：
  P0  把 safe_http.py 改为委托载入 agent_platform/safe_urlopen.py（实现只留一份）
  P1  把产品侧剩余 14 处 `urllib.request.urlopen` 换成 safe_urlopen（正则口径同
      tools/research/scan_loopback_urlopen.py 的 VICTIM 判定）

排除项：deliverables/2026-11-08_结果包_加速版/.../smoke_monitor.py 是交付件里的
证据脚本（docstring 写明从 C:\\Temp 直接跑），改它等于篡改已交付物，故不动。

用法
----
  python -X utf8 tools/research/patch_loopback_batch5.py --dry     # 只看计划
  python -X utf8 tools/research/patch_loopback_batch5.py --apply   # 落盘
  python -X utf8 tools/research/patch_loopback_batch5.py --check   # 校验是否已全部改完

纪律（与批 3/批 4 一致）
  - 每个替换前断言 old 出现次数 == expect（默认 1），不符即整文件跳过并报错
  - 替换后断言 new 存在、old 消失
  - 保留原文件行尾（本批 13 个 CRLF、1 个 LF）
  - 落盘前 ast.parse 校验语法；任一文件失败则退出码非 0
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

PROBE_BLOCK = '''\
# 回环禁代理（2026-11-07 批 5）：本机服务的请求不走系统代理，实现见 tools/safe_urlopen.py
import sys as _sys
from pathlib import Path as _Path

for _base in _Path(__file__).resolve().parents:
    for _cand in (_base, _base / "tools"):
        if (_cand / "safe_urlopen.py").exists():
            _sys.path.insert(0, str(_cand))
            break
    else:
        continue
    break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen
'''

LEGION_BLOCK = '''\
try:  # 双形态入口：脚本形态 import safe_http，包形态 import legion_command.safe_http
    from safe_http import safe_urlopen
except ImportError:  # 作为包导入时
    from legion_command.safe_http import safe_urlopen
'''

SAFE_HTTP_NEW = '''\
# -*- coding: utf-8 -*-
"""安全 urlopen（legion_command 树入口）：scheme allowlist + 回环禁代理。

2026-11-07 修：本文件是原先唯一一份没同步回环豁免的实现，导致 legion_command 全树
12 处打本机服务（11434 Ollama / 8770-8771 OpenHands / 8787 Hermes / 8796 模型网关）
的请求在 HTTP_PROXY 存在时仍被交给代理 —— 代理一严格，探活全部静默失效（显示成
服务宕机，日志里看不出是代理的锅）。

现改为委托载入共享实现 `agent_platform/safe_urlopen.py`（与
`hermes_auth._load_safe_urlopen` 同款 spec 载入方式），实现只保留一份，以后
打补丁不会再漏。

文件名**保持不变**：legion_command 有「包入口 / 脚本入口」双形态，脚本形态下本目录
在 sys.path[0]，若这里也叫 safe_urlopen.py 会遮蔽其它树的同名副本 —— 出现
「改了根目录那份、这个入口不生效」。故沿用 safe_http 这个名字，只做委派。
"""
from __future__ import annotations

import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _Path

ALLOWED_SCHEMES = ("http", "https")


def _load_shared():
    here = _Path(__file__).resolve()
    cands = [
        here.parents[1] / "safe_urlopen.py",              # agent_platform/safe_urlopen.py
        here.parents[2] / "portal" / "safe_urlopen.py",   # 仓库 portal 副本
        here.parents[2] / "tools" / "safe_urlopen.py",    # 仓库 tools 副本
    ]
    for cand in cands:
        if not cand.exists():
            continue
        spec = _ilu.spec_from_file_location("_zb_safe_urlopen", cand)
        mod = _ilu.module_from_spec(spec)
        _sys.modules.setdefault("_zb_safe_urlopen", mod)
        spec.loader.exec_module(mod)
        return mod
    return None


_shared = _load_shared()

if _shared is not None:
    safe_urlopen = _shared.safe_urlopen
else:  # 兜底：至少保留 scheme 校验（fail-visible，绝不静默放行）
    import urllib.request
    from urllib.parse import urlsplit

    def safe_urlopen(url, *args, **kwargs):
        target = getattr(url, "full_url", None) or str(url)
        scheme = urlsplit(target).scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            raise ValueError("safe_urlopen 拒绝非 http(s) scheme: %r（url=%r）"
                             % (scheme or "<none>", target[:120]))
        return urllib.request.urlopen(url, *args, **kwargs)  # nosec B310
'''


def _ins(anchor, block):
    """在 anchor 行之后插入 block（保持段落间空行）。"""
    return {"kind": "insert_after", "anchor": anchor, "block": block}


def _rep(old, new, expect=1):
    return {"kind": "replace", "old": old, "new": new, "expect": expect}


# 每项：(相对路径, 操作列表)
SPEC = [
    # ---------- P0：safe_http 补齐回环豁免 ----------
    ("agent_platform/legion_command/safe_http.py", [{"kind": "write", "text": SAFE_HTTP_NEW}]),

    # ---------- P1：产品侧 14 处 ----------
    ("agent_platform/legion_command/case_orchestrator.py", [
        _ins("from legion_command import hq_meeting", LEGION_BLOCK),
        _rep("with urllib.request.urlopen(req, timeout=timeout) as r:",
             "with safe_urlopen(req, timeout=timeout) as r:"),
    ]),
    ("agent_platform/legion_command/serve.py", [
        _rep("with urllib.request.urlopen(req, timeout=3) as resp:",
             "with safe_urlopen(req, timeout=3) as resp:"),
    ]),
    ("agent_platform/legion_command/openhands_engine.py", [
        _rep('with urllib.request.urlopen("http://127.0.0.1:8771/health", timeout=1.5) as r:',
             'with safe_urlopen("http://127.0.0.1:8771/health", timeout=1.5) as r:'),
    ]),
    ("agent_platform/tools/d1_research_mcp.py", [
        _ins("import urllib.request", PROBE_BLOCK),
        _rep("with urllib.request.urlopen(BASE, timeout=TIMEOUT) as r:",
             "with safe_urlopen(BASE, timeout=TIMEOUT) as r:"),
    ]),
    ("agent_platform/tools/h1_e2e_local.py", [
        _ins("from http.server import ThreadingHTTPServer", PROBE_BLOCK),
        _rep("with urllib.request.urlopen(req, timeout=10) as r:",
             "with safe_urlopen(req, timeout=10) as r:"),
    ]),
    ("agent_platform/tools/stock_analysis_mcp.py", [
        _ins("import urllib.request", PROBE_BLOCK),
        _rep("with urllib.request.urlopen(req, timeout=TIMEOUT) as r:",
             "with safe_urlopen(req, timeout=TIMEOUT) as r:"),
    ]),
    ("analysis/views.py", [
        _ins("from . import config", PROBE_BLOCK),
        _rep('with urllib.request.urlopen("http://127.0.0.1:8091/api/research?c=%s&force=1" % code, timeout=180) as r:',
             'with safe_urlopen("http://127.0.0.1:8091/api/research?c=%s&force=1" % code, timeout=180) as r:'),
    ]),
    ("jev-eval/jev_qd_bridge.py", [
        _rep("with urllib.request.urlopen(req, timeout=10) as resp:",
             "with jc.safe_urlopen(req, timeout=10) as resp:"),
        _rep("with urllib.request.urlopen(req, timeout=15) as resp:",
             "with jc.safe_urlopen(req, timeout=15) as resp:"),
    ]),
    ("ops/rotate_push_secrets.py", [
        _ins("PORT = 8097", PROBE_BLOCK),
        _rep('def _alive():\n    import urllib.request\n    try:\n'
             '        urllib.request.urlopen("http://127.0.0.1:%d/" % PORT, timeout=3)',
             'def _alive():\n    try:\n'
             '        safe_urlopen("http://127.0.0.1:%d/" % PORT, timeout=3)'),
    ]),
    ("portal/api_health_portal.py", [
        _rep('        if http_path:\n'
             '            from urllib.request import urlopen\n'
             '            ok = urlopen("http://127.0.0.1:%d%s" % (port, http_path), timeout=2.5).status == 200',
             '        if http_path:\n'
             '            ok = safe_urlopen("http://127.0.0.1:%d%s" % (port, http_path), timeout=2.5).status == 200'),
    ]),
    ("portal/feishu_bot_ws.py", [
        # 必须是模块级导入：函数内导入的话 `bot.safe_urlopen` 不存在，测试无法 patch
        _ins('sys.path.insert(0, os.path.join(os.path.dirname(HERE), "quant_backtest"))',
             "from safe_urlopen import safe_urlopen  # 回环禁代理（2026-11-07 批 5）"),
        _rep("with urllib.request.urlopen(req, timeout=20) as resp:",
             "with safe_urlopen(req, timeout=20) as resp:"),
    ]),
    ("tools/datawarehouse/kpi_report.py", [
        _ins("from pathlib import Path", PROBE_BLOCK),
        _rep("with urllib.request.urlopen(url, timeout=timeout) as r:",
             "with safe_urlopen(url, timeout=timeout) as r:"),
    ]),
    ("tools/legion_probe.py", [
        _ins("import urllib.request", PROBE_BLOCK),
        _rep("with urllib.request.urlopen(req, timeout=timeout) as r:",
             "with safe_urlopen(req, timeout=timeout) as r:"),
    ]),

    # ---------- 测试对齐：patch 目标从 urllib.request.urlopen 改为模块的 safe_urlopen ----------
    # 原因：safe_urlopen 对回环走独立 ProxyHandler({}) opener，patch 全局 urlopen 拦不住，
    # 真连本机服务 -> 红且依赖环境（判例：test_rerank_degrade_rule_only / test_web_chat）。
    ("tools/datawarehouse/tests/test_kpi_report.py", [
        _rep('monkeypatch.setattr(kr.urllib.request, "urlopen",',
             'monkeypatch.setattr(kr, "safe_urlopen",', expect=4),
    ]),
    ("portal/tests/test_jev_card_forward.py", [
        _rep('monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)',
             'monkeypatch.setattr(bot, "safe_urlopen", _fake_urlopen)'),
        _rep('monkeypatch.setattr(urllib.request, "urlopen", _boom)',
             'monkeypatch.setattr(bot, "safe_urlopen", _boom)'),
    ]),
]


def _eol_of(text: str) -> str:
    crlf = text.count("\r\n")
    bare = text.count("\n") - crlf
    if crlf and not bare:
        return "\r\n"
    return "\n"


def _apply_ops(path: pathlib.Path, ops, dry: bool, log):
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    eol = _eol_of(text)
    if eol != "\n":
        text = text  # 保持原样，下面的 pattern 统一转 eol
    orig = text

    for op in ops:
        if op["kind"] == "write":
            body = op["text"].replace("\n", eol) if eol != "\n" else op["text"]
            if text == body:
                log("  = 整文件已是目标内容，跳过")
                continue
            log("  * 整文件重写（%d 行，eol=%r）" % (body.count(eol), eol))
            text = body
            continue

        if op["kind"] == "insert_after":
            anchor = op["anchor"].replace("\n", eol)
            block = op["block"].replace("\n", eol)
            if block.strip().split(eol)[0] in text:
                log("  = 导入块已存在，跳过插入")
                continue
            n = text.count(anchor)
            if n != 1:
                raise SystemExit("%s: 插入锚点出现 %d 次（应为 1）：%r" % (path, n, op["anchor"][:60]))
            text = text.replace(anchor, anchor + eol + block.rstrip(eol), 1)
            log("  + 插入导入块（锚点：%s）" % op["anchor"][:50])
            continue

        if op["kind"] == "replace":
            old = op["old"].replace("\n", eol)
            new = op["new"].replace("\n", eol)
            n = text.count(old)
            if n == 0 and new in text:
                log("  = 已替换过，跳过")
                continue
            if n != op["expect"]:
                raise SystemExit("%s: old 出现 %d 次（应为 %d）：%r"
                                 % (path, n, op["expect"], op["old"][:80]))
            text = text.replace(old, new)
            if new not in text:
                raise SystemExit("%s: 替换后未见新串：%r" % (path, op["new"][:80]))
            log("  ~ 替换 %d 处：%s" % (n, op["new"].split(eol)[0].strip()[:70]))
            continue

        raise SystemExit("未知操作：%r" % (op["kind"],))

    # 顺手清理：改完后 urllib 完全不再被引用，则删掉 `import urllib.request`（否则 F401 卡 ratchet）
    # 注意：判"是否还被引用"必须先把 import 行本身排除，否则 import 行自带 "urllib." 永远为真。
    if path.suffix == ".py":
        for cand in ("import urllib.request" + eol, "import urllib.request\n" + eol):
            if text.count(cand) == 1 and "urllib." not in text.replace(cand, "", 1):
                text = text.replace(cand, "", 1)
                log("  - 删除已无引用的 import urllib.request")
                break

    if text != orig:
        ast.parse(text)  # 语法校验，失败直接抛
        if not dry:
            path.write_bytes(text.encode("utf-8"))
    return text != orig


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry", action="store_true", help="只打印计划，不落盘")
    g.add_argument("--apply", action="store_true", help="落盘")
    g.add_argument("--check", action="store_true", help="只校验是否已改完")
    a = ap.parse_args()
    if not (a.dry or a.apply or a.check):
        ap.print_help()
        return 2

    if a.check:
        bad = 0
        for rel, ops in SPEC:
            p = REPO / rel
            text = p.read_text(encoding="utf-8")
            miss = []
            for op in ops:
                if op["kind"] == "replace":
                    if op["new"].split("\n")[0].strip() not in text:
                        miss.append(op["new"].split("\n")[0].strip()[:60])
                elif op["kind"] == "insert_after":
                    if op["block"].strip().split("\n")[0] not in text:
                        miss.append("block:%s" % op["block"].strip().split("\n")[0][:40])
            if miss:
                bad += 1
                print("[TODO] %s" % rel)
                for m in miss:
                    print("        %s" % m)
            else:
                print("[ OK ] %s" % rel)
        print("\n未完成文件：%d / %d" % (bad, len(SPEC)))
        return 0 if bad == 0 else 1

    changed = 0
    for rel, ops in SPEC:
        p = REPO / rel
        print("→ %s" % rel)
        try:
            if _apply_ops(p, ops, dry=a.dry, log=print):
                changed += 1
        except SystemExit as e:
            print("  ! %s" % e)
            return 1
    print("\n%s：%d / %d 个文件有改动" % ("DRY-RUN" if a.dry else "APPLIED", changed, len(SPEC)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
