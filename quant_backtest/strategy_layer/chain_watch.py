# -*- coding: utf-8 -*-
"""链级看板 + 每日简报（P1 整合：把「失败必告警」升级为全链语义）。

═══ 设计要点 ═══
1. 链定义 CHAIN_DEFS：晨链 / 风控环 / 采集环 / 基础设施四条链，
   每环 (task_name, max_age_hours)；max_age_hours=0 表示常驻型
   （HTTP 探活判健康为主，任务态仅辅助注记；无探活端点的常驻
   任务降级按 State=Running 判）。
2. 状态采集 _ps_query_tasks()：单次 PowerShell 批查全部相关任务
   （State/LastRunTime/LastTaskResult/NextRunTime），模块级 60s 缓存
   —— 8791 请求高频但调度状态变化低频。
   判例对齐：任务名全 ASCII，规避 PS 管道 GBK 搅碎多字节问题；
   LastRunTime 中文区域格式 "2026/10/17 8:19:00" 正则容错解析。
3. 语义：result==0 → ok；267009/267011 → running/never-run（不判
   fail，staleness 单独判）；其余 → fail。LastRunTime 距今超
   max_age_hours → stale。链健康 = worst(steps)：
   FAIL > STALE > OK，unknown（查询缺失）同 FAIL。
4. 每日简报 build_daily_brief()：信号（自调本机 /api/signals）+
   风控（risk_bridge）+ 数据池（自调 /api/pool）+ 链路状态四段，
   产 markdown；/api/brief 优先读当日缓存文件，无则现场生成。

配套：dashboard_api.py 路由 GET /api/chain、/api/brief；
chain_watchdog.py 守链人（计划任务 KgChainWatchdog 08:00/19:40）；
daily_brief.py 简报落盘（计划任务 DailyBrief0745 07:45）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

# ── 链定义（K-STOCK-424 P1）─────────────────────────────────────────
CHAIN_DEFS = {
    "morning": {
        "name": "晨链",
        "sla": "05:50 起四环递进，07:30 出组合信号",
        "steps": [
            ("ChanlunScan", 26),  # 05:50 缠论结构扫描
            ("XmapSync", 26),  # 06:00 映射
            ("KGAnalysis", 26),  # 07:00 图谱分析
            ("StrategyDaily", 26),  # 07:30 多策略编排
        ],
    },
    "risk": {
        "name": "风控环",
        "sla": "19:10 影子日跑；周日 08:19 R3 周批",
        "steps": [
            ("RiskGuardShadowDaily", 26),
            ("RiskGuardR3Weekly", 8 * 24),
        ],
    },
    "ingest": {
        "name": "采集环",
        "sla": "各源日更（tushare/tdx/guba/xueqiu/crypto）",
        "steps": [
            ("TushareSync", 30),
            ("TdxSync", 30),
            ("GubaCrawl", 30),
            ("XueqiuSync", 30),
            ("XueqiuCrawl", 30),
        ],
    },
    "infra": {
        "name": "基础设施",
        "sla": "8791 常驻；Ollama 看门狗 5 分钟；token 6h；权重周日",
        "steps": [
            ("KgDataDashboard", 0),  # 常驻：HTTP探活为主（B4 修复）
            ("OllamaWatchdog", 1),
            ("XueqiuTokenRefresh", 8),
            ("WeightUpdater", 8 * 24),
        ],
    },
}

# 电池豁免监视名单 = 链上全部任务 + 链外但欠账的（K268）；动态查询见
# _ps_query_tasks 第 6 字段（K-STOCK-429：旧版硬编码 BATT_PENDING 在第一轮
# 豁免落地后失真误报，改为实时扫描）。
_BATT_EXTRA = ("HermesWebUI-Guard",)

# 已知外部依赖注记（fail 时的 context，非豁免——告警语义不变）
_STEP_NOTES = {
    "XmapSync": "内网 MySQL 192.168.203.x 需 VPN/跳板，网络不可达时"
    "退出码 3，恢复网络后自动重跑即可（外部依赖降级）",
}

_PS_TIMEOUT = 25  # PowerShell 子进程秒数上限
_CACHE_TTL = 60.0  # 调度状态缓存秒数
_RESULT_OK = {0}
_RESULT_SOFT = {
    267009,  # 任务正在运行
    267011,  # 尚未运行过（新装任务不判 fail）
    267014,
}  # 被终止（历史态，staleness 兜底）
_DT_RE = re.compile(
    r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})[ T]"
    r"(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?"
)
_DASH_PORT = "http://127.0.0.1:8791"
_PORTAL_API = "http://127.0.0.1:8091"  # 门户 API（/api/kg/lineage 血缘体检）

# ── 常驻型 HTTP 探活（B4 修复）───────────────────────────────────────
# 病灶：Windows 计划任务常态是 Ready（仅触发瞬间 Running），以
# State=="Running" 判常驻进程健康 → dashboard 活着却三连误报 DOWN。
# 修法：对服务本体发 HTTP GET，2xx=UP；任务态降为 detail 辅助注记。
# 无端点条目的常驻任务降级沿用旧任务态判定（保持向后兼容）。
# 2026-10-25 K-ORG-20261025-PROBETO：/api/overview 聚合查询实测 2.9s（批量跑批时更慢），
# 4.0s 会让 KgDataDashboard 频繁误判 DOWN（22:00 夜间独占时必现）。放宽到 10s。
_HTTP_PROBE_TIMEOUT = 10.0  # 探活超时秒数（watchdog 调用方容忍窗口内）

_RESIDENT_PROBES = {
    "KgDataDashboard": _DASH_PORT + "/api/overview",
}


def _http_alive(url: str, timeout: float = _HTTP_PROBE_TIMEOUT) -> tuple:
    """GET url 探活 → (alive: bool, detail: str)。

    HTTP 2xx → (True, "HTTP 200")；其余一律 (False, 原因)。
    fail-visible：异常类型+摘要原样回传给调用方展示，绝不静默吞错。
    """
    from urllib.error import HTTPError, URLError  # 局部导入对齐既有风格

    try:
        with safe_urlopen(url, timeout=timeout) as resp:
            code = resp.getcode()
        if 200 <= code < 300:
            return True, "HTTP %d" % code
        return False, "HTTP %d（非 2xx）" % code
    except HTTPError as exc:
        return False, "HTTPError %d: %s" % (exc.code, exc.reason)
    except (URLError, OSError, ValueError) as exc:
        # URLError 承载 DNS/拒绝连接/超时；OSError 兜底 socket 层异常
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:120])


# 简报缓存目录（与交付文档同仓）
BRIEF_DIR = r"C:\aios\zerobound-kg\deliverables\daily_brief"

_cache = {"ts": 0.0, "tasks": None}


# ── 调度状态采集 ─────────────────────────────────────────────────────
def _ps_query_tasks() -> dict:
    """单次 PowerShell 批查链上全部任务（+电池监视名单）→ {name: info}。

    行格式 name|state|lastrun|result|nextrun[|bat]；bat=
    DisallowStartIfOnBatteries（K-STOCK-429 动态电池豁免监视）。
    缺任务不产生行。
    """
    names = sorted(
        {t for ch in CHAIN_DEFS.values() for t, _ in ch["steps"]} | set(_BATT_EXTRA)
    )
    ps_list = ",".join('"%s"' % n for n in names)
    cmd = (
        "Get-ScheduledTask -TaskName @(%s) -ErrorAction SilentlyContinue"
        " | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo;"
        " '{0}|{1}|{2}|{3}|{4}|{5}' -f $_.TaskName,$_.State,"
        "$i.LastRunTime,$i.LastTaskResult,$i.NextRunTime,"
        "$_.Settings.DisallowStartIfOnBatteries }" % ps_list
    )
    try:
        out = (
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_PS_TIMEOUT,
            ).stdout
            or ""
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"__error__": str(exc)[:200]}

    tasks = {}
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) not in (5, 6) or not parts[0]:
            continue
        name, state, lastrun, result, nextrun = parts[:5]
        bat = parts[5].strip().lower() == "true" if len(parts) == 6 else None
        m = _DT_RE.search(lastrun or "")
        last_dt = None
        if m:
            try:
                cand = datetime(*(int(g) for g in m.groups(0)[:6]))
                # Windows 从未运行哨兵日期（1999/1899 等）→ 视为从未运行
                if cand.year >= 2000:
                    last_dt = cand
            except ValueError:
                last_dt = None
        try:
            result_code = int(result)
        except ValueError:
            result_code = -1
        tasks[name] = {
            "state": state,
            "last_run": last_dt.strftime("%Y-%m-%d %H:%M")
            if last_dt
            else "",  # 哨兵日期/空 → 一律「从未运行」
            "_last_dt": last_dt,
            "result": result_code,
            "next_run": (nextrun or "").strip(),
            "bat": bat,
        }
    return tasks


def _tasks_cached() -> dict:
    now = time.time()
    if _cache["tasks"] is None or now - _cache["ts"] > _CACHE_TTL:
        _cache["tasks"] = _ps_query_tasks()
        _cache["ts"] = now
    return _cache["tasks"]


# ── 链健康判定 ────────────────────────────────────────────────────────
def _step_status(step: tuple, tasks: dict) -> dict:
    name, max_age = step
    info = tasks.get(name)
    if info is None:
        return {"task": name, "status": "unknown", "detail": "任务不存在或查询失败"}
    if max_age == 0:  # 常驻型：HTTP 探活为主，任务态仅辅助（B4 修复）
        probe_url = _RESIDENT_PROBES.get(name)
        if probe_url:
            alive, probe_detail = _http_alive(probe_url)
            return {
                "task": name,
                "status": "ok" if alive else "fail",
                "last_run": info["last_run"],
                "result": info["result"],
                "state": info["state"],
                "detail": (
                    "HTTP探活 %s（%s）；任务态 %s"
                    % ("UP" if alive else "DOWN", probe_detail, info["state"])
                ),
            }
        # 降级路径：无探活端点的常驻任务沿用任务态判定
        ok = info["state"] == "Running"
        return {
            "task": name,
            "status": "ok" if ok else "fail",
            "last_run": info["last_run"],
            "result": info["result"],
            "state": info["state"],
            "detail": "常驻进程 %s" % info["state"],
        }
    last_dt = info.get("_last_dt")
    stale = last_dt is None or (datetime.now() - last_dt) > timedelta(hours=max_age)
    if info["result"] in _RESULT_OK:
        status = "stale" if stale else "ok"
    elif info["result"] in _RESULT_SOFT:
        status = "stale" if stale else "ok"
    elif info["result"] in (267008, 267045):
        status = "stale" if stale else "ok"  # 尚未运行/排队中
    else:
        status = "fail"
    return {
        "task": name,
        "status": status,
        "last_run": info["last_run"],
        "result": info["result"],
        "state": info["state"],
        "max_age_h": max_age,
        "note": _STEP_NOTES.get(name),
        "detail": (
            "上次 %s（阈值 %dh）" % (info["last_run"], max_age)
            if info["last_run"]
            else "从未运行（待首跑）"
        ),
    }


_WORST = {"ok": 0, "stale": 1, "fail": 2, "unknown": 2}
_LEVEL_NAME = {0: "ok", 1: "stale", 2: "fail"}  # 独立映射防 key 冲突


def chain_payload() -> tuple:
    """GET /api/chain → (payload, http_code)。链级看板聚合。"""
    tasks = _tasks_cached()
    if "__error__" in tasks:
        return ({"ok": False, "error": "调度查询失败: %s" % tasks["__error__"]}, 503)
    chains = {}
    worst_all = 0
    for key, ch in CHAIN_DEFS.items():
        steps = [_step_status(s, tasks) for s in ch["steps"]]
        worst = max(_WORST[s["status"]] for s in steps)
        worst_all = max(worst_all, worst)
        chains[key] = {
            "name": ch["name"],
            "sla": ch["sla"],
            "health": _LEVEL_NAME[worst],
            "steps": steps,
        }
    batt_pending = sorted(name for name, info in tasks.items() if info.get("bat"))
    return (
        {
            "ok": True,
            "overall": _LEVEL_NAME[worst_all],
            "chains": chains,
            "batt_pending": batt_pending,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
        200,
    )


# ── 每日简报 ─────────────────────────────────────────────────────────
def _http_get_json(url: str, timeout: float = 6.0):

    with safe_urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"), strict=False)


def _brief_markdown() -> str:
    today = time.strftime("%Y%m%d")
    lines = ["# 每日简报 %s" % time.strftime("%Y-%m-%d (%a)"), ""]

    # 1. 链路状态
    chain_payload_, _ = chain_payload()
    lines.append("## 一、链路状态（overall: %s）" % chain_payload_.get("overall"))
    for key, ch in chain_payload_.get("chains", {}).items():
        bad = [s for s in ch["steps"] if s["status"] != "ok"]
        flag = "✅" if ch["health"] == "ok" else "⚠️"
        detail = (
            (
                "异常环: "
                + "; ".join(
                    "%s=%s(%s%s)"
                    % (
                        s["task"],
                        s["status"],
                        s.get("last_run") or s["detail"],
                        ("；注: %s" % s["note"]) if s.get("note") else "",
                    )
                    for s in bad
                )
            )
            if bad
            else ch["sla"]
        )
        lines.append("- %s **%s** [%s]：%s" % (flag, ch["name"], ch["health"], detail))
    lines.append("")

    # 2. 组合信号
    try:
        sig = _http_get_json("%s/api/signals?date=%s" % (_DASH_PORT, today))
        lines.append(
            "## 二、组合信号（%s）：%d 条"
            % (sig.get("trade_date"), sig.get("count", 0))
        )
        for s in (sig.get("signals") or [])[:10]:
            lines.append(
                "- %s %s score=%.3f conf=%.2f%s"
                % (
                    s["symbol"],
                    s["action"],
                    s["score"],
                    s["confidence"],
                    " [分歧]" if s.get("divergence") else "",
                )
            )
        if sig.get("count", 0) > 10:
            lines.append("- …共 %d 条（详见 /api/signals）" % sig["count"])
    except Exception as exc:  # noqa: BLE001
        lines.append("## 二、组合信号：获取失败（%s）" % str(exc)[:100])
    lines.append("")

    # 3. 风控水位
    try:
        from strategy_layer.risk_bridge import risk_context

        rk = risk_context()
        if rk.get("available"):
            lines.append("## 三、风控水位（RiskGuard 影子）")
            for rid, r in sorted(rk.get("rules", {}).items()):
                gate = r.get("market_gate")
                lines.append(
                    "- %s: pass=%d block=%d 最新score=%s%s"
                    % (
                        rid,
                        r.get("pass", 0),
                        r.get("block", 0),
                        r.get("last_score"),
                        " | 市场级闸[%s]" % gate.get("action") if gate else "",
                    )
                )
        else:
            lines.append("## 三、风控水位：影子数据暂不可用")
    except Exception as exc:  # noqa: BLE001
        lines.append("## 三、风控水位：获取失败（%s）" % str(exc)[:100])
    lines.append("")

    # 4. 数据池健康
    try:
        pool = _http_get_json("%s/api/pool" % _DASH_PORT)
        subs = pool.get("submodules") or []
        fails = []
        for sm in subs:
            for c in sm.get("collectors", []):
                if str(c.get("result_kind", "")) != "success":
                    fails.append(
                        "%s/%s(%s)"
                        % (
                            sm.get("key"),
                            c.get("name"),
                            c.get("result_kind") or "unknown",
                        )
                    )
        lines.append(
            "## 四、数据池健康：%d 子模块，异常采集器 %d 个" % (len(subs), len(fails))
        )
        if fails:
            lines.append("- 异常: %s" % ", ".join(fails[:12]))
    except Exception as exc:  # noqa: BLE001
        lines.append("## 四、数据池健康：获取失败（%s）" % str(exc)[:100])

    # 5. 数据血缘体检（管道断链亮牌；来源=门户 8091 /api/kg/lineage，
    #    与情报板块 /lineage.html 同一数据面。fail-visible：一行报不可达，
    #    血缘亮牌吞错=假绿。断链>0 才展开明细，健康时一行带过压简报长度）
    try:
        lin = _http_get_json("%s/api/kg/lineage?view=breaks" % _PORTAL_API)
        d = lin.get("data") or {}
        broken = d.get("broken") or []
        # P1-A 新鲜度维度：API 附 freshness 汇总时加报 stale（沉默断链：
        # status=ok 但数据超过 cadence+grace 交易日未更新，管道绿数据馊）
        fr = d.get("freshness") or {}
        stale = fr.get("stale") or []
        if broken or stale:
            lines.append(
                "## 五、数据血缘体检：%d 表在册，断链 %d，陈旧 %d"
                % (d.get("tracked_tables", 0), len(broken), len(stale))
            )
        else:
            lines.append(
                "## 五、数据血缘体检：%d 表在册，断链 %d"
                % (d.get("tracked_tables", 0), len(broken))
            )
        for r in broken[:6]:
            lines.append(
                "- 🚨 %s [%s]（%s/%s @ %s）"
                % (
                    r.get("output_table"),
                    r.get("status"),
                    r.get("job"),
                    r.get("step"),
                    (r.get("started_at") or "")[:19],
                )
            )
        if len(broken) > 6:
            lines.append("- …共 %d 张断链（详见 /lineage.html）" % len(broken))
        for r in stale[:4]:
            lines.append(
                "- 🟡 陈旧 %s（已 %s 个交易日未更新）"
                % (r.get("table"), r.get("age_trade_days", "?"))
            )
        if len(stale) > 4:
            lines.append("- …共 %d 张陈旧（详见 /lineage.html）" % len(stale))
    except Exception as exc:  # noqa: BLE001
        lines.append("## 五、数据血缘体检：不可达（%s）" % str(exc)[:80])

    lines.append("")
    lines.append(
        "> 生成时间 %s | 通道: 8791 仪表盘（Hermes 推送待重启挂载）"
        % time.strftime("%Y-%m-%d %H:%M:%S")
    )
    return "\n".join(lines)


def brief_payload(force: bool = False) -> tuple:
    """GET /api/brief → 当日简报 markdown。优先读缓存文件。"""
    today = time.strftime("%Y%m%d")
    path = os.path.join(BRIEF_DIR, "brief_%s.md" % today)
    text = None
    if not force and os.path.isfile(path):
        mtime = os.path.getmtime(path)
        if time.time() - mtime < 12 * 3600:  # 当日 12h 内视为有效
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                text = None
    if text is None:
        try:
            text = _brief_markdown()
            os.makedirs(BRIEF_DIR, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)  # 原子落盘（K-STOCK-419 判例）
        except Exception as exc:  # noqa: BLE001
            return ({"ok": False, "error": str(exc)[:200]}, 503)
    return (
        {"ok": True, "date": today, "cached": os.path.isfile(path), "brief": text},
        200,
    )


def build_daily_brief_markdown() -> str:
    """每日简报 markdown 生成（供 daily_brief.py 计划任务复用）。"""
    return _brief_markdown()


__all__ = ["chain_payload", "brief_payload", "CHAIN_DEFS", "build_daily_brief_markdown"]
