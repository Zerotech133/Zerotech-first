# -*- coding: utf-8 -*-
"""端口注册表守卫（K-STOCK-103）：治抢端口/私起服务。

做三件事：
1. 注册端口逐个 TCP 探测 → 在线/离线状态；
2. netstat 实测全机 LISTENING 端口 → 不在注册表的报「未登记」；
3. --strict 时未登记>0 返回 rc=1（可挂晨检/CI）。

用法：python tools/check_ports.py [--strict] [--json]

--json：输出机器可读结果（registered/unknown/summary），供晨检与
自动化消费（K-STOCK-114b）；与 --strict 可同用，rc 语义不变。
"""
import json
import re
import socket
import subprocess
import sys

REGISTRY = {
    8091: "零界门户API(portal_api.py)",
    8080: "nginx ai-portal(Docker)",
    8734: "零界首页(portproxy→8080,K-STOCK-107收编)",
    3000: "Open WebUI(Docker)",
    3001: "图谱studio(学习仓)",
    5175: "智策OS前端(zhice)",
    8102: "智策OS API(zhice)",
    8030: "llamacpp 35b(手动)",
    8787: "Hermes WebUI",
    8888: "QuantDinger前端(Docker)",
    8899: "本地 Ollama Web 聊天(AI/111111/web_chat.py)",
    15000: "QuantDinger API(Docker)",
    15432: "QuantDinger DB(Docker)",
    16379: "QuantDinger Redis(Docker)",
    9101: "jev_gate",
    9102: "signal_review",
    9103: "QD桥(jev_qd_bridge)",
    9104: "飞书卡片回调(lark_card)",
    9899: "CowAgent console",
    11434: "Ollama",
    3307: "菜鸟MySQL(Docker)",
    8093: "数据仓管理预览(daily_ui_preview_server.py)",
    8096: "Codex实验·智能体学院签名审核(Documents,非平台栈)",
    8123: "claudemind server(桌面第三方,非平台栈)",
}


def tcp_up(port, timeout=0.8):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def listening_ports():
    """netstat 解析本机 TCP LISTENING {port: pid}（IPv4/IPv6）。"""
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                         capture_output=True, text=True,
                         encoding="gbk", errors="replace").stdout or ""
    pairs = {}
    for line in out.splitlines():
        if "LISTENING" not in line:
            continue
        m = re.search(r":(\d+)\s+\S+\s+\S+\s+(\d+)\s*$", line)
        if m:
            pairs[int(m.group(1))] = int(m.group(2))
    return pairs


_PROC_CACHE = {}


def _powershell_proc_name(pid):
    """在 tasklist 被沙箱拒绝时，用 Get-Process 读取进程名。"""
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "(Get-Process -Id %d -ErrorAction SilentlyContinue).ProcessName" % pid],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = (r.stdout or "").strip().splitlines()
    return name[0].strip() if name and name[0].strip() else None


def proc_name(pid):
    if pid not in _PROC_CACHE:
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid,
                            "/FO", "CSV", "/NH"],
                           capture_output=True, text=True,
                           encoding="gbk", errors="replace").stdout or ""
        m = re.match(r'"([^"]+)"', r.strip())
        _PROC_CACHE[pid] = m.group(1) if m else (
            _powershell_proc_name(pid) or "unknown(%d)" % pid)
    return _PROC_CACHE[pid]


# 系统级/第三方豁免名单（随机口合法持有者，非我方技术栈）
# 2026-10-12 只读核验：TdxW.exe 位于 D:\\new_tdx64\\TdxW.exe，
# 公司为深圳市财富趋势科技股份有限公司，14571/17709 属通达信自身监听。
WHITELIST = ("system", "svchost", "clash", "todesk", "editor_sdk",
             "workbuddy", "explorer", "msedgewebview2", "wininit",
             "winlogon", "lsass", "services", "spoolsv", "dockerd",
             "com.docker", "vpn", "wslhost", "wsl", "sihost",
             "weixin", "wechat", "gameviewer", "tailscale", "dubbo",
             "nvidia", "qq", "feishu", "dingtalk", "quark", "tdxw",
             "yundetect", "vmms", "wpscloudsvr", "sgtool")


# 注册但允许离线的口（手动/按需型服务：strict 只防被抢，不因闲置判负）
# 9104 lark_card 于 2026-10-12 老田拍板转 OPTIONAL_OFFLINE（无启动链，
# 卡片回调疑被 feishu_bot_ws 长连接替代；转按需语义，strict 仍防被抢）
OPTIONAL_OFFLINE = {3001, 8030, 8899, 9104}


def main():
    strict = "--strict" in sys.argv
    as_json = "--json" in sys.argv
    # --json 模式 stdout 只出纯 JSON（人读横幅关闭），供自动化直接解析
    say = (lambda *a, **k: None) if as_json else print
    rc = 0
    say("== 注册端口在线状态 ==")
    down = []
    registered = []
    for p in sorted(REGISTRY):
        up = tcp_up(p)
        say("  %-5d %-5s %s%s" % (p, "✓在线" if up else "✗离线",
                                    REGISTRY[p],
                                    "（可离线）" if p in OPTIONAL_OFFLINE
                                    else ""))
        registered.append({"port": p, "name": REGISTRY[p], "up": up,
                           "optional_offline": p in OPTIONAL_OFFLINE})
        if not up:
            down.append(p)
    say("== 未登记监听（服务段 1024-49151，系统/第三方豁免）==")
    listen = listening_ports()
    unknown = []
    for p, pid in sorted(listen.items()):
        if not (1024 < p <= 49151) or p in REGISTRY:
            continue
        name = proc_name(pid)
        if any(w in name.lower() for w in WHITELIST):
            continue
        unknown.append((p, name))
    if unknown:
        rc = 1
        for p, name in unknown:
            say("  ⚠ %d 由 %s 监听——我方服务未上注册表！"
                  % (p, name))
        if not strict:
            rc = 0  # 非 strict：警示不挡道（瞬时 dev server 常态存在）
            say("  （非 strict 模式：仅警示；--strict 才 rc=1）")
    else:
        say("  无 ✓（注册表外仅系统/第三方豁免进程）")
    say("== 汇总：注册 %d 个，离线 %d（%s），未登记 %d =="
          % (len(REGISTRY), len(down),
             ",".join(map(str, down)) or "无", len(unknown)))
    hard_down = [p for p in down if p not in OPTIONAL_OFFLINE]
    strict_fail = bool(strict and (unknown or hard_down))
    if as_json:
        print(json.dumps({
            "strict": strict,
            "registered": registered,
            "offline": down,
            "unknown": [{"port": p, "pid_name": name}
                        for p, name in unknown],
            "summary": {"registered_total": len(REGISTRY),
                        "offline": len(down),
                        "unregistered": len(unknown)},
            "strict_fail": strict_fail,
            "rc": 1 if strict_fail else rc,
        }, ensure_ascii=False, indent=2))
    if strict_fail:
        say("STRICT FAIL")
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
