# -*- coding: utf-8 -*-
"""采集链健康只读出口 mixin（L0/L1 观测面 → 情报工作台面板，2026-11-08）。

GET /api/intel/health
  数据源：intel_collector/registry/sources.json（rights 8 键 + sla + enabled）
        + intel_collector/store/observations.db（观测新鲜度）。
  计算内核：复用 intel_collector/examples/feedback_dry.py 的 run()——
        compute_sla + scan_store_pii + compute_sla_feedback 纯计算，
        自带零写入断言（前后 sha256 一致），与每日 08:35 计划任务干跑
        同一内核，面板数字 = 台账数字。

只读纪律（沿 api_intel_bus 判例）：零写入、fail-visible（错误进 errors/
ok=false，不整包假绿）、无缓存直读。本出口只消费，绝不触发采集侧任何
动作（不 apply、不 force_due、不写 DB/真源/配置）。

信封：{"ok","schema_ver","endpoint","generated_at","summary",
       "sources","pii","actions","n_sources","n_obs","errors"}
sources[i] = {"id","name","enabled","state","age_min","sla_freshness",
              "note","rights":{8键},"rights_meta_receipt":bool}
"""

from __future__ import annotations

import contextlib
import importlib.util
import io as _io
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTOR_ROOT = os.path.join(ROOT, "intel_collector")
FEEDBACK_DRY = os.path.join(COLLECTOR_ROOT, "examples", "feedback_dry.py")
SOURCES_PATH = os.path.join(COLLECTOR_ROOT, "registry", "sources.json")

SCHEMA_VER = "1.0"

# 周期采集器覆盖的源（与 intel_collector/adapters/ 目录对齐：guba/news/xueqiu/
# xueqiu_live/youtube_envelope；news 族 8 子源均由 news_adapter 覆盖）。
# 不在此清单内的源为静态/工具型（youtube_corpus、pdf_methodology、
# res_downloader_py 等，入库即完成、无周期采集）→ 无观测心跳属预期，
# 面板标 "static" 与真断供(no_data)区分。新增 adapter 时须同步此清单。
PERIODIC_ADAPTERS = {
    "guba", "news", "xueqiu", "xueqiu_live", "youtube_envelope",
    "news_sina", "news_announcement", "news_chinanews", "news_eastmoney",
    "news_govpolicy", "news_report", "news_survey", "news_wallstreetcn",
    "news_world",
}

# 内部 5 用途（本轮 C9A 批 true）与外发 3 用途（批 false）——展示摘要用
USE_DISPLAY = [
    ("internal_retention", True), ("cleaning", True), ("indexing", True),
    ("candidate_labeling", True), ("controlled_query", True),
    ("redistribution", False), ("commercial", False), ("training", False),
]


def _load_feedback_dry():
    spec = importlib.util.spec_from_file_location("feedback_dry_panel", FEEDBACK_DRY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def collect_health() -> tuple[dict, int]:
    """只读聚合：SLA 计算 + 权利摘要。返回 (信封, http_status)。"""
    errors: dict[str, str] = {}
    raw = json.loads(_io.open(SOURCES_PATH, encoding="utf-8").read())

    # ① SLA/观测计算——复用干跑内核（自带零写入断言）；抑制其控制台打印
    fd = _load_feedback_dry()
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = fd.run()

    sla = res.get("sla") or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    sources = []
    summary = {"ok": 0, "breach": 0, "no_data": 0, "static": 0,
               "disabled": 0, "other": 0}
    for sid, spec in raw.items():
        if sid.startswith("_"):
            continue  # _governance 等治理占位不入面板
        s = sla.get(sid) or {}
        state = s.get("state") or ("disabled" if not spec.get("enabled") else "unknown")
        # 静态/工具型源无周期采集心跳，no_data 属预期 → 与真断供区分
        if state == "no_data" and sid not in PERIODIC_ADAPTERS:
            state = "static"
        if not spec.get("enabled"):
            summary["disabled"] += 1
        elif state in summary:
            summary[state] += 1
        else:
            summary["other"] += 1
        rights = spec.get("rights") or {}
        sources.append({
            "id": sid,
            "name": spec.get("name", ""),
            "enabled": bool(spec.get("enabled")),
            "state": state,
            "age_min": s.get("age_minutes"),
            "sla_freshness": (spec.get("sla") or {}).get("freshness_minutes"),
            "note": s.get("note", ""),
            "rights": {k: bool(rights.get(k)) for k, _ in USE_DISPLAY},
            "rights_external_open": any(
                rights.get(k) for k in ("redistribution", "commercial", "training")),
            "rights_meta_receipt": all(
                (spec.get("rights_meta") or {}).get(u, {}).get("receipt", "").startswith("C9A-YI")
                for u in ("internal_retention", "cleaning", "indexing", "candidate_labeling",
                          "controlled_query", "redistribution", "commercial", "training")),
        })

    envelope = {
        "ok": not errors,
        "schema_ver": SCHEMA_VER,
        "endpoint": "/api/intel/health",
        "generated_at": now,
        "summary": summary,
        "sources": sources,
        "pii": res.get("pii") or [],
        "actions": res.get("actions") or [],
        "n_sources": res.get("n_sources"),
        "n_obs": res.get("n_obs"),
        "errors": errors,
        "caveat": "生产观测回写缺失（L1）期间，观测面数字仅反映演示/桥接观测现状，不代表采集链全貌",
    }
    return envelope, 200


class Handler:  # Mixin 壳：经 portal_api 基座多继承并入
    def _api_intel_health(self):
        try:
            body, status = collect_health()
        except AssertionError as exc:  # 干跑内核零写入断言被破坏——重大异常，如实报红
            body = {"ok": False, "endpoint": "/api/intel/health",
                    "error": "零写入断言失败：%s" % exc}
            status = 500
        except Exception as exc:
            body = {"ok": False, "endpoint": "/api/intel/health",
                    "error": "健康聚合失败：%s" % exc}
            status = 500
        self._json(body, status)
