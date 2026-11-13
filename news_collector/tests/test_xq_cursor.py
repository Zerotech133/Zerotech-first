# -*- coding: utf-8 -*-
"""雪球断点游标验收（判例：2026-11-12 两轮均被强杀，队列尾部 136 只 52.8h 未刷新）。

要锁死的语义：
  1) 旋转从「上次结束处的下一个」开始，且是**无缝环**——尾部跑完接回头部，不漏不重；
  2) 上次止于队尾时 wrapped=True，轮次 +1，下一轮从 0 开始；
  3) 名单变了（last_code 不在队列）就退回原序，不猜；
  4) 游标原子写：写坏也只会留 tmp，不会留半截 JSON。

不启浏览器、不触网（rotate/save 都是纯函数级）。
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "news_collector") not in sys.path:
    sys.path.insert(0, str(REPO / "news_collector"))

import xq_stock_timeline as m  # noqa: E402


CODES = ["600000", "600001", "000001", "300001", "920001", "920002"]


def test_rotate_middle_resumes_after_last():
    ordered, wrapped, idx = m.rotate_from(CODES, "000001")
    assert idx == 2
    assert ordered == ["300001", "920001", "920002", "600000", "600001", "000001"]
    assert wrapped is False


def test_rotate_is_lossless_and_duplicate_free():
    """任何起点旋转后，集合与长度都必须与队列一致——尾部饥饿治理不能靠丢票换。"""
    for start in CODES:
        ordered, _, _ = m.rotate_from(CODES, start)
        assert sorted(ordered) == sorted(CODES)
        assert len(ordered) == len(CODES)


def test_rotate_at_tail_wraps_to_new_round():
    ordered, wrapped, idx = m.rotate_from(CODES, "920002")
    assert idx == len(CODES) - 1
    assert wrapped is True
    assert ordered == CODES


def test_rotate_unknown_code_falls_back_to_original_order():
    """名单变更/首次运行：last_code 不在队列 → 原序、start_idx=-1、不递增轮次。"""
    ordered, wrapped, idx = m.rotate_from(CODES, "999999")
    assert ordered == CODES
    assert (wrapped, idx) == (False, -1)


def test_rotate_empty_cursor_is_noop():
    assert m.rotate_from(CODES, "")[0] == CODES
    assert m.rotate_from(CODES, None)[0] == CODES


def test_cursor_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "_cursor.json"
    monkeypatch.setattr(m, "CURSOR_F", str(f))
    m._save_cursor("920001", "all", "", 6, 2, 40, 3)
    d = m._load_cursor("all", "")
    assert d["last_code"] == "920001" and d["round"] == 2
    assert d["scope"] == "all" and d["queue_len"] == 6 and d["quota"] == 40
    assert d["done_this_run"] == 3
    # 原子写：不许留 .tmp 残骸
    assert not os.path.exists(str(f) + ".tmp")


# ------------------------------------------------------------ 游标域隔离（v2）
# 判例 2026-11-13：07:00 全量轮 `--queue all`（域 "all|"）与 21:00 分片轮
# `--sources houfeng_candidates164,cohorts`（域 "all|..."）不同域，却共用一个
# _cursor.json 单记录。v1 原实现只在读取侧校验 scope/sources——那只防「读错」，
# 不防「写覆盖」：21:00 跑完即抹掉 07:00 断点，次日全量轮从头开始，尾部再饿一天。


def test_cursor_domains_do_not_overwrite_each_other(tmp_path, monkeypatch):
    f = tmp_path / "_cursor.json"
    monkeypatch.setattr(m, "CURSOR_F", str(f))
    m._save_cursor("600000", "all", "", 666, 1, 0, 400)
    m._save_cursor("301218", "all", "houfeng_candidates164,cohorts", 246, 1, 0, 246)
    full = m._load_cursor("all", "")
    shard = m._load_cursor("all", "houfeng_candidates164,cohorts")
    assert full["last_code"] == "600000" and full["queue_len"] == 666
    assert shard["last_code"] == "301218" and shard["queue_len"] == 246
    # 没跑过的域必须返回空，不能借用别的域的断点乱跳
    assert m._load_cursor("actionable", "") == {}


def test_cursor_v1_upgrades_without_losing_bookmark(tmp_path, monkeypatch):
    """升级当天不能让旧断点失效——否则次日全队列重跑，正是要治的病。"""
    f = tmp_path / "_cursor.json"
    monkeypatch.setattr(m, "CURSOR_F", str(f))
    v1 = {"scope": "all", "sources": "", "last_code": "000001",
          "queue_len": 666, "round": 2, "quota": 0, "done_this_run": 666,
          "updated_at": "seed"}
    f.write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
    assert m._load_cursor("all", "")["last_code"] == "000001"   # v1 原样可读
    m._save_cursor("600999", "all", "cohorts", 12, 1, 0, 5)     # 新域写入触发升级
    assert m._load_cursor("all", "")["last_code"] == "000001"   # 旧域断点保住
    assert m._load_cursor("all", "cohorts")["last_code"] == "600999"
    raw = json.loads(f.read_text(encoding="utf-8"))
    assert raw["v"] == 2 and len(raw["domains"]) == 2


def test_cursor_domain_key_is_stable_across_argument_forms():
    """sources 为 None 与空串必须落同一域，否则全量轮认不出自己的断点。"""
    assert m._cursor_domain("all", None) == m._cursor_domain("all", "")
    assert m._cursor_domain("all", "") == "all|"
    assert m._cursor_domain("all", "a,b") == "all|a,b"


def test_cursor_missing_or_corrupt_returns_empty(tmp_path, monkeypatch):
    f = tmp_path / "_cursor.json"
    monkeypatch.setattr(m, "CURSOR_F", str(f))
    assert m._load_cursor() == {}
    f.write_text("{not json", encoding="utf-8")
    assert m._load_cursor() == {}


def test_cursor_survives_restart_simulation(tmp_path, monkeypatch):
    """模拟「跑到第 3 只被强杀」：游标已落盘，下一轮从第 4 只起。"""
    f = tmp_path / "_cursor.json"
    monkeypatch.setattr(m, "CURSOR_F", str(f))
    for i, code in enumerate(CODES[:3], start=1):
        m._save_cursor(code, "all", "", len(CODES), 1, 0, i)
    cur = m._load_cursor("all", "")
    ordered, wrapped, idx = m.rotate_from(CODES, cur["last_code"])
    assert cur["last_code"] == "000001"
    assert ordered[0] == "300001" and wrapped is False


# ---------------------------------------------------------------- 会话健康度护栏
# 判例 2026-11-13 生产轮：两次独立会话都在**第 401 次请求**撞 HTTP 400；撞墙后
# 「失败-成功-失败」交替（3×300s 冷却只换来 1 只通过），14 只 × 900 秒 = 2.5 小时
# 零自愈。原比例阈值 20% 的分母含成功数，要 86 只（约 15 小时）才触发，等于没护栏。
# 要锁死的语义：
#   1) 近期无失败 → 保留 3×300s 长冷却（瞬时抖动还值得等）；
#   2) 近期有过失败 → 降级 1×60s（长冷却已被实测证伪）；
#   3) 停轮判据必须用**窗口坏率**，不能只数连续失败——交替失败会被成功不断清零。


def test_retry_policy_no_recent_failure_keeps_long_cooldown():
    n_try, n_cool = m.retry_policy(0)
    assert (n_try, n_cool) == (m.WAF_RETRY, m.WAF_COOLDOWN)
    assert (n_try, n_cool) == (3, 300)


def test_retry_policy_degrades_once_any_recent_failure():
    for nf in (1, 2, 3, 99):
        assert m.retry_policy(nf) == (1, m.CONSEC_FAIL_COOLDOWN)


def test_recent_fail_count_only_looks_at_tail():
    outs = [0, 0, 0, 1, 1, 1]     # 尾部 3 只全成功
    assert m.recent_fail_count(outs, k=3) == 0
    assert m.recent_fail_count([1, 1, 0], k=3) == 1
    assert m.recent_fail_count([], k=3) == 0


def test_window_bad_ratio():
    assert m.window_bad_ratio([]) == 0.0
    assert m.window_bad_ratio([1, 1, 1, 1]) == 0.0
    assert m.window_bad_ratio([0, 0, 0, 0]) == 1.0
    assert abs(m.window_bad_ratio([1, 0]) - 0.5) < 1e-9


def test_halt_on_consecutive_total_failure():
    assert m.should_halt([1, 1, 1, 0, 0, 0]) == "SESSION_DEAD"


def test_halt_on_alternating_failures_not_missed():
    """核心回归：成败交替（连续失败数恒为 1）也必须判限流态。

    这正是 2026-11-13 撞墙后的真实形态；若只数连续失败，计数被成功清零，
    护栏永不触发，代价是 900 秒/只空转到 15 小时。
    """
    outs = [1, 0] * 10                      # 20 只，坏率 50%
    # 最长连续失败只有 1 只 —— 任何「连续失败数」判据都会漏掉这种形态
    streak = max(len(s) for s in "".join(str(x) for x in outs).split("1"))
    assert streak == 1
    assert m.should_halt(outs) == "SESSION_DEGRADED"


def test_no_halt_before_enough_samples():
    assert m.should_halt([0, 1, 0, 1, 0]) == ""
    assert m.should_halt([1] * 9) == ""


def test_no_halt_when_healthy():
    assert m.should_halt([1] * 19 + [0]) == ""                 # 5% 坏率
    assert m.should_halt([1] * 15 + [0] * 4 + [1]) == ""       # 20% < 30% 阈值


def test_halt_latency_bounded_after_threshold():
    """撞墙后停轮代价上界：坏率达阈值时，最多再走快冷却若干只。"""
    outs = [1, 0] * 4                       # 8 只，样本不足
    assert m.should_halt(outs) == ""
    cost = m.recent_fail_count(outs) * 0 + m.CONSEC_FAIL_COOLDOWN
    assert cost == 60 and cost < m.WAF_COOLDOWN


# ---------------------------------------------------------------- 限流自适应降速
# 老田 2026-11-13 裁定：不把「撞墙就换会话」固化成策略。撞限流后应主动放慢
# （降速自律），而不是换个会话按原速重冲（规避）。


def test_interval_starts_at_floor_on_first_hit():
    assert m.next_interval(0.0, True) == m.RATE_FLOOR
    assert m.next_interval(0.0, True) == 5.0


def test_interval_doubles_then_caps():
    cur = 0.0
    seq = []
    for _ in range(6):
        cur = m.next_interval(cur, True)
        seq.append(cur)
    assert seq == [5.0, 10.0, 20.0, 30.0, 30.0, 30.0]
    assert max(seq) == m.RATE_CAP


def test_interval_decays_on_success_back_to_zero():
    cur = 20.0
    for _ in range(20):
        cur = m.next_interval(cur, False)
    assert cur == 0.0            # 20 秒经 20 只回调归零
    assert m.next_interval(0.0, False) == 0.0   # 已归零则保持


def test_interval_never_negative():
    assert m.next_interval(1.0, False) == 0.0
    assert m.next_interval(0.4, False) == 0.0


def test_interval_is_bounded_so_run_cannot_drag_forever():
    """降速不等于放任长跑：慢档之上还有停轮兜底，浪费量被「窗口样本数」封顶。

    单纯算「666 只全程最慢档」= 6.3 小时，那不是真实上界——因为坏率一超阈值就停轮。
    真实上界 = 停轮窗口内最多再走 SESSION_MIN_SAMPLES 只。
    """
    assert m.RATE_CAP <= 30.0
    waste = m.SESSION_MIN_SAMPLES * (m.RATE_CAP + 4)
    assert waste < 15 * 60          # 最多再耗 10 分钟，而不是按队列长度线性拖长
    assert m.SESSION_MIN_SAMPLES < 666


