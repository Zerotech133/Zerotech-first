"""Step 4 门禁：生产页 L1 零改动 + L2/L3 下钻层契约（判例 #31）。

纯静态读取两个 html，不依赖服务、不联网、不碰 8091。
背景：`/board` 指向 chanlun_board.html（结构池与模拟仓·缠论），与 JEV 无关；
JEV 的生产页是 `jev_graph.html`。本门禁锁死「替换下钻层时不得重排 L1 总览」。
"""
import io
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GRAPH = os.path.join(ROOT, "portal", "jev_graph.html")
INTEL = os.path.join(ROOT, "portal", "jev_intel.html")


@pytest.fixture(scope="module")
def pages():
    with io.open(GRAPH, encoding="utf-8", newline="") as f:
        g = f.read()
    with io.open(INTEL, encoding="utf-8", newline="") as f:
        i = f.read()
    return g, i


L1_IDS = (
    "kpis", "boards", "net", "tl", "flow", "disc", "social", "posts",
    "ladder", "relay", "cross", "basis", "tip",
)
L1_RENDER = (
    "drawNet()", "drawTiers()", "drawTL()", "drawCross()", "drawFlow()",
    "drawDisc()", "drawSocial()", "drawChain()", "drawBasis()",
)


def test_l1_containers_all_present(pages):
    g, _ = pages
    missing = [x for x in L1_IDS if 'id="%s"' % x not in g]
    assert not missing, "L1 容器被删: %s" % missing


def test_l1_render_chain_untouched(pages):
    g, _ = pages
    missing = [x for x in L1_RENDER if x not in g]
    assert not missing, "L1 渲染链被删: %s" % missing
    assert "drawNet(); drawTiers(); drawTL(); drawCross(); drawFlow();" in g


def test_drill_layer_lives_outside_main(pages):
    """覆盖层必须在 </main> 之外，才算没侵入 L1 可见结构。"""
    g, _ = pages
    assert '<div id="drill" hidden>' in g
    assert g.index('id="drill"') > g.index("</main>")


def test_drill_layer_parts(pages):
    g, _ = pages
    for part in ("drillWhere", "drillOpen", "drillClose", "drillFrame"):
        assert 'id="%s"' % part in g
    assert "#drill[hidden]{display:none}" in g


def test_drill_url_contract(pages):
    g, _ = pages
    assert '"/jev_intel.html?embed=1"' in g
    for q in ("&day=", "&kind=", "&board=", "&code="):
        assert q in g
    assert "encodeURIComponent" in g


def test_three_drill_entrypoints(pages):
    """点板块→L2、点联动网节点→L3、输入个股成图→L3。"""
    g, _ = pages
    assert 'openDrill(b, "")' in g
    assert "openDrill(b, c)" in g
    assert 'openDrill("", typed)' in g


def test_code_drill_does_not_pin_old_board(pages):
    """个股下钻必须让 /api/jev/graph?code= 自动定位个股所属板块，
    否则 KG 编译的是上一个板块，个股不在 nodes 里会开不出 L3（判例 #31）。"""
    g, _ = pages
    assert "openDrill(focusB" not in g
    assert "openDrill(b, focusB" not in g


def test_new_window_link_strips_embed(pages):
    g, _ = pages
    assert 'replace("?embed=1&","?").replace("&embed=1","")' in g


def test_drill_is_collapsible(pages):
    g, _ = pages
    assert "function closeDrill()" in g
    assert 'e.key==="Escape"' in g
    assert 'e.data.jevDrill==="close"' in g
    assert 'document.body.style.overflow="hidden"' in g
    assert 'document.body.style.overflow=""' in g


def test_intel_embed_mode(pages):
    _, i = pages
    assert "const EMBED = new URLSearchParams(location.search).get('embed') === '1';" in i
    assert 'id="embedBack"' in i
    assert "document.body.classList.add('embed')" in i
    assert "body.embed .topbar .crumb, body.embed .topbar .kpis{display:none}" in i
    assert "jevDrill: 'close'" in i


def test_intel_code_deeplink_passes_to_graph(pages):
    """build() 第 1053 行按 code 定位 focus_name，深链必须透传 code。"""
    _, i = pages
    assert "const dc = new URLSearchParams(location.search).get('code');" in i
    assert "u += '&code=' + encodeURIComponent(dc)" in i


def test_intel_kg_entity_fallback(pages):
    """NODE_MAX 截断 / 跨板块：视图里没该股时，从 KG 实体表按 6 位码反查。"""
    _, i = pages
    assert "showEntity(ref || _deepCode)" in i
    assert "S.kg.type(k) === 'stock' && c6(k) === c6(_deepCode)" in i
    assert "if (_deepCode) {" in i


def test_intel_embed_code_opens_l2_background(pages):
    _, i = pages
    assert "if (EMBED && _deepCode && !board) {" in i
    assert "S.view = 'graph';" in i


def _inline_script(html):
    m = re.search(r"<script>([\s\S]*?)</script>", html)
    assert m, "找不到内联 script 块"
    return m.group(1)


@pytest.mark.parametrize("name", ["jev_graph.html", "jev_intel.html"])
def test_inline_script_syntax(pages, name, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过 JS 语法门禁")
    g, i = pages
    js = _inline_script(g if name == "jev_graph.html" else i)
    p = tmp_path / (name + ".js")
    p.write_text(js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(p)], capture_output=True, text=True)
    assert r.returncode == 0, "%s 内联脚本语法错误: %s" % (name, r.stderr[:400])
