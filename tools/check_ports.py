# -*- coding: utf-8 -*-
"""端口注册表守卫（K-STOCK-103）：治抢端口/私起服务。

做四件事：
1. 注册端口逐个 TCP 探测 → 在线/离线状态；**离线仅提示，不判失败**
   （手动/按需型服务、容器未启属正常态）；
2. netstat 实测全机 LISTENING 端口 → 不在注册表的报「未登记」；
3. 静态端口冲突检测：
   a) REGISTRY 字面量里同一端口重复登记（Python dict 静默覆盖 = 归属不定）；
   b) tools/ports_allocated.json 里同一端口分配给多个服务名；
4. --strict 时「未登记监听 > 0」或「端口冲突 > 0」返回 rc=1（合并门/CI 用）。
   在册服务离线**永不**导致 strict 失败。

用法：python tools/check_ports.py [--strict] [--json]

--json：输出机器可读结果（registered/unknown/conflicts/offline/summary），
供晨检与自动化消费（K-STOCK-114b）；与 --strict 可同用，rc 语义不变。

2026-10-26 WO-UPGRADE-20261026 语义拆分（审计 B-02 修复）：此前 strict 把
「在册但离线」也算失败（20 个按需口离线 → 恒红），而 devtask 合并门又调非
strict（恒 0）→ 既假红又假绿。现在 strict 只拦「未登记监听 + 端口冲突」。
"""
import json
import os
import re
import socket
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import logging

# 2026-10-25 B5/P1-07（审计 RC-04）：Windows 下 stdout 被管道重定向时默认按 GBK
# 编码，本文件输出的 ✓/✗ 会 UnicodeEncodeError 崩溃（check_ports.py:224）→
# 评审门在干净 shell 假红。强制 UTF-8；--json 模式不受影响（纯 ASCII 转义）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError) as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "tools/check_ports.py", exc)

REGISTRY = {
    8091: "零界门户API(portal_api.py)",
    8080: "nginx ai-portal(Docker)",
    8081: "移动端公网受限门(m_public_gate.py,2026-10-25)",
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
    8100: "datamart-api 数仓读层HTTP门面(zerobound-kg 数据市集API/datamart_api.py,K-STOCK-415)",  # 2026-10-26 去重：原 10-17 与本行两次登记，Python dict 静默覆盖
    8123: "claudemind server(桌面第三方,非平台栈)",
    8104: "Bytebase 数据治理UI(Docker,2026-10-27 上架)",
    6006: "Phoenix UI(Docker,AI trace 观测,2026-10-27)",  # P1-⑤ OTEL 可观测
    4317: "OTLP gRPC(Docker/Phoenix,2026-10-27)",
    4318: "OTLP HTTP(Docker/Phoenix,2026-10-27)",
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
    # 2026-10-26 WO-UPGRADE-20261026：原 8100 在此重复登记一次（与上方
    # K-STOCK-415 条目同口不同名，dict 静默覆盖），已归并到上方条目，勿再加。
    8788: "Hermes WebUI v2(hermes-webui-next)",
    # 2026-10-22 老田裁端口处置（K-INTEL 情报线守卫收口）：
    # 8099=KM知识库飞书联动(portal/km_feishu_hub.py)，推送中台8097 iframe引用、
    # CowAgent看门狗(ops/keepalive_9899.py)保活、inbound_gateway幂等注册——自研常驻；
    # 8096 注册名同为 km_feishu_hub 系历史漂移，当前持有者待 TF-AUDIT 核对，不改名。
    8099: "KM知识库飞书联动(portal/km_feishu_hub.py)",
    # 8796=军团指挥台(agent_platform/legion_command/serve.py)，HKCU Run 用户级自启。
    8796: "军团指挥台(legion_command/serve.py)",  # 按需自启（HKCU Run），同 8030 手动语义：strict 只防抢占，不因未运行判负
    # 8795=军团网关 HTTP 通道（OSLINK-W4 §二 契约端口，gateway/serve.py）。默认不启动；
    # 2026-10-26 老田批准 Scope A 隔离验收后入册（K-QUANT-20261026-OSLEG-8795PREP），
    # 按需语义同 8796：strict 只防抢占、不因闲置判负。
    8795: "军团网关(gateway/serve.py)",
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
    8798: "议会厅交互桥(council_ops.py·2026-10-28 收编入 8796 /api/hq/agenda，只读归档不再启动)",
    # 2026-10-23 K-MDC-20261023-REV：8101=情报交付评审实例。portal_api.py 的
    # PORT=8091 硬编码，评审实例以导入方式另起（127.0.0.1:8101，同一 Handler），
    # 用于冷启动前零停机验证 /intel-dossier 与 /api/intel-graph/dossier；
    # 不触 8091 现役进程。按需起停，同 8796 语义入 OPTIONAL_OFFLINE。
    8101: "情报交付评审实例(portal_api导入式)",
    # 2026-10-26 WO-LEGION-IDE-STANDALONE：军团 Agent IDE 独立形态。
    # 自有进程/端口/启动器（ops/start_legion_ide.ps1，Edge App 模式窗口），
    # 脱离 8734 门户页面路由；数据=portal/agent_ide_bundle.json 预烘只读。
    # 按需起停，同 8796 语义入 OPTIONAL_OFFLINE（strict 防抢占不判离线）。
    8103: "军团独立IDE(legion_ide/serve.py)",
    # 2026-10-30 K-QUANT-20261030-OHENGINE：方案A OpenHands Agent Server（本地执行引擎）。
    # 仅绑 127.0.0.1，venv 在 C:\项目\legion-openhands\.venv312；按需起停（strict 防抢占不判离线）。
    8770: "OpenHands Agent Server(方案A执行引擎)",
}


def tcp_up(port, timeout=0.8):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def tcp_up_many(ports, timeout=0.8, workers=24):
    """并行探测多个端口（2026-10-29 P40：56 口串行 0.8s 超时 → 合并门每单多付 ~28s）。

    返回 {port: bool}；探测顺序无关，输出仍按 ports 顺序渲染。
    """
    ports = list(ports)
    if not ports:
        return {}
    with ThreadPoolExecutor(max_workers=min(workers, len(ports))) as ex:
        results = list(ex.map(lambda p: tcp_up(p, timeout), ports))
    return dict(zip(ports, results))


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



def proc_start_age_min(pid, timeout=3.0):
    """监听进程已运行分钟数；无法判定返回 None（strict 侧 fail-visible 处理）。

    2026-10-30 P56：并行会话会临时拉起 python 监听（随机高端口，几分钟即退），
    旧 strict 一遇就全家合并门 FAIL。按「启动 <N 分钟 = 瞬态」只告警不判负。
    """
    try:
        import psutil

        return (max(0.0, __import__("time").time() - psutil.Process(int(pid))
                    .create_time()) / 60.0)
    except Exception:  # noqa: BLE001
        return None


def _prime_proc_names(pids, timeout=15):
    """批量预热 PID→进程名（tasklist 一次拿全，避免逐个 PowerShell 查询）。

    2026-10-29 P40：原 proc_name 对每个未登记监听 PID 起一次 PowerShell，
    机器上第三方监听多时合并门要额外十几秒；tasklist 一次性解析即可。
    """
    todo = [int(p) for p in pids if int(p) not in _PROC_CACHE]
    if not todo:
        return
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True,
                             encoding="gbk", errors="replace",
                             timeout=timeout).stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return
    import csv as _csv
    for row in _csv.reader(out.splitlines()):
        if len(row) < 2:
            continue
        try:
            pid = int(row[1])
        except (TypeError, ValueError):
            continue
        if pid in todo:
            _PROC_CACHE[pid] = (row[0] or "").strip()


def registry_duplicate_ports(path=None):
    """静态扫描本文件 REGISTRY 字面量，返回重复登记的端口列表。

    Python dict 字面量重复键会静默覆盖（后者生效），端口归属因此不定；
    只在源码层面可查，故用行首 ``<port>: "<name>"`` 形态匹配（REGISTRY 是
    本文件唯一的该形态字面量；集合字面量 ``{8100, ...}`` 不匹配）。
    2026-10-26 实测命中 8100 重复登记（10-17 与 K-STOCK-415 两次入册），
    去重后注册总数 51 = AGENTS.md §1 口径。
    """
    src = path or os.path.abspath(__file__)
    try:
        with open(src, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return []
    seen, dup = set(), set()
    for match in re.finditer(r"^\s*(\d{2,5}):\s*['\"]", text, re.M):
        port = int(match.group(1))
        if port in seen:
            dup.add(port)
        seen.add(port)
    return sorted(dup)


def allocation_conflicts(path=None):
    """tools/ports_allocated.json 中同一端口分配给多个服务名 → 冲突列表。"""
    alloc = path or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "ports_allocated.json")
    try:
        with open(alloc, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    by_port = {}
    for name, port in (data or {}).items():
        by_port.setdefault(port, []).append(str(name))
    return [{"port": p, "names": sorted(names)}
            for p, names in sorted(by_port.items()) if len(names) > 1]


def port_conflicts():
    """端口冲突清单：注册表重复登记 + 端口分配表重复分配。"""
    out = []
    for port in registry_duplicate_ports():
        out.append({"kind": "registry_duplicate", "port": port,
                    "detail": "REGISTRY 中同一端口登记多次（dict 静默覆盖）"})
    for item in allocation_conflicts():
        out.append({"kind": "allocation_duplicate", "port": item["port"],
                    "detail": "ports_allocated.json 中同端口分配给 %s"
                              % "/".join(item["names"])})
    return out


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
# 2026-10-25 EXEC-R11 更正（审计 P2-18）：安装根已自 D 盘迁入
# C:/项目/new_tdx64/TdxW.exe（AGENTS.md §8 别名表），D:\ 旧路径已失效、勿再引用。
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
             "deepseek",
             # 2026-10-25 K-ORG-20261025-CFWHITELIST：cloudflared.exe 隧道工具
             # 监听 20241/20242（快速隧道管理口），非我方服务；同 trae/codex 先例入豁免。
             "cloudflared")
# 2026-10-13 QA1-R2质检排查(K-ORG-029)：5283=SogouCloud.exe(搜狗输入法云)、
# 8475=baidunetdiskhost.exe(百度网盘)——均为终端第三方软件，非平台栈，入豁免。
# 2026-10-14 K-STOCK-394：17788=Trae CN.exe（IDE 自身，桌面第三方非平台栈），入豁免
# 否则 IDE 在线期间 check_ports --strict 必挂评审门。


# 注册但允许离线的口（手动/按需型服务：strict 只防被抢，不因闲置判负）
# 9104 lark_card 于 2026-10-12 老田拍板转 OPTIONAL_OFFLINE（无启动链，
# 卡片回调疑被 feishu_bot_ws 长连接替代；转按需语义，strict 仍防被抢）
# 2026-10-14 K-STOCK-394：8787 Hermes WebUI 无 keepalive 链，start_hermes_bg.py
# 幂等按需拉起（K271 配方），同 lark_card 按需语义入列，否则 strict 必挂。
OPTIONAL_OFFLINE = {3001, 8030, 8093, 8096, 8099, 8101, 8103, 8770, 8787, 8796, 8798, 8899, 9104}  # 8099/8796/8798/8101/8103/8770 加入：看门狗/HKCU 自启/会话级后台/评审按需实例/独立IDE/OpenHands执行引擎，strict 防抢占不判离线（2026-10-22 老田裁端口；8798 于 2026-10-23 P8798 入列；8101 于 2026-10-23 REV 入列；8103 于 2026-10-26 IDE-STANDALONE 入列；8770 于 2026-10-30 OHENGINE 入列）
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
# 2026-10-26 K-QUANT-20261026-OSLEG-8795PREP：8795=W4 军团网关（默认不启，
# 隔离验收/按需语义同 8796；strict 语义=防抢占、不因闲置判负）。
OPTIONAL_OFFLINE |= {8795}


def main():
    strict = "--strict" in sys.argv
    as_json = "--json" in sys.argv
    transient_min = 30.0
    if "--transient-min" in sys.argv:
        try:
            transient_min = float(sys.argv[sys.argv.index("--transient-min") + 1])
        except (IndexError, ValueError):
            transient_min = 30.0
    # --json 模式 stdout 只出纯 JSON（人读横幅关闭），供自动化直接解析
    say = (lambda *a, **k: None) if as_json else print
    say("== 注册端口在线状态 ==")
    down = []
    registered = []
    up_map = tcp_up_many(sorted(REGISTRY))
    for p in sorted(REGISTRY):
        up = up_map[p]
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
    _prime_proc_names([pid for p, pid in listen.items()
                       if 1024 < p <= 49151 and p not in REGISTRY])
    unknown = []
    transient = []
    for p, pid in sorted(listen.items()):
        if not (1024 < p <= 49151) or p in REGISTRY:
            continue
        name = proc_name(pid)
        if any(w in name.lower() for w in WHITELIST):
            continue
        age = proc_start_age_min(pid)
        if age is not None and age < transient_min:
            transient.append((p, name, age))
            say("  ⚠ %d 由 %s 监听——启动 %.1fm < %.0fm，判瞬态（strict 告警放行）"
                % (p, name, age, transient_min))
            continue
        unknown.append((p, name))
    if unknown:
        for p, name in unknown:
            say("  ⚠ %d 由 %s 监听——我方服务未上注册表！"
                  % (p, name))
        if not strict:
            say("  （非 strict 模式：仅警示；--strict 才 rc=1）")
    else:
        say("  无 ✓（注册表外仅系统/第三方豁免进程）")
    say("== 端口冲突（注册表重复登记 / 分配表重复分配）==")
    conflicts = port_conflicts()
    if conflicts:
        for c in conflicts:
            say("  ⚠ %d %s（%s）" % (c["port"], c["kind"], c["detail"]))
    else:
        say("  无 ✓")
    say("== 汇总：注册 %d 个，离线 %d（%s），未登记 %d，瞬态监听 %d，冲突 %d =="
          % (len(REGISTRY), len(down),
             ",".join(map(str, down)) or "无", len(unknown),
             len(transient), len(conflicts)))
    hard_down = [p for p in down if p not in OPTIONAL_OFFLINE]
    if hard_down:
        say("  （在册服务离线 %d 个，仅提示不判负——strict 只拦未登记/冲突：%s）"
            % (len(hard_down), ",".join(map(str, hard_down))))
    # 2026-10-26 WO-UPGRADE-20261026：strict 只对「未登记监听/端口冲突」失败；
    # 在册但离线永不判负（否则合并门被 20 个按需口天天误红 → 假门）。
    strict_fail = bool(strict and (unknown or conflicts))
    if as_json:
        print(json.dumps({
            "strict": strict,
            "registered": registered,
            "offline": down,
            "hard_offline": hard_down,
            "unknown": [{"port": p, "pid_name": name}
                        for p, name in unknown],
            "unknown_transient": [{"port": p, "pid_name": name,
                                   "age_min": round(age, 1)}
                                  for p, name, age in transient],
            "conflicts": conflicts,
            "summary": {"registered_total": len(REGISTRY),
                        "offline": len(down),
                        "unregistered": len(unknown),
                        "conflicts": len(conflicts)},
            "strict_fail": strict_fail,
            "rc": 1 if strict_fail else 0,
        }, ensure_ascii=False, indent=2))
    if strict_fail:
        say("STRICT FAIL（未登记 %d，端口冲突 %d）"
            % (len(unknown), len(conflicts)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
