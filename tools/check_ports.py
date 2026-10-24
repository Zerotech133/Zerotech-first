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
    9105: "agent_platform ack-serve",  # K-ORG-191 收编：2026-10-18 实证活监听（agent_platform.cli ack-serve）
    8098: "模型管理台(api_model_admin.py)",  # K-ORG-191 收编：2026-10-18 实证活监听（并行线 model_admin）
    9899: "CowAgent console",
    11434: "Ollama",
    3307: "菜鸟MySQL(Docker)",
    8093: "数据仓管理预览(daily_ui_preview_server.py)",
    8096: "军团作战大屏API(api_legion_ops.py/v2)",  # 2026-10-22 TF-AUDIT 归因纠正：K-STOCK-394 原登记 km_feishu_hub 已迁 8099，现持有者=军团大屏（/api/legion/ops 实测返回 roster），legion_scheduler/academy_worker_plan 均调用此口
    8095: "picks定时发布调度(publish_schedule_server.py)",
    8097: "推送中台总览(push_admin_server.py)",
    8100: "datamart-api(数仓读层HTTP门面,K-STOCK-415)",
    8123: "claudemind server(桌面第三方,非平台栈)",
    # 2026-10-16 数据仓止血入册：PG/Neo4j 容器口此前仅靠 dockerd 豁免（治理盲区）。
    # 2026-10-16 审计修正：5432 实为 RAGFlow infinity；zhice-db 真实映射 5446。
    # 2026-10-16 二次勘误：5442 并非残留占用，实为 quant-pg 容器
    # （quant_system=xmap 基准+lineage 血缘、quant_meta=P1 迁移库）；
    # 曾误建 5443（quant-system-pg）已删除。
    5432: "RAGFlow infinity(Docker,非数据仓)",
    5442: "quant-pg PostgreSQL(Docker,quant_system+quant_meta)",
    5446: "zhice-db PostgreSQL(Docker,v2_bar_daily)",
    7474: "neo4j-kg HTTP(Docker,时序知识图谱)",
    7687: "neo4j-kg Bolt(Docker,时序知识图谱)",
    17474: "zerobound Neo4j HTTP(Docker,独立图谱)",
    17687: "zerobound Neo4j Bolt(Docker,独立图谱)",
    # 2026-10-17 K-STOCK-211 任务4集成门禁：全量 netstat 归因后入册
    # （此前仅靠进程未撞豁免名单而未报，属注册表治理盲区收编）。
    8100: "zerobound-kg 数据市集API(datamart_api.py)",
    8788: "Hermes WebUI v2(hermes-webui-next)",
    # 2026-10-22 老田裁端口处置（K-INTEL 情报线守卫收口）：
    # 8099=KM知识库飞书联动(portal/km_feishu_hub.py)，推送中台8097 iframe引用、
    # CowAgent看门狗(ops/keepalive_9899.py)保活、inbound_gateway幂等注册——自研常驻；
    # 8096 注册名同为 km_feishu_hub 系历史漂移，当前持有者待 TF-AUDIT 核对，不改名。
    8099: "KM知识库飞书联动(portal/km_feishu_hub.py)",
    # 8796=军团指挥台(agent_platform/legion_command/serve.py)，HKCU Run 用户级自启。
    8796: "军团指挥台(legion_command/serve.py)",  # 按需自启（HKCU Run），同 8030 手动语义：strict 只防抢占，不因未运行判负
    8791: "图谱数据看板(kg_data_dashboard.py)",
    8898: "res-downloader 自用工具(uvicorn)",
    18899: "res-downloader 自用工具(uvicorn)",
    11440: "Ollama 模型看板(model_dashboard)",
    1234: "llama-server(8030 同族,手动)",
    1238: "llama-server(8030 同族,手动)",
    5050: "灵拢NovaStudio(桌面第三方,非平台栈)",
    15048: "灵拢NovaStudio(桌面第三方,非平台栈)",
    # 2026-10-23 K-MDC-20261023-P8798：8798 实测 PID33388=portal/council_ops.py
    # （议会厅交互桥 v3，127.0.0.1 单线程 HTTP，落盘本仓，git 可溯）。
    # 评审门 non-strict 曾示警"未登记监听"——按 8796 同语义入册。
    8798: "议会厅交互桥(council_ops.py)",
    # 2026-10-23 K-MDC-20261023-REV：8101=情报交付评审实例。portal_api.py 的
    # PORT=8091 硬编码，评审实例以导入方式另起（127.0.0.1:8101，同一 Handler），
    # 用于冷启动前零停机验证 /intel-dossier 与 /api/intel-graph/dossier；
    # 不触 8091 现役进程。按需起停，同 8796 语义入 OPTIONAL_OFFLINE。
    8101: "情报交付评审实例(portal_api导入式)",
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
             "yundetect", "vmms", "wpscloudsvr", "sgtool",
             "sogoucloud", "baidunetdiskhost", "trae",
             # 2026-10-17 K-STOCK-211 任务4：随机口第三方/系统组件豁免
             # novaairouter/model-router-plugin=灵拢生态路由组件；
             # leishensdk_yyb=雷神加速器SDK；mqsvc=MSMQ 消息队列(系统服务)；
             # androwsstore/androwssvr=Androws 安卓引擎组件。
             "novaairouter", "model-router-plugin", "leishensdk_yyb",
             "mqsvc", "androwsstore", "androwssvr",
             # 2026-10-18 K-ORG-191：codex=Codex CLI 本地认证服务（用户桌面
             # 第三方工具，1455），同 trae 先例入豁免，否则 strict 必挂。
             # 2026-10-22 老田裁端口处置：androws=Androws 主进程（5555/15118）；
             # tencent.androw=腾讯办公安卓扩展（14900）——均桌面第三方非平台栈。
             "codex", "androws", "tencent.androws",
             # 2026-10-24 收编：DeepSeek Harness（本机 AI 编码工具，桌面第三方
             # 非平台栈）监听 9230/18080。同 trae / codex 先例入豁免——
             # 它是工具不是我们的服务，登记进 REGISTRY 反而是错误事实。
             # 触发背景：OPS1 每日事项「端口注册表零漂移」判红，查明是这两口。
             "deepseek")
# 2026-10-13 QA1-R2质检排查(K-ORG-029)：5283=SogouCloud.exe(搜狗输入法云)、
# 8475=baidunetdiskhost.exe(百度网盘)——均为终端第三方软件，非平台栈，入豁免。
# 2026-10-14 K-STOCK-394：17788=Trae CN.exe（IDE 自身，桌面第三方非平台栈），入豁免
# 否则 IDE 在线期间 check_ports --strict 必挂评审门。


# 注册但允许离线的口（手动/按需型服务：strict 只防被抢，不因闲置判负）
# 9104 lark_card 于 2026-10-12 老田拍板转 OPTIONAL_OFFLINE（无启动链，
# 卡片回调疑被 feishu_bot_ws 长连接替代；转按需语义，strict 仍防被抢）
# 2026-10-14 K-STOCK-394：8787 Hermes WebUI 无 keepalive 链，start_hermes_bg.py
# 幂等按需拉起（K271 配方），同 lark_card 按需语义入列，否则 strict 必挂。
OPTIONAL_OFFLINE = {3001, 8030, 8093, 8096, 8099, 8101, 8787, 8796, 8798, 8899, 9104}  # 8099/8796/8798/8101 加入：看门狗/HKCU 自启/会话级后台/评审按需实例，strict 防抢占不判离线（2026-10-22 老田裁端口；8798 于 2026-10-23 P8798 入列；8101 于 2026-10-23 REV 入列）
# 2026-10-16 数据仓止血：PG/Neo4j 容器按需启停，
# strict 语义=防抢占不判离线；库内健康由 kg_check / freshness_report / xmap 水位各自监控。
# 2026-10-16 二次勘误：5442 实为 quant-pg 容器口；5443 误建容器已删、条目移除。
OPTIONAL_OFFLINE |= {5432, 5442, 5446, 7474, 7687, 17474, 17687}
# 2026-10-17 K-STOCK-211 任务4：新入册 8 口均为手动/按需型（无 systemd 级
# keepalive 链），语义同 8030/8787——strict 只防抢占，不因闲置判负。
# 5050/15048 NovaStudio 为常驻桌面应用，同 8123 claudemind 先例不入列。
OPTIONAL_OFFLINE |= {8100, 8788, 8791, 8898, 18899, 11440, 1234, 1238}
# 2026-10-17 K-STOCK-211 任务4 strict 首跑归因（全部按 10-16 按需先例收编）：
# 3000 Open WebUI 容器已删；8888/15000/15432/16379 QuantDinger 全家容器已删
# （防抢占语义保留，容器若重建仍受注册表管辖）；
# 8123 claudemind 桌面应用用户未开；9899 CowAgent 无 keepalive 链按需；
# 8095 picks 调度为按需拉起型（TodaysPicks 计划任务在位）；
# 8734 portproxy→8080 规则已被系统 reset 清空（v4tov4 空），8080 本体在线，
# 规则若重建仍防抢占。
OPTIONAL_OFFLINE |= {3000, 8095, 8123, 8734, 8888, 9899, 15000, 15432, 16379}
# 2026-10-25 K-ORG-20261025-BATCH：合并门实测定性——1234/1238 是手动 llama-server
# 同族口、8898/18899 是 res-downloader 自用工具、15000/15432/16379 为历史占位；
# 这些都是「按需启停」语义，strict 只该防抢占，不该因闲置判负。
# 教训：把「未登记」与「在册服务离线」混在一个返回码里，会让合并门天天误报。
OPTIONAL_OFFLINE |= {1234, 1238, 8898, 18899}


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
