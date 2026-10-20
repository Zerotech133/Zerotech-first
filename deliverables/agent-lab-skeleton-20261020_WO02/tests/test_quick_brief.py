"""工位N S3 测试：田丰快速结论简报 quick_brief（v0 契约）。

被测对象为纯函数（无 IO 无网络），不实例化 BaseAgent，
故无需清理 Prometheus 注册表。
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from roles.tian_feng import QUICK_BRIEF_RULES, quick_brief


def _rep(platform="p1", doing="推演", progress=50, attention="", ts=1000.0,
         **kw):
    """构造平台日报条目 dict。"""
    d = {"platform": platform, "doing": doing, "progress": progress,
         "attention": attention, "ts": ts}
    d.update(kw)
    return d


# ---- 1) 正常拼装（含 status_points 合并） ----

def test_normal_assembly():
    reports = [_rep("p1", doing="回测", progress=60, ts=100.0),
               _rep("p2", doing="选股", progress=40, ts=200.0)]
    points = [{"platform": "p3", "doing": "新闻汇总", "progress": 10,
               "ts": 50.0}]
    b = quick_brief(reports, points)

    # v0 契约字段齐全
    assert set(b) == {"generated_at", "headline", "conclusions",
                      "attention", "data_gaps"}
    datetime.fromisoformat(b["generated_at"])
    # 无 attention → headline 取 progress 最高平台
    assert b["headline"] == "进展最快平台 p1（progress 60）"
    # 结论按平台首次出现顺序，一句话模板 platform+doing+progress
    assert b["conclusions"] == [
        "p1：回测；progress 60",
        "p2：选股；progress 40",
        "p3：新闻汇总；progress 10",
    ]
    assert b["attention"] == []
    assert b["data_gaps"] == []


# ---- 2) 空输入 headline 如实 ----

def test_empty_inputs_honest():
    b = quick_brief([], None)
    assert b["headline"] == "暂无可用情报"
    assert b["conclusions"] == []
    assert b["attention"] == []
    # 输入为空也如实记录，不造数
    assert any("无任何平台情报输入" in g for g in b["data_gaps"])
    # None 输入同口径
    assert quick_brief(None)["headline"] == "暂无可用情报"


# ---- 3) attention 优先规则 ----

def test_attention_first_rule():
    reports = [_rep("p1", progress=90, attention="模拟账本与台账不一致",
                    ts=1.0),
               _rep("p2", progress=10, ts=2.0)]
    b = quick_brief(reports)
    # 有 attention → 风险优先，即便 p1 的 progress 最高
    assert "需本人留意" in b["headline"]
    assert "p1" in b["headline"]
    assert b["attention"] == ["平台 p1 需留意：模拟账本与台账不一致"]


# ---- 4) progress 最高平台规则 ----

def test_progress_max_rule():
    reports = [_rep("p1", doing="回测", progress=20, ts=1.0),
               _rep("p2", doing="选股", progress=35, ts=2.0),
               _rep("p3", doing="采集", progress=80, ts=3.0)]
    b = quick_brief(reports)
    assert b["headline"] == "进展最快平台 p3（progress 80）"


# ---- 5) 规则可配（attention_first 关闭回落；empty_headline 可换） ----

def test_rules_configurable():
    QUICK_BRIEF_RULES["attention_first"] = False
    try:
        b = quick_brief([_rep("p1", progress=90, attention="需人工核账")])
        # attention 仍如实列出，但 headline 回落 progress 规则
        assert b["attention"]
        assert b["headline"] == "进展最快平台 p1（progress 90）"
    finally:
        QUICK_BRIEF_RULES["attention_first"] = True

    QUICK_BRIEF_RULES["empty_headline"] = "（自定义）本轮无情报"
    try:
        assert quick_brief([])["headline"] == "（自定义）本轮无情报"
    finally:
        QUICK_BRIEF_RULES["empty_headline"] = "暂无可用情报"


# ---- 6) data_gaps 如实（缺失字段不造数） ----

def test_data_gaps_reported_honestly():
    reports = [{"doing": "推演", "progress": 10, "ts": 1.0},   # 缺 platform
               _rep("p1", doing="", progress="", ts=2.0),      # doing/progress 空
               {"platform": "p2", "ts": 3.0}]                  # doing/progress 缺失
    b = quick_brief(reports)

    assert any("platform 缺失" in g for g in b["data_gaps"])
    assert any("doing 缺失" in g for g in b["data_gaps"])
    assert any("progress 缺失" in g for g in b["data_gaps"])
    # 结论中如实标注缺失，不造数
    assert "p1：doing 缺失；progress 缺失" in b["conclusions"]
    assert "p2：doing 缺失；progress 缺失" in b["conclusions"]


# ---- 7) 重复平台取最新（数值 ts / 数值 progress） ----

def test_duplicate_platform_keeps_latest():
    reports = [_rep("p1", doing="回测", progress=30, ts=100.0),
               _rep("p1", doing="回测", progress=70, ts=200.0),  # 最新
               _rep("p1", doing="回测", progress=50, ts=150.0)]
    b = quick_brief(reports)
    assert len(b["conclusions"]) == 1
    assert "progress 70" in b["conclusions"][0]
    assert b["headline"] == "进展最快平台 p1（progress 70）"


# ---- 8) 字符串 progress 不参与大小比较（混型） ----

def test_string_progress_not_miscompared():
    reports = [_rep("p1", doing="回测", progress="70%", ts=1.0),
               _rep("p2", doing="选股", progress=10, ts=2.0)]
    b = quick_brief(reports)
    # "70%" 不被误当数值参与比较 → 数值最高的是 p2
    assert b["headline"] == "进展最快平台 p2（progress 10）"
    # 字符串 progress 如实标注
    assert any("p1" in g and "非数值" in g for g in b["data_gaps"])
    assert "p1：回测；progress 70%" in b["conclusions"]


# ---- 9) 全为字符串 progress：如实「无法排序」，不误比 ----

def test_all_string_progress_headline_honest():
    reports = [_rep("p1", progress="50%", ts=1.0),
               _rep("p2", progress="70%", ts=2.0)]
    b = quick_brief(reports)
    # 不按字符串字典序误选 p2，而是如实标注无法排序
    assert b["headline"] == QUICK_BRIEF_RULES["no_numeric_headline"]
    assert b["headline"].startswith("各平台 progress 均缺失或非数值")
    assert b["conclusions"] == ["p1：推演；progress 50%",
                                "p2：推演；progress 70%"]


# ---- 10) 属性载体（StatusPoint 风格对象）兼容 ----

def test_attribute_carrier_status_point():
    sp = SimpleNamespace(platform="news", doing="舆情采集", progress=5,
                         attention="", blocked=False, ts=10.0)
    b = quick_brief([], [sp])
    assert b["conclusions"] == ["news：舆情采集；progress 5"]
    assert b["headline"] == "进展最快平台 news（progress 5）"
    assert b["data_gaps"] == []

    blocked = SimpleNamespace(platform="news", doing="舆情采集", progress=5,
                              ts=20.0, blocked=True)
    b2 = quick_brief([], [blocked])
    assert b2["attention"] == ["平台 news 自报 blocked"]
    assert "需本人留意" in b2["headline"]
