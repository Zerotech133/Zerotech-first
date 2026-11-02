"""M8-1 试运行前置验收（只读）：一键检查「能不能开始试运行」。

检查项（全部只读，不触发任务、不写 DB、不改任务计划）：
  1. 四个端口/服务：5175（Vite dev）、8105（Zhice API）、8787（Hermes）、8734（门户唯一入口）
  2. Windows 计划任务 ZhiceAcad1Pool：状态 / 上次运行 / 下次运行 / 上次结果
  3. 任务运行态 job status：C:\\Temp\\zhice_job_status.json + GET /api/v1/system/job-status
  4. 最新池日：v2_selection_pool / v2_selection_report / v2_acad1_pool 三类锚点
  5. 四市场（CN/HK/US/CRYPTO）报告 / 选股池 / ACAD1 池覆盖
  6. 复用 scripts/trial_daily_check.py 的 7 项检查（reports_ge_10 / four_markets /
     acad1_total_le_30 / acad1_cn_hk_priority / acad1_all_markets / feedback_ge_1 / supervision_ge_1）

输出：
  C:\\Temp\\trial_preflight.json（机器可读全量）
  C:\\Temp\\trial_preflight.md（人工复核摘要，明确列出通过/缺口，不伪造）

用法（需 py312 环境：zhice_os_v2 editable 安装）：
  C:\\项目\\py312\\python.exe scripts\\trial_preflight.py
  C:\\项目\\py312\\python.exe scripts\\trial_preflight.py --date 2026-10-31
  C:\\项目\\py312\\python.exe scripts\\trial_preflight.py --date 2026-10-31 --json-out C:\\Temp\\x.json

退出码：0 = READY（关键项全通过，仅剩试运行期才产生的待办）；2 = 有硬缺口 NOT_READY；1 = 脚本自身错误。
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import socket
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE / "src"))
sys.path.insert(0, str(SCRIPTS))

OUT_JSON = Path(r"C:\Temp\trial_preflight.json")
OUT_MD = Path(r"C:\Temp\trial_preflight.md")
JOB_STATUS_FILE = Path(r"C:\Temp\zhice_job_status.json")
TRIAL_START = date(2026, 11, 2)   # P1 三天试运行 11-02 ~ 11-04
TRIAL_END = date(2026, 11, 4)
REPORT_FRESH_DAYS = 3             # 与 trial_daily_check 的 report_fresh_strategies 口径一致
CST = timezone(timedelta(hours=8))
SCHEDULED_TASK = "ZhiceAcad1Pool"
SUPPLEMENTARY_TASKS = ["ZhiceAPI8105Guard", "ZhiceObsCalc"]

SERVICES = [
    {"name": "5175", "label": "Vite dev（前端）", "port": 5175,
     "urls": ["http://127.0.0.1:5175/"]},
    {"name": "8105", "label": "Zhice API", "port": 8105,
     "urls": ["http://127.0.0.1:8105/api/v1/health"]},
    {"name": "8787", "label": "Hermes WebUI", "port": 8787,
     "urls": ["http://127.0.0.1:8787/health"]},
    {"name": "8734", "label": "门户唯一入口（nginx）", "port": 8734,
     "urls": ["http://127.0.0.1:8734/"]},
]

DISCLAIMER = (
    "只读验收：仅 TCP/HTTP 探测、只读文件、只读 SQL(SELECT)、计划任务查询；"
    "不触发任何任务、不写 DB、不改任务计划/配置；缺口如实列出，不伪造通过。"
)


# --------------------------------------------------------------------------- #
# 基础探测
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def tcp_connect(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_probe(url: str, timeout: float = 5.0) -> dict:
    started = time.time()
    try:
        req = urllib.request.Request(url, method="GET", headers={"Cache-Control": "no-store"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(400)
            return {
                "url": url,
                "ok": 200 <= resp.status < 400,
                "status": resp.status,
                "latency_ms": round((time.time() - started) * 1000),
                "body_head": raw.decode("utf-8", "replace")[:200],
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        raw = b""
        try:
            raw = exc.read(400)
        except Exception:
            pass
        return {
            "url": url,
            "ok": 200 <= exc.code < 400,
            "status": exc.code,
            "latency_ms": round((time.time() - started) * 1000),
            "body_head": raw.decode("utf-8", "replace")[:200],
            "error": f"HTTP {exc.code}",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "url": url,
            "ok": False,
            "status": 0,
            "latency_ms": round((time.time() - started) * 1000),
            "body_head": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def probe_service(svc: dict) -> dict:
    tcp_ok = tcp_connect(svc["port"])
    http = None
    url = ""
    for candidate in svc["urls"]:
        http = http_probe(candidate)
        url = candidate
        if http["ok"]:
            break
    ok = bool(tcp_ok and http and http["ok"])
    detail = ""
    if not tcp_ok:
        detail = f"TCP 127.0.0.1:{svc['port']} 未监听"
    elif not http or not http["ok"]:
        detail = f"HTTP {url} 异常：{(http or {}).get('error', '')}"
    else:
        detail = f"HTTP {url} -> {http['status']}（{http['latency_ms']}ms）"
    return {
        "name": svc["name"],
        "label": svc["label"],
        "port": svc["port"],
        "tcp_ok": tcp_ok,
        "http": http,
        "ok": ok,
        "detail": detail,
    }


# --------------------------------------------------------------------------- #
# Windows 计划任务（只读查询）
# --------------------------------------------------------------------------- #
def powershell_json(script: str, timeout: float = 40.0) -> tuple[dict | None, str]:
    """执行 PowerShell 片段并解析 JSON；只用于只读查询。"""
    ps = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + script,
    ]
    try:
        proc = subprocess.run(ps, capture_output=True, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    out = (proc.stdout or b"").decode("utf-8", "replace").strip()
    err = (proc.stderr or b"").decode("utf-8", "replace").strip()
    if not out:
        return None, (err or "empty output")[:400]
    try:
        return json.loads(out), ""
    except json.JSONDecodeError:
        return None, out[:400]


def _ps_task_query(names: list[str]) -> str:
    names_ps = ",".join("'{}'".format(n.replace("'", "''")) for n in names)
    return (
        f"$names=@({names_ps}); $res=@(); "
        "foreach($n in $names){ "
        "  $t=Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue; "
        "  if(-not $t){ $res += [pscustomobject]@{task=$n; found=$false}; continue } "
        "  $i=Get-ScheduledTaskInfo -TaskName $t.TaskName -TaskPath $t.TaskPath -ErrorAction SilentlyContinue; "
        "  $res += [pscustomobject]@{ task=$t.TaskName; found=$true; path=$t.TaskPath; "
        "    state=[string]$t.State; "
        "    last_run= if($i -and $i.LastRunTime){$i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')}else{''}; "
        "    next_run= if($i -and $i.NextRunTime){$i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')}else{''}; "
        "    last_result= if($i){$i.LastTaskResult}else{$null} } }; "
        "$res | ConvertTo-Json -Depth 4"
    )


def scheduled_task_facts() -> dict:
    names = [SCHEDULED_TASK] + SUPPLEMENTARY_TASKS
    data, err = powershell_json(_ps_task_query(names))
    tasks: dict[str, dict] = {}
    if isinstance(data, dict):
        data = [data]
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("task"):
                tasks[str(item["task"])] = item
    primary = tasks.get(SCHEDULED_TASK) or {"task": SCHEDULED_TASK, "found": False}
    others = [tasks[n] for n in SUPPLEMENTARY_TASKS if n in tasks]
    return {"query_error": err, "primary": primary, "others": others}


# --------------------------------------------------------------------------- #
# job status（文件 + 只读 API 双源对账）
# --------------------------------------------------------------------------- #
JOB_STATUS_FIELDS = ("run_id", "status", "source", "scheduled_at", "started_at",
                     "finished_at", "data_date", "rc")


def parse_dt(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CST)
    return parsed.astimezone(CST)


def http_json(url: str, timeout: float = 6.0) -> tuple[dict, dict]:
    """GET 一个只读 JSON 接口，返回 (probe, body)。probe 结构同 http_probe。"""
    probe = http_probe(url, timeout=timeout)
    body: dict = {}
    if probe["ok"]:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                raw = resp.read(2 * 1024 * 1024)
            parsed = json.loads(raw.decode("utf-8", "replace"))
            if isinstance(parsed, dict):
                body = parsed
        except Exception:  # noqa: BLE001
            body = {}
    return probe, body


def job_status_facts() -> dict:
    file_info: dict = {"path": str(JOB_STATUS_FILE), "exists": JOB_STATUS_FILE.is_file()}
    payload: dict = {}
    parse_error = ""
    if file_info["exists"]:
        try:
            raw = json.loads(JOB_STATUS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload = raw
            else:
                parse_error = "顶层不是 object"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            parse_error = f"{type(exc).__name__}: {exc}"
        file_info["mtime"] = datetime.fromtimestamp(
            JOB_STATUS_FILE.stat().st_mtime, CST).isoformat(timespec="seconds")
        file_info["bytes"] = JOB_STATUS_FILE.stat().st_size
    file_info["fields"] = {k: payload.get(k) for k in JOB_STATUS_FIELDS}
    file_info["steps"] = len(payload.get("steps") or []) if isinstance(payload, dict) else 0
    file_info["parse_error"] = parse_error

    api, api_body = http_json("http://127.0.0.1:8105/api/v1/system/job-status", timeout=6)
    api["fields"] = {k: api_body.get(k) for k in JOB_STATUS_FIELDS}
    api["reported"] = bool(api_body.get("reported"))
    api["reported_at"] = api_body.get("reported_at")

    finished = parse_dt(file_info["fields"].get("finished_at"))
    fresh_hours = None
    if finished:
        fresh_hours = round((datetime.now(CST) - finished).total_seconds() / 3600.0, 1)
    return {"file": file_info, "api": api, "fresh_hours": fresh_hours}


# --------------------------------------------------------------------------- #
# 只读 SQL：池日 / 四市场
# --------------------------------------------------------------------------- #
def _db_engine():
    import sqlalchemy as sa

    from zhice_os_v2.config.settings import get_settings

    url = get_settings().db_main_url
    return sa.create_engine(url)


def db_facts(day: date | None) -> dict:
    import sqlalchemy as sa

    facts: dict = {"error": "", "resolved_day": str(day) if day else None}
    try:
        engine = _db_engine()
        with engine.connect() as conn:
            latest_pool = conn.execute(sa.text("SELECT max(pool_date) FROM v2_selection_pool")).scalar()
            latest_report = conn.execute(sa.text("SELECT max(report_date) FROM v2_selection_report")).scalar()
            latest_acad1 = conn.execute(sa.text("SELECT max(pool_date) FROM v2_acad1_pool")).scalar()
            resolved = day or latest_pool
            facts.update({
                "latest_pool_day": str(latest_pool) if latest_pool else None,
                "latest_report_day": str(latest_report) if latest_report else None,
                "latest_acad1_day": str(latest_acad1) if latest_acad1 else None,
                "resolved_day": str(resolved) if resolved else None,
            })
            if resolved is None:
                return facts
            report_day = conn.execute(sa.text(
                "SELECT market, count(*) AS n FROM v2_selection_report "
                "WHERE report_date=:d GROUP BY market ORDER BY market"), {"d": resolved}).all()
            latest_report_by_market = conn.execute(sa.text(
                "SELECT market, max(report_date) AS latest FROM v2_selection_report "
                "GROUP BY market ORDER BY market")).all()
            fresh_from = resolved - timedelta(days=REPORT_FRESH_DAYS)
            fresh_report_by_market = conn.execute(sa.text(
                "SELECT market, count(*) AS n, count(DISTINCT strategy_id) AS strategies "
                "FROM v2_selection_report WHERE report_date >= :d GROUP BY market ORDER BY market"),
                {"d": fresh_from}).all()
            pool = conn.execute(sa.text(
                "SELECT market, count(*) AS n FROM v2_selection_pool "
                "WHERE pool_date=:d GROUP BY market ORDER BY market"), {"d": resolved}).all()
            acad1 = conn.execute(sa.text(
                "SELECT market, count(*) AS n FROM v2_acad1_pool "
                "WHERE pool_date=:d GROUP BY market ORDER BY market"), {"d": resolved}).all()
            cards = conn.execute(sa.text(
                "SELECT status, count(*) AS n FROM v2_stock_research "
                "WHERE pool_date=:d GROUP BY status"), {"d": resolved}).all()
            observations = conn.execute(sa.text(
                "SELECT status, count(*) AS n FROM v2_strategy_observation "
                "WHERE signal_date=:d GROUP BY status"), {"d": resolved}).all()
        facts.update({
            "report_on_day": {m: n for m, n in report_day},
            "report_latest_by_market": {
                m: (str(d) if d else None) for m, d in latest_report_by_market},
            "report_fresh_since": str(fresh_from),
            "report_fresh_by_market": {
                m: {"n": n, "strategies": s} for m, n, s in fresh_report_by_market},
            "pool": {m: n for m, n in pool},
            "acad1": {m: n for m, n in acad1},
            "cards": {s: n for s, n in cards},
            "observations": {s: n for s, n in observations},
        })
    except Exception as exc:  # noqa: BLE001
        facts["error"] = f"{type(exc).__name__}: {exc}"
        facts["traceback_tail"] = traceback.format_exc().splitlines()[-3:]
    return facts


# --------------------------------------------------------------------------- #
# 复用 trial_daily_check 的 7 项检查
# --------------------------------------------------------------------------- #
DAILY_CHECK_LABELS = {
    "reports_ge_10": "近 3 日报告覆盖策略数 ≥ 10",
    "four_markets": "选股池四市场齐全（CN/HK/US/CRYPTO）",
    "acad1_total_le_30": "ACAD1 池总数 ≤ 30（日复核上限）",
    "acad1_cn_hk_priority": "ACAD1 CN ≥ 10 且 HK ≥ 8（配额优先级）",
    "acad1_all_markets": "ACAD1 池四市场齐全",
    "feedback_ge_1": "当日反馈 ≥ 1 条",
    "supervision_ge_1": "当日督导 ≥ 1 条",
}
# 试运行开始前必然为 0 的检查（试运行期间产生数据后才应通过）
TRIAL_ACTIVITY_CHECKS = {"feedback_ge_1", "supervision_ge_1"}
DAILY_CHECK = SCRIPTS / "trial_daily_check.py"


def run_daily_check(day: date) -> dict:
    """复用 trial_daily_check.main()：只读 SQL + 写它自己的 C:\\Temp\\trial_check_<day>.json。"""
    result: dict = {"ok": False, "rc": None, "error": "", "result": None,
                   "module": str(DAILY_CHECK)}
    if not DAILY_CHECK.is_file():
        result["error"] = f"trial_daily_check.py 不存在：{DAILY_CHECK}"
        return result
    try:
        module = importlib.import_module("trial_daily_check")
    except Exception as exc:  # noqa: BLE001
        result["error"] = (
            f"导入 trial_daily_check 失败：{type(exc).__name__}: {exc}；"
            "请用 C:\\项目\\py312\\python.exe 运行本脚本")
        return result
    old_argv = sys.argv
    buffer = io.StringIO()
    try:
        sys.argv = [str(DAILY_CHECK), str(day)]
        with contextlib.redirect_stdout(buffer):
            rc = module.main()
        result["rc"] = rc
        text = buffer.getvalue()
        end = text.rfind("}")
        if end >= 0:
            result["result"] = json.loads(text[: end + 1])
        else:
            fallback = Path(r"C:\Temp") / f"trial_check_{day}.json"
            if fallback.is_file():
                result["result"] = json.loads(fallback.read_text(encoding="utf-8"))
        result["ok"] = isinstance(result["result"], dict)
        if not result["ok"]:
            result["error"] = "trial_daily_check 输出无法解析为 JSON"
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback_tail"] = traceback.format_exc().splitlines()[-3:]
    finally:
        sys.argv = old_argv
    return result


# --------------------------------------------------------------------------- #
# 汇总判定
# --------------------------------------------------------------------------- #
def build_checks(ports: list[dict], tasks: dict, job: dict, db: dict,
                 daily: dict, markets: list[str]) -> list[dict]:
    checks: list[dict] = []

    def add(cid: str, label: str, status: str, detail: str, evidence=None) -> None:
        checks.append({"id": cid, "label": label, "status": status,
                       "detail": detail, "evidence": evidence or {}})

    # 1) 端口/服务
    for p in ports:
        add(f"port_{p['name']}", f"{p['label']} :{p['name']} 可用", "pass" if p["ok"] else "gap",
            p["detail"], {"tcp_ok": p["tcp_ok"], "http_status": (p["http"] or {}).get("status")})

    # 2) 计划任务
    primary = tasks.get("primary") or {}
    found = bool(primary.get("found"))
    add("task_found", f"计划任务 {SCHEDULED_TASK} 存在", "pass" if found else "gap",
        primary.get("path") or (tasks.get("query_error") or "未找到"),
        {"task": SCHEDULED_TASK})
    if found:
        add("task_state_ready", f"{SCHEDULED_TASK} 状态Ready",
            "pass" if str(primary.get("state")) == "Ready" else "gap",
            f"state={primary.get('state')}")
        add("task_next_run", f"{SCHEDULED_TASK} 有下次运行时间",
            "pass" if primary.get("next_run") else "gap",
            f"next={primary.get('next_run') or '—'}")
        add("task_last_result", f"{SCHEDULED_TASK} 上次运行结果=0",
            "pass" if primary.get("last_result") in (0, "0") else "warn",
            f"last={primary.get('last_run') or '—'} result={primary.get('last_result')}")

    # 3) job status
    file_info = job.get("file") or {}
    fields = file_info.get("fields") or {}
    reported = bool(file_info.get("exists") and not file_info.get("parse_error")
                    and fields.get("status") in ("running", "success", "failed"))
    add("job_status_reported", "任务运行态已上报（job status）", "pass" if reported else "gap",
        f"{JOB_STATUS_FILE} status={fields.get('status') or '—'}",
        {"exists": file_info.get("exists"), "parse_error": file_info.get("parse_error")})
    if reported:
        add("job_status_source", "运行态来源/计划时间已记录",
            "pass" if fields.get("source") and fields.get("scheduled_at") else "gap",
            f"source={fields.get('source') or '—'} scheduled_at={fields.get('scheduled_at') or '—'}")
        add("job_status_data_date", "运行态数据日已记录",
            "pass" if fields.get("data_date") else "gap",
            f"data_date={fields.get('data_date') or '—'}")
        ok_rc = fields.get("status") != "failed" or fields.get("rc") == 0
        add("job_status_rc", "运行态 rc 正常（failed 时 rc≠0 可解释）",
            "pass" if ok_rc else "warn", f"status={fields.get('status')} rc={fields.get('rc')}")
        fresh = job.get("fresh_hours")
        add("job_status_fresh", "运行态新鲜（完成时间 ≤ 24h）",
            "pass" if (fresh is not None and fresh <= 24) else "warn",
            f"finished_at={fields.get('finished_at') or '—'} 距今 {fresh}h" if fresh is not None
            else "无 finished_at（可能正在运行）")
    api_ok = bool((job.get("api") or {}).get("ok"))
    add("job_status_api", "GET /api/v1/system/job-status 只读可用", "pass" if api_ok else "gap",
        f"HTTP {(job.get('api') or {}).get('status')}")

    # 4) 最新池日
    if db.get("error"):
        add("db_readable", "只读 SQL 可连接数据库", "gap", db["error"])
    else:
        add("db_readable", "只读 SQL 可连接数据库", "pass", "SELECT only")
        resolved = db.get("resolved_day")
        latest_pool = db.get("latest_pool_day")
        add("pool_day_resolved", "解析出检查池日", "pass" if resolved else "gap",
            f"pool_day={resolved or '—'}（latest={latest_pool or '—'}）")
        add("pool_day_latest", "使用最新池日", "pass" if resolved == latest_pool else "warn",
            f"resolved={resolved} latest_pool={latest_pool}")
        same_day = (db.get("latest_report_day") == resolved and db.get("latest_acad1_day") == resolved)
        add("day_anchors_consistent", "池日锚点一致（selection/report/acad1）",
            "pass" if same_day else "warn",
            f"pool={latest_pool} report={db.get('latest_report_day')} acad1={db.get('latest_acad1_day')}")

        # 5) 四市场覆盖：报告（近3日窗口）/ pool / acad1
        fresh = db.get("report_fresh_by_market") or {}
        report_latest = db.get("report_latest_by_market") or {}
        lag_days: dict[str, int | None] = {}
        day_obj = date.fromisoformat(resolved) if resolved else None
        for market in markets:
            latest = report_latest.get(market)
            lag_days[market] = (day_obj - date.fromisoformat(latest)).days if (day_obj and latest) else None
        missing_fresh = [m for m in markets if not fresh.get(m)]
        add("four_markets_report", f"四市场报告覆盖（近 {REPORT_FRESH_DAYS} 日）",
            "pass" if not missing_fresh else "gap",
            "missing=" + (",".join(missing_fresh) if missing_fresh else "none"),
            {"fresh": fresh, "latest": report_latest, "lag_days": lag_days})
        pool_cov = db.get("pool") or {}
        missing_pool = [m for m in markets if not pool_cov.get(m)]
        add("four_markets_pool", "四市场选股池覆盖（当日）",
            "pass" if not missing_pool else "gap",
            "counts=" + json.dumps(pool_cov, ensure_ascii=False),
            {"missing": missing_pool})
        acad_cov = db.get("acad1") or {}
        missing_acad = [m for m in markets if not acad_cov.get(m)]
        add("four_markets_acad1", "四市场 ACAD1 池覆盖（当日）",
            "pass" if not missing_acad else "gap",
            "counts=" + json.dumps(acad_cov, ensure_ascii=False),
            {"missing": missing_acad})

    # 6) 复用 trial_daily_check 7 项
    daily_result = (daily.get("result") or {})
    daily_checks = daily_result.get("checks") or {}
    if not daily.get("ok"):
        add("daily_check_reused", "复用 trial_daily_check 7 项检查", "gap",
            daily.get("error") or "trial_daily_check 执行失败")
    else:
        add("daily_check_reused", "复用 trial_daily_check 7 项检查", "pass",
            f"trial_daily_check rc={daily.get('rc')} day={daily_result.get('day')}")
        for key, label in DAILY_CHECK_LABELS.items():
            value = daily_checks.get(key)
            if value is True:
                status = "pass"
                detail = "通过"
            elif key in TRIAL_ACTIVITY_CHECKS:
                status = "pending"
                detail = "0（试运行期间应≥1；试运行开始前属预期待办）"
            else:
                status = "gap"
                detail = "未通过"
            add(f"daily_{key}", f"[daily] {label}", status, detail,
                {"value": value, "trial_activity": key in TRIAL_ACTIVITY_CHECKS})
    return checks


def summarize(checks: list[dict]) -> dict:
    gaps = [c for c in checks if c["status"] == "gap"]
    warns = [c for c in checks if c["status"] == "warn"]
    pendings = [c for c in checks if c["status"] == "pending"]
    hard_ids = [c["id"] for c in gaps]
    pass_checks = [c for c in checks if c["status"] == "pass"]
    hard_gap_ids = [cid for cid in hard_ids if not cid.startswith("daily_") or
                    cid.replace("daily_", "") not in TRIAL_ACTIVITY_CHECKS]
    verdict = "READY" if not hard_gap_ids else "NOT_READY"
    return {
        "verdict": verdict,
        "pass": verdict == "READY",
        "total": len(checks),
        "passed": len(pass_checks),
        "gaps": [{"id": c["id"], "label": c["label"], "detail": c["detail"]} for c in gaps],
        "hard_gaps": hard_gap_ids,
        "warnings": [{"id": c["id"], "label": c["label"], "detail": c["detail"]} for c in warns],
        "pending_trial": [{"id": c["id"], "label": c["label"], "detail": c["detail"]}
                          for c in pendings],
    }


# --------------------------------------------------------------------------- #
# Markdown 摘要
# --------------------------------------------------------------------------- #
def render_markdown(payload: dict) -> str:
    checks = payload["checks"]
    summary = payload["summary"]
    ports = payload["services"]
    jobs = payload["job_status"]
    fields = (jobs.get("file") or {}).get("fields") or {}
    task = payload["scheduled_tasks"].get("primary") or {}
    db = payload["db"]
    markets = payload["markets"]
    icon = {"pass": "✅", "gap": "❌", "warn": "⚠️", "pending": "⏳"}
    lines: list[str] = []
    add = lines.append
    add("# Zhice 试运行前置验收（trial_preflight）")
    add("")
    add(f"> 生成时间：{payload['generated_at']} ｜ 目标池日：**{payload['target_date']}**"
        f" ｜ 判定：**{summary['verdict']}**"
        f"（通过 {summary['passed']}/{summary['total']}，硬缺口 {len(summary['hard_gaps'])}，"
        f"警告 {len(summary['warnings'])}，试运行期待办 {len(summary['pending_trial'])}）")
    add(f"> 试运行窗口：{payload['trial_window']} ｜ {DISCLAIMER}")
    add("")
    add("## 1. 端口/服务")
    add("")
    add("| 服务 | 端口 | TCP | HTTP | 结果 |")
    add("|---|---|---|---|---|")
    for p in ports:
        http = p.get("http") or {}
        add(f"| {p['label']} | {p['name']} | {'✅' if p['tcp_ok'] else '❌'} | "
            f"{http.get('status') or '—'} | {icon['pass'] if p['ok'] else icon['gap']} {p['detail']} |")
    add("")
    add(f"## 2. 计划任务 {SCHEDULED_TASK}")
    add("")
    add("| 字段 | 值 |")
    add("|---|---|")
    for key, label in (("state", "状态"), ("last_run", "上次运行"),
                       ("next_run", "下次运行"), ("last_result", "上次结果"),
                       ("path", "路径")):
        add(f"| {label} | `{task.get(key) if task.get(key) is not None else '—'}` |")
    others = payload["scheduled_tasks"].get("others") or []
    if others:
        add("")
        add("补充（只读参考）：" + "；".join(
            f"{o.get('task')} state={o.get('state')} next={o.get('next_run')}" for o in others))
    add("")
    add("## 3. 任务运行态（job status）")
    add("")
    add("| 字段 | 值 |")
    add("|---|---|")
    for key in ("run_id", "status", "source", "scheduled_at", "started_at",
                "finished_at", "data_date", "rc"):
        add(f"| {key} | `{fields.get(key) if fields.get(key) is not None else '—'}` |")
    add(f"| 文件 | `{jobs.get('file', {}).get('path')}`"
        f"（存在={jobs.get('file', {}).get('exists')}，mtime={jobs.get('file', {}).get('mtime') or '—'}） |")
    add(f"| 只读 API | HTTP `{(jobs.get('api') or {}).get('status')}`"
        f"（`/api/v1/system/job-status`） |")
    add("")
    add("## 4. 最新池日与四市场")
    add("")
    add(f"- 最新池日：selection `{db.get('latest_pool_day')}` / report `{db.get('latest_report_day')}`"
        f" / acad1 `{db.get('latest_acad1_day')}`；本次检查池日 = **{db.get('resolved_day')}**")
    add(f"- 报告近 {REPORT_FRESH_DAYS} 日窗口：`{db.get('report_fresh_since')}` 起"
        f"（字段口径同 trial_daily_check.report_fresh_strategies）")
    add("")
    add("| 市场 | 报告(窗口内) | 最新报告日 | 滞后(天) | 选股池 | ACAD1 池 |")
    add("|---|---|---|---|---|---|")
    fresh = db.get("report_fresh_by_market") or {}
    latest = db.get("report_latest_by_market") or {}
    lag = (payload.get("report_lag_days") or {})
    for market in markets:
        fr = fresh.get(market) or {}
        add(f"| {market} | {fr.get('n', 0)} 条 / {fr.get('strategies', 0)} 策略 | "
            f"{latest.get(market) or '—'} | {lag.get(market) if lag.get(market) is not None else '—'} | "
            f"{(db.get('pool') or {}).get(market, 0)} | {(db.get('acad1') or {}).get(market, 0)} |")
    add("")
    add(f"- 投研卡：`{json.dumps(db.get('cards') or {}, ensure_ascii=False)}`；"
        f"观测：`{json.dumps(db.get('observations') or {}, ensure_ascii=False)}`")
    add("")
    add("## 5. 检查明细（含 trial_daily_check 7 项）")
    add("")
    add("| # | 检查 | 状态 | 证据 |")
    add("|---|---|---|---|")
    for i, c in enumerate(checks, 1):
        add(f"| {i} | {c['label']} | {icon.get(c['status'], c['status'])} {c['status']} | {c['detail']} |")
    add("")
    add("## 6. 缺口 / 警告 / 试运行期待办")
    add("")
    if summary["hard_gaps"]:
        add("**硬缺口（阻断试运行）**：")
        for g in summary["gaps"]:
            add(f"- ❌ `{g['id']}` {g['label']} — {g['detail']}")
    else:
        add("- ✅ 无硬缺口（关键项全通过）")
    if summary["warnings"]:
        add("")
        add("**警告（不阻断，需留意）**：")
        for w in summary["warnings"]:
            add(f"- ⚠️ `{w['id']}` {w['label']} — {w['detail']}")
    if summary["pending_trial"]:
        add("")
        add("**试运行期才产生的待办（当前为预期缺口，不算失败）**：")
        for p in summary["pending_trial"]:
            add(f"- ⏳ `{p['id']}` {p['label']} — {p['detail']}")
    add("")
    add("## 7. 只读声明与复现")
    add("")
    add("- " + DISCLAIMER)
    add("- 复现命令：")
    add("")
    add("```powershell")
    add(f"C:\\项目\\py312\\python.exe scripts\\trial_preflight.py --date {payload['target_date']}")
    add("```")
    add("")
    add(f"- 机器可读：`{payload['outputs']['json']}`；人工摘要：`{payload['outputs']['md']}`")
    add(f"- 复用的每日检查产物：`C:\\Temp\\trial_check_{payload['target_date']}.json`"
        "（由 trial_daily_check.py 自身写出，属既有行为）")
    add("")
    add("> 本报告只读验收，不代表试运行已通过；试运行结果以 11-02~11-04 每日检查与收口报告为准。")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M8-1 试运行前置验收（只读）：端口/计划任务/job status/池日/四市场 + trial_daily_check 7 项")
    parser.add_argument("--date", default=None,
                        help="检查池日 YYYY-MM-DD（默认取最新 v2_selection_pool.pool_date）")
    parser.add_argument("--json-out", default=str(OUT_JSON), help="JSON 输出路径")
    parser.add_argument("--md-out", default=str(OUT_MD), help="Markdown 输出路径")
    parser.add_argument("--timeout", type=float, default=6.0, help="HTTP 探测超时秒")
    args = parser.parse_args(argv)

    target: date | None = None
    if args.date:
        try:
            target = date.fromisoformat(args.date)
        except ValueError:
            print(f"[FAIL] --date 非法：{args.date}（需 YYYY-MM-DD）", file=sys.stderr)
            return 1

    started = time.time()
    print("[trial_preflight] 开始只读验收…", flush=True)

    # 1) 端口
    services = [probe_service(svc) for svc in SERVICES]
    ports_line = ", ".join(f"{p['name']}={'OK' if p['ok'] else 'FAIL'}" for p in services)
    print(f"[trial_preflight] 端口探测完成：{ports_line}", flush=True)

    # 2) 计划任务
    tasks = scheduled_task_facts()
    tasks_state = "found" if tasks.get("primary", {}).get("found") else "NOT FOUND"
    tasks_err = f" query_error={tasks.get('query_error')}" if tasks.get("query_error") else ""
    print(f"[trial_preflight] 计划任务：{tasks_state}{tasks_err}", flush=True)

    # 3) job status
    job = job_status_facts()
    print(f"[trial_preflight] job status：file={job['file']['exists']} "
          f"api={(job['api'] or {}).get('status')}", flush=True)

    # 4) 只读 DB
    db = db_facts(target)
    if db.get("error"):
        print(f"[trial_preflight] DB 读取失败：{db['error']}", file=sys.stderr, flush=True)
    resolved_str = db.get("resolved_day") or (str(target) if target else None)
    if not resolved_str:
        print("[FAIL] 无法解析检查池日（DB 不可用且未提供 --date）", file=sys.stderr)
        return 1
    resolved_day = date.fromisoformat(resolved_str)

    # 5) 复用 trial_daily_check 7 项
    print(f"[trial_preflight] 复用 trial_daily_check（day={resolved_day}）…", flush=True)
    daily = run_daily_check(resolved_day)
    if not daily.get("ok"):
        print(f"[trial_preflight] trial_daily_check 未成功：{daily.get('error')}", file=sys.stderr,
              flush=True)

    # 6) 汇总
    markets = ["CN", "HK", "US", "CRYPTO"]
    report_lag_days = {}
    if not db.get("error") and db.get("report_latest_by_market"):
        for market in markets:
            latest = db["report_latest_by_market"].get(market)
            report_lag_days[market] = ((resolved_day - date.fromisoformat(latest)).days
                                       if latest else None)
    checks = build_checks(services, tasks, job, db, daily, markets)
    summary = summarize(checks)
    payload = {
        "schema": "zhice_trial_preflight/v1",
        "generated_at": now_iso(),
        "target_date": str(resolved_day),
        "trial_window": f"{TRIAL_START} ~ {TRIAL_END}",
        "mode": "read_only",
        "read_only": {
            "db_writes": False,
            "tasks_triggered": False,
            "scheduled_tasks_modified": False,
            "config_changed": False,
            "sql_only_select": True,
        },
        "host": socket.gethostname(),
        "python": sys.version.split()[0],
        "repo": str(CODE),
        "services": services,
        "scheduled_tasks": tasks,
        "job_status": job,
        "db": db,
        "report_lag_days": report_lag_days,
        "markets": markets,
        "daily_check": daily,
        "checks": checks,
        "summary": summary,
        "outputs": {"json": args.json_out, "md": args.md_out},
        "disclaimer": DISCLAIMER,
        "elapsed_s": round(time.time() - started, 1),
    }

    json_out = Path(args.json_out)
    md_out = Path(args.md_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_out.write_text(render_markdown(payload), encoding="utf-8")

    print(json.dumps({
        "verdict": summary["verdict"],
        "target_date": payload["target_date"],
        "passed": summary["passed"],
        "total": summary["total"],
        "hard_gaps": summary["hard_gaps"],
        "warnings": [w["id"] for w in summary["warnings"]],
        "pending_trial": [p["id"] for p in summary["pending_trial"]],
        "json": str(json_out),
        "md": str(md_out),
        "elapsed_s": payload["elapsed_s"],
    }, ensure_ascii=False, indent=2))
    return 0 if summary["verdict"] == "READY" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
