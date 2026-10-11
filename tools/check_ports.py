# -*- coding: utf-8 -*-
"""端口注册表守卫（K-STOCK-103）：治抢端口/私起服务。

做三件事：
1. 注册端口逐个 TCP 探测 → 在线/离线状态；
2. netstat 实测全机 LISTENING 端口 → 不在注册表的报「未登记」；
3. --strict 时未登记>0 返回 rc=1（可挂晨检/CI）。

用法：python tools/check_ports.py [--strict]
"""
import re
import socket
import subprocess
import sys

REGISTRY = {
    8091: "零界门户API(portal_api.py)",
    8094: "零界驾驶舱(home_server.py)",
    8080: "nginx ai-portal(Docker)",
    8734: "零界首页镜像(临时静态服务·待收编,见K-STOCK-107)",
    3000: "Open WebUI(Docker)",
    3001: "图谱studio(学习仓)",
    5175: "智策OS前端(zhice)",
    8102: "智策OS API(zhice)",
    8030: "llamacpp 35b(手动)",
    8787: "Hermes WebUI",
    8888: "QuantDinger前端(Docker)",
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


def proc_name(pid):
    if pid not in _PROC_CACHE:
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid,
                            "/FO", "CSV", "/NH"],
                           capture_output=True, text=True,
                           encoding="gbk", errors="replace").stdout or ""
        m = re.match(r'"([^"]+)"', r.strip())
        _PROC_CACHE[pid] = m.group(1) if m else "unknown(%d)" % pid
    return _PROC_CACHE[pid]


# 系统级/第三方豁免名单（随机口合法持有者，非我方技术栈）
WHITELIST = ("system", "svchost", "clash", "todesk", "editor_sdk",
             "workbuddy", "explorer", "msedgewebview2", "wininit",
             "winlogon", "lsass", "services", "spoolsv", "dockerd",
             "com.docker", "vpn", "wslhost", "wsl", "sihost",
             "weixin", "wechat", "gameviewer", "tailscale", "dubbo",
             "nvidia", "qq", "feishu", "dingtalk", "quark",
             "yundetect")


def main():
    strict = "--strict" in sys.argv
    rc = 0
    print("== 注册端口在线状态 ==")
    down = []
    for p in sorted(REGISTRY):
        up = tcp_up(p)
        print("  %-5d %-5s %s" % (p, "✓在线" if up else "✗离线",
                                  REGISTRY[p]))
        if not up:
            down.append(p)
    print("== 未登记监听（服务段 1024-49151，系统/第三方豁免）==")
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
            print("  ⚠ %d 由 %s 监听——我方服务未上注册表！"
                  % (p, name))
        if not strict:
            rc = 0  # 非 strict：警示不挡道（瞬时 dev server 常态存在）
            print("  （非 strict 模式：仅警示；--strict 才 rc=1）")
    else:
        print("  无 ✓（注册表外仅系统/第三方豁免进程）")
    print("== 汇总：注册 %d 个，离线 %d（%s），未登记 %d =="
          % (len(REGISTRY), len(down),
             ",".join(map(str, down)) or "无", len(unknown)))
    if strict and (unknown or down):
        print("STRICT FAIL")
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
