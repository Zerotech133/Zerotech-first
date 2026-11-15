# -*- coding: utf-8 -*-
"""I1b §3.1 导航域回归：新域 4 页高亮正确 + 老 8 页零漂移 + 带斜杠 falsifier。

为什么必须有 falsifier（I1 施工单 §1.2）
────────────────────────────────────────
nav.js 的当前页判定是「遍历全部域、无 break、后匹配者胜」，且用纯字符串前缀
`path.indexOf(p)===0`。新域入口若误写成 `/chanlun`（不带斜杠），会静默吃掉
/chanlun-png、/chanlun-anno-report、/chanlun-learn-report/ 三条现存路由的
域归属，且**日志无错**。只断言「新页高亮对」挡不住这个错误，必须构造错版
数据证明「不带斜杠就确实会吃掉」，把硬要求锁成可证伪的测试。

数据取自真实 nav.js（不硬编码导航表），算法为 nav.js 的逐行复刻。
"""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal"
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))


def _nav_data():
    src = (PORTAL / "static" / "nav.js").read_text(encoding="utf-8")
    m = re.search(r"var NAV=(\{.*?\});\r?\n", src)
    assert m, "nav.js 里找不到 var NAV 数据行"
    return json.loads(m.group(1))


def _resolve(data, path):
    """复刻 nav.js DOMContentLoaded 里的当前页判定（无 break，后匹配者胜）。"""
    p_path = re.sub(r"\.html$", "", path)
    cur, sec = None, data["sections"][0][0]
    for s in data["sections"]:
        for _glabel, items in data["groups"].get(s[0], []):
            for label, href, key in items:
                p = href.replace("http://127.0.0.1", "")
                p = re.sub(r"\.html$", "", p)
                if (p == p_path or p + "/" == p_path + "/"
                        or (p != "/" and p_path.startswith(p)
                            and href.startswith("/"))):
                    cur, sec = key, s[0]
    return cur, sec


def _api_common_literals():
    """AST 只取字面量：api_common 顶层 import 会触发 api_common_assets 循环导入，
    按路径 exec_module 不可用（与 tools/export_nav.py 同一纪律：零副作用、零依赖）。"""
    tree = ast.parse((PORTAL / "api_common.py").read_text(encoding="utf-8"))
    names = ("SECTIONS", "DOMAIN_NAV_GROUPS", "PAGE_SECTION")
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    out[t.id] = ast.literal_eval(node.value)
    assert len(out) == len(names), sorted(out)
    return out


def test_seven_domains_and_chanlun_href_has_trailing_slash():
    data = _nav_data()
    keys = [s[0] for s in data["sections"]]
    assert len(keys) == 7, keys
    assert "chanlun" in keys
    href = dict((k, h) for k, _lb, h in data["sections"])["chanlun"]
    assert href == "/chanlun/", "新域入口必须带尾斜杠（§1.2 硬要求）"


def test_four_new_pages_resolve_to_chanlun_domain():
    data = _nav_data()
    cases = {
        "/chanlun/": "cl-today",
        "/chanlun/radar": "cl-radar",
        "/chanlun/action": "cl-action",
        "/chanlun/learn": "cl-learn",
    }
    for path, key in cases.items():
        cur, sec = _resolve(data, path)
        assert (cur, sec) == (key, "chanlun"), (path, cur, sec)
    # 同域内父路径在子路径之前：否则父会覆盖子的高亮（§1.2 第 2 条）
    group = data["groups"]["chanlun"]
    hrefs = [it[1] for _g, its in group for it in its]
    assert hrefs.index("/chanlun/") < min(
        i for i, h in enumerate(hrefs) if h != "/chanlun/")


def test_legacy_eight_chanlun_pages_stay_in_quant_domain():
    """施工单 §3.1 最关键的回归项：老 8 页顶栏高亮不跳到新域。"""
    lit = _api_common_literals()
    legacy = ("chanlun", "chanlun-buy", "tbe", "chanlun-pool",
              "linkage", "cases", "9dim", "cross")
    # 注意：PAGE_SECTION 不是全量表——tbe 等键本就不在表内，pill_nav 用
    # PAGE_SECTION.get(page, "quant") 默认回落到 quant。判据是「不得被改到新域」，
    # 不是「必须显式写 quant」（首次写测试时把基准错当成缺映射，实测纠正）。
    for key in legacy:
        assert lit["PAGE_SECTION"].get(key, "quant") == "quant", key
    data = _nav_data()
    # 对照组 = 把新域整体摘掉（等价于改动前的 NAV 数据），逐路径比对解析结果。
    # 这才是「对现有页回归影响面为 0」的直接判据；不要硬编码 cur 的期望值——
    # 实测 /bt-chanlun 既有高亮本就是 bt（被 /bt 前缀覆盖），不是 chanlun，
    # 首次写死期望值又一次把基准错当成回归。
    before = json.loads(json.dumps(data, ensure_ascii=False))
    before["sections"] = [s for s in before["sections"] if s[0] != "chanlun"]
    before["groups"].pop("chanlun", None)
    new_keys = ("cl-today", "cl-radar", "cl-action", "cl-learn")
    for path in ("/bt-chanlun", "/bt-buy", "/bt-tbe",
                 "/bt-chanlun-pool", "/bt-linkage", "/bt-weak",
                 "/bt-9dim", "/bt-cross"):
        cur, sec = _resolve(data, path)
        assert sec == "quant", (path, cur, sec)
        assert cur not in new_keys, (path, cur)
        assert _resolve(before, path) == (cur, sec), path


def test_existing_chanlun_dash_routes_not_swallowed():
    """三条带连字符的现存路由，域归属不得被新域吃掉。"""
    data = _nav_data()
    for path in ("/chanlun-png", "/chanlun-anno",
                 "/chanlun-anno-report", "/chanlun-learn-report/"):
        _cur, sec = _resolve(data, path)
        assert sec == "quant", (path, sec)


def test_falsifier_missing_trailing_slash_would_swallow_legacy_routes():
    """反证：把新域入口改成不带斜杠，现存三条路由立刻被吃掉。

    这条测试不是为了验「现在对」，而是为了证明「那条硬要求不是风格选择」——
    构造错版数据后断言确实发生误匹配，若未来有人把斜杠去掉而本测试仍绿，
    说明复刻算法本身已失真，需要重新对照 nav.js。
    """
    data = _nav_data()
    broken = json.loads(json.dumps(data, ensure_ascii=False))
    for s in broken["sections"]:
        if s[0] == "chanlun":
            s[2] = "/chanlun"
    for _g, items in broken["groups"]["chanlun"]:
        for it in items:
            if it[1] == "/chanlun/":
                it[1] = "/chanlun"
    for path in ("/chanlun-png", "/chanlun-anno",
                 "/chanlun-anno-report", "/chanlun-learn-report/"):
        assert _resolve(broken, path)[1] == "chanlun", path
    # 正版数据下，同一组路径全部仍归 quant
    for path in ("/chanlun-png", "/chanlun-anno",
                 "/chanlun-anno-report", "/chanlun-learn-report/"):
        assert _resolve(data, path)[1] == "quant", path


def test_pill_nav_new_pages_point_to_chanlun_domain():
    lit = _api_common_literals()
    for key in ("cl-today", "cl-radar", "cl-action", "cl-learn"):
        assert lit["PAGE_SECTION"].get(key) == "chanlun", key
    dom = dict((k, (lb, h)) for k, lb, h in lit["SECTIONS"])["chanlun"]
    assert dom[1] == "/chanlun/"
    # pill_nav 的 SECTION_PAGES 由 DOMAIN_NAV_GROUPS 推导，新域必须非空
    items = [it for _g, its in lit["DOMAIN_NAV_GROUPS"]["chanlun"] for it in its]
    assert [it[2] for it in items] == ["cl-today", "cl-radar",
                                       "cl-action", "cl-learn"]
