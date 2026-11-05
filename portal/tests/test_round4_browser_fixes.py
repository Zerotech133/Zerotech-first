"""浏览器复核回归守卫（2026-11-06）。

在真实 Chromium 里复核五页目录/弹窗渲染时抓到两处单测盖不住的缺陷：

1. `clamped=false` 时弹窗仍无条件写「注意：limit 已截断」——把「样本非全量」
   与「请求 limit 被服务端夹到上限」两种语义混为一谈，会误导使用者。
   统一改为「样本说明：... clamped=<bool>。仅返回样本行，不代表全表数据。」，
   只有 `clamped===true` 才额外说明「已夹至 N 行」。
2. `lineage.html` 的 `#r3cNotice` 初始文案「实时通道未就绪」在实时成功后
   **从未隐藏**（`r3cSetState` 只改了 `#r3cState`），用户会看到自相矛盾的状态。
   改为按状态同步 `r3c-hidden`。

本文件只做静态契约断言，不依赖浏览器，防止两处缺陷回流。
"""
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]

# R2-A / R3-C 五页（上轮 T2 的 dw.html 写法本来就正确，单独作对照）
PAGES = [
    "static/dw.html",
    "static/dw_v2.html",
    "picks.html",
    "dataops.html",
    "lineage.html",
]


def _text(rel):
    return (PORTAL / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", PAGES)
def test_no_misleading_clamped_wording(rel):
    """不得再出现无条件宣称「limit 已截断」的旧文案。"""
    src = _text(rel)
    assert "注意：limit 已截断" not in src, rel
    assert "样本说明：" in src, rel
    assert "仅返回样本行，不代表全表数据。" in src, rel


@pytest.mark.parametrize("rel", PAGES)
def test_clamped_branch_only_when_true(rel):
    """「已夹至 N 行」必须挂在 d.clamped 条件分支里，不能无条件输出。"""
    src = _text(rel)
    assert "d.clamped?'请求的 limit 超过上限，已夹至 '" in src, rel
    assert "clamped='+esc(!!d.clamped)+'" in src, rel


@pytest.mark.parametrize("rel", PAGES)
def test_sample_note_and_freshness_still_rendered(rel):
    """修文案不得顺手删掉「样本按存储顺序」与 catalog 新鲜度提示。"""
    src = _text(rel)
    assert "d.sample_note" in src, rel
    assert "meta.freshness" in src or "freshness" in src, rel


def test_lineage_notice_hidden_on_live():
    src = _text("lineage.html")
    # r3cSetState 必须同时处理 notice，且实时/非实时两分支都有
    marker = "function r3cSetState(t)"
    assert src.count(marker) == 1
    body = src[src.index(marker): src.index(marker) + 400]
    assert "r3cNotice" in body
    assert "n.classList.add('r3c-hidden')" in body
    assert "n.classList.remove('r3c-hidden')" in body
    assert "catalog 实时" in body


def test_dw_html_original_wording_unchanged():
    """上轮 T2 的 dw.html 只在 clamped 为真时说截断，未被本轮批量替换误伤。"""
    src = _text("dw.html")
    assert "d.clamped?' · limit 已截断至 '" in src
    assert "注意：limit 已截断" not in src


@pytest.mark.parametrize("rel", PAGES)
def test_upstream_chains_intact(rel):
    """修文案不得破坏各页既有主数据链。"""
    src = _text(rel)
    if rel == "static/dw.html":
        assert "/api/dataops/overview" in src
        assert "/static/dw_snapshot.json" in src
    elif rel == "static/dw_v2.html":
        assert "const DW_DATA" in src
        assert "/api/dw/query" in src
    elif rel == "picks.html":
        assert "data/todays_picks.json" in src
    elif rel == "dataops.html":
        assert "/api/dataops/map" in src
        assert "/api/dataops/entity" in src
    else:  # lineage.html
        assert "/api/kg/lineage" in src
        assert "127.0.0.1:8585" in src
