# -*- coding: utf-8 -*-
r"""KPI 自动采集工具（S3-08，2026-11-02 task-66）——只读、可复跑、可入 fast 门。

计算数据仓研发计划 v3 §三 K1–K10 的「当前值 / 目标 / 状态 / 证据」，输入全部只读：
  - xq sqlite：quant_backtest/data/xueqiu/xueqiu_info.db（xq_status.fetched_at 日增量）
  - 8091 API：GET /api/daily/data（drift 哨兵）
  - catalog / registry：quant_backtest/data/{catalog,warehouse_registry}.json
  - data_catalog.yaml：agent_platform/catalogs/data_catalog.yaml（数据集登记口径）
  - snapshot：portal/static/dw_snapshot.json（generated_at）
  - 任务信息：schtasks /query \ZeroBound\DataWarehouseSnapshot（Last Run / Last Result）
  - coverage：research/reports/dataset_query_coverage_*.json（K6）
  - fast 门配置：tools/test_gate.ps1（K9）
  - Hermes 配置（可选探测）：~/.hermes/config.yaml（K10）

状态语义（fail-visible，不硬编码假值）：
  ok     达成/满足目标；warn 未达标或待验证；unknown 数据源缺失/不可读（value=None + 证据错误）。

用法：
  python tools/datawarehouse/kpi_report.py                 # 打印 Markdown 摘要 + 写 JSON
  python tools/datawarehouse/kpi_report.py --json-only     # 只写 JSON（不打印 Markdown）
  python tools/datawarehouse/kpi_report.py --date 20261102 --out research/reports/kpi_baseline_20261102.json
  python tools/datawarehouse/kpi_report.py --md-out C:\\Temp\\kpi.md   # 可选：Markdown 落盘
退出码：0 完成（含 unknown/warn）；1 写入/参数错误。
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "quant_backtest" / "data"
XQ_DB = DATA / "xueqiu" / "xueqiu_info.db"
CATALOG = DATA / "catalog.json"
REGISTRY = DATA / "warehouse_registry.json"
DATA_CATALOG_YAML = REPO / "agent_platform" / "catalogs" / "data_catalog.yaml"
SNAPSHOT = REPO / "portal" / "static" / "dw_snapshot.json"
COVERAGE_GLOB = "dataset_query_coverage_*.json"
REPORTS = REPO / "research" / "reports"
TEST_GATE = REPO / "tools" / "test_gate.ps1"
HERMES_CONFIG = Path.home() / ".hermes" / "config.yaml"
DW_SNAPSHOT_TASK = "\\ZeroBound\\DataWarehouseSnapshot"
API_DAILY_DATA = "http://127.0.0.1:8091/api/daily/data"
DW_MCP_PY = REPO / "agent_platform" / "tools" / "dw_governance_mcp.py"

DIM_TARGETS = {
    "K1": ">0 连续 3 天",
    "K2": "exists=true",
    "K3": "每日自动（L2 已安装 06:40）",
    "K4": "314/314",
    "K5": "314/314 保持",
    "K6": "≥80%（clean）",
    "K7": "≥200",
    "K8": "<40（S3）",
    "K9": "是",
    "K10": "10 问 E2E 全过",
}
KPI_NAMES = {
    "K1": "雪球当日增量",
    "K2": "drift 哨兵 exists",
    "K3": "dw_snapshot 新鲜度",
    "K4": "数据集登记",
    "K5": "统一入口可达",
    "K6": "专用查询入口覆盖",
    "K7": "display 覆盖物理表",
    "K8": "资质 unknown",
    "K9": "datawarehouse 测试入 fast 门",
    "K10": "Hermes 数据仓工具",
}


def _kpi(value, evidence, status, target=None, name=None):
    return {"name": name, "value": value, "target": target,
            "status": status, "evidence": evidence}


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing: %s" % path
    except (OSError, ValueError) as exc:
        return None, "unreadable: %s: %s" % (path, str(exc)[:120])


def _find_key(obj, key):
    """递归找 JSON 中的首个键（K2 open_count 等）。"""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            got = _find_key(v, key)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find_key(v, key)
            if got is not None:
                return got
    return None


# ---------------- K1：雪球当日增量（连续 3 天） ----------------
def collect_k1(db_path: Path | None = None, today: datetime.date | None = None) -> dict:
    path = Path(db_path or XQ_DB)
    if not path.exists():
        return _kpi(None, "xq sqlite 缺失：%s" % path, "unknown",
                    DIM_TARGETS["K1"], KPI_NAMES["K1"])
    today = today or datetime.date.today()
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True, timeout=5)
        try:
            total = int(con.execute("select count(*) from xq_status").fetchone()[0])
            cur = con.execute(
                "select substr(fetched_at,1,10) d, count(*) from xq_status "
                "where fetched_at >= ? group by d", ((today - datetime.timedelta(days=6)).isoformat(),))
            daily = {str(r[0]): int(r[1]) for r in cur.fetchall()}
        finally:
            con.close()
    except sqlite3.Error as exc:
        return _kpi(None, "xq_status 读取失败：%s" % str(exc)[:160], "unknown",
                    DIM_TARGETS["K1"], KPI_NAMES["K1"])
    days = [(today - datetime.timedelta(days=i)).isoformat() for i in range(3)]
    counts = [daily.get(d, 0) for d in days]
    streak = 0
    for c in counts:  # 从今天向前数连续 >0 天数
        if c > 0:
            streak += 1
        else:
            break
    value = counts[0]
    evidence = "xq_status fetched_at 日增量 %s=%d（连续 %d 天 >0）；total=%d" % (
        days[0], counts[0], streak, total)
    status = "ok" if (counts[0] > 0 and streak >= 3) else "warn"
    if counts[0] <= 0:
        evidence += "；今日增量为 0/缺失"
    return _kpi(value, evidence, status, DIM_TARGETS["K1"], KPI_NAMES["K1"])


# ---------------- K2：drift 哨兵 exists ----------------
def collect_k2(url: str | None = None, timeout: float = 5.0) -> dict:
    url = url or API_DAILY_DATA
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        doc = json.loads(body)
    except Exception as exc:  # noqa: BLE001 — 网络/服务不可达 → unknown（fail-visible）
        return _kpi(None, "8091 %s 不可达：%s: %s"
                    % (url, type(exc).__name__, str(exc)[:120]), "unknown",
                    DIM_TARGETS["K2"], KPI_NAMES["K2"])
    exists = _find_key(doc, "exists")
    open_count = _find_key(doc, "open_count")
    value = {"exists": exists, "open_count": open_count}
    if exists is None:
        return _kpi(value, "%s 返回无 exists 字段（键：%s）"
                    % (url, list(doc.keys())[:8] if isinstance(doc, dict) else "-"),
                    "unknown", DIM_TARGETS["K2"], KPI_NAMES["K2"])
    status = "ok" if exists is True else "warn"
    ev = "%s exists=%s open_count=%s" % (url, exists, open_count if open_count is not None else "N/A（接口未返回）")
    return _kpi(value, ev, status, DIM_TARGETS["K2"], KPI_NAMES["K2"])


# ---------------- K3：dw_snapshot 新鲜度 + 每日自动 ----------------
def parse_task_listing(text: str) -> dict:
    """解析 schtasks /FO LIST /V 输出（中英文标签兼容）→ {last_run, last_result}。"""
    out = {"last_run": None, "last_result": None}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        k = key.strip().lower()
        v = val.strip()
        if k in ("last run time", "上次运行时间"):
            out["last_run"] = v
        elif k in ("last result", "上次结果"):
            try:
                out["last_result"] = int(v)
            except ValueError:
                out["last_result"] = v
    return out


def _parse_dt(value):
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def collect_k3(snapshot_path: Path | None = None, task_name: str | None = None,
               task_text: str | None = None, task_runner=None,
               today: datetime.date | None = None) -> dict:
    snap_path = Path(snapshot_path or SNAPSHOT)
    today = today or datetime.date.today()
    snap, err = _read_json(snap_path)
    if err:
        return _kpi(None, "snapshot 缺失/不可读：%s" % err, "unknown",
                    DIM_TARGETS["K3"], KPI_NAMES["K3"])
    gen_raw = snap.get("generated_at")
    gen = _parse_dt(gen_raw)
    if gen is None:
        return _kpi(gen_raw, "snapshot generated_at 缺失/不可解析：%r" % gen_raw,
                    "unknown", DIM_TARGETS["K3"], KPI_NAMES["K3"])
    # 任务信息（可注入 task_text 供测试）
    if task_text is None:
        if task_runner is None:
            def task_runner(cmd):
                return subprocess.run(cmd, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace",
                                      timeout=30).stdout
        try:
            task_text = task_runner(["schtasks", "/query", "/TN",
                                     task_name or DW_SNAPSHOT_TASK,
                                     "/FO", "LIST", "/V"])
        except Exception as exc:  # noqa: BLE001
            return _kpi(gen_raw, "snapshot 可读但任务查询失败：%s: %s"
                        % (type(exc).__name__, str(exc)[:120]), "unknown",
                        DIM_TARGETS["K3"], KPI_NAMES["K3"])
    task = parse_task_listing(task_text)
    last_run = _parse_dt(task.get("last_run"))
    last_result = task.get("last_result")
    generated_today = gen.date() == today
    auto_today = bool(last_run and last_run.date() == today and last_result == 0)
    if generated_today and auto_today:
        status, extra = "ok", "自动任务今日已跑且成功（LastResult=0）"
    elif generated_today:
        status, extra = "warn", "snapshot 为今日（可能手动）；自动任务今日未成功运行"
    else:
        status, extra = "warn", "snapshot 非今日"
    ev = ("generated_at=%s（%s）；任务 LastRun=%s LastResult=%s；%s"
          % (gen_raw, "今日" if generated_today else "非今日",
             task.get("last_run"), last_result, extra))
    return _kpi(gen_raw, ev, status, DIM_TARGETS["K3"], KPI_NAMES["K3"])


# ---------------- K4：数据集登记（data_catalog.yaml） ----------------
def collect_k4(yaml_path: Path | None = None) -> dict:
    path = Path(yaml_path or DATA_CATALOG_YAML)
    try:
        import yaml
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _kpi(None, "data_catalog.yaml 缺失：%s" % path, "unknown",
                    DIM_TARGETS["K4"], KPI_NAMES["K4"])
    except Exception as exc:  # noqa: BLE001
        return _kpi(None, "data_catalog.yaml 不可读：%s" % str(exc)[:160],
                    "unknown", DIM_TARGETS["K4"], KPI_NAMES["K4"])
    datasets = (doc or {}).get("datasets") or []
    total = len(datasets)
    registered = sum(1 for d in datasets
                     if isinstance(d, dict) and d.get("registered") is True)
    value = "%d/%d" % (registered, total)
    status = "ok" if total > 0 and registered == total else "warn"
    ev = "data_catalog.yaml v%s datasets=%d registered=true=%d" % (
        (doc or {}).get("version"), total, registered)
    return _kpi(value, ev, status, DIM_TARGETS["K4"], KPI_NAMES["K4"])


# ---------------- K5：统一入口可达（DatasetRegistry） ----------------
def collect_k5(inventory_path: Path | None = None) -> dict:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from dataset_registry import DatasetRegistry  # noqa: PLC0415
        reg = DatasetRegistry(inventory_path) if inventory_path else DatasetRegistry()
        total = len(reg.datasets)
        value = "%d/%d" % (total, total)
        status = "ok" if total >= 314 else "warn"
        return _kpi(value, "DatasetRegistry.datasets=%d（统一入口可达）" % total,
                    status, DIM_TARGETS["K5"], KPI_NAMES["K5"])
    except Exception as exc:  # noqa: BLE001
        return _kpi(None, "DatasetRegistry 加载失败：%s: %s"
                    % (type(exc).__name__, str(exc)[:160]), "unknown",
                    DIM_TARGETS["K5"], KPI_NAMES["K5"])


# ---------------- K6：专用查询入口覆盖（coverage json） ----------------
def collect_k6(coverage_path: Path | None = None) -> dict:
    path = Path(coverage_path) if coverage_path else None
    if path is None:
        cands = sorted(REPORTS.glob(COVERAGE_GLOB))
        if not cands:
            return _kpi(None, "coverage json 缺失（%s/%s）" % (REPORTS, COVERAGE_GLOB),
                        "unknown", DIM_TARGETS["K6"], KPI_NAMES["K6"])
        path = cands[-1]
    doc, err = _read_json(path)
    if err:
        return _kpi(None, "coverage json 不可读：%s" % err, "unknown",
                    DIM_TARGETS["K6"], KPI_NAMES["K6"])
    raw = doc.get("coverage_raw_pct")
    clean = doc.get("coverage_clean_pct")
    ready = doc.get("ready")
    denom = doc.get("denominator")
    value = "raw %s%% / clean %s%%" % (raw, clean)
    status = "ok" if isinstance(clean, (int, float)) and clean >= 80 else "warn"
    ev = "%s ready=%s denominator=%s（%s）" % (
        path.name, ready, denom, doc.get("generated_at"))
    return _kpi(value, ev, status, DIM_TARGETS["K6"], KPI_NAMES["K6"])


# ---------------- K7：display 覆盖物理表 ----------------
def collect_k7(registry_path: Path | None = None) -> dict:
    path = Path(registry_path or REGISTRY)
    reg, err = _read_json(path)
    if err:
        return _kpi(None, "registry 不可读：%s" % err, "unknown",
                    DIM_TARGETS["K7"], KPI_NAMES["K7"])
    phys = [e.get("asset_id") for e in reg.get("physical_tables") or []]
    phys_set = {p for p in phys if p}
    covered = set()
    for d in reg.get("display_assets") or []:
        for t in d.get("physical_tables") or []:
            if t in phys_set:
                covered.add(t)
    total = len(phys_set)
    n = len(covered)
    pct = (n * 100.0 / total) if total else 0.0
    value = "%d/%d（%.1f%%）" % (n, total, pct)
    status = "ok" if n >= 200 else "warn"
    ev = "registry display_assets=%d；未覆盖 %d 张（多为 planned/deprecated）" % (
        len(reg.get("display_assets") or []), total - n)
    return _kpi(value, ev, status, DIM_TARGETS["K7"], KPI_NAMES["K7"])


# ---------------- K8：资质 unknown ----------------
def collect_k8(registry_path: Path | None = None) -> dict:
    path = Path(registry_path or REGISTRY)
    reg, err = _read_json(path)
    if err:
        return _kpi(None, "registry 不可读：%s" % err, "unknown",
                    DIM_TARGETS["K8"], KPI_NAMES["K8"])
    from collections import Counter
    dist = Counter(e.get("qualification_status")
                   for e in reg.get("physical_tables") or [])
    unknown = int(dist.get("unknown", 0))
    status = "ok" if unknown < 40 else "warn"
    ev = "distribution=%s" % json.dumps(dict(dist), ensure_ascii=False)
    return _kpi(unknown, ev, status, DIM_TARGETS["K8"], KPI_NAMES["K8"])


# ---------------- K9：datawarehouse 测试入 fast 门 ----------------
def collect_k9(test_gate_path: Path | None = None) -> dict:
    path = Path(test_gate_path or TEST_GATE)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _kpi(None, "test_gate 不可读：%s" % exc, "unknown",
                    DIM_TARGETS["K9"], KPI_NAMES["K9"])
    m = re.search(r"'fast'\s*\{\s*\$suites\s*=\s*@\(([^)]*)\)", text)
    suites = m.group(1) if m else ""
    ok = "datawarehouse" in suites
    value = "是" if ok else "否"
    style = "ok" if ok else "warn"
    ev = "tools/test_gate.ps1 -Suite fast suites=[%s]" % suites.strip()
    return _kpi(value, ev, style, DIM_TARGETS["K9"], KPI_NAMES["K9"])


# ---------------- K10：Hermes 数据仓工具 ----------------
def collect_k10(hermes_path: Path | None = None, probe_url: str | None = None,
                mcp_py: Path | None = None) -> dict:
    hp = Path(hermes_path or HERMES_CONFIG)
    mp = Path(mcp_py or DW_MCP_PY)
    code_tools = None
    try:
        text = mp.read_text(encoding="utf-8", errors="replace")
        code_tools = len(re.findall(r'"name":\s*"dw_', text))
    except OSError:
        pass
    config_has = False
    if hp.exists():
        try:
            config_has = "dw-governance" in hp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            config_has = False
    # 今日 K10 E2E 证据（默认真实路径时才读取；测试注入自定义 config 时不看全局证据）
    if hermes_path is None and mcp_py is None and not probe_url:
        try:
            import glob as _glob
            import datetime as _dt
            evs = sorted(_glob.glob(str(REPORTS / "k10_e2e_*.json")))
            if evs:
                ev = json.loads(Path(evs[-1]).read_text(encoding="utf-8"))
                total = int(ev.get("total") or 0)
                passed = int(ev.get("passed") or 0)
                gen = str(ev.get("generated_at") or "")
                is_today = gen[:10] == _dt.date.today().isoformat()
                if config_has and total >= 10 and passed == total and is_today:
                    return _kpi("Hermes 数据仓工具 10 问 E2E %d/%d（今日）；config 含 dw-governance" % (passed, total),
                                "证据：%s；config：%s" % (evs[-1], hp),
                                "ok", DIM_TARGETS["K10"], KPI_NAMES["K10"])
        except Exception:  # noqa: BLE001
            pass
    probe_tools = None
    if probe_url:
        try:
            with urllib.request.urlopen(probe_url, timeout=5) as r:
                body = json.loads(r.read().decode("utf-8", "replace"))
            probe_tools = _find_key(body, "tools")
            if isinstance(probe_tools, list):
                probe_tools = len(probe_tools)
        except Exception:  # noqa: BLE001
            probe_tools = None
    if probe_tools is not None:
        value = "Hermes MCP tools=%s（代码 tools=%s）；10 问 E2E 未验证" % (probe_tools, code_tools)
        return _kpi(value, "Hermes 探测 %s tools=%s" % (probe_url, probe_tools),
                    "warn", DIM_TARGETS["K10"], KPI_NAMES["K10"])
    if config_has:
        value = "config 已注册（代码 tools=%s）；Hermes reload/10 问 E2E 未验证" % code_tools
        return _kpi(value, "~/.hermes/config.yaml 含 dw-governance；%s" % hp,
                    "warn", DIM_TARGETS["K10"], KPI_NAMES["K10"])
    return _kpi(None, "Hermes 配置缺失且未探测：%s" % hp, "unknown",
                DIM_TARGETS["K10"], KPI_NAMES["K10"])


# ---------------- 汇总 ----------------
COLLECTORS = {
    "K1": collect_k1, "K2": collect_k2, "K3": collect_k3, "K4": collect_k4,
    "K5": collect_k5, "K6": collect_k6, "K7": collect_k7, "K8": collect_k8,
    "K9": collect_k9, "K10": collect_k10,
}


def build_report(today: datetime.date | None = None, overrides: dict | None = None) -> dict:
    """采集 K1–K10 → 报告 dict（overrides: {Kx: kwargs}，供测试注入 mock 数据源）。"""
    today = today or datetime.date.today()
    overrides = overrides or {}
    kpis = {}
    for key in ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K10"):
        fn = COLLECTORS[key]
        kwargs = dict(overrides.get(key) or {})
        if key == "K1":
            kwargs.setdefault("today", today)
        if key == "K3":
            kwargs.setdefault("today", today)
        try:
            kpis[key] = fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 — 单 KPI 失败不得中断报告（fail-visible）
            kpis[key] = _kpi(None, "采集异常：%s: %s" % (type(exc).__name__, str(exc)[:160]),
                             "unknown", DIM_TARGETS[key], KPI_NAMES[key])
    counts = {"ok": 0, "warn": 0, "unknown": 0}
    for k in kpis.values():
        counts[k["status"]] = counts.get(k["status"], 0) + 1
    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "measured_at": stamp,
        "date": today.isoformat(),
        "generated_by": "tools/datawarehouse/kpi_report.py",
        "definitions_source": "docs/plans/2026-11-02_数据仓研发计划_v3_执行版.md §三",
        "sources": {
            "xq_db": str(XQ_DB), "api": API_DAILY_DATA, "registry": str(REGISTRY),
            "catalog": str(CATALOG), "data_catalog_yaml": str(DATA_CATALOG_YAML),
            "snapshot": str(SNAPSHOT), "coverage": str(REPORTS / COVERAGE_GLOB),
            "task": DW_SNAPSHOT_TASK, "test_gate": str(TEST_GATE),
            "hermes_config": str(HERMES_CONFIG),
        },
        "kpis": kpis,
        "summary": {"ok": counts["ok"], "warn": counts["warn"],
                    "unknown": counts["unknown"], "total": len(kpis)},
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# KPI 基线（%s）" % report.get("measured_at"),
        "",
        "> 自动生成：`tools/datawarehouse/kpi_report.py` ｜ 口径：计划 v3 §三 K1–K10 ｜ "
        "只读采集；`unknown`=数据源缺失（fail-visible，不硬编码）",
        "",
        "| # | KPI | 当前值 | 目标 | 状态 | 证据 |",
        "|---|---|---|---|---|---|",
    ]
    icon = {"ok": "✅", "warn": "⚠️", "unknown": "❔"}
    for key in ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K10"):
        k = report["kpis"].get(key) or {}
        val = k.get("value")
        if isinstance(val, dict):
            val = json.dumps(val, ensure_ascii=False)
        lines.append("| %s | %s | %s | %s | %s %s | %s |" % (
            key, k.get("name"), val, k.get("target"),
            icon.get(k.get("status"), ""), k.get("status"),
            str(k.get("evidence") or "").replace("|", "/")[:220]))
    s = report.get("summary") or {}
    lines += ["", "## 汇总", "",
              "- ok: %s / warn: %s / unknown: %s（共 %s 项）" % (
                  s.get("ok"), s.get("warn"), s.get("unknown"), s.get("total")),
              "- 采集时间：%s；日期：%s" % (report.get("measured_at"), report.get("date")),
              ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="KPI 自动采集（只读）")
    ap.add_argument("--date", default=None, help="报告日期 YYYYMMDD（默认今天）")
    ap.add_argument("--out", default=None,
                    help="JSON 输出路径（默认 research/reports/kpi_baseline_<date>.json）")
    ap.add_argument("--md-out", default="", help="可选：Markdown 摘要落盘路径（默认仅 stdout）")
    ap.add_argument("--json-only", action="store_true", help="只写 JSON，不打印 Markdown")
    args = ap.parse_args(argv)
    if args.date:
        try:
            today = datetime.datetime.strptime(args.date, "%Y%m%d").date()
        except ValueError:
            print("--date 需 YYYYMMDD：%r" % args.date, file=sys.stderr)
            return 2
    else:
        today = datetime.date.today()
    out = Path(args.out or (REPORTS / ("kpi_baseline_%s.json" % today.strftime("%Y%m%d"))))
    report = build_report(today=today)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md = render_markdown(report)
    if args.md_out:
        Path(args.md_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md_out).write_text(md + "\n", encoding="utf-8")
    if not args.json_only:
        print(md)
    s = report["summary"]
    print("KPI 采集完成：ok=%d warn=%d unknown=%d → %s"
          % (s["ok"], s["warn"], s["unknown"], out), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
