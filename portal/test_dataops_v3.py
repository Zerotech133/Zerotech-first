#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_dataops_v3.py — 门户「指挥操作系统 v3」统一验收脚本
标准库 only（urllib/json/时间），全部 GET 只读。

覆盖四组断言：
  A. map 结构断言（layers/nodes/health 枚举/edges 完整性/节点重叠告警）
  B. entity 契约断言（upstream/downstream/404/400/连接完整性）
  C. 静态面断言（8 个 URL 全 200）
  D. 回归断言（xq/intel、perf/board、overview?view=summary）

退出码：0 = 全部 PASS，1 = 存在 FAIL。
若首轮存在 FAIL，等待 20 秒后整体重试一次（容忍并行工位中间态），以末轮结果为准。
"""

import json
import time
import urllib.request
import urllib.error

API = "http://127.0.0.1:8091"
STATIC = "http://127.0.0.1:8080"

EXPECT_LAYERS = ["服务", "采集", "数据池", "管道", "消费"]
HEALTH_ENUM = {
    "ok",
    "down",
    "running",
    "yielding",
    "blocked",
    "backoff",
    "done",
    "complete",
    "unknown",
    "disabled",
    "degraded",  # A06 收口：部分在岗（"N/M 在岗" 下沉 detail）
    "stale",  # 第二标准强化：armed 但产出落后 ≥1 天（GubaCrawl 判例）
}
STATIC_URLS = [
    "/dataops.html",
    "/home.html",
    "/xq_intel.html",
    "/picks.html",
    "/org.html",
    "/lineage.html",
    "/static/dataops_link.js",
    "/static/nav.js",
]
RETRY_WAIT = 20  # 秒，失败后重试前的等待


class Results:
    """收集断言结果。warning 只展示不影响退出码。"""

    def __init__(self, group):
        self.group = group
        self.passed = []  # (name, detail)
        self.failed = []  # (name, detail)
        self.warned = []  # (name, detail)

    def check(self, name, cond, detail=""):
        if cond:
            self.passed.append((name, detail))
            return True
        self.failed.append((name, detail))
        return False

    def warn(self, name, detail):
        self.warned.append((name, detail))


def fetch(url, timeout=15):
    """GET，返回 (status_code, body_text)。异常返回 (0, err)。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "qa-v3/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 — 网络类异常统一降级
        return 0, repr(e)


def fetch_json(url, timeout=15):
    """GET 并解析 JSON；非 200 也尝试解析 body（如 404 的 {error, known}）。"""
    status, body = fetch(url, timeout)
    if not body:
        return status, None
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, None


# ---------------------------------------------------------------- A. map
def check_map(res):
    status, mp = fetch_json(f"{API}/api/dataops/map")
    if not res.check(
        "A01 map 接口 200 且 JSON 可解析",
        status == 200 and isinstance(mp, dict),
        f"status={status}",
    ):
        return
    if mp is None:
        return

    layers = mp.get("layers")
    res.check(
        "A02 layers 恰为 服务/采集/数据池/管道/消费",
        layers == EXPECT_LAYERS,
        f"layers={layers}",
    )

    nodes = mp.get("nodes")
    res.check(
        "A03 nodes 为列表且数量 >= 22",
        isinstance(nodes, list) and len(nodes) >= 22,
        f"node_count={len(nodes) if isinstance(nodes, list) else 'N/A'}",
    )

    if not (isinstance(nodes, list) and nodes):
        return

    ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    res.check(
        "A04 每节点均有 id",
        all(ids),
        "存在缺失 id 的节点" if not all(ids) else f"{len(ids)} 个 id",
    )
    res.check(
        "A05 节点 id 全局唯一",
        len(ids) == len(set(ids)),
        f"重复项={sorted({i for i in ids if ids.count(i) > 1})}",
    )

    bad_health = [
        f"{n.get('id')}={n.get('health')!r}"
        for n in nodes
        if isinstance(n, dict) and n.get("health") not in HEALTH_ENUM
    ]
    res.check(
        "A06 所有节点 health 在合法枚举",
        not bad_health,
        f"越界值({len(bad_health)}): {', '.join(bad_health[:8])}",
    )

    bad_layer = [
        n.get("id")
        for n in nodes
        if isinstance(n, dict) and n.get("layer") not in EXPECT_LAYERS
    ]
    res.check("A07 每节点 layer 属于五层之一", not bad_layer, f"越界: {bad_layer[:5]}")

    bad_xy = [
        n.get("id")
        for n in nodes
        if isinstance(n, dict)
        and not (
            isinstance(n.get("x"), (int, float))
            and isinstance(n.get("y"), (int, float))
        )
    ]
    res.check("A08 每节点 x/y 为数值", not bad_xy, f"异常: {bad_xy[:5]}")
    bad_name = [n.get("id") for n in nodes if isinstance(n, dict) and not n.get("name")]
    res.check("A09 每节点有 name", not bad_name, f"缺失: {bad_name[:5]}")

    edges = mp.get("edges")
    id_set = set(ids)
    if isinstance(edges, list) and edges:
        bad_from = sorted(
            {
                e.get("from")
                for e in edges
                if isinstance(e, dict) and e.get("from") not in id_set
            }
        )
        bad_to = sorted(
            {
                e.get("to")
                for e in edges
                if isinstance(e, dict) and e.get("to") not in id_set
            }
        )
        res.check(
            "A10 edges.from 均在 nodes 集合内", not bad_from, f"悬空 from: {bad_from}"
        )
        res.check("A11 edges.to 均在 nodes 集合内", not bad_to, f"悬空 to: {bad_to}")
        no_rel = [e for e in edges if isinstance(e, dict) and not e.get("rel")]
        res.check("A12 每条边有 rel 字段", not no_rel, f"缺失条数={len(no_rel)}")
    else:
        res.check("A10 edges 为非空列表", False, f"edges={edges!r}")

    res.check(
        "A13 map 含 summary 键", "summary" in mp and isinstance(mp.get("summary"), dict)
    )

    # 告警级：同层内 x 差 <140 且 y 相同 → 视觉重叠
    overlaps = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            if not (isinstance(a, dict) and isinstance(b, dict)):
                continue
            if (
                a.get("layer") == b.get("layer")
                and a.get("y") == b.get("y")
                and isinstance(a.get("x"), (int, float))
                and abs(a["x"] - b["x"]) < 140
            ):
                overlaps.append(f"{a.get('id')}~{b.get('id')}")
    if overlaps:
        res.warn(
            "W01 同层节点疑似视觉重叠（x 差<140 且 y 相同）", "; ".join(overlaps[:10])
        )
    else:
        res.check("A14 无同层节点重叠", True)


# ------------------------------------------------------------- B. entity
def check_entity(res, node_ids):
    # pipe:classify 契约
    status, ent = fetch_json(f"{API}/api/dataops/entity?id=pipe:classify")
    if not res.check(
        "B01 pipe:classify 200 且 JSON 可解析",
        status == 200 and isinstance(ent, dict),
        f"status={status}",
    ):
        return
    if ent is None:
        return

    ups = [u.get("id") for u in ent.get("upstream", []) if isinstance(u, dict)]
    downs = [d.get("id") for d in ent.get("downstream", []) if isinstance(d, dict)]
    res.check(
        "B02 pipe:classify upstream 含 pool:xueqiu",
        "pool:xueqiu" in ups,
        f"upstream={ups}",
    )
    res.check(
        "B03 pipe:classify downstream 含 pipe:report",
        "pipe:report" in downs,
        f"downstream={downs}",
    )

    if node_ids is not None:
        dangling = sorted(set(ups + downs) - node_ids)
        res.check(
            "B04 pipe:classify 上下游 id 均能在 map.nodes 找到",
            not dangling,
            f"悬空: {dangling}",
        )

    # pool:xueqiu 契约
    status, pool = fetch_json(f"{API}/api/dataops/entity?id=pool:xueqiu")
    if (
        res.check(
            "B05 pool:xueqiu 200 且 JSON 可解析",
            status == 200 and isinstance(pool, dict),
            f"status={status}",
        )
        and pool
    ):
        res.check(
            "B06 pool:xueqiu metrics 非空",
            bool(pool.get("metrics")),
            f"metrics={pool.get('metrics')!r}",
        )
        pool_ups = [
            u.get("id") for u in pool.get("upstream", []) if isinstance(u, dict)
        ]
        pool_downs = [
            d.get("id") for d in pool.get("downstream", []) if isinstance(d, dict)
        ]
        if node_ids is not None:
            dangling2 = sorted(set(pool_ups + pool_downs) - node_ids)
            res.check(
                "B07 pool:xueqiu 上下游 id 均能在 map.nodes 找到",
                not dangling2,
                f"悬空: {dangling2}",
            )

    # svc:hermes 契约
    status, hermes = fetch_json(f"{API}/api/dataops/entity?id=svc:hermes")
    if (
        res.check(
            "B08 svc:hermes 200",
            status == 200 and isinstance(hermes, dict),
            f"status={status}",
        )
        and hermes
    ):
        h = (hermes.get("status") or {}).get("health")
        res.check(
            "B09 svc:hermes status.health ∈ {ok, down}",
            h in ("ok", "down"),
            f"health={h!r}",
        )

    # 404 / 400
    status, body404 = fetch_json(f"{API}/api/dataops/entity?id=nonexistent:qa_probe")
    res.check("B10 未知 id 返回 HTTP 404", status == 404, f"status={status}")
    res.check(
        "B11 404 body 含 known 键",
        isinstance(body404, dict) and "known" in body404,
        f"keys={list(body404) if isinstance(body404, dict) else body404!r}",
    )

    status, body400 = fetch_json(f"{API}/api/dataops/entity")
    res.check("B12 缺参返回 HTTP 400", status == 400, f"status={status}")


# ------------------------------------------------------------ C. 静态面
def check_static(res):
    for path in STATIC_URLS:
        status, _ = fetch(f"{STATIC}{path}")
        res.check(f"C-{path} 返回 200", status == 200, f"status={status}")


# ------------------------------------------------------------ D. 回归
def check_regression(res):
    status, intel = fetch_json(f"{API}/api/xq/intel", timeout=20)
    ok = status == 200 and isinstance(intel, dict)
    res.check("D01 xq/intel 200", status == 200, f"status={status}")
    res.check(
        "D02 xq/intel 含 report_exists 键",
        ok and "report_exists" in intel,
        f"keys={list(intel)[:10] if ok else 'N/A'}",
    )

    status, _ = fetch_json(f"{API}/api/perf/board?limit=1")
    res.check("D03 perf/board?limit=1 200", status == 200, f"status={status}")

    status, ov = fetch_json(f"{API}/api/dataops/overview?view=summary")
    ok = status == 200 and isinstance(ov, dict)
    res.check("D04 overview?view=summary 200", status == 200, f"status={status}")
    res.check(
        "D05 overview?view=summary 含 summary 键",
        ok and "summary" in ov,
        f"keys={list(ov)[:10] if ok else 'N/A'}",
    )
    res.check(
        "D06 overview?view=summary 含 errors 键",
        ok and "errors" in ov,
        f"keys={list(ov)[:10] if ok else 'N/A'}",
    )


def run_all():
    """跑一轮全量断言，返回 (结果列表, 总耗时)。"""
    t0 = time.time()
    results = []

    # A. map
    res = Results("A. map 结构")
    check_map(res)
    results.append(res)

    # 取 map 节点 id 集合，供连接完整性断言用
    _, mp = fetch_json(f"{API}/api/dataops/map")
    node_ids = set(n.get("id") for n in mp.get("nodes", [])) if mp else None

    # B. entity
    res = Results("B. entity 契约")
    check_entity(res, node_ids)
    results.append(res)

    # C. 静态面
    res = Results("C. 静态面")
    check_static(res)
    results.append(res)

    # D. 回归
    res = Results("D. 回归")
    check_regression(res)
    results.append(res)

    return results, time.time() - t0


def summarize(results, elapsed, retried):
    total = failed_n = 0
    print("=" * 72)
    print("指挥操作系统 v3 验收报告（qa-v3）")
    print("=" * 72)
    for res in results:
        g_total = len(res.passed) + len(res.failed)
        total += g_total
        failed_n += len(res.failed)
        mark = "PASS" if not res.failed else "FAIL"
        print(f"\n[{res.group}] {mark}  （{len(res.passed)}/{g_total} 通过）")
        for name, detail in res.passed:
            print(f"  PASS  {name}" + (f"  [{detail}]" if detail else ""))
        for name, detail in res.failed:
            print(f"  FAIL  {name}  [{detail}]")
        for name, detail in res.warned:
            print(f"  WARN  {name}  [{detail}]")
    if retried:
        print(f"\n(首轮存在 FAIL，已等待 {RETRY_WAIT}s 重试一次，以上为末轮结果)")
    print("-" * 72)
    verdict = "PASS" if failed_n == 0 else "FAIL"
    print(
        f"总计: {total - failed_n}/{total} 通过  |  耗时 {elapsed:.1f}s  |  结论: {verdict}"
    )
    return failed_n == 0


# ------------------------------------------------- E. 雪球源一档（T11，pytest 直调模块）
# 线上 8091 跑的是旧进程，新增字段不进 HTTP 断言；本组用例直接 import api_dataops
# 调组装函数验证（生产进程下次重启自然带上 sources 字段）。
import importlib
import os as _os
import sys as _sys

_PORTAL_DIR = _os.path.dirname(_os.path.abspath(__file__))
if _PORTAL_DIR not in _sys.path:
    _sys.path.insert(0, _PORTAL_DIR)

REQUIRED_XQ_FIELDS = (
    "key",
    "name",
    "carrier",
    "status",
    "created_at_range",
    "fetched_at_range",
    "raw_total",
    "classified_total",
    "classified_pct",
    "posts_2024plus",
    "graph_watermark",
)


def _api_dataops():
    return importlib.import_module("api_dataops")


def test_xq_source_card_present():
    ov = _api_dataops().overview()
    cards = [s for s in ov.get("sources", []) if s.get("key") == "xueqiu"]
    assert cards, "overview.sources 缺少雪球源条目（一源一档未登记）: %r" % (
        ov.get("sources"),
    )


def test_xq_source_card_fields():
    ov = _api_dataops().overview()
    card = next(s for s in ov["sources"] if s["key"] == "xueqiu")
    missing = [f for f in REQUIRED_XQ_FIELDS if f not in card]
    assert not missing, "雪球卡片缺字段: %s" % missing
    if card["status"] == "ok":
        assert card["name"] == "雪球舆情帖池"
        assert isinstance(card["raw_total"], int) and card["raw_total"] > 20000
        lo, hi = card["created_at_range"]
        assert lo and hi and lo <= hi
        assert 0 <= card["classified_pct"] <= 1
        assert card["classified_total"] <= card["raw_total"]


def test_xq_source_card_degrades_when_db_missing(tmp_path, monkeypatch):
    api = _api_dataops()
    monkeypatch.setattr(api, "DATA", str(tmp_path))  # 指向空目录 → 库缺失
    errors = []
    card = api._xq_source_card(errors)
    assert card["status"] == "unavailable", "DB 缺失必须降级 unavailable"
    assert card["raw_total"] is None and card["created_at_range"] is None
    assert errors, "fail-visible：降级必须留痕 errors，不许静默吞"


# ------------------------------------- T11 缺口补齐（覆盖维度/graph 接线/缓存与批次）
# 直调模块验证：生产 8091 进程是旧代码，新字段等下次自然重启才进 HTTP 面。
import glob as _glob


def _audit_snapshot():
    """读最新审计文件的标准答案（测试同源校验用，只 glob 小 JSON）。"""
    files = sorted(_glob.glob(_api_dataops().XQ_AUDIT_GLOB))
    if not files:
        return None, None
    with open(files[-1], encoding="utf-8") as fh:
        a = json.load(fh)
    return (a.get("funnel") or {}).get("cached_candidate_symbols"), a.get(
        "generated_at"
    )


def test_xq_card_coverage_fields():
    """覆盖维度新字段存在且取值与最新离线审计产物一致。"""
    api = _api_dataops()
    ov = api.overview()
    card = next(s for s in ov["sources"] if s["key"] == "xueqiu")
    for f in ("cached_symbols", "cached_as_of"):
        assert f in card, "雪球卡缺覆盖维度字段 %s" % f
    n_audit, ts_audit = _audit_snapshot()
    if n_audit is None:
        assert card["cached_symbols"] is None, "无审计产物时覆盖数必须 null"
    else:
        assert card["cached_symbols"] == int(n_audit), "覆盖数 %r 与审计 %r 不一致" % (
            card["cached_symbols"],
            n_audit,
        )
        assert (
            isinstance(card["cached_as_of"], str) and card["cached_as_of"][:4] == "2026"
        )
        assert card["cached_as_of"] == ts_audit


def test_xq_card_graph_wiring():
    """graph 接线：watermark 或 note 均必须非空，不得回到未接线旧文案。"""
    ov = _api_dataops().overview()
    card = next(s for s in ov["sources"] if s["key"] == "xueqiu")
    assert card["graph_watermark"] is None or isinstance(card["graph_watermark"], str)
    assert card["graph_note"], "graph_note 必须非空"
    assert "仅覆盖 news/guba" not in card["graph_note"], "不得残留未接线旧文案"
    if card["graph_watermark"] is None:
        assert "已接线" in card["graph_note"], "无水位时 note 必须注明已接线"


def test_xq_audit_missing_degrades_to_null(tmp_path, monkeypatch):
    """审计产物缺失 → 覆盖字段 null + note 注明，绝不抛错。"""
    api = _api_dataops()
    monkeypatch.setattr(api, "XQ_AUDIT_GLOB", str(tmp_path / "none_*.json"))
    errors = []
    cov = api._xq_audit_coverage(errors)
    assert cov["cached_symbols"] is None and cov["cached_as_of"] is None
    assert cov["note"], "缺失必须留 note"
    errors2 = []
    card = api._xq_source_card(errors2, cov, None)
    assert card["cached_symbols"] is None and card["cached_as_of"] is None
    assert card["coverage_note"], "卡片侧也必须可见缺失原因"


def test_graph_latest_missing_tolerant(tmp_path, monkeypatch):
    """graph_check/latest.json 缺失 → watermark null + 容错 note，绝不抛错。"""
    api = _api_dataops()
    monkeypatch.setattr(api, "GRAPH_CHECK_LATEST", str(tmp_path / "nope.json"))
    errors = []
    gw = api._xq_graph_watermark(errors)
    assert gw["watermark"] is None
    assert gw["note"], "缺失必须留容错 note"
    assert errors, "fail-visible：探测失败留痕 errors"
    # 雪球卡整体也不得被拖垮（status 与 DB 无关部分照常返回）
    card = api._xq_source_card([], None, gw)
    assert card["graph_watermark"] is None and card["graph_note"] == gw["note"]


def test_quote_cache_meta_header_parse():
    """行情缓存池 _meta 头解析正确性（只读头 4KB，days/saved_at 必须取到）。"""
    api = _api_dataops()
    meta = api._read_cache_meta(api.QUOTE_CACHE_PATH)
    assert isinstance(meta.get("days"), int) and meta["days"] > 0
    assert isinstance(meta.get("saved_at"), str) and meta["saved_at"][:4] == "2026"
    card = api._xq_quote_cache_card([])
    assert card["status"] == "ok", "缓存文件在位时不得降级: %r" % card.get("note")
    assert card["days"] == meta["days"]
    assert card["saved_at"] == meta["saved_at"]
    assert isinstance(card["size_mb"], int) and card["size_mb"] >= 100, (
        "daily_cache.json 为百 MB 级，size_mb=%r 异常" % card["size_mb"]
    )
    assert card["path"] == api.QUOTE_CACHE_PATH
    ov = api.overview()
    qc = next(s for s in ov["sources"] if s["key"] == "xq_quote_cache")
    assert qc["status"] == "ok" and qc["days"] == meta["days"]


def test_quote_cache_missing_degrades(tmp_path, monkeypatch):
    """缓存文件缺失 → status unavailable + note，绝不抛错、绝不整载。"""
    api = _api_dataops()
    monkeypatch.setattr(api, "QUOTE_CACHE_PATH", str(tmp_path / "nope.json"))
    errors = []
    card = api._xq_quote_cache_card(errors)
    assert card["status"] == "unavailable"
    assert card["note"] and errors, "fail-visible：降级必须留痕"
    assert card["size_mb"] is None and card["days"] is None


def test_batch_collector_row_registered():
    """采集批次行：COLLECTORS 登记且 overview 出卡，目录在位时 detail 列出最新批次文件。"""
    api = _api_dataops()
    assert any(k == "xq_user_timeline_batch" for k, *_ in api.COLLECTORS), (
        "COLLECTORS 未登记 xq_user_timeline_batch"
    )
    ov = api.overview()
    row = next(c for c in ov["collectors"] if c["key"] == "xq_user_timeline_batch")
    assert row["health"] in {
        "ok",
        "down",
        "running",
        "yielding",
        "blocked",
        "backoff",
        "done",
        "complete",
        "unknown",
        "disabled",
        "degraded",
        "stale",
    }
    if row["health"] == "ok":
        assert "batch_result_" in row["detail"], (
            "在岗时 detail 必须列出最新批次文件: %r" % row["detail"]
        )
    else:
        assert row["detail"], "降级必须带 detail 说明"


def test_batch_collector_dir_missing_degrades(tmp_path, monkeypatch):
    """批次目录缺失 → health unknown 降级，绝不抛错。"""
    api = _api_dataops()
    monkeypatch.setattr(api, "XQ_BATCH_DIR", str(tmp_path / "nope_batch"))
    ov = api.overview()
    row = next(c for c in ov["collectors"] if c["key"] == "xq_user_timeline_batch")
    assert row["health"] == "unknown"
    assert "缺失" in row["detail"] or "失败" in row["detail"]


def main():
    results, elapsed = run_all()
    retried = False
    if any(r.failed for r in results):
        print(f"\n首轮存在 FAIL，等待 {RETRY_WAIT}s 后重试一次（容忍并行工位中间态）……")
        time.sleep(RETRY_WAIT)
        results, elapsed = run_all()
        retried = True
    ok = summarize(results, elapsed, retried)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
