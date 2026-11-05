# -*- coding: utf-8 -*-
"""源可用性探活。

两种模式：
  默认（静态）  只查登记表自身一致性，不出网，毫秒级
  --live        对每个 source.probe.url 发最小请求，实测状态码与延迟

判定口径（写死，避免各人各判）：
  ok          2xx/3xx，且延迟 <= slow_ms
  slow        2xx/3xx，但延迟 > slow_ms（默认 3000ms）
  guarded     4xx（站点可达，缺头/参数/鉴权），不是故障
  unreachable 连接层失败（超时/DNS/重置）
  skipped     probe.url 为空（私有协议/无 HTTP 入口）

输出：reports/data_hub/probe_<ts>.jsonl 逐源事实 + 同名 .md 汇总。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import net, registry

HERE = Path(__file__).resolve().parent
REPORTS = HERE.parent.parent / "reports" / "data_hub"
SLOW_MS = 3000


def static_checks(data: dict, only: list[str] | None = None) -> list[dict]:
    """不出网的登记体检：结构问题 + 语义可疑点。only 给定时只检这些源。"""
    rows: list[dict] = []
    for problem in registry.validate(data):
        rows.append({"kind": "structure", "ok": False, "detail": problem})
    wanted = {x.strip() for x in only} if only else None
    for src in data.get("sources", []):
        sid = src.get("id")
        if wanted and sid not in wanted:
            continue
        probe = src.get("probe") or {}
        if src.get("status") == "verified" and not probe.get("url"):
            rows.append({"kind": "semantic", "ok": False, "source": sid,
                         "detail": "状态 verified 但无可探活 URL，需补 probe 或降级为 planned"})
        if src.get("status") == "unreachable" and not src.get("note"):
            rows.append({"kind": "semantic", "ok": False, "source": sid,
                         "detail": "不可达必须写 note 说明是出口限制还是源消失"})
        if src.get("status") == "verified" and src.get("proxy_policy") == "auto":
            rows.append({"kind": "semantic", "ok": True, "source": sid,
                         "detail": "verified 源用 auto 每次都要试错，建议固化为实测胜出路径"})
    return rows


def _verdict(result: dict, slow_ms: int) -> str:
    if not result.get("ok"):
        status = result.get("status")
        if status is None:
            return "unreachable"
        if 400 <= status < 500:
            return "guarded"
        return "unreachable"
    return "slow" if result.get("latency_ms", 0) > slow_ms else "ok"


def probe_one(src: dict, *, slow_ms: int = SLOW_MS, timeout: tuple[float, float] = (6.0, 15.0)) -> dict:
    probe = src.get("probe") or {}
    url = (probe.get("url") or "").strip()
    base = {
        "source": src.get("id"),
        "name": src.get("name"),
        "domain": src.get("domain"),
        "declared": src.get("status"),
        "policy": src.get("proxy_policy"),
    }
    if not url:
        return dict(base, verdict="skipped", detail=probe.get("note") or "无 HTTP 探针")
    started = time.time()
    result = net.fetch(
        url,
        policy=probe.get("policy") or src.get("proxy_policy", "auto"),
        timeout=timeout,
        headers=probe.get("headers") or None,
        source_id=f"probe:{src.get('id')}",
    )
    verdict = _verdict(result, slow_ms)
    # 只看状态码会漏掉"200 但返回 JS 挑战页/登录页"——实测 stooq 返回 796B
    # "This site requires JavaScript" 却是 200，之前被误判为可用。
    # probe.contains 给出"响应体必须出现"的判据，缺失即降级为 guarded。
    contains = probe.get("contains")
    detail = None
    if verdict in {"ok", "slow"} and contains:
        body = result.get("text") or ""
        if contains not in body:
            verdict = "guarded"
            detail = f"content_mismatch: 响应体未包含 {contains!r}（可能是挑战页/登录页）"
    return dict(
        base,
        verdict=verdict,
        status=result.get("status"),
        latency_ms=result.get("latency_ms"),
        bytes=result.get("bytes"),
        policy_used=result.get("policy"),
        error=result.get("error") or detail,
        url=url,
        elapsed_ms=int((time.time() - started) * 1000),
    )


def run_live(data: dict, *, only: list[str] | None = None, workers: int = 6,
             slow_ms: int = SLOW_MS) -> list[dict]:
    sources = [s for s in data.get("sources", []) if s.get("id")]
    if only:
        wanted = {x.strip() for x in only if x.strip()}
        sources = [s for s in sources if s.get("id") in wanted]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda s: probe_one(s, slow_ms=slow_ms), sources))


def _md(rows: list[dict], static: list[dict], started: str) -> str:
    live = [r for r in rows if r.get("verdict")]
    counts: dict[str, int] = {}
    for r in live:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    lines = [
        f"# 数据源探活报告 · {started}",
        "",
        "## 一、结论",
        "",
        f"- 探活源数：{len(live)}；结果分布：" + "，".join(f"{k} {v}" for k, v in sorted(counts.items())),
        f"- 登记表静态问题：{len([s for s in static if not s['ok']])} 条",
        "",
        "## 二、逐源结果",
        "",
        "| 源 | 域 | 声明 | 结论 | HTTP | 延迟 | 出口 | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(live, key=lambda x: (str(x.get("verdict")), str(x.get("source")))):
        lines.append(
            "| {s} | {d} | {dec} | {v} | {c} | {ms} | {p} | {note} |".format(
                s=r.get("source"),
                d=r.get("domain"),
                dec=r.get("declared"),
                v=r.get("verdict"),
                c=r.get("status") or "-",
                ms=(str(r.get("latency_ms")) + "ms") if r.get("latency_ms") is not None else "-",
                p=r.get("policy_used") or r.get("policy"),
                note=(r.get("error") or r.get("detail") or "")[:60].replace("|", "/"),
            )
        )
    drift = [r for r in live if r.get("declared") == "verified" and r.get("verdict") in {"unreachable", "guarded"}]
    if drift:
        lines += ["", "## 三、声明与实测不一致（需改登记或修出口）", ""]
        for r in drift:
            lines.append(f"- {r.get('source')}：登记 verified，实测 {r.get('verdict')}（{r.get('error') or r.get('status')}）")
    return "\n".join(lines) + "\n"


def write_report(rows: list[dict], static: list[dict], out_dir: Path | None = None) -> tuple[Path, Path]:
    out = Path(out_dir or REPORTS)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    jsonl = out / f"probe_{stamp}.jsonl"
    with jsonl.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        for row in static:
            fh.write(json.dumps(dict(row, kind=row.get("kind", "static")), ensure_ascii=False) + "\n")
    md = out / f"probe_{stamp}.md"
    md.write_text(_md(rows, static, datetime.now().strftime("%Y-%m-%d %H:%M")), encoding="utf-8")
    return jsonl, md
