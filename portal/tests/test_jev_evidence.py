"""Step 4b 门禁：L1 下半区证据层可收，但 L1 结构零删除（判例 #31 补）。

纯静态读取 jev_graph.html，不依赖服务、不联网、不碰 8091。
背景：老田 2026-11-09 批准「中间不要长图」→ 4 组 8 张证据卡默认收起、逐组可展；
首屏只留控件 / kpis / 板块榜 / 联动网 / 焦点板块 / 时间轴。
"""
import io
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GRAPH = os.path.join(ROOT, "portal", "jev_graph.html")
GROUPS = ("fund", "social", "relay", "cross")
L1_IDS = (
    "kpis", "boards", "net", "tl", "flow", "disc", "social", "posts",
    "ladder", "relay", "cross", "basis", "tip",
)
L1_RENDER = (
    "drawNet()", "drawTiers()", "drawTL()", "drawCross()", "drawFlow()",
    "drawDisc()", "drawSocial()", "drawChain()", "drawBasis()",
)


@pytest.fixture(scope="module")
def g():
    with io.open(GRAPH, encoding="utf-8", newline="") as f:
        return f.read()


def test_four_evidence_groups_exist(g):
    assert g.count('class="grid evidence"') == 4


@pytest.mark.parametrize("key", GROUPS)
def test_group_defaults_to_collapsed(g, key):
    assert 'data-ev="%s" hidden' % key in g, "%s 组未默认收起" % key


def test_hidden_overrides_grid_display(g):
    """.grid{display:grid} 会盖掉 UA 的 [hidden]{display:none}，必须显式覆写。"""
    assert ".evidence[hidden]{display:none}" in g


def test_evbar_controls(g):
    assert 'id="evBar"' in g
    assert g.count('aria-pressed="false"') == 5, "4 组 + 全部展开共 5 个 chip"
    assert 'data-ev="all"' in g


def test_toggle_logic_present(g):
    for part in ("function toggle(k)", "function sync()", "e.hidden=allOpen",
                 'b.setAttribute("aria-pressed"', 'b.onclick=function()',
                 '"全部收起":"全部展开"'):
        assert part in g, "折叠交互缺失: " + part


def test_l1_containers_not_deleted(g):
    missing = [x for x in L1_IDS if 'id="%s"' % x not in g]
    assert not missing, "折叠把 L1 容器删了: %s" % missing


def test_l1_render_chain_not_deleted(g):
    missing = [x for x in L1_RENDER if x not in g]
    assert not missing, "折叠把渲染链删了: %s" % missing
    assert "drawNet(); drawTiers(); drawTL(); drawCross(); drawFlow();" in g


def test_middle_graph_stays_visible(g):
    """中间联动网是 L2 下钻入口，不能被折叠。"""
    assert g.index('id="net"') < g.index('class="grid evidence"')


def test_evbar_sits_after_timeline(g):
    assert g.index('id="evBar"') > g.index('id="tl"')
    assert g.index('id="evBar"') < g.index('class="grid evidence"')


def test_drill_layer_still_outside_main(g):
    assert '<div id="drill" hidden>' in g
    assert g.index('id="drill"') > g.index("</main>")


def test_inline_script_syntax(g, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用")
    m = re.search(r"<script>([\s\S]*?)</script>", g)
    assert m, "找不到内联 script 块"
    p = tmp_path / "jev_graph.js"
    p.write_text(m.group(1), encoding="utf-8")
    r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, "内联脚本语法错误: %s" % r.stderr[:400]
