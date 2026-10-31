# -*- coding: utf-8 -*-
"""黄忠性能/SLO 信号（只读）：关键端口/页面延迟抽样，纳入验收证据面。

对默认目标做 N 次 GET 采样，计算 p50 / max / ok_rate，与 SLO 阈值比较后给出
`ok|warn|fail` 信号（JSON + 中文 md），供定时巡检/告警/11-04 复核材料引用。

默认目标（可用 --targets "name=url[,name2=url2]" 覆盖）：
  · portal 8734：http://127.0.0.1:8734/static/huangzhong_events.html
    SLO：p50 ≤ 800ms 且 ok_rate ≥ 2/3（必需目标）；
  · events 8091：http://127.0.0.1:8091/api/huangzhong/events
    SLO：p50 ≤ 500ms 且 ok_rate ≥ 2/3（必需目标）；
  · hermes 8787：http://127.0.0.1:8787/ —— **可选目标**（不可达/失败 → warn 不 fail）。

判定（逐目标，必需目标）：
  · 全失败 / ok_rate < 2/3 / p50 > SLO → **fail**；
  · 个别失败（ok_rate 达标但不为 1.0）→ **warn**；
  · 全部成功且 p50 达标 → **ok**。
可选目标：全失败/部分失败 → warn（不 fail）；全部成功 → ok。
总体：任一 fail → fail；否则任一 warn → warn；否则 ok。
退出码：0=ok / 1=warn / 2=fail（数据/参数错误同为 2，fail-visible 不抛异常）。

边界：只读采样（GET 本机 URL），不改服务/端口/配置、不写 work/；HTTP 经模块级
`_http(url, timeout)` 可注入（单测禁用真实网络）。md 报告默认落
C:\\项目\\deliverables\\2026-10-31_黄忠性能SLO_<ts>.md，`--no-write` 可关闭。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_OUT_DIR = Path(r"C:\项目\deliverables")
DEFAULT_SAMPLES = 3
DEFAULT_TIMEOUT = 5.0
MIN_OK_RATE = 2.0 / 3.0
DEFAULT_TARGETS = (
    {"name": "portal_8734",
     "url": "http://127.0.0.1:8734/static/huangzhong_events.html",
     "slo_p50_ms": 800.0, "min_ok_rate": MIN_OK_RATE, "required": True,
     "note": "黄忠事件页（门户 8734）"},
    {"name": "events_8091",
     "url": "http://127.0.0.1:8091/api/huangzhong/events",
     "slo_p50_ms": 500.0, "min_ok_rate": MIN_OK_RATE, "required": True,
     "note": "黄忠事件 API（8091）"},
    {"name": "hermes_8787", "url": "http://127.0.0.1:8787/",
     "slo_p50_ms": None, "min_ok_rate": None, "required": False,
     "note": "可选：Hermes 8787（不可达→warn 不 fail）"},
)
TOOL_VERSION = "v1 (2026-10-31)"
STATUS_LABEL = {"ok": "SLO 达标", "warn": "部分信号待关注",
                "fail": "SLO 不达标"}


# ------------------------------------------------------------------ 基础工具

def _now() -> dt.datetime:
    """当前时间（本机时区 aware）；测试可注入 now。"""
    return dt.datetime.now().astimezone()


def _iso(ts: dt.datetime | None) -> str | None:
    return ts.isoformat(timespec="seconds") if isinstance(ts, dt.datetime) else None


def _http(url: str, timeout: float = DEFAULT_TIMEOUT) -> dict:
    """模块级可注入的 HTTP GET（urllib）：→ {ok, status, latency_ms, error}。"""
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            url, method="GET", headers={"User-Agent": "huangzhong-perf/1.0"})
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            resp.read(1024)          # 读少量 body 使连接完成（不解析页面）
        latency = round((time.perf_counter() - t0) * 1000.0, 2)
        ok = bool(isinstance(status, int) and 200 <= status < 400)
        return {"ok": ok, "status": status, "latency_ms": latency, "error": None}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": getattr(e, "code", None),
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "error": "HTTPError: %s" % getattr(e, "code", "?")}
    except Exception as e:  # noqa: BLE001  fail-visible，不中断采样
        return {"ok": False, "status": None,
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "error": "%s: %s" % (type(e).__name__, str(e)[:200])}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ------------------------------------------------------------------ 目标解析

def parse_targets(spec: str) -> list[dict]:
    """解析 --targets "name=url[,name2=url2]"；非法 → ValueError。"""
    targets: list[dict] = []
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError("目标格式应为 name=url（实际：%r）" % part)
        name, _, url = part.partition("=")
        name, url = name.strip(), url.strip()
        if not name or not url:
            raise ValueError("目标 name/url 不能为空（实际：%r）" % part)
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("目标 URL 需以 http(s):// 开头（实际：%r）" % url)
        slo = 500.0 if ("8091" in name or "8091" in url) else 800.0
        targets.append({"name": name, "url": url, "slo_p50_ms": slo,
                        "min_ok_rate": MIN_OK_RATE, "required": True,
                        "note": "CLI --targets 覆盖目标"})
    if not targets:
        raise ValueError("--targets 未解析出任何目标")
    return targets


# ------------------------------------------------------------------ 采样与判定

def sample_target(target: dict, samples: int, timeout: float) -> dict:
    """对单个目标采样 N 次并计算 p50/max/ok_rate（不抛异常）。"""
    attempts = []
    for i in range(1, max(1, int(samples)) + 1):
        try:
            got = _http(target["url"], timeout)
        except Exception as e:  # noqa: BLE001  注入/网络异常一律记失败，不中断
            got = {"ok": False, "status": None, "latency_ms": None,
                   "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
        got = got if isinstance(got, dict) else {
            "ok": False, "status": None, "latency_ms": None,
            "error": "invalid _http return"}
        attempts.append({
            "i": i, "ok": bool(got.get("ok")),
            "status": got.get("status"),
            "latency_ms": got.get("latency_ms"),
            "error": got.get("error"),
        })
    ok_lat = [a["latency_ms"] for a in attempts
              if a["ok"] and _num(a["latency_ms"])]
    total = len(attempts)
    ok_count = sum(1 for a in attempts if a["ok"])
    ok_rate = round(ok_count / total, 4) if total else 0.0
    p50 = round(float(statistics.median(ok_lat)), 2) if ok_lat else None
    max_ms = round(float(max(ok_lat)), 2) if ok_lat else None
    slo = target.get("slo_p50_ms")
    min_rate = target.get("min_ok_rate")
    required = bool(target.get("required", True))
    reasons: list[str] = []
    if required:
        if total == 0:
            status = "fail"
            reasons.append("无采样")
        elif ok_count == 0:
            status = "fail"
            reasons.append("全部失败（ok_rate=0）")
        else:
            status = "ok"
            if _num(slo) and p50 is not None and p50 > float(slo):
                status = "fail"
                reasons.append("p50 %.1fms > SLO %.0fms" % (p50, float(slo)))
            if (min_rate is not None and _num(min_rate)
                    and ok_rate < float(min_rate)) and status != "fail":
                status = "fail"
                reasons.append("ok_rate %.2f < 阈值 %.2f"
                               % (ok_rate, float(min_rate)))
            elif ok_rate < 1.0 and status == "ok":
                status = "warn"
                reasons.append("个别失败（%d/%d 成功）" % (ok_count, total))
    else:
        if total == 0 or ok_count == 0:
            status = "warn"
            reasons.append("可选目标不可达/全部失败（warn 不 fail）")
        elif ok_rate < 1.0:
            status = "warn"
            reasons.append("可选目标部分失败（%d/%d 成功）" % (ok_count, total))
        else:
            status = "ok"
            reasons.append("可选目标全部成功（无硬 SLO）")
    return {
        "name": target["name"], "url": target["url"],
        "required": required, "note": target.get("note") or "",
        "slo_p50_ms": slo if _num(slo) else None,
        "min_ok_rate": float(min_rate) if _num(min_rate) else None,
        "samples": total, "ok_count": ok_count, "fail_count": total - ok_count,
        "ok_rate": ok_rate, "p50_ms": p50, "max_ms": max_ms,
        "status": status, "reasons": reasons, "attempts": attempts,
    }


def _aggregate(targets: list[dict]) -> str:
    statuses = [t["status"] for t in targets]
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "ok"


# ------------------------------------------------------------------ 报告渲染

def _render_md(rep: dict) -> str:
    md = ["# 黄忠性能/SLO 信号报告", "",
          "> 只读采样：GET 本机关键端口/页面 N 次，计算 p50/max/ok_rate 与 SLO 阈值；"
          "不改服务/端口/配置、不写 work/。",
          "> 生成时间：%s ｜ 采样数：%d ｜ 单次超时：%.1f s"
          % (rep["generated_at"], rep["samples"], rep["timeout"]),
          "", "## 一、总体结论", "",
          "- 状态：**%s**（%s）" % (rep["status"], rep["status_label"]),
          "- 目标：%d（ok=%d / warn=%d / fail=%d）"
          % (rep["summary"]["total"], rep["summary"]["ok"],
             rep["summary"]["warn"], rep["summary"]["fail"]),
          "", "## 二、SLO 明细", "",
          "| 目标 | URL | 类型 | 采样 | ok_rate | p50(ms) | max(ms) | SLO | 结论 | 说明 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for t in rep["targets"]:
        slo = ("p50≤%.0fms 且 ok_rate≥%.2f"
               % (t["slo_p50_ms"], t["min_ok_rate"])
               if t["required"] and _num(t["slo_p50_ms"]) else "无硬 SLO（可选）")
        md.append("| %s | %s | %s | %d | %.2f | %s | %s | %s | %s | %s |"
                  % (t["name"], t["url"], "必需" if t["required"] else "可选",
                     t["samples"], t["ok_rate"],
                     ("%.1f" % t["p50_ms"]) if t["p50_ms"] is not None else "-",
                     ("%.1f" % t["max_ms"]) if t["max_ms"] is not None else "-",
                     slo, t["status"], "；".join(t["reasons"]) or "-"))
    md += ["", "## 三、逐次采样", ""]
    for t in rep["targets"]:
        md.append("- **%s**（%s）：" % (t["name"], t["status"]))
        for a in t["attempts"]:
            md.append("  - #%d：latency=%sms status=%s ok=%s%s"
                      % (a["i"],
                         a["latency_ms"] if a["latency_ms"] is not None else "-",
                         a["status"] if a["status"] is not None else "-",
                         a["ok"],
                         "；error=%s" % a["error"] if a.get("error") else ""))
    md += ["", "## 四、建议", ""]
    suggestions = rep.get("suggestions") or []
    md += ["- " + x for x in suggestions] if suggestions else ["- 无"]
    md += ["", "## 五、Warnings（fail-visible）", ""]
    md += ["- " + w for w in rep["warnings"]] if rep.get("warnings") else \
        ["- 无（fail-visible 检查全部通过）"]
    md += ["", "## 六、复跑命令", "",
           "```powershell",
           "cd C:\\项目\\老田的AI助手",
           "C:\\项目\\py312\\python.exe -X utf8 tools\\huangzhong_perf_signal.py --json",
           "```",
           "",
           "> 免责声明：本信号为**本机采样时点**的性能观测，不构成 SLA 承诺；"
           "仅作验收证据面/巡检告警输入。", ""]
    return "\n".join(md)


# ------------------------------------------------------------------ 主流程

def run(targets: list[dict] | None = None, samples: int = DEFAULT_SAMPLES,
        timeout: float = DEFAULT_TIMEOUT, out_dir: Path | str | None = None,
        now: dt.datetime | None = None, write_md: bool = True) -> dict:
    """执行采样 + 判定 + 报告；参数/数据异常 fail-visible（不抛异常）。"""
    now = now or _now()
    samples = int(samples)
    if samples < 1:
        return {"ok": False, "tool": "huangzhong_perf_signal.py",
                "version": TOOL_VERSION, "generated_at": _iso(now),
                "status": "fail", "status_label": STATUS_LABEL["fail"],
                "error": "--samples 必须 ≥1（实际：%d）" % samples,
                "targets": [], "summary": {"total": 0, "ok": 0, "warn": 0,
                                           "fail": 0},
                "suggestions": [], "warnings": [],
                "report_md": None}
    if not _num(timeout) or float(timeout) <= 0:
        return {"ok": False, "tool": "huangzhong_perf_signal.py",
                "version": TOOL_VERSION, "generated_at": _iso(now),
                "status": "fail", "status_label": STATUS_LABEL["fail"],
                "error": "--timeout 必须 >0（实际：%r）" % timeout,
                "targets": [], "summary": {"total": 0, "ok": 0, "warn": 0,
                                           "fail": 0},
                "suggestions": [], "warnings": [],
                "report_md": None}
    targets = [dict(t) for t in (targets if targets is not None
                                 else DEFAULT_TARGETS)]
    rows = [sample_target(t, samples, float(timeout)) for t in targets]
    status = _aggregate(rows)
    summary = {"total": len(rows),
               "ok": sum(1 for r in rows if r["status"] == "ok"),
               "warn": sum(1 for r in rows if r["status"] == "warn"),
               "fail": sum(1 for r in rows if r["status"] == "fail")}
    suggestions, warnings = [], []
    for r in rows:
        if r["status"] == "fail":
            warnings.append("%s 失败：%s" % (r["name"], "；".join(r["reasons"])))
        elif r["status"] == "warn":
            warnings.append("%s 告警：%s" % (r["name"], "；".join(r["reasons"])))
    if status == "ok":
        suggestions.append("SLO 达标：可将本信号作为 11-04 复核「性能证据面」附件；"
                           "建议定时复跑（如每 30 分钟）")
    elif status == "warn":
        suggestions.append("部分采样失败/可选目标不可达：先核服务存活与网络回环，"
                           "再复跑；个别失败不影响 SLO 结论但需留档")
    else:
        suggestions.append("SLO 不达标：检查对应服务（8734 门户 / 8091 API）进程、"
                           "端口监听与负载；修复后复跑并留证")
    rep = {
        "ok": status != "fail" and not any(r["required"] and r["status"] == "fail"
                                           for r in rows) if rows else False,
        "tool": "huangzhong_perf_signal.py",
        "version": TOOL_VERSION,
        "generated_at": _iso(now),
        "status": status,
        "status_label": STATUS_LABEL[status],
        "samples": samples,
        "timeout": float(timeout),
        "read_only": True,
        "targets": rows,
        "summary": summary,
        "suggestions": suggestions,
        "warnings": warnings,
        "report_md": None,
    }
    if write_md:
        out = Path(out_dir) if out_dir else DEFAULT_OUT_DIR
        try:
            out.mkdir(parents=True, exist_ok=True)
            mp = out / ("2026-10-31_黄忠性能SLO_%s.md"
                        % now.strftime("%Y%m%d_%H%M%S"))
            mp.write_text(_render_md(rep), encoding="utf-8")
            rep["report_md"] = str(mp)
        except OSError as e:
            warnings.append("md 报告写入失败：%s: %s" % (type(e).__name__, e))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="黄忠性能/SLO 信号（只读采样：8734 页面 / 8091 API / 可选 8787）")
    ap.add_argument("--json", action="store_true", help="输出完整 JSON")
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES,
                    help="每目标采样次数（默认 3）")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help="单次请求超时秒（默认 5）")
    ap.add_argument("--targets", default="",
                    help='覆盖目标："name=url[,name2=url2]"（默认 8734/8091/8787）')
    ap.add_argument("--out-dir", default="",
                    help="md 报告目录（默认 C:\\项目\\deliverables）")
    ap.add_argument("--no-write", action="store_true",
                    help="不写 md 报告（仅输出 JSON/信号）")
    args = ap.parse_args(argv)
    targets = None
    try:
        if args.targets:
            targets = parse_targets(args.targets)
    except ValueError as e:
        rep = {"ok": False, "tool": "huangzhong_perf_signal.py",
               "version": TOOL_VERSION, "generated_at": _iso(_now()),
               "status": "fail", "status_label": STATUS_LABEL["fail"],
               "error": str(e), "targets": [],
               "summary": {"total": 0, "ok": 0, "warn": 0, "fail": 0},
               "suggestions": [], "warnings": [], "report_md": None}
        print(json.dumps(rep, ensure_ascii=False, indent=1)
              if args.json else "错误：%s" % e)
        return 2
    rep = run(targets=targets, samples=args.samples, timeout=args.timeout,
              out_dir=args.out_dir or None, write_md=not args.no_write)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print("黄忠性能/SLO：status=%s（ok=%d warn=%d fail=%d）"
              % (rep["status"], rep["summary"]["ok"], rep["summary"]["warn"],
                 rep["summary"]["fail"]))
        for t in rep["targets"]:
            print("  [%s] %s：ok_rate=%.2f p50=%sms max=%sms（%s）"
                  % (t["status"], t["name"], t["ok_rate"],
                     t["p50_ms"] if t["p50_ms"] is not None else "-",
                     t["max_ms"] if t["max_ms"] is not None else "-",
                     "；".join(t["reasons"]) or "-"))
        if rep.get("report_md"):
            print("报告：%s" % rep["report_md"])
    if rep.get("error"):
        return 2
    return {"ok": 0, "warn": 1, "fail": 2}.get(rep["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
