# -*- coding: utf-8 -*-
"""R2-B 清理收口 · 第二轮契约测试（2026-11-05）。

纯读 portal 前端源文本做断言，不依赖浏览器 / 服务 / 网络，
与 test_dw_catalog_fallback.py 同风格（模块级 SRC + 纯 assert）。

覆盖 spec §5「B 组本轮执行范围」的零风险零决策子集：
  D5 / D1 / D6 / D12 / C1 / G2（仅注释）/ F1-标注。
并用负向 / 守卫断言证明「明确不做」的 6 类项及两个只读 Python/nginx
文件未被本轮误改。

约定：
  * 「8094 直连已消失」指 http://127.0.0.1:8094/api/chat 这一绝对 URL，
    不含注释里作为说明文字出现的纯数字 8094。
  * 「D12 只断言改的那一行」：6 个绝对 URL 全在 d1_war_room.html 的
    const DATA 单行快照里，故按该行断言。
  * G2 负向断言只排除「被当作 fetch URL 的带尾斜杠写法」——
    即被引号精确包围的 '/api/os/' / "/api/os/"；带子路径的
    /api/os/xxx 本就不在 301 规避范围（T5 §7.1），不在此断言内。
"""
from pathlib import Path
import json
import re

PORTAL = Path(__file__).resolve().parents[1]


def _src(rel):
    return (PORTAL / rel).read_text(encoding="utf-8")


def _raw(rel):
    return (PORTAL / rel).read_bytes()


QUANT_DRILL = _src("quant_drill.html")
INDEX = _src("index.html")
OPS = _src("ops.html")
OS = _src("os.html")
CAINIAO = _src("static/cainiao.js")
D1_WAR_ROOM = _src("d1_war_room.html")
METHODS = _src("methods.html")
RADAR = _src("radar.html")
OS_PERSONAL = _src("os_personal.html")
QUANT = _src("quant.html")


def _d12_line():
    """返回 d1_war_room.html 中含 admin_url 的快照超长行（应唯一）。"""
    lines = [ln for ln in D1_WAR_ROOM.splitlines() if '"admin_url"' in ln]
    assert len(lines) == 1, "D12 目标快照行应唯一，实际 %d 行" % len(lines)
    return lines[0]


# ---------------------------------------------------------------- 源文件真实性
def test_source_files_real():
    for rel in ("quant_drill.html", "index.html", "ops.html", "os.html",
                "static/cainiao.js", "d1_war_room.html", "methods.html",
                "radar.html", "os_personal.html", "quant.html",
                "nginx.conf", "portal_api.py"):
        assert (PORTAL / rel).is_file(), rel
        assert len(_raw(rel)) > 1000, "文件体积异常: %s" % rel
    # 防止读到空文件 / 错文件（否则后面的断言会退化成恒真）
    assert "fetchFirst" in QUANT_DRILL
    assert "const DATA" in D1_WAR_ROOM
    assert "CAI_API_FALLBACK" in CAINIAO


# ---------------------------------------------------------------- D5
def test_d5_admission_direct_fallback_removed():
    assert 'fetchFirst(["/api/admission"])' in QUANT_DRILL, "数组未收敛为仅同源位"
    assert QUANT_DRILL.count('fetchFirst(["/api/admission"])') == 1
    assert "/api/admission" in QUANT_DRILL, "同源 /api/admission 仍在"
    assert "http://127.0.0.1:8791/api/admission" not in QUANT_DRILL, "8791 直连兜底项已消失"


# ---------------------------------------------------------------- D1
def test_d1_ollama_tags_same_origin_on_three_pages():
    for name, src in (("index.html", INDEX), ("ops.html", OPS), ("os.html", OS)):
        assert "/ollama/api/tags" in src, "%s 缺少 /ollama/api/tags" % name
        assert src.count("/ollama/api/tags") >= 1, "%s 替换处数异常" % name
        assert "http://localhost:11434/api/tags" not in src, "%s 旧跨域 URL 仍在" % name
        assert re.search(r"fetch\('/ollama/api/tags'", src), "%s 未经过 fetch 发出" % name


# ---------------------------------------------------------------- D6
def test_d6_cai_fallback_same_origin():
    assert "/cai/api/chat" in CAINIAO
    assert "var CAI_API_FALLBACK = '/cai/api/chat';" in CAINIAO, "兜底值未改"
    assert "http://127.0.0.1:8094/api/chat" not in CAINIAO, "8094 直连已消失"
    assert "http://127.0.0.1:8094" not in CAINIAO, "任何 8094 绝对 URL 已消失"
    # 注释里作为说明文字的纯数字 8094 允许保留（本轮只改 URL 字面量）


# ---------------------------------------------------------------- D12
def test_d12_snapshot_urls_are_site_relative():
    line = _d12_line()
    for s in ('"admin_url": "/watch-admin"',
              '"watch_admin": "/watch-admin"',
              '"d1": "/d1_research.html"',
              '"quant_paper": "/quant_paper.html"',
              '"legacy_paper": "/paper"',
              '"confirm": "/sim_confirm.html"'):
        assert s in line, "缺相对路径: %s" % s
    # 只断言改的这一行：不再出现两个端口的绝对 URL 字面量
    assert "127.0.0.1:8734" not in line
    assert "127.0.0.1:8789" not in line
    # 本页 6 处全在该行，全文断言亦成立
    assert "http://127.0.0.1:8734" not in D1_WAR_ROOM
    assert "http://127.0.0.1:8789" not in D1_WAR_ROOM


def test_d12_embedded_object_still_valid_json():
    line = _d12_line()
    i = line.find("{")
    assert i >= 0
    obj, _ = json.JSONDecoder().raw_decode(line[i:])
    vals = []

    def walk(o):
        if isinstance(o, str):
            vals.append(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    assert not any("127.0.0.1" in v or "://" in v for v in vals), "内嵌对象仍含绝对 URL"
    for v in ("/watch-admin", "/d1_research.html", "/quant_paper.html",
              "/paper", "/sim_confirm.html"):
        assert v in vals, "缺相对路径值: %s" % v


# ---------------------------------------------------------------- C1
def test_c1_design_baseline_comments_point_to_os_entry():
    for name, src in (("methods.html", METHODS), ("radar.html", RADAR)):
        line8 = src.splitlines()[7]
        assert "设计基线" in line8, "%s 第 8 行不是设计基线注释" % name
        assert "/os-entry" in line8, "%s 注释未改指 /os-entry" % name
        assert "os_entry.html" not in line8, "%s 注释仍指向评审稿" % name
    assert "os_entry.html" not in METHODS
    assert "os_entry.html" not in RADAR


# ---------------------------------------------------------------- G2
def test_g2_bare_call_annotated_not_rewritten():
    # 裸调用逐字未改
    assert "['OS','/api/os',x=>fromOS(x,today)]" in OS_PERSONAL
    # 新增「待 L2 修 301」注释，且只加一条
    assert "待 L2 修 301" in OS_PERSONAL
    assert OS_PERSONAL.count("待 L2 修 301") == 1
    # 负向：绝不能把裸调用改成被引号包围的带尾斜杠 fetch URL
    assert not re.search(r"""['"]/api/os/['"]""", OS_PERSONAL), "/api/os 被误改为 /api/os/"
    assert not re.search(r"""['"]/api/org/['"]""", OS_PERSONAL), "/api/org 被误改为 /api/org/"
    assert "'/api/os/'" not in OS_PERSONAL and '"/api/os/"' not in OS_PERSONAL


def test_g2_quant_html_left_untouched():
    # quant.html 不存在 /api/os、/api/org 裸调用，故按指示「否则不动」
    assert "/api/os" not in QUANT and "/api/org" not in QUANT
    assert "待 L2 修 301" not in QUANT, "quant.html 不应被加 G2 注释"


# ---------------------------------------------------------------- F1-标注
def test_f1_non_production_marker_and_nav_intact():
    """[R6 2026-11-06] 本页在 R6 又追加了一条页尾统一角标，故由 ==1 放宽为
    「首部原始标注仍在」（原文案不动）+ 统一标记存在；导航引用守卫不变。"""
    assert "非生产标注：本页为「产品原型 / 静态快照」" in D1_WAR_ROOM
    assert 'data-nonprod="1"' in D1_WAR_ROOM
    # 严禁摘除任何导航引用
    assert '<script src="/static/nav.js" defer></script>' in D1_WAR_ROOM
    assert '<script src="/static/shortscreen.js" defer></script>' in D1_WAR_ROOM


# ---------------------------------------------------------------- 不做项守卫
def test_guard_explicitly_not_done_items_untouched():
    """R5（2026-11-06）老田批准执行 T5 六类后，原「不做项」翻转为「已执行」。

    函数名保留以维持引用稳定；断言方向已反转，完整验证见
    portal/tests/test_round5_t5.py。
    """
    assert "127.0.0.1:8796" not in _src("ide.html"), "D2 未执行（应已改 /openhands）"
    assert "127.0.0.1:8096" not in _src("legion.html"), "D3 未执行（legion 应已改 /legion-api）"
    assert "9899" not in _src("static/nav.js"), "D7 未执行（nav.js 应已含 /cowagent/）"
    assert INDEX.count("/screen.html") == 1, "E1 旧路径应只剩 FAV_ALIAS 的 key 一处"
    assert INDEX.count("/audit.html") == 1, "E1 旧路径应只剩 FAV_ALIAS 的 key 一处"
    assert "localhost:3000" not in _src("dev.html"), "D4 未执行（应已改 /probe/webui）"
    assert "localhost:4100" in QUANT and "localhost:8888" in QUANT, "D4 工具外链被误改"


# ---------------------------------------------------------------- 全局守卫
def test_global_guard_no_round2_markers_in_readonly_files():
    marks = ("待 L2 修 301", "G2 标注", "非生产标注", "/os-entry（现役入口）")
    for rel in ("nginx.conf", "portal_api.py"):
        src = _src(rel)
        for m in marks:
            assert m not in src, "%s 出现本轮新增标记 %r（被误改）" % (rel, m)


# ---------------------------------------------------------------- 字节 / 换行纪律
def test_newline_style_preserved_crlf_raw_bytes():
    for rel in ("quant_drill.html", "index.html", "ops.html", "os.html",
                "static/cainiao.js", "d1_war_room.html", "methods.html",
                "radar.html", "os_personal.html"):
        b = _raw(rel)
        assert b.count(b"\r\n") > 0, "%s 换行风格不是 CRLF" % rel
        assert b.replace(b"\r\n", b"").find(b"\n") == -1, "%s 混入 LF-only 换行" % rel


def test_ord_verify_ascii_digits_and_raw_byte_counts():
    # 日期/编号类结论用 ord() / 原始字节复核，不凭打印结果判断
    line = _d12_line()
    m = re.search(r'"date":\s*"(\d{4}-\d{2}-\d{2})"', line)
    assert m, "快照日期缺失"
    for ch in m.group(1):
        assert ord(ch) == 45 or 48 <= ord(ch) <= 57, "日期含非 ASCII 数字: %r" % ch
    raw = _raw("d1_war_room.html")
    assert raw.count(b'"admin_url": "/watch-admin"') == 1
    assert raw.count(b'"d1": "/d1_research.html"') == 1
    assert raw.count(b'"confirm": "/sim_confirm.html"') == 1

# ================================================================ R3-B 注释收口（2026-11-05）
# 本轮只改注释文字、零代码逻辑改动。以下 5 个用例在原有 14 用例基础上新增：
# 断言注释与代码一致、API 值未被动、刻意不做项未被误改。

def _cainiao_comments():
    """抽取 cainiao.js 的 // 行注释与 /* */ 块注释文本。
    本文件字符串字面量内不含 '//' 或 '/*'，故该简化状态机对本文件足够。"""
    src = CAINIAO
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j == -1:
                j = n - 2
            out.append(src[i:j + 2])
            i = j + 2
        elif src.startswith("//", i):
            j = src.find("\n", i)
            if j == -1:
                j = n
            out.append(src[i:j])
            i = j
        else:
            i += 1
    return "\n".join(out)


def test_r3b_cainiao_api_values_unchanged():
    """R3-B: PRIMARY/FALLBACK 值仍同源 /cai/api/chat，FALLBACK 重试逻辑未删。"""
    assert "var CAI_API_PRIMARY = '/cai/api/chat';" in CAINIAO
    assert "var CAI_API_FALLBACK = '/cai/api/chat';" in CAINIAO
    assert "fetch(CAI_API_PRIMARY, body)" in CAINIAO, "主链 fetch 未保留"
    assert "fetch(CAI_API_FALLBACK, body)" in CAINIAO, "FALLBACK 同源重试逻辑被删"


def test_r3b_cainiao_8094_gone_from_comments_and_retry_note():
    """R3-B: 三处过时注释已收口，注释里 8094 消失，出现「同源重试」说明。"""
    for old in ("失败回退 127.0.0.1:8094",
                "8094 home_server（CORS 已配）",
                "同源失败（网络/非 ok）→ 回退 8094"):
        assert old not in CAINIAO, "过时注释仍在: %s" % old
    comments = _cainiao_comments()
    assert "8094" not in comments, "注释里仍含 8094"
    assert "同源重试" in comments, "缺「同源重试」类说明"
    # 全文件不再出现 8094 绝对 URL / :8094/api/chat 形式的代码
    assert "127.0.0.1:8094" not in CAINIAO
    assert ":8094/api/chat" not in CAINIAO
    assert "http://127.0.0.1:8094/api/chat" not in CAINIAO


def test_r3b_cainiao_code_strings_intact():
    """R3-B 零代码改动：8094 全文件仅剩报错文案这一处代码字符串，且逐字保留。"""
    assert CAINIAO.count("8094") == 1, "8094 应仅存在于代码字符串一处（注释已清、代码未动）"
    assert "caiAdd('菜鸟后端（8094）没应答：' + e.message, 'cai');" in CAINIAO


def test_r3b_quant_drill_comments_updated():
    """R3-B: 两条过时注释已更新，fetchFirst 数组与 /api/admission 未动。"""
    assert 'fetchFirst(["/api/admission"])' in QUANT_DRILL, "fetchFirst 数组被改动"
    assert QUANT_DRILL.count('fetchFirst(["/api/admission"])') == 1
    assert "/api/admission" in QUANT_DRILL
    assert "http://127.0.0.1:8791/api/admission" not in QUANT_DRILL, "8791 直连兜底项已消失"
    assert "127.0.0.1:8791" not in QUANT_DRILL, "8791 直连绝对地址已消失"
    assert "直连 8791" not in QUANT_DRILL, "第182行旧注释仍在"
    assert "→ 8791" not in QUANT_DRILL, "第158行旧注释仍在"
    assert "8791 直连已退役" in QUANT_DRILL, "L0 准入新注释缺失"
    assert "同源取数 → 缺源 null 灰态" in QUANT_DRILL, "取数新注释缺失"


def test_r3b_global_guard_five_intentional_noops():
    """R3-B 原「5 个刻意不做项」守卫 —— R5 已被老田批准执行，断言方向反转。

    改为「已执行」正向校验；完整验证见 portal/tests/test_round5_t5.py。
    """
    assert "127.0.0.1:8796" not in _src("ide.html"), "ide.html 8796 未清理"
    assert "127.0.0.1:8096" not in _src("legion.html"), "legion.html 8096 未清理"
    assert "9899" not in _src("static/nav.js"), "nav.js 9899 未清理"
    _index = _src("index.html")
    assert _index.count("/screen.html") == 1, "index.html 旧路径应只剩 FAV_ALIAS key"
    assert _index.count("/audit.html") == 1, "index.html 旧路径应只剩 FAV_ALIAS key"
    assert "localhost:3000" not in _src("dev.html"), "dev.html localhost:3000 未清理"
