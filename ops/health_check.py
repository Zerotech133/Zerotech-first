# -*- coding: utf-8 -*-
"""OS 系统自检：服务 / 容器 / 计划任务 / 数据链 四层健康检查。

╔══════════════════════════════════════════════════════════════════╗
║ ⚠️ 退役阀（R2 陈旧数据治理，联动层一期 2026-10-18）                ║
║ 本脚本已退役：健康快照无调度者=陈旧第二本账（K-STOCK-349）。       ║
║ 后继唯一事实源：GET http://127.0.0.1:8091/api/execution/health    ║
║ （portal/api_execution.py 执行健康总线，manifest 白名单只读投影）。║
║ 手工拉起默认拒绝（exit 2）；确需临时复活传 --force（结果不再落盘）。║
╚══════════════════════════════════════════════════════════════════╝

用法:
    python health_check.py            # 打印退役说明并 exit 2（防复活第二本账）
    python health_check.py --force    # 旧四层检查照旧跑（不落盘，仅人工诊断）

历史用法（--force 下仍有效）:
    python health_check.py --json   # 仅输出一行 JSON（供统一日报聚合）

结果**不再**落盘 ops/health_latest.json（该文件已重写为弃用标记 JSON）。
"""
import json
import os
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
# 兼容 ``python ops/health_check.py`` 直接调用：此时 sys.path 默认只有 ops/。
if STATION not in sys.path:
    sys.path.insert(0, STATION)

from ollama_service.health import HealthCheckConfig, check_ollama_health

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _j(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _http_ok(url, timeout=4):
    try:
        return urllib.request.urlopen(url, timeout=timeout).status == 200
    except Exception:
        return False


def _tcp_ok(port, timeout=3):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _jev_py_ok():
    """Jev 四件套解释器巡检：在跑的必须全为产线 3.11（双 Python 治理护栏，
    防裸 python 调用静默回归到 PATH 的用户 3.12）。

    P1-4 fail-explicit：巡检自身故障不再静默返回 True（假绿），
    改为返回显式 "checker-error" 状态，由上层落盘标注、不中断其他检查。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object {$_.CommandLine -match 'jev_gate|jev_qd_bridge|"
             "signal_review_gate|lark_card_handler'}).ExecutablePath"],
            capture_output=True, text=True, timeout=15)
        paths = [l.strip() for l in r.stdout.splitlines()
                 if l.strip().lower().endswith("python.exe")]
        return bool(paths) and all("Python311" in p for p in paths)
    except Exception:
        return "checker-error"  # P1-4: 巡检自身故障显式标注（fail-explicit）


def check_junction():
    """K267 治理（方案B加固 2026-10-08）：C:\aios 必须是指向工作站的 junction。

    C:\aios 为 NTFS junction（fsutil Tag 0xa0000003，约 2026-09-11 建），
    指向 C:\项目\老田的AI助手——C 盘全部脚本路径依赖它。E 盘换盘符/移盘/
    junction 误删 → C 盘脚本批量静默瘫痪，巡检需分钟级发现。
    依据: docs/ops/2026-10-08_Jev双副本治理方案.md 方案B第2步。"""
    # K-STOCK-098 #4 修探针：原实现拿 realpath(C:/aios)（解析结果=
    # C://项目//老田的AI助手）与 STATION（=C://aios，本脚本目录推导）比较，
    # junction 越健康越恒假。改为与既定目标比对 +「既是 reparse point
    # 又能解析到目标」双条件。
    # K-STOCK-414：换机后 junction 目标已变（旧机 C:\项目\老田的AI助手 →
    # 本机 C:\项目\老田的AI助手），期望值不再硬编码——junction 只要
    # 解析到「本仓库实际所在（STATION 的真实路径）」且 portal 在位即健康。
    expected = os.path.realpath(STATION).lower()
    try:
        real = os.path.realpath("C:/aios").lower()
        return real == expected and os.path.isdir("C:/aios/portal")
    except OSError:
        return False


def _check_ollama_service():
    """复用 Ollama 专用探针，并保留服务层需要的布尔结果与失败原因。"""
    try:
        report = check_ollama_health(HealthCheckConfig())
        http_result = report.checks.get("ollama_http")
    except Exception as exc:
        return False, "Ollama 健康探针异常（%s）" % str(exc)[:160]
    if http_result is None:
        return False, "Ollama 健康探针未返回 HTTP 检查结果"
    return bool(http_result.ok), http_result.message


def check_services(failure_messages=None):
    """检查服务；可选地收集带上下文的失败原因，不改变原返回结构。"""
    ollama_ok, ollama_message = _check_ollama_service()
    if failure_messages is not None and not ollama_ok:
        failure_messages["Ollama(11434)"] = ollama_message
    return {
        "门户API(8091)": _http_ok("http://127.0.0.1:8091/health") or _tcp_ok(8091),
        "nginx(8080)": _http_ok("http://127.0.0.1:8080/"),
        "量化前端(5175)": _http_ok("http://127.0.0.1:5175/"),
        "QD桥接(9103)": _tcp_ok(9103),
        "卡片回调(9104)": _tcp_ok(9104),
        "Jev解释器(3.11)": _jev_py_ok(),
        # K267 盲区治理（2026-10-08）：20+ AI 模块依赖 Ollama，但此前服务层
        # 无探针——实例死掉而任务 rc 恰好良性时会「全绿假象」
        #（根因见 docs/ops/2026-10-08_Ollama任务根因排查.md §三）。
        "Ollama(11434)": ollama_ok,
    }


def check_docker():
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            return None
        names = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        return {"total": len(names),
                "ai_portal": "ai-portal" in names}
    except Exception:
        return None


# 0=成功 267009=0x41301运行中 267011=0x41303尚未跑 267014=0x41306被终止
# 2147946720=0x800710E0 周期重入被拒（门户API常驻任务，无害）
# 2147943467=0x8007042B ERROR_PROCESS_ABORTED：A5 良性豁免——系统关机/重启时
#   运行中的计划任务被被动终止留下的结果码，语义=「上次是被动终止」而非业务故障。
#   定性依据：docs/runbooks/2026-10-07_subpusheod-rc1排查与处置.md §5——10-06 停机
#   与 ChanlunRefill / RtdxRtMinPull / _sentinelEOD / _sentinelKgTdx / Git for
#   Windows Updater 五任务同码时间吻合（含长时等待哨兵进程被杀）。豁免按 rc 码
#   而非按任务名（Git Updater 等系统自带任务不宜点名）。
_BENIGN_RC = {0, 267009, 267011, 267014, 2147946720, 2147943467,
              3221225786}  # +0xC000013A 关机/CTRL_C 被动终止（K-STOCK-098 #4：CryptoSync/RiskGuardR3Weekly 同类，非业务故障）
# A5: 命中后仍保留可见性的良性码子集——不进 bad、不触发 exit 1，
# 但在 issues 记一条 [info] 级（区别于静默豁免的 0/0x41301 等）。
_BENIGN_RC_INFO = {2147943467, 3221225786}
_BENIGN_RC_INFO_NOTE = "关机被动终止(0x8007042B)，非业务故障，定性见 runbook 2026-10-07 §5"
# 系统自带噪音任务（OneDrive 更新器等，与本站无关）
_IGNORE_NAME = ("OneDrive",)


def check_tasks():
    # K-STOCK-098 #4 加固：PS 输出强制 UTF-8 + 读侧 errors=replace。
    # 此前 text=True 跟随解释器 locale/UTF-8 模式，PS 管道输出按 GBK 写、
    # UTF-8 读 → reader 线程 UnicodeDecodeError → 任务清单静默变 0 条
    #（20:23 实测翻车，快照 tasks.total=0 看似「无任务」实为探针残废）。
    ps = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
          "Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\\Microsoft*' } | "
          "ForEach-Object { $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue; "
          "'{0}|{1}|{2}|{3}' -f $_.TaskName, $_.State, $i.LastTaskResult, "
          "$i.LastRunTime.ToString('yyyy-MM-dd HH:mm') }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        lines = [l.strip() for l in (r.stdout or "").splitlines() if "|" in l]
    except Exception:
        return {"total": 0, "bad": [{"name": "查询失败", "rc": -1}],
                "benign": [], "stale": []}
    total, bad, benign, stale = 0, [], [], []
    now = datetime.now()
    for line in lines:
        total += 1
        try:
            name, state, rc_s, last_s = line.split("|", 3)
        except ValueError:
            continue  # 格式异常行跳过（正常输出恒为 4 段）
        if state.strip() == "Disabled":
            continue
        if name.strip().startswith(_IGNORE_NAME):
            continue
        # C5: 每日类任务陈旧度（在 rc 判定之前——「跑了失败」由 rc 报，
        # 「没跑」由陈旧度报，两者可并存）
        if name.strip() in _DAILY_TASKS:
            if _is_stale(last_s, _log_mtime_ts(name.strip()), now):
                stale.append(name.strip())
        try:
            rc = int(rc_s.strip())
        except (ValueError, AttributeError):
            rc = "unknown"  # P1-4: 查询不到结果码显式标注 unknown，不按健康处理
        if rc == "unknown" or rc not in _BENIGN_RC:
            bad.append({"name": name.strip(), "rc": rc})
        elif rc in _BENIGN_RC_INFO:
            benign.append({"name": name.strip(), "rc": rc})  # A5: 良性但保可见性
    return {"total": total, "bad": bad, "benign": benign, "stale": stale}


def _rc_str(rc):
    """统一格式化任务结果码（兼容 unknown 字符串态）。"""
    if rc == "unknown":
        return "unknown"
    return "0x%X" % (rc & 0xFFFFFFFF)


# ---------- C5 任务陈旧度：区分「没跑」与「跑了失败」 ----------
# 阈值 26h = 每日任务错过一个完整调度日 + 2h 余量（runbook 2026-10-07 §4.3）。
_STALE_MAX_HOURS = 26
# 每日/EOD 类任务白名单（只对这些任务查陈旧度，避免一次性任务
# 如 RtdxRtMinPull 完成后长期未跑被误报）。值 = 该任务日志文件路径
# （None = 未登记日志，仅靠 LastRunTime 数据源）。发现新每日任务时在此登记。
_DAILY_TASKS = {
    "SubPushEod": os.path.join(STATION, "quant_backtest", "logs", "subscribe_task.out"),
    "MorningReport": None,
    "TushareSync": r"C:\Temp\tushare_sync_out.log",
    "ChanlunScan": None,  # 2026-11-05：原 ChanlunRefill 06:30 已由 \ChanlunScan 07:20 接管
    "RpsDaily": None,
    "WeakDailyHealth": None,
    "AI工作站每日备份": None,
}


def _last_run_ts(last_s):
    """数据源①：LastRunTime（check_tasks 的 PowerShell 查询附带，yyyy-MM-dd HH:mm）。
    说明：本开发沙箱拦截 Get-ScheduledTask 无法实机验证读取，但产线每日 03:00
    同一管道读 LastTaskResult 正常，同一 CimInstance 会话取 LastRunTime 风险低。"""
    try:
        return datetime.strptime(last_s.strip(), "%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return None


def _log_mtime_ts(name):
    """数据源②：任务日志文件 mtime 兜底（LastRunTime 读不到时使用，
    仅对 _DAILY_TASKS 里登记了日志路径的任务生效）。"""
    log = _DAILY_TASKS.get(name)
    if not log:
        return None
    try:
        return datetime.fromtimestamp(os.path.getmtime(log))
    except OSError:
        return None


def _is_stale(last_s, log_ts, now):
    """陈旧判定。防炸：两个数据源均不可得 → 跳过不报（陈旧度检查自身
    故障不产生误报）；LastRunTime 为 MinValue(0001 年) 视为从未跑 → stale。"""
    ts = _last_run_ts(last_s) or log_ts
    if ts is None:
        return False
    if ts.year < 2000:
        return True
    return (now - ts).total_seconds() > _STALE_MAX_HOURS * 3600


def check_data():
    sec = _j(os.path.join(STATION, "news_collector", "sectors.json")) or {}
    led = _j(os.path.join(STATION, "quant_backtest", "positions.json")) or {}
    st = _j(os.path.join(STATION, "news_collector", "status.json")) or {}
    snap_dir = os.path.join(STATION, "quant_backtest", "reports", "snap")
    snap = sorted(os.listdir(snap_dir))[-1] if os.path.isdir(snap_dir) else "NONE"
    return {
        "news_24h": st.get("news_24h"),
        "sectors_count": sec.get("count", 0),
        "sectors_ts": sec.get("ts"),
        "pending": len(led.get("pending") or []),
        "open": len(led.get("open") or []),
        "closed": len(led.get("closed") or []),
        "latest_snapshot": snap,
    }


def run():
    result = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M")}
    issues = []

    service_failures = {}
    result["services"] = check_services(service_failures)
    for name, ok in result["services"].items():
        if ok == "checker-error":
            issues.append("检查器故障(degraded): %s" % name)
        elif not ok:
            detail = service_failures.get(name)
            if detail:
                issues.append("服务下线: %s（%s）" % (name, detail))
            else:
                issues.append("服务下线: %s" % name)

    result["junction_c_aios"] = check_junction()
    if not result["junction_c_aios"]:
        issues.append("C:\\aios junction 失效: 应指向工作站根目录，"
                      "C 盘全部脚本路径将批量瘫痪（治理方案B第2步）")

    result["docker"] = check_docker()
    if result["docker"] and not result["docker"].get("ai_portal"):
        issues.append("ai-portal 容器未运行")

    result["tasks"] = check_tasks()
    for b in result["tasks"]["bad"]:
        if b["rc"] == "unknown":
            issues.append("任务 %s RC=unknown（结果码未获取，需人工确认）" % b["name"])
        else:
            issues.append("任务 %s RC=0x%X" % (b["name"], b["rc"] & 0xFFFFFFFF))
    # A5: 良性豁免任务保留 info 级可见性（不触发 exit 1，见 main 的 [info] 过滤）
    for bn in result["tasks"].get("benign", []):
        issues.append("[info] 任务 %s RC=%s（%s）" % (
            bn["name"], _rc_str(bn["rc"]), _BENIGN_RC_INFO_NOTE))
    # C5: 陈旧度——「没跑」与「跑了失败」区分（后者由 rc 上报，前者在此）
    for s in result["tasks"].get("stale", []):
        issues.append("任务陈旧: %s 超过 %dh 未跑（没跑≠失败，需查调度/停机）" % (
            s, _STALE_MAX_HOURS))

    result["data"] = check_data()
    result["issues"] = issues

    # R2 退役（2026-10-18）：不再落盘 health_latest.json——该文件已重写为
    # 弃用标记 JSON（deprecated:true + successor），任何写入都会把弃用标记
    # 覆盖回陈旧快照，复活第二本账。portal/data/health.json 副本同步停发。

    return result


def main():
    # R2 退役阀（联动层一期 2026-10-18）：防旧入口被手工拉起复活第二本账。
    # 默认拒绝执行（exit 2，N1/人工可捕获）；--force 才放行旧四层检查，
    # 且不再落盘 health_latest.json（已重写为弃用标记 JSON）。
    if "--force" not in sys.argv:
        print("== ops/health_check.py 已退役（R2 陈旧数据治理 2026-10-18）==")
        print("   健康快照无调度者=陈旧第二本账（K-STOCK-349）。")
        print("   后继唯一事实源: GET http://127.0.0.1:8091/api/execution/health")
        print("   （portal/api_execution.py 执行健康总线）")
        print("   确需临时复活请显式传 --force（结果不再落盘）。")
        sys.exit(2)
    h = run()
    if "--json" in sys.argv:
        print(json.dumps(h, ensure_ascii=False))
        return
    print("== OS 系统自检 %s ==" % h["ts"])
    for name, ok in h["services"].items():
        if ok == "checker-error":
            mark = "? checker-error"
        elif ok:
            mark = "✓"
        else:
            mark = "✗ 下线"
        print("  服务 %-14s %s" % (name, mark))
    dk = h["docker"] or {}
    print("  容器 total=%s ai_portal=%s" % (dk.get("total", "?"), dk.get("ai_portal")))
    print("  计划任务 total=%d 异常=%d" % (h["tasks"]["total"], len(h["tasks"]["bad"])))
    for b in h["tasks"]["bad"]:
        print("    ✗ %s RC=%s" % (b["name"], _rc_str(b["rc"])))
    for bn in h["tasks"].get("benign", []):
        print("    ~ %s RC=%s（良性豁免）" % (bn["name"], _rc_str(bn["rc"])))
    for s in h["tasks"].get("stale", []):
        print("    ⏰ %s 超 %dh 未跑（陈旧）" % (s, _STALE_MAX_HOURS))
    d = h["data"]
    print("  数据: 24h新闻=%s 板块=%s 待买/持/平=%d/%d/%d 快照=%s" % (
        d["news_24h"], d["sectors_count"], d["pending"], d["open"],
        d["closed"], d["latest_snapshot"]))
    # A5: [info] 级 issues 仅作可见性，不计入异常数、不触发 exit 1
    real_issues = [i for i in h["issues"] if not i.startswith("[info] ")]
    if real_issues:
        print("!! 发现 %d 个异常（另有 %d 条 info）" % (
            len(real_issues), len(h["issues"]) - len(real_issues)))
        sys.exit(1)
    print("全绿")


if __name__ == "__main__":
    main()
