# WO-OS-04 契约测试：诸葛亮建议引擎（规则版，纯函数离线）
# 先红后绿：本文件先于 api_zhuge_brief.py 落地时必须红。
# 运行：python -m pytest test_api_zhuge_brief.py -q
import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "api_zhuge_brief", Path(__file__).with_name("api_zhuge_brief.py")
)
mod = importlib.util.module_from_spec(spec)

BEIJING = timezone(timedelta(hours=8))
CLOCK = datetime(2026, 10, 21, 9, 0, tzinfo=BEIJING)

GOOD_SIX = {
    "os": {"ok": True, "biz": {"near": [], "notes": []}},
    "home": {"ok": True, "calendar": {"items": []}},
    "alerts": {
        "ok": True,
        "source_health": {
            "sources": {
                "premarket": {"status": "ok", "as_of": "2026-10-21T08:00:00+08:00"}
            }
        },
    },
    "notify": {
        "ok": True,
        "errors": [],
        "items": [
            {
                "id": "n1",
                "title": "阻断通知",
                "state": "blocked",
                "created_at": "2026-10-21T07:00:00+08:00",
                "reason": "通道异常",
            }
        ],
    },
    "tasks": {
        "ok": True,
        "tasks": [
            {
                "task_id": "t1",
                "title": "今日到期工单",
                "status": "open",
                "due": "2026-10-21",
                "created_at": "2026-10-20T10:00:00+08:00",
            },
            {
                "task_id": "t2",
                "title": "逾期工单",
                "status": "open",
                "due": "2026-10-19",
                "created_at": "2026-10-18T10:00:00+08:00",
            },
        ],
    },
    "capability": {"ok": True, "production_lines": []},
}

# WO-OS-20 第七源 pushflow：当日通知投递成败统计（真实账本口径）
PUSHFLOW_HIGH = {
    "ok": True,
    "today": 276,
    "attempted": 276,
    "ok_count": 124,
    "fail_count": 152,
    "fail_rate": 0.55,
    "policy_suppressed": 0,
    "channels": {
        "openapi": {"ok": 100, "fail": 150},
        "lark-cli": {"ok": 24, "fail": 2},
    },
}


def _load():
    spec.loader.exec_module(mod)
    return mod


def test_module_loadable_and_pure():
    m = _load()
    out = m.build_brief(GOOD_SIX, CLOCK)
    # 纯函数：不修改输入
    import copy

    snapshot = copy.deepcopy(GOOD_SIX)
    m.build_brief(GOOD_SIX, CLOCK)
    assert GOOD_SIX == snapshot


def test_output_contract():
    m = _load()
    out = m.build_brief(GOOD_SIX, CLOCK)
    assert out["engine"] == "rule-based"
    assert out["model_invoked"] is False
    assert out["dispatched"] is False
    assert out["generated_at"] == CLOCK.isoformat()
    assert isinstance(out["suggestions"], list) and out["suggestions"], (
        "应至少有一条建议"
    )
    for s in out["suggestions"]:
        # 每条建议必须带来源与生产者时点（可为 None 但键必须在）
        for key in (
            "id",
            "domain",
            "priority",
            "reason",
            "source",
            "as_of",
            "confidence",
        ):
            assert key in s, f"建议缺 {key}"
        assert s["confidence"] == "rule-based"
    ids = [s["id"] for s in out["suggestions"]]
    assert len(ids) == len(set(ids)), "建议 id 不得重复"


def test_overdue_ranks_first_and_blocked_is_clustered():
    """WO-OS-23 语义变更：blocked 历史簇聚合为 1 条 P2，不再逐条占满 P1。

    逾期工单仍是 P1 排最前；blocked 通知聚合为 notify:history_cluster（P2）。
    设计理由：21 条同簇 P1 会把首页变成噪声墙，且"历史簇"不等于当前故障。
    """
    m = _load()
    out = m.build_brief(GOOD_SIX, CLOCK)
    ids = [s["id"] for s in out["suggestions"]]
    assert ids[0] == "task:t2", "逾期工单应排最前"
    assert "notify:history_cluster" in ids, "blocked 通知应聚合成簇建议"
    assert "notify:n1" not in ids, "不应再逐条展开 blocked"
    cluster = next(s for s in out["suggestions"] if s["id"] == "notify:history_cluster")
    assert cluster["priority"] == 2, "历史簇降为 P2（当日真实失败率由 pushflow 源承担）"
    assert "近 7 天" in cluster["reason"] and "不代表此刻发不出去" in cluster["reason"]


def test_deadline_blocked_stays_p1():
    """deadline 类（到点未就绪）是真 P1，不参与历史簇聚合。"""
    m = _load()
    raw = dict(GOOD_SIX)
    raw["notify"] = {
        "ok": True,
        "errors": [],
        "items": [
            {
                "id": "d1",
                "title": "数据锚到点未就绪 · us / postclose",
                "state": "blocked",
                "created_at": "2026-10-21T08:00:00+08:00",
                "reason": "锚 guojia 状态 stale",
            },
            {
                "id": "n1",
                "title": "hub 投递失败",
                "state": "blocked",
                "created_at": "2026-10-21T07:00:00+08:00",
                "reason": "ok=0",
            },
        ],
    }
    out = m.build_brief(raw, CLOCK)
    d = next((s for s in out["suggestions"] if s["id"] == "notify:d1"), None)
    assert d is not None and d["priority"] == 1, "deadline 必须单列 P1"
    assert "到点未就绪" in d["reason"]
    cluster = next(
        (s for s in out["suggestions"] if s["id"] == "notify:history_cluster"), None
    )
    assert cluster is not None and cluster["priority"] == 2
    assert "1 条" in cluster["reason"], "簇只含非 deadline 的 blocked"


def test_due_today_called_out():
    m = _load()
    out = m.build_brief(GOOD_SIX, CLOCK)
    s = next(x for x in out["suggestions"] if x["id"] == "task:t1")
    assert "今日" in s["reason"]


def test_missing_sources_degrade_explicitly():
    m = _load()
    out = m.build_brief({}, CLOCK)
    assert out["suggestions"] == []
    gaps = {g["source"] for g in out["gaps"]}
    assert gaps == {
        "os",
        "home",
        "alerts",
        "notify",
        "tasks",
        "capability",
        "pushflow",
    }, "七源全缺应逐一点名（WO-OS-20 扩容）"
    for g in out["gaps"]:
        assert g["note"].startswith("待核"), "缺源说明必须以待核开头"


def test_ok_false_source_degrades():
    m = _load()
    raw = dict(GOOD_SIX, notify={"ok": False})
    out = m.build_brief(raw, CLOCK)
    assert all(s["id"] != "notify:n1" for s in out["suggestions"]), (
        "ok:false 不得产出建议"
    )
    assert any(g["source"] == "notify" for g in out["gaps"])


def test_stale_as_of_flags_low_freshness():
    m = _load()
    raw = dict(
        GOOD_SIX,
        alerts={
            "ok": True,
            "source_health": {
                "sources": {
                    "premarket": {"status": "ok", "as_of": "2026-10-01T08:00:00+08:00"}
                }
            },
        },
    )
    out = m.build_brief(raw, CLOCK)
    s = next(x for x in out["suggestions"] if x["id"] == "alert:premarket")
    assert s["as_of"] is not None
    assert any("新鲜度" in k or "新鲜度" in s["reason"] for k in [s["reason"]])


def test_bad_clock_raises():
    m = _load()
    try:
        m.build_brief(GOOD_SIX, "not-a-date")
    except (ValueError, TypeError):
        return
    raise AssertionError("坏时钟应抛 ValueError/TypeError")


def test_no_write_side_effects_by_design():
    m = _load()
    # 模块级不得出现写库/HTTP派单调用痕迹
    src = Path(__file__).with_name("api_zhuge_brief.py").read_text(encoding="utf-8")
    for banned in (
        "requests.post",
        "urllib.request",
        "subprocess",
        "sqlite3.connect",
        "open(",
    ):
        assert banned not in src, f"建议引擎源码不得出现 {banned}"


# ── WO-OS-20 第七源 pushflow：当日实际失败率（不是历史失败簇）──────────
def _pushflow_case(rate, fail_count=11, today=20):
    """构造自洽的 pushflow 夹具（WO-OS-22 起必须带 attempted/policy_suppressed）。"""
    return dict(
        GOOD_SIX,
        pushflow={
            "ok": True,
            "today": today,
            "attempted": today,
            "ok_count": today - fail_count,
            "fail_count": fail_count,
            "fail_rate": rate,
            "policy_suppressed": 0,
            "channels": {"openapi": {"ok": today - fail_count, "fail": fail_count}},
        },
    )


def _pick(out, sid="pushflow:daily_rate"):
    return next((s for s in out["suggestions"] if s["id"] == sid), None)


def test_pushflow_high_rate_is_priority_1_with_percent():
    m = _load()
    out = m.build_brief(dict(GOOD_SIX, pushflow=PUSHFLOW_HIGH), CLOCK)
    s = _pick(out)
    assert s is not None, "fail_rate=0.55 必须产出 pushflow 建议"
    assert s["priority"] == 1, "≥50% 应为 P1"
    assert s["domain"] == "通知"
    assert s["confidence"] == "rule-based"
    assert "今日真实投递失败率 55%" in s["reason"]
    assert "失败 152/尝试 276" in s["reason"], "分母必须是真实投递尝试数"
    assert "不是历史失败簇" in s["reason"], "必须区分当日实际失败率与历史失败簇"
    assert s["source"] == "/api/zhuge/brief.pushflow"
    assert "pushflow.jsonl" in s["reason"], "建议需说明原始来源"


def test_pushflow_policy_suppressed_excluded_and_noted():
    """WO-OS-20-FIX：静默窗口策略抑制既不算成功也不算失败，只在文案附注。

    真实口径（2026-10-21 实测账本）：总行 282、策略抑制 103、真实尝试 179、
    失败 49 → 真实失败率 27.4%（若把抑制算进去会虚报 53.9%）。
    """
    m = _load()
    stats = {
        "ok": True,
        "today": 282,
        "attempted": 179,
        "ok_count": 130,
        "fail_count": 49,
        "fail_rate": 0.274,
        "policy_suppressed": 103,
        "channels": {
            "openapi": {"ok": 64, "fail": 46},
            "lark-cli": {"ok": 65, "fail": 3},
        },
    }
    out = m.build_brief(dict(GOOD_SIX, pushflow=stats), CLOCK)
    s = _pick(out)
    assert s is not None and s["priority"] == 3, "27.4% 属 P3 区间"
    assert "失败 49/尝试 179" in s["reason"], "分母不得包含 103 条抑制行"
    assert "103 条静默窗口策略抑制" in s["reason"]
    assert "不计入失败" in s["reason"]


def test_pushflow_mid_rate_is_priority_2():
    m = _load()
    out = m.build_brief(_pushflow_case(0.3), CLOCK)
    s = _pick(out)
    assert s is not None and s["priority"] == 2, "≥30% 应为 P2"


def test_pushflow_low_rate_emits_no_suggestion():
    m = _load()
    out = m.build_brief(_pushflow_case(0.05), CLOCK)
    assert _pick(out) is None, "<10% 视为正常，不产建议"
    assert all(g["source"] != "pushflow" for g in out["gaps"]), "源可用时不得进 gaps"


def test_pushflow_missing_goes_to_gaps_pending():
    m = _load()
    out = m.build_brief(GOOD_SIX, CLOCK)  # 无 pushflow 键
    gap = next((g for g in out["gaps"] if g["source"] == "pushflow"), None)
    assert gap is not None, "缺 pushflow 必须进 gaps"
    assert gap["note"].startswith("待核")


def test_pushflow_inconsistent_counts_goes_to_gaps():
    """WO-OS-22 自洽校验：total - attempted 必须等于 policy_suppressed。

    防的是"上游只给 today 不给 attempted"时静默退回旧口径（含抑制的假失败率）。
    账本行数与尝试数对不上 = 口径不可信 → 记待核，不产失败率建议。
    """
    m = _load()
    stats = {
        "ok": True,
        "today": 282,
        "attempted": 179,
        "ok_count": 130,
        "fail_count": 49,
        "fail_rate": 0.274,
        "policy_suppressed": 50,
        "channels": {},
    }
    out = m.build_brief(dict(GOOD_SIX, pushflow=stats), CLOCK)
    gap = next((g for g in out["gaps"] if g["source"] == "pushflow"), None)
    assert gap is not None, "行数不自洽必须进 gaps"
    assert gap["note"].startswith("待核")
    assert not _pick(out), "口径不可信时不得产出失败率建议"


def test_pushflow_consistent_counts_pass():
    """自洽时正常产建议（282=179+103）。"""
    m = _load()
    stats = {
        "ok": True,
        "today": 282,
        "attempted": 179,
        "ok_count": 130,
        "fail_count": 49,
        "fail_rate": 0.274,
        "policy_suppressed": 103,
        "channels": {},
    }
    out = m.build_brief(dict(GOOD_SIX, pushflow=stats), CLOCK)
    s = _pick(out)
    assert s is not None and s["priority"] == 3
    assert all(g["source"] != "pushflow" for g in out["gaps"])


# ── WO-OS-25 趋势：让"25%"能读出方向（改善/恶化/持平）──────────────
def _trend_case(today_rate, prev_rate):
    return dict(
        GOOD_SIX,
        pushflow={
            "ok": True,
            "today": 100,
            "attempted": 100,
            "ok_count": int(100 * (1 - today_rate)),
            "fail_count": int(100 * today_rate),
            "fail_rate": today_rate,
            "policy_suppressed": 0,
            "channels": {},
            "recent": [
                {
                    "day": "2026-10-20",
                    "attempted": 100,
                    "fail_count": int(100 * prev_rate),
                    "fail_rate": prev_rate,
                },
                {
                    "day": "2026-10-21",
                    "attempted": 100,
                    "fail_count": int(100 * today_rate),
                    "fail_rate": today_rate,
                },
            ],
        },
    )


def test_pushflow_trend_improving():
    m = _load()
    out = m.build_brief(_trend_case(0.24, 0.53), CLOCK)
    s = _pick(out)
    assert s is not None
    assert "较前一日" in s["reason"] or "改善" in s["reason"], "应给出趋势判断"
    assert "改善" in s["reason"], "0.53→0.24 应判为改善"
    assert "53" in s["reason"], "应带上对比基准数值"


def test_pushflow_trend_worsening():
    m = _load()
    out = m.build_brief(_trend_case(0.55, 0.28), CLOCK)
    s = _pick(out)
    assert s is not None and "恶化" in s["reason"], "0.28→0.55 应判为恶化"


def test_pushflow_trend_flat():
    m = _load()
    out = m.build_brief(_trend_case(0.30, 0.31), CLOCK)
    s = _pick(out)
    assert s is not None and "基本持平" in s["reason"], "±5pp 内应判为基本持平"


def test_pushflow_trend_boundary_symmetric():
    """WO-OS-25-FIX：所有正好 ±5pp 的组合必须判定一致（注释口径：±5pp 内含边界→持平）。

    实测缺陷：40 组 5pp 边界里 20 组判"恶化/改善"、20 组判"持平"——同为上升
    5 个百分点，结果取决于浮点表示（0.05 精确 vs 0.25-0.20 有误差）。
    修法：先把百分点差四舍五入到 4 位再比较。
    """
    m = _load()
    verdicts = []
    for a in range(0, 101, 5):
        for b in range(0, 101, 5):
            if abs(a - b) != 5:
                continue
            if b < 10:
                continue  # <10% 本就不产建议（正常投递水平），无趋势可言
            s = _pick(m.build_brief(_trend_case(b / 100, a / 100), CLOCK))
            assert s is not None, "5pp 边界(%d→%d)应仍产建议" % (a, b)
            if "持平" in s["reason"]:
                verdicts.append("持平")
            elif "恶化" in s["reason"]:
                verdicts.append("恶化")
            else:
                verdicts.append("改善")
    assert len(set(verdicts)) == 1, "5pp 边界判定不一致，出现 %s 共 %d 组" % (
        sorted(set(verdicts)),
        len(verdicts),
    )
    assert verdicts[0] == "持平", "按注释 ±5pp 内含边界应判持平，实得 " + verdicts[0]


def test_pushflow_trend_beyond_boundary():
    """越过 ±5pp 才判改善/恶化（6pp 必须生效）。"""
    m = _load()
    worse = _pick(m.build_brief(_trend_case(0.31, 0.25), CLOCK))
    better = _pick(m.build_brief(_trend_case(0.25, 0.31), CLOCK))
    assert "恶化" in worse["reason"], "+6.0pp 应判恶化"
    assert "改善" in better["reason"], "-6.0pp 应判改善"


def test_pushflow_trend_missing_recent_degrades_silently():
    """无 recent 字段（旧载荷）不影响主结论，只是不给趋势。"""
    m = _load()
    stats = {
        "ok": True,
        "today": 100,
        "attempted": 100,
        "ok_count": 70,
        "fail_count": 30,
        "fail_rate": 0.30,
        "policy_suppressed": 0,
        "channels": {},
    }
    out = m.build_brief(dict(GOOD_SIX, pushflow=stats), CLOCK)
    s = _pick(out)
    assert s is not None and s["priority"] == 2
    assert "改善" not in s["reason"] and "恶化" not in s["reason"]


def test_pushflow_legacy_payload_without_attempted_guarded():
    """WO-OS-22：旧载荷(无 attempted/policy_suppressed)不得静默按 today 算。

    today=276 但无 attempted 字段 → 无法区分抑制与失败 → 待核，不虚报。
    """
    m = _load()
    legacy = {
        "ok": True,
        "today": 276,
        "ok_count": 124,
        "fail_count": 152,
        "fail_rate": 0.55,
        "channels": {},
    }
    out = m.build_brief(dict(GOOD_SIX, pushflow=legacy), CLOCK)
    gap = next((g for g in out["gaps"] if g["source"] == "pushflow"), None)
    assert gap is not None, "旧载荷缺 attempted 必须待核"
    assert not _pick(out), "旧载荷不得静默产出含抑制的假失败率"
    assert _pick(out) is None, "缺源不得产建议"
