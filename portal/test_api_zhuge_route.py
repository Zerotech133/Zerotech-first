# -*- coding: utf-8 -*-
# WO-OS-09 契约测试：/api/zhuge/brief 只读路由挂载（先红后绿）。
# 路由挂载验证不依赖真实 8091 进程（沿 test_api_tickets / test_portal_compat_regressions
# 判例：stub 子类捕获 _json / object.__new__(portal_api.Handler) 直调方法）。
# 运行：python -m pytest portal/test_api_zhuge_route.py -q
import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

ROUTE_PATH = "/api/zhuge/brief"


def _load_module(name):
    return importlib.import_module(name)


def _make_handler():
    """构造 portal_api.Handler 实例（不起服务器）+ 捕获 _json 输出。

    沿 test_portal_compat_regressions 判例：object.__new__ 绕过
    BaseHTTPRequestHandler.__init__（其需要真实 socket）。
    """
    portal_api = _load_module("portal_api")
    seen = {}

    class _Stub(portal_api.Handler):
        def _json(self, obj, code=200):
            seen["obj"] = obj
            seen["code"] = code

    return object.__new__(_Stub), seen, portal_api


class _Resp:  # 简易响应记录（send_error 等裸协议方法捕获）
    pass


def _h_get(handler, path=ROUTE_PATH, qs=""):
    handler.path = path + ("?" + qs if qs else "")
    handler.do_GET()


# ── ① 路由已注册：GET 返回 200 + 引擎契约字段 ─────────────────────────
def test_get_returns_200_engine_contract():
    h, seen, _ = _make_handler()
    _h_get(h)
    assert seen.get("code") == 200, "GET /api/zhuge/brief 应 200"
    obj = seen["obj"]
    assert obj["engine"] == "rule-based"
    assert obj["model_invoked"] is False
    assert obj["dispatched"] is False
    assert isinstance(obj["suggestions"], list)
    assert isinstance(obj["gaps"], list)


# ── ② 六源全缺（stub 注入）→ gaps 覆盖六源且 note 以「待核」开头 ──────
def test_all_sources_missing_gaps_cover_six():
    import api_zhuge_route as zr
    out = zr.build_route_payload({k: (lambda: None) for k in zr.SOURCES})
    gaps = {g["source"] for g in out["gaps"]}
    assert gaps == {"os", "home", "alerts", "notify", "tasks", "capability",
                    "pushflow"}
    for g in out["gaps"]:
        assert g["note"].startswith("待核")


# ── ③ 六源获取函数抛异常 → 按 None 降级进 gaps（不 500 不静默）───────
def test_fetcher_exception_degrades_to_gap():
    import api_zhuge_route as zr

    def _boom():
        raise RuntimeError("boom")

    out = zr.build_route_payload({
        "os": _boom, "home": lambda: None, "alerts": lambda: None,
        "notify": lambda: None, "tasks": lambda: None, "capability": lambda: None,
    })
    assert out["engine"] == "rule-based"  # 单源坏不拖垮整体
    assert any(g["source"] == "os" for g in out["gaps"])


# ── ④ 坏时钟参数（?clock=垃圾）→ 4xx + 错误 JSON（拒绝而非静默错）────
def test_bad_clock_param_rejected_4xx():
    h, seen, _ = _make_handler()
    _h_get(h, qs="clock=not-a-date")
    assert 400 <= seen.get("code", 0) < 500, "坏 clock 应 4xx"
    assert "error" in seen["obj"] or "ok" in seen["obj"]


# ── ⑤ 好时钟参数被接受（可回放口径：ISO 字符串）───────────────────────
def test_good_clock_param_accepted():
    h, seen, _ = _make_handler()
    _h_get(h, qs="clock=2026-10-21T09:00:00+08:00")
    assert seen.get("code") == 200
    assert seen["obj"]["business_day"] == "2026-10-21"


def test_good_clock_param_urlencoded_accepted():
    """A2-B07：标准客户端把 + 编成 %2B、冒号编成 %3A，必须仍 200。"""
    h, seen, _ = _make_handler()
    _h_get(h, qs="clock=2026-10-21T09%3A00%3A00%2B08%3A00")
    assert seen.get("code") == 200
    assert seen["obj"]["business_day"] == "2026-10-21"


# ── ⑥ 非 GET 方法不被允许（POST/OPTIONS/PUT/DELETE → 405）───────────
def test_post_method_not_allowed_405():
    portal_api = _load_module("portal_api")
    seen = {}

    class _Stub(portal_api.Handler):
        def _json(self, obj, code=200):
            seen["obj"] = obj
            seen["code"] = code

        def send_error(self, code, message=None, explain=None):
            seen["code"] = code

    import io
    for verb in ("do_POST", "do_OPTIONS", "do_PUT", "do_DELETE"):
        h = object.__new__(_Stub)
        h.path = ROUTE_PATH
        h.headers = {}
        h.rfile = io.BytesIO(b"")
        getattr(h, verb)()
        assert seen.get("code") == 405, f"{verb} /api/zhuge/brief 应 405，得 {seen.get('code')}"


# ── ⑦ 路由源码零写操作纪律 ────────────────────────────────────────────
def test_route_source_has_no_write_ops():
    src = (HERE / "api_zhuge_route.py").read_text(encoding="utf-8")
    for banned in ("requests.post", "urllib.request", "subprocess",
                   "sqlite3.connect"):
        assert banned not in src, f"路由源码不得出现 {banned}"
    # open( 仅允许只读形态（路由层本应零文件 IO，六源函数各司其职）
    assert 'open(' not in src.replace('mode=ro', '')


# ── ⑧ 引擎 04 的 9 例不回退（引用守卫：模块同名加载）──────────────────
def test_engine_module_still_importable_and_pure():
    m = _load_module("api_zhuge_brief")
    out = m.build_brief({}, datetime(2026, 10, 21, 9, 0,
                                     tzinfo=m.BEIJING))
    assert out["engine"] == "rule-based" and out["suggestions"] == []


# ── WO-OS-20 ⑨ _pushflow_stats：只读临时账本，按北京日期算当日成败 ──────
TMP_DIR = Path("C:/Temp")  # 临时夹具目录，绝不碰 portal/work/push_flow.jsonl


def _write_temp_ledger(rows, name=None):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / (name or "wo_os_20_push_flow_fixture.jsonl")
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")
    return path


def _row(day, ok, channel="openapi"):
    return {"ts": day + "T10:00:00", "date": day, "kind": "hub",
            "channel": channel, "target": "oc_test", "count": 1,
            "ok": ok, "tenant": "default"}


def test_pushflow_stats_counts_today_by_channel():
    import api_zhuge_route as zr
    rows = ([_row("2026-10-21", True) for _ in range(4)]
            + [_row("2026-10-21", False) for _ in range(5)]
            + [_row("2026-10-21", True, "lark-cli")]
            + [_row("2026-10-20", False)])  # 非当日，必须忽略
    path = _write_temp_ledger(rows)
    try:
        stats = zr._pushflow_stats(path=path, day="2026-10-21")
    finally:
        path.unlink()
    assert stats is not None
    assert stats["ok"] is True
    assert stats["today"] == 10
    assert stats["ok_count"] == 5 and stats["fail_count"] == 5
    assert stats["fail_rate"] == 0.5
    assert stats["channels"] == {"openapi": {"ok": 4, "fail": 5},
                                 "lark-cli": {"ok": 1, "fail": 0}}


def test_pushflow_stats_missing_file_returns_none():
    import api_zhuge_route as zr
    missing = TMP_DIR / "wo_os_20_not_exists.jsonl"
    if missing.exists():
        missing.unlink()
    assert zr._pushflow_stats(path=missing, day="2026-10-21") is None


# ── WO-OS-20-FIX 口径缺陷：免打扰窗口策略抑制不得算作投递失败 ──────
def _gate_row(day, channel="hub-gate", kind="hub|deny:silent window"):
    return {"ts": day + "T02:00:00", "date": day, "kind": kind,
            "channel": channel, "target": "oc_test", "count": 1,
            "ok": False, "tenant": "default"}


def test_pushflow_stats_excludes_policy_suppressed_from_fail_rate():
    """hub-gate / kind 含 |deny: 的行是免打扰窗口主动拦截，不是投递失败。

    真实事实（2026-10-21 实测）：当日 282 行中 103 行是 hub-gate 抑制，
    若计入失败会虚报 53.9%，排除后真实失败率 27.4%。
    """
    import api_zhuge_route as zr
    rows = ([_row("2026-10-21", True) for _ in range(7)]
            + [_row("2026-10-21", False) for _ in range(3)]
            + [_gate_row("2026-10-21") for _ in range(10)])
    path = _write_temp_ledger(rows, "wo_os_20fix_gate.jsonl")
    try:
        stats = zr._pushflow_stats(path=path, day="2026-10-21")
    finally:
        path.unlink()
    assert stats is not None
    # 分母只算真实投递尝试：7+3=10，不含 10 条抑制
    assert stats["attempted"] == 10
    assert stats["ok_count"] == 7 and stats["fail_count"] == 3
    assert stats["fail_rate"] == 0.3, "失败率分母必须是真实尝试数，不是含抑制的总行数"
    assert stats["policy_suppressed"] == 10
    assert "hub-gate" not in stats["channels"], "抑制行不得混进通道成败统计"


def test_pushflow_stats_all_suppressed_returns_none():
    """当日若全部是抑制行（无真实尝试），失败率无意义 → 返回 None 交引擎待核。"""
    import api_zhuge_route as zr
    rows = [_gate_row("2026-10-21") for _ in range(5)]
    path = _write_temp_ledger(rows, "wo_os_20fix_allgate.jsonl")
    try:
        stats = zr._pushflow_stats(path=path, day="2026-10-21")
    finally:
        path.unlink()
    assert stats is None


def test_pushflow_stats_gate_by_kind_without_channel():
    """channel 非 hub-gate 但 kind 带 |deny: 也算抑制（matrix:us|deny:silent）。"""
    import api_zhuge_route as zr
    rows = ([_row("2026-10-21", True) for _ in range(2)]
            + [_gate_row("2026-10-21", channel="openapi",
                         kind="matrix:us|deny:silent window")])
    path = _write_temp_ledger(rows, "wo_os_20fix_denykind.jsonl")
    try:
        stats = zr._pushflow_stats(path=path, day="2026-10-21")
    finally:
        path.unlink()
    assert stats["policy_suppressed"] == 1
    assert stats["attempted"] == 2
    assert stats["fail_rate"] == 0.0


# ── WO-OS-22 口径加固：政策行词表扩展（配额/策略类不得算失败）─────────
def test_pushflow_quota_block_is_policy_not_failure():
    """km_feishu_hub 的 quota_block:km_card 属配额拦截，不是投递失败。

    验收席提示：旧判据只认 hub-gate 与 |deny:，会把配额拦截算成真实失败；
    真实账本当前 0 条（未触发），一旦触发就会虚报，故按词表加固。
    """
    import api_zhuge_route as zr
    rows = ([_row("2026-10-21", True) for _ in range(3)]
            + [{"ts": "2026-10-21T11:00:00", "date": "2026-10-21",
                "kind": "quota_block:km_card", "channel": "feishu",
                "target": "oc_test", "count": 1, "ok": False,
                "tenant": "default"}])
    path = _write_temp_ledger(rows, "wo_os_22_quota.jsonl")
    try:
        stats = zr._pushflow_stats(path=path, day="2026-10-21")
    finally:
        path.unlink()
    assert stats["policy_suppressed"] == 1, "配额拦截应计入抑制而非失败"
    assert stats["fail_count"] == 0
    assert stats["attempted"] == 3
    assert stats["fail_rate"] == 0.0


def test_pushflow_policy_word_variants():
    """政策词表覆盖 deny/quota/policy/silent/hold 常见变体。"""
    import api_zhuge_route as zr
    kinds = ["hub|deny:silent window", "quota_block:km_card",
             "policy:deferred", "hold:rate_limit", "silent:night"]
    rows = ([_row("2026-10-21", True)]
            + [{"ts": "2026-10-21T12:00:00", "date": "2026-10-21",
                "kind": k, "channel": "feishu", "target": "oc_t",
                "count": 1, "ok": False, "tenant": "default"} for k in kinds])
    path = _write_temp_ledger(rows, "wo_os_22_kinds.jsonl")
    try:
        stats = zr._pushflow_stats(path=path, day="2026-10-21")
    finally:
        path.unlink()
    assert stats["policy_suppressed"] == len(kinds), "五种政策行都应被识别"
    assert stats["attempted"] == 1
    assert stats["fail_count"] == 0


# ── WO-OS-25 近 4 日趋势：让"25%"能看出是恶化还是恢复 ─────────────
def test_pushflow_recent_trend_series():
    """recent 应按日期升序给近 4 天真实失败率（排除政策行、跳过空日）。"""
    import api_zhuge_route as zr
    rows = []
    # 10-18：2 成 1 败 → 33.3%
    rows += [_row("2026-10-18", True), _row("2026-10-18", True),
             _row("2026-10-18", False)]
    # 10-19：全败 → 100%
    rows += [_row("2026-10-19", False), _row("2026-10-19", False)]
    # 10-20：全成 → 0%
    rows += [_row("2026-10-20", True), _row("2026-10-20", True)]
    # 10-21：1 成 1 败 + 2 条抑制（抑制不进统计）
    rows += [_row("2026-10-21", True), _row("2026-10-21", False)]
    rows += [_gate_row("2026-10-21") for _ in range(2)]
    path = _write_temp_ledger(rows, "wo_os_25_trend.jsonl")
    try:
        stats = zr._pushflow_stats(path=path, day="2026-10-21")
    finally:
        path.unlink()
    recent = stats["recent"]
    assert [r["day"] for r in recent] == ["2026-10-18", "2026-10-19",
                                         "2026-10-20", "2026-10-21"]
    assert [round(r["fail_rate"], 3) for r in recent] == [0.333, 1.0, 0.0, 0.5]
    assert [r["attempted"] for r in recent] == [3, 2, 2, 2]
    assert recent[-1]["fail_rate"] == stats["fail_rate"], "末日应与当日口径一致"


def test_pushflow_recent_capped_at_four_days():
    """超过 4 天时只取最近 4 天（按日期升序）。"""
    import api_zhuge_route as zr
    rows = []
    for day in ("2026-10-15", "2026-10-16", "2026-10-17",
                "2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21"):
        rows.append(_row(day, True))
    path = _write_temp_ledger(rows, "wo_os_25_cap.jsonl")
    try:
        stats = zr._pushflow_stats(path=path, day="2026-10-21")
    finally:
        path.unlink()
    assert len(stats["recent"]) == 4
    assert stats["recent"][0]["day"] == "2026-10-18"


# ── WO-OS-25-FIX：recent 只收合法且不晚于当日的日期 ──────────────
def test_pushflow_recent_rejects_missing_and_future_dates():
    """缺 date 行会落成 day:'None' 桶并排到最后，污染趋势基准；未来日期同理。

    实测缺陷：今日 2 条 + 无 date 2 条 → recent[-1] = day:'None'，
    趋势拿"None"日与今日比，且末日≠顶层口径。
    """
    import api_zhuge_route as zr
    rows = [_row("2026-10-21", True), _row("2026-10-21", False),
            {"ts": "2026-10-21T10:00:00", "kind": "hub", "channel": "openapi",
             "target": "oc_t", "count": 1, "ok": False, "tenant": "default"},  # 无 date
            {"ts": "2026-10-21T10:00:01", "date": "2026-10-21T10:00:01",
             "kind": "hub", "channel": "openapi", "target": "oc_t",
             "count": 1, "ok": False, "tenant": "default"},  # 非法 date 格式
            {"ts": "2026-10-25T10:00:00", "date": "2026-10-25",
             "kind": "hub", "channel": "openapi", "target": "oc_t",
             "count": 1, "ok": True, "tenant": "default"}]  # 未来日期
    path = _write_temp_ledger(rows, "wo_os_25fix_dates.jsonl")
    try:
        stats = zr._pushflow_stats(path=path, day="2026-10-21")
    finally:
        path.unlink()
    assert stats is not None
    days = [r["day"] for r in stats["recent"]]
    assert "None" not in days, "缺 date 行不得进 recent"
    assert "2026-10-25" not in days, "未来日期不得进 recent"
    assert days[-1] == "2026-10-21", "末日必须是当日"
    assert stats["recent"][-1]["fail_rate"] == stats["fail_rate"]

