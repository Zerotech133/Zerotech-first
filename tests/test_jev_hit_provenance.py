# -*- coding: utf-8 -*-
r"""port v1 · 判定命中溯源（portal/jev_hit_basis.py）单元测试。

【判例来源】2026-11-08 老田实测截图：「判定快照·三面依据·宁德时代」里
  出现「2026中原农谷种业大会在河南启幕 聚焦国际育种合作」「医保影像AI大赛
  收官」两条利多项，conf 0.8/0.6。追问「命中点在哪」——查库坐实：
      title  「2026中原农谷种业大会在河南启幕 聚焦国际育种合作」
      content 64 字，通篇不含「宁德时代」「宁德」「300750」「电池」
      jev    {"companies":["300750"],"anchor":{"type":"stock","code":"300750"},
              "bias":"bull","conf":0.8,
              "reason":"种业大会促国际合作，利好生物育种龙头股价短期上行"}
  而同一新闻的另一副本 companies 为 []。即：判定期 LLM 自由填代码，聚合层
  裸 LIKE 采信，两层都没有任何「原文里到底提没提这只票」的校验。

【硬边界】本测试不写库、不联网、不起服务；stock_basic 名录用 monkeypatch
  注入假数据，不依赖 datamart/pandas 是否安装（CI 与生产解释器不同）。

用例清单：
  HP-N01 P0 幻觉拒绝：正文提都不提本标的 → code_only / keep=False
  HP-N02 P0 文本确证：标题出现公司名 → text_confirmed / keep=True
  HP-N03 P0 板块级：未点名个股但出现所属行业词 → industry_only（保留并标注）
  HP-N04 P0 代码直出：正文出现 6 位代码 → text_confirmed
  HP-N05 P0 判定为空时按标题名称判（未判定行也要能算关不相关）
  HP-N06 P1 rank 单调且与 keep 一致
  HP-N07 P1 跨行业词不误认：种业词不得被「电气设备」认领
"""
from __future__ import annotations

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTAL = os.path.join(ROOT, "portal")
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import jev_hit_basis as H  # noqa: E402

FAKE_REG = {
    "300750": {"name": "宁德时代", "industry": "电气设备", "area": "福建"},
    "300014": {"name": "亿纬锂能", "industry": "电气设备", "area": "广东"},
    "002415": {"name": "海康威视", "industry": "元器件", "area": "浙江"},
}


@pytest.fixture(autouse=True)
def _fake_registry(monkeypatch):
    """注入假名录：不让单测依赖 datamart / pandas。"""
    monkeypatch.setitem(H._CACHE, "by_code", dict(FAKE_REG))
    monkeypatch.setitem(H._CACHE, "names", [])
    monkeypatch.setitem(H._CACHE, "at", time.time())
    monkeypatch.setattr(H, "_load_registry", lambda: dict(FAKE_REG))
    yield


SEED_TITLE = "2026中原农谷种业大会在河南启幕 聚焦国际育种合作"
SEED_BODY = ("中新社河南新乡11月7日电 (记者 韩章云)2026中原农谷种业大会7日"
             "在河南新乡开幕，国际育种合作成为与会中外嘉宾关注的焦点。")


def test_hp_n01_hallucinated_code_is_rejected():
    """HP-N01 幻觉拒绝：这才是老田截图那条。"""
    r = H.verify(SEED_TITLE, SEED_BODY, "300750",
                 {"companies": ["300750"], "bias": "bull", "conf": 0.8})
    assert r["verdict"] == "code_only"
    assert r["keep"] is False
    assert r["in_code"] is True          # 绑定确实存在
    assert r["terms"]["name"] == []      # 但文本零支撑
    assert r["terms"]["industry"] == []  # 连行业词都没有
    assert "无文本支撑" in r["why"]


def test_hp_n02_text_confirmed_by_name():
    """HP-N02 标题出现公司名 → 采信，并给出命中片段。"""
    t = "宁德时代在福鼎成立新材料科技公司 注册资本1.2亿"
    r = H.verify(t, "", "300750", {"companies": ["300750"]})
    assert r["verdict"] == "text_confirmed"
    assert r["keep"] is True
    assert r["terms"]["name"] == ["宁德时代"]
    assert "宁德时代" in r["hit_text"]


def test_hp_n03_industry_only_is_kept_but_labelled():
    """HP-N03 板块级：文中只有行业词，没有个股名 → 保留但明确标注。"""
    t = "固态电池中试产能集中落地 产业发展进入关键窗口期"
    r = H.verify(t, "", "300750", {"companies": ["300750"]})
    assert r["verdict"] == "industry_only"
    assert r["keep"] is True
    assert r["terms"]["industry"], "应命中行业同义词（电池/固态电池）"
    assert "板块级" in r["why"]


def test_hp_n04_code_in_text():
    """HP-N04 正文直接出现 6 位代码 → 视为文本确证。"""
    r = H.verify("盘中异动", "300750 盘中涨超5%，现报252.22元。", "300750", {})
    assert r["verdict"] == "text_confirmed"
    assert r["terms"]["code"] == ["300750"]


def test_hp_n05_no_jev_still_works():
    """HP-N05 未判定行（jev 为空）也要能判关不相关。"""
    ok = H.verify("宁德时代与上海电气签署战略合作协议", "", "300750", {})
    no = H.verify("某地发布暴雨橙色预警", "", "300750", {})
    assert ok["verdict"] == "text_confirmed"
    assert no["verdict"] == "none"
    assert no["keep"] is False


def test_hp_n06_rank_monotonic():
    """HP-N06 rank 严格单调，且 keep 恰好落在 rank≥2 的两档。"""
    assert H.rank("none") == 0
    assert H.rank("code_only") == 1
    assert H.rank("industry_only") == 2
    assert H.rank("text_confirmed") == 3
    # keep ⇔ rank≥2：三档实测样例
    samples = [
        ("宁德时代签约", {"companies": ["300750"]}, True),     # text_confirmed
        ("固态电池产能落地", {"companies": ["300750"]}, True),  # industry_only
        (SEED_TITLE, {"companies": ["300750"]}, False),        # code_only
    ]
    for title, jev, want_keep in samples:
        r = H.verify(title, "", "300750", jev)
        assert r["keep"] is want_keep, (r["verdict"], title)
        assert (H.rank(r["verdict"]) >= 2) is want_keep


def test_hp_n07_no_cross_industry_false_positive():
    """HP-N07 跨行业不误认：种业类词汇不能给「电气设备」的票背书。"""
    r = H.verify("种业大会聚焦国际育种合作", "粮食增产、良种推广", "300750", {})
    assert r["verdict"] == "none", r["terms"]
    assert not r["terms"]["industry"], "育种/种业不是电气设备行业词，不得命中"
