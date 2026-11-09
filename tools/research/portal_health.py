# -*- coding: utf-8 -*-
"""全站页面巡检 + 静态 JSON 契约校验 + 系统总览刷新（task-114）。

① 页面巡检：扫描 portal/*.html → GET http://127.0.0.1:8734/<page>，记录状态/大小/耗时/异常；
② 静态契约：portal/static/*.json 逐个解析合法 JSON，并校验关键 schema：
   kb_learning_cards（卡数 104）、kb_qa_samples（schema 2 / prompt v4 / 8 条）、
   daily_screening（schema/日期/RS 交集 18）、report_panel（reports/candidates 等）；
③ 系统总览：卡/单元/工具/页面/提交数 → research/reports/system_status_20261102.json
   + deliverables/2026-11-02_系统总览_v1.md；
④ 输出 research/reports/portal_health_20261102.{json,md}。

约束：只读巡检，不重启服务、不修改业务数据。
运行：python tools/research/portal_health.py [--base-url http://127.0.0.1:8734] [--timeout 5]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PORTAL = REPO / "portal"
STATIC = PORTAL / "static"
REPORTS = REPO / "research" / "reports"
DELIVER = REPO / "deliverables"
BASE_URL = "http://127.0.0.1:8734"
SCHEMA_VERSION = "portal_health.v1"
DATE_TAG = "20261102"

# 仅以「无扩展名路由」提供的页面：巡检用别名路由而不是 .html 文件路径（task-116）
# 依据 portal_api.py：/btview→bt_view.html、/bt2view→bt2_view.html、/sandbox→sandbox.html
PAGE_ROUTE_ALIASES = {
    "bt_view.html": "/btview",
    "bt2_view.html": "/bt2view",
    "sandbox.html": "/sandbox",
}

# 关键静态契约（期望值；不符则 contract_ok=False 并记录实际值）
CONTRACTS = {
    "kb_learning_cards.json": {"cards_min": 100},
    "kb_qa_samples.json": {"schema_version": 2, "prompt_version": "v4", "samples": 8},
    "daily_screening.json": {"schema_version": "daily_screening_portal.v1",
                             "date": "20261030", "rs_intersection": 18},
    "report_panel.json": {"is_dict": True, "has_any": ["reports", "candidates"]},
}


# ---------------------------------------------------------------------------
# 页面巡检
# ---------------------------------------------------------------------------
def list_pages(portal_dir: Path | str = PORTAL) -> list[str]:
    return sorted(p.name for p in Path(portal_dir).glob("*.html"))


def http_get(url: str, timeout: float = 5.0) -> dict:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
            return {"status": int(resp.status), "size": len(data),
                    "ms": round((time.monotonic() - started) * 1000, 1), "error": None}
    except urllib.error.HTTPError as exc:
        return {"status": int(exc.code), "size": 0,
                "ms": round((time.monotonic() - started) * 1000, 1),
                "error": f"HTTPError {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": None, "size": 0,
                "ms": round((time.monotonic() - started) * 1000, 1),
                "error": f"{type(exc).__name__}: {exc}"}


def inspect_pages(base_url: str = BASE_URL, pages: list[str] | None = None,
                  timeout: float = 5.0, fetch_fn=None) -> list[dict]:
    fetch = fetch_fn or http_get
    pages = pages if pages is not None else list_pages()
    out = []
    for name in pages:
        route = PAGE_ROUTE_ALIASES.get(name)
        path = route if route else f"/{name}"
        url = f"{base_url.rstrip('/')}{path}"
        r = fetch(url, timeout) if fetch_fn is None else fetch(url, timeout=timeout)
        rec = {"page": name, "url": url, "status": r.get("status"),
               "size": r.get("size"), "ms": r.get("ms"),
               "ok": bool(r.get("status") == 200 and r.get("size", 0) > 0),
               "error": r.get("error")}
        if route:
            rec["alias_of"] = route      # 页面以别名路由提供（.html 路径 404 属预期）
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# 静态 JSON 契约
# ---------------------------------------------------------------------------
def check_contract(name: str, data) -> dict:
    spec = CONTRACTS.get(name)
    if not spec:
        return {"ok": True, "detail": "仅合法 JSON（无额外契约）", "issues": [], "actual": {}}
    actual: dict = {}
    issues: list[str] = []
    if isinstance(data, dict):
        for key, expected in spec.items():
            if key == "is_dict":
                continue
            if key == "has_any":
                ok = any(k in data for k in expected)
                actual[key] = ok
                if not ok:
                    issues.append(f"缺少任一字段：{expected}")
                continue
            raw = data.get(key)
            if key == "cards":
                actual[key] = len(raw or [])
            elif key == "cards_min":
                actual["cards"] = len(data.get("cards") or [])
                if actual["cards"] < int(expected):
                    issues.append(f"cards: 期望 ≥{expected}，实际 {actual['cards']}")
                continue
            elif key == "samples":
                actual[key] = len(raw or [])
            elif key == "rs_intersection":
                actual[key] = len(raw or [])
            else:
                actual[key] = raw
            if actual[key] != expected:
                issues.append(f"{key}: 期望 {expected}，实际 {actual[key]}")
    else:
        if spec.get("is_dict"):
            issues.append("顶层不是 JSON 对象")
    return {"ok": not issues, "actual": actual, "expected": spec, "issues": issues}


def inspect_static(static_dir: Path | str = STATIC) -> list[dict]:
    records = []
    for p in sorted(Path(static_dir).glob("*.json")):
        rec = {"file": p.name, "size": p.stat().st_size,
               "valid_json": False, "contract_ok": None, "issues": []}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            rec["valid_json"] = True
            check = check_contract(p.name, data)
            rec["contract_ok"] = check["ok"]
            rec["issues"] = check["issues"]
            rec["type"] = type(data).__name__
        except (OSError, json.JSONDecodeError) as exc:
            rec["issues"] = [f"JSON 解析失败：{exc}"]
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# 系统总览
# ---------------------------------------------------------------------------
def git_commits() -> dict:
    try:
        count = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=REPO,
                               capture_output=True, text=True, timeout=20)
        latest = subprocess.run(["git", "log", "-1", "--format=%h %s"], cwd=REPO,
                                capture_output=True, text=True, timeout=20)
        return {"count": int(count.stdout.strip()) if count.returncode == 0 else None,
                "latest": latest.stdout.strip() if latest.returncode == 0 else None}
    except Exception:  # noqa: BLE001
        return {"count": None, "latest": None}


def load_json(path: Path | str) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def system_snapshot(portal_dir: Path | str = PORTAL) -> dict:
    static = Path(portal_dir) / "static"
    cards = load_json(static / "kb_learning_cards.json") or {}
    units = load_json(static / "kb_units_all.json") or {}
    pages = list_pages(portal_dir)
    tools = [p for p in (REPO / "tools").rglob("*.py")
             if not any(part in {"__pycache__", ".worktrees", "tmp"} for part in p.parts)]
    scripts = [p for p in (REPO / "tools" / "research").glob("*.py")]
    commits = git_commits()
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "learning_cards": len(cards.get("cards") or []),
        "kb_units": units.get("total") if isinstance(units, dict) else None,
        "tools_py": len(tools),
        "research_scripts": len(scripts),
        "portal_pages": len(pages),
        "commits": commits,
        "daily_screening": {
            "date": (load_json(static / "daily_screening.json") or {}).get("date"),
            "rs_intersection": len((load_json(static / "daily_screening.json") or {})
                                   .get("rs_intersection") or []),
        },
        "qa_samples": {
            "schema_version": (load_json(static / "kb_qa_samples.json") or {}).get("schema_version"),
            "prompt_version": (load_json(static / "kb_qa_samples.json") or {}).get("prompt_version"),
            "n": len((load_json(static / "kb_qa_samples.json") or {}).get("samples") or []),
        },
    }


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def build_report(pages: list[dict], statics: list[dict], system: dict,
                 base_url: str = BASE_URL) -> dict:
    slow = [p for p in pages if (p.get("ms") or 0) > 1500]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "portal_health",
        "date_tag": DATE_TAG,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "base_url": base_url,
        "summary": {
            "pages_total": len(pages),
            "pages_ok": sum(1 for p in pages if p["ok"]),
            "pages_fail": sum(1 for p in pages if not p["ok"]),
            "slow_pages": len(slow),
            "static_total": len(statics),
            "json_invalid": sum(1 for s in statics if not s["valid_json"]),
            "contract_fail": sum(1 for s in statics if s.get("contract_ok") is False),
        },
        "pages": pages,
        "slow_pages": slow,
        "static": statics,
        "system": system,
    }


def render_md(rep: dict) -> str:
    s = rep["summary"]
    L = [f"# 门户健康巡检（{rep['date_tag']}）", ""]
    L.append(f"- 生成：{rep['generated_at']}；站点：{rep['base_url']}")
    L.append(f"- 页面 {s['pages_total']} 个：**OK {s['pages_ok']}** / 失败 {s['pages_fail']} / "
             f"慢页(>1.5s) {s['slow_pages']}；静态 JSON {s['static_total']} 个（非法 {s['json_invalid']}、"
             f"契约不符 {s['contract_fail']}）")
    L.append("")
    L += ["## 一、页面巡检", ""]
    bad = [p for p in rep["pages"] if not p["ok"]]
    if bad:
        L += ["**异常页：**", "", "| 页面 | 状态 | 大小 | 耗时(ms) | 错误 |", "|---|---|---|---|---|"]
        for p in bad:
            L.append(f"| /{p['page']} | {p['status']} | {p['size']} | {p['ms']} | {p['error']} |")
        L.append("")
    else:
        L += ["- 全部页面 HTTP 200 ✅", ""]
    L += ["| 页面 | 状态 | 大小(B) | 耗时(ms) |", "|---|---|---|---|"]
    for p in rep["pages"]:
        mark = "✅" if p["ok"] else "⚠️"
        L.append(f"| /{p['page']} | {mark} {p['status']} | {p['size']} | {p['ms']} |")
    L.append("")
    L += ["## 二、静态 JSON 契约", ""]
    L += ["| 文件 | 大小(B) | JSON | 契约 | 问题 |", "|---|---|---|---|---|"]
    for st in rep["static"]:
        contract = "—" if st.get("contract_ok") is None else ("✅" if st["contract_ok"] else "❌")
        L.append(f"| {st['file']} | {st['size']} | {'✅' if st['valid_json'] else '❌'} | "
                 f"{contract} | {'；'.join(st['issues'])[:120] or '—'} |")
    L.append("")
    sysd = rep["system"]
    L += ["## 三、系统总览", "",
          f"- 学习卡 **{sysd['learning_cards']}** 张；知识单元 **{sysd['kb_units']}**；"
          f"工具脚本 **{sysd['tools_py']}** 个（research {sysd['research_scripts']}）；"
          f"门户页面 **{sysd['portal_pages']}**",
          f"- 提交数 **{(sysd.get('commits') or {}).get('count')}**；最新 "
          f"`{(sysd.get('commits') or {}).get('latest')}`",
          f"- daily_screening：date={(sysd.get('daily_screening') or {}).get('date')}、"
          f"RS 交集 {(sysd.get('daily_screening') or {}).get('rs_intersection')}；"
          f"问答样例 schema={ (sysd.get('qa_samples') or {}).get('schema_version') }/"
          f"{ (sysd.get('qa_samples') or {}).get('prompt_version') }/"
          f"{ (sysd.get('qa_samples') or {}).get('n') } 条", ""]
    L += ["## 结论", ""]
    L.append("- 巡检只读，不重启服务；异常页/契约不符项见上表，需按需修复。")
    L.append("")
    return "\n".join(L)


def render_system_md(system: dict) -> str:
    commits = system.get("commits") or {}
    L = [f"# 零界系统总览（{DATE_TAG}）", ""]
    L.append(f"- 生成：{system['generated_at']}")
    L += ["", "| 指标 | 数值 |", "|---|---|",
          f"| 学习卡 | **{system['learning_cards']}** 张 |",
          f"| 知识单元 | **{system['kb_units']}** |",
          f"| 工具脚本（tools/**/*.py） | **{system['tools_py']}** |",
          f"| research 脚本 | {system['research_scripts']} |",
          f"| 门户页面（portal/*.html） | **{system['portal_pages']}** |",
          f"| Git 提交数 | **{commits.get('count')}** |",
          f"| 最新提交 | `{commits.get('latest')}` |", ""]
    ds = system.get("daily_screening") or {}
    qa = system.get("qa_samples") or {}
    L += ["## 近期产物", "",
          f"- 每日筛选：数据日 {ds.get('date')}，量学∩RS {ds.get('rs_intersection')} 只（门户 /screening.html）",
          f"- 问答样例：schema {qa.get('schema_version')}、prompt {qa.get('prompt_version')}、"
          f"{qa.get('n')} 条（/kb_reports.html）", "",
          "## 门户", "",
          f"- 健康巡检见 `research/reports/portal_health_{DATE_TAG}.md`（逐页状态/契约/异常）。", ""]
    return "\n".join(L)


def run(base_url: str = BASE_URL, timeout: float = 5.0,
        out_dir: Path | str = REPORTS, deliver_dir: Path | str = DELIVER,
        report_json: Path | str | None = None, report_md: Path | str | None = None,
        system_json: Path | str | None = None, system_md: Path | str | None = None,
        fetch_fn=None) -> dict:
    pages = inspect_pages(base_url, timeout=timeout, fetch_fn=fetch_fn)
    statics = inspect_static()
    system = system_snapshot()
    rep = build_report(pages, statics, system, base_url)
    out_dir, deliver_dir = Path(out_dir), Path(deliver_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    deliver_dir.mkdir(parents=True, exist_ok=True)
    rj = Path(report_json) if report_json else out_dir / f"portal_health_{DATE_TAG}.json"
    rm = Path(report_md) if report_md else out_dir / f"portal_health_{DATE_TAG}.md"
    sj = Path(system_json) if system_json else out_dir / f"system_status_{DATE_TAG}.json"
    sm = Path(system_md) if system_md else deliver_dir / "2026-11-02_系统总览_v1.md"
    rj.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    rm.write_text(render_md(rep), encoding="utf-8")
    sj.write_text(json.dumps({"schema_version": "system_status.v1", **system},
                             ensure_ascii=False, indent=1), encoding="utf-8")
    sm.write_text(render_system_md(system), encoding="utf-8")
    rep["outputs"] = {"portal_health_json": str(rj), "portal_health_md": str(rm),
                      "system_status": str(sj), "system_overview_md": str(sm)}
    return rep


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="门户全站巡检 + 静态契约 + 系统总览")
    ap.add_argument("--base-url", default=BASE_URL)
    ap.add_argument("--timeout", type=float, default=5.0)
    ap.add_argument("--out-dir", default=str(REPORTS))
    ap.add_argument("--deliver-dir", default=str(DELIVER))
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    rep = run(base_url=args.base_url, timeout=args.timeout,
              out_dir=args.out_dir, deliver_dir=args.deliver_dir)
    print(json.dumps({"summary": rep["summary"], "system": {
        "cards": rep["system"]["learning_cards"], "units": rep["system"]["kb_units"],
        "tools": rep["system"]["tools_py"], "pages": rep["system"]["portal_pages"],
        "commits": (rep["system"].get("commits") or {}).get("count")},
        "outputs": rep["outputs"]}, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
