#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合 Q2 健康探针（只读）：只 GET 本机只读端点，不写服务、不改 manifest。

WO-ACAD1-PROBE-20261104 扩展 acad1-legion（司马徽军团接口，见
docs/plans/2026-11-04_司马徽军团接口探针_v1.md）：
  - 公开 4 端点按 2xx 判 ok：8105 `/api/v1/health`、8734 `/academy/shuijing`（页面 200）、
    8091 `/api/acad1/pool`、8734 `/quant-api/api/v1/hermes/acad1-comparison`；
  - 6 个 `acad1-research` 端点**未登录 401 是预期鉴权语义**（8734 宿主入口 → 8105 JWT
    auth）：按 `expected_status=401` 判定，check 标 ``auth_required=True``，
    **不判失败**；若这些端点不该公开却返回 2xx（鉴权门缺失）或网络/5xx，则 ok=False 并
    带 `unexpected_status`/`errors`（不掩盖）。
  - 所有请求仍只带 `Accept: application/json`，**不发送 Cookie/Authorization**，不伪造 200。

WO-Q2-CLOSE-20261104 扩展：
  - 新增 `lusu-d1-flow` 目标（8091 `/api/health`、`/health`、D1 research/
    decisions/handoffs/meeting/watch-plans/training episodes），**只读 GET**；
    默认每端点 5 样本（真实采样，样本字段 samples/statuses/latencies_ms/
    latency_ms_min|median|max）；
  - `--target d1`（别名：d1/lusu→lusu-d1-flow、acad1→acad1-legion、dw→
    data-warehouse）；输出支持 `--json` / `--markdown`；`--out` 显式落盘证据
    （建议 C:\\Temp，不写仓库生产数据）；
  - 原有 acad1-legion / data-warehouse 保持兼容：默认 1 样本，check 既有字段
    （name/url/ok/status/latency_ms/evidence）不变，仅新增聚合字段。

不启停服务、不写数据、不发起写请求（无 POST）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TARGETS = {
    # WO-ACAD1-PROBE-20261104：公开 4 端点（未登录应 2xx）
    "acad1-legion": [
        ("pool", "http://127.0.0.1:8091/api/acad1/pool"),
        ("zhice-health", "http://127.0.0.1:8105/api/v1/health"),
        ("shuijing-page", "http://127.0.0.1:8734/academy/shuijing"),
        ("hermes-acad1-comparison",
         "http://127.0.0.1:8734/quant-api/api/v1/hermes/acad1-comparison"),
    ],
    "data-warehouse": [
        ("dw-stats", "http://127.0.0.1:8091/api/dw/stats"),
        ("catalog", "http://127.0.0.1:8091/api/dw/search?q=__none__"),
    ],
    # WO-Q2-CLOSE-20261104：D1 只读健康探针（8 目标 × 5 样本）
    "lusu-d1-flow": [
        ("api-health", "http://127.0.0.1:8091/api/health"),
        ("health", "http://127.0.0.1:8091/health"),
        ("d1-research", "http://127.0.0.1:8091/api/d1/research"),
        ("d1-decisions", "http://127.0.0.1:8091/api/d1/decisions"),
        ("d1-handoffs", "http://127.0.0.1:8091/api/d1/handoffs"),
        ("d1-meeting", "http://127.0.0.1:8091/api/d1/meeting"),
        ("d1-watch-plans", "http://127.0.0.1:8091/api/d1/watch-plans"),
        ("d1-training-episodes", "http://127.0.0.1:8091/api/d1/training/episodes"),
    ],
}

# WO-ACAD1-PROBE-20261104：未登录应 401 的端点（预期鉴权语义，不判失败）。
# 6 个 acad1-research 接口经 8734 /quant-api/ 反代到 8105：8105 服务内 JWT auth
# （get_current_user）未带 Bearer 时返回 401 {"detail":"missing token"}。
AUTH_TARGETS = {
    "acad1-legion": [
        ("acad1-research-pool",
         "http://127.0.0.1:8734/quant-api/api/v1/acad1-research/pool"),
        ("acad1-research-stats",
         "http://127.0.0.1:8734/quant-api/api/v1/acad1-research/stats"),
        ("acad1-research-reviews",
         "http://127.0.0.1:8734/quant-api/api/v1/acad1-research/reviews"),
        ("acad1-research-decisions",
         "http://127.0.0.1:8734/quant-api/api/v1/acad1-research/decisions"),
        ("acad1-research-evidence",
         "http://127.0.0.1:8734/quant-api/api/v1/acad1-research/evidence"),
        ("acad1-research-calendar",
         "http://127.0.0.1:8734/quant-api/api/v1/acad1-research/calendar"),
    ],
}

# 预期鉴权端点：HTTP 状态必须等于该值才算 ok（401=auth_required 预期）
AUTH_EXPECTED_STATUS = 401

# --target 别名（D1 收口：允许 --target d1）
ALIASES = {
    "d1": "lusu-d1-flow",
    "lusu": "lusu-d1-flow",
    "lusu-d1": "lusu-d1-flow",
    "acad1": "acad1-legion",
    "dw": "data-warehouse",
}

# 每端点样本数：D1 按验收要求真实 5 样本；其余沿用单样本（兼容既有调用/门禁耗时）
DEFAULT_SAMPLES = {"lusu-d1-flow": 5}

SCHEMA = "zerobound/composition_health_probe/v1"
BATCH_SCHEMA = "zerobound/composition_health_probe_batch/v1"


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _get(url, timeout=5.0, expected_status=None):
    """只读 GET；expected_status 给定时按预期状态判 ok（如 401=auth_required）。

    不发送 Cookie/Authorization；HTTP 错误码也按状态记录（不吞成 status=0），
    便于区分「预期 401」与「真实 5xx/网络错误」。
    """
    t = time.time()
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            body = r.read(2048).decode("utf-8", "replace")[:500]
    except urllib.error.HTTPError as e:  # 4xx/5xx：状态码本身是证据
        status = int(e.code)
        try:
            body = e.read(2048).decode("utf-8", "replace")[:500]
        except Exception:  # noqa: BLE001
            body = ""
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 0,
                "latency_ms": int((time.time() - t) * 1000),
                "error": type(e).__name__ + ":" + str(e)[:200]}
    latency = int((time.time() - t) * 1000)
    if expected_status is not None:
        ok = status == expected_status
        row = {"ok": ok, "status": status, "latency_ms": latency,
               "evidence": body, "auth_required": True, "expected_status": expected_status}
        if not ok:
            row["error"] = "unexpected_status:%s (expected %s auth_required)" % (
                status, expected_status)
        return row
    return {"ok": 200 <= status < 300, "status": status,
            "latency_ms": latency, "evidence": body}


def _median(values):
    return int(statistics.median(values)) if values else None


def _aggregate(name, url, runs, expected_status=None):
    """多次采样聚合：全 ok 才算 ok；记录样本/状态/延迟分布（不伪造单样本绿）。

    expected_status 给定时即「预期鉴权端点」：status 全等于预期值（如 401）才 ok，
    并标 ``auth_required=True``；返回 2xx（鉴权门缺失）或网络错误均 ok=False。
    """
    ok = bool(runs) and all(bool(r.get("ok")) for r in runs)
    latencies = [r["latency_ms"] for r in runs if isinstance(r.get("latency_ms"), int)]
    first = next((r for r in runs if r.get("ok")), runs[0] if runs else {})
    check = {
        "name": name,
        "url": url,
        "ok": ok,
        "status": first.get("status", 0),
        "statuses": [r.get("status") for r in runs],
        "samples": len(runs),
        "latencies_ms": latencies,
        "latency_ms": _median(latencies),
        "latency_ms_min": min(latencies) if latencies else None,
        "latency_ms_median": _median(latencies),
        "latency_ms_max": max(latencies) if latencies else None,
        "evidence": str(first.get("evidence") or "")[:500],
        "auth_required": expected_status is not None,
        "expected_status": expected_status,
    }
    errors = [r.get("error") for r in runs if r.get("error")]
    if errors:
        check["errors"] = errors
    return check


def probe(composition, samples=None):
    """按组合探针；公开端点按 2xx、预期鉴权端点（AUTH_TARGETS）按 expected_status 判定。"""
    public_targets = TARGETS.get(composition) or []
    auth_targets = AUTH_TARGETS.get(composition) or []
    count = samples if samples is not None else DEFAULT_SAMPLES.get(composition, 1)
    count = max(1, int(count))
    entries = [(name, url, None) for name, url in public_targets]
    entries += [(name, url, AUTH_EXPECTED_STATUS) for name, url in auth_targets]
    checks = []
    for name, url, expected in entries:
        if expected is None:  # 公开端点：保持旧调用签名（_get(url)），兼容既有 monkeypatch
            runs = [_get(url) for _ in range(count)]
        else:  # 预期鉴权端点：按 expected_status 判定
            runs = [_get(url, expected_status=expected) for _ in range(count)]
        checks.append(_aggregate(name, url, runs, expected_status=expected))
    return {"schema": SCHEMA,
            "composition": composition, "checked_at": _now(),
            "samples_per_target": count,
            "public_targets": len(public_targets),
            "auth_required_targets": len(auth_targets),
            "ok": bool(checks) and all(x["ok"] for x in checks), "checks": checks}


def resolve_names(composition="", target="", all_compositions=False):
    """解析 --composition / --target 别名 / --all；返回 (names, error)。"""
    if all_compositions:
        return list(TARGETS), ""
    raw = str(composition or "").strip()
    if not raw and target:
        key = str(target).strip().lower()
        raw = ALIASES.get(key, key)
    if not raw:
        return [], "需要 --composition <name> / --target <d1|acad1|dw> / --all"
    if raw not in TARGETS:
        return [], "未知组合: %s（可选: %s）" % (raw, ", ".join(sorted(TARGETS)))
    return [raw], ""


def render_markdown(batch):
    """批结果 → Markdown（汇报/证据可直接贴）。"""
    lines = [
        "# Q2 健康探针（只读 GET）",
        "",
        "- 生成：%s｜组合数：%d｜ok：%d/%d"
        % (batch.get("generated_at"), batch["summary"]["compositions"],
           batch["summary"]["ok"], batch["summary"]["compositions"]),
        "",
        "| 组合 | 目标 | 样本 | 状态 | 延迟 median/max (ms) | ok | 鉴权 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in batch.get("results") or []:
        for check in row.get("checks") or []:
            lines.append(
                "| %s | %s | %s | %s | %s / %s | %s | %s |"
                % (row.get("composition"), check.get("name"), check.get("samples"),
                   check.get("status"), check.get("latency_ms_median"),
                   check.get("latency_ms_max"), check.get("ok"),
                   "auth_required" if check.get("auth_required") else "public")
            )
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="组合 Q2 健康探针（只读）")
    ap.add_argument("--composition", default="")
    ap.add_argument("--target", default="", help="别名：d1/lusu/acad1/dw")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--samples", type=int, default=None, help="每端点样本数（D1 默认 5）")
    ap.add_argument("--out", default="", help="将 JSON 证据写到指定路径（建议 C:\\Temp）")
    a = ap.parse_args(argv)
    names, err = resolve_names(a.composition, a.target, a.all)
    if err:
        print("q2_probe_error: " + err, file=sys.stderr)
        return 2
    rows = [probe(n, samples=a.samples) for n in names]
    out = {"schema": BATCH_SCHEMA,
           "generated_at": _now(), "results": rows,
           "summary": {"compositions": len(rows),
                       "ok": sum(1 for r in rows if r["ok"])}}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        path = Path(a.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    if a.markdown:
        print(render_markdown(out))
    elif a.json:
        print(text)
    else:
        print("\n".join("%s ok=%s checks=%s"
                        % (r["composition"], r["ok"],
                           [(c["name"], c["status"], c["ok"], c["samples"],
                             "auth_required" if c.get("auth_required") else "public")
                            for c in r["checks"]])
                        for r in rows))
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
