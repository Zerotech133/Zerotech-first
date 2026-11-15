# -*- coding: utf-8 -*-
"""I1b §3.2 nginx 新域路由：离线 location 选择 + CRLF/结构校验。

⚠️ 这不是 `nginx -t` 的替代品
─────────────────────────────
本机无 nginx 二进制（PATH 无命中）、Docker daemon 未运行，`nginx -t` 与
reload 均不可执行。本测试只复刻 nginx 的「= 精确优先 → ^~ 最长前缀」选择规则，
用于锁住「新增块不会吃掉既有路由」这一相对关系；**配置能否被 nginx 真正解析、
以及 reload 后的 200 验收，仍待生产环境实测**，见交付报告「验收缺口」。

施工单 §3.2 理由复核（写进测试以便审计）
────────────────────────────────────────
施工单断言「写成 ^~ /chanlun 会吃掉 /chanlun-png、/chanlun-anno-report、
/chanlun-learn-report/ 三条现存路由」。按 nginx 最长前缀规则，这三条**各自都有
更长的专门块**（^~ /chanlun-png、= /chanlun-anno-report、^~ /chanlun-learn-report/），
因此错版下仍会命中原块——该理由不成立。带斜杠依然照做，真实收益是：
① 收窄前缀面，任何未来以 /chanlun 开头的未知路径都不会被误代理到 8091；
② 与 nav.js 的 `path.indexOf('/chanlun/')===0` 前缀判定保持同形（§1.2）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONF = ROOT / "portal" / "nginx.conf"


def _locations():
    raw = CONF.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n"), "出现裸 LF（CRLF 混行会撕配置）"
    text = raw.decode("utf-8")
    eq = re.findall(r"^\s*location\s*=\s*(\S+)\s*\{", text, re.M)
    pref = re.findall(r"^\s*location\s*\^~\s*(\S+)\s*\{", text, re.M)
    return text, eq, pref


def _choose(eq, pref, path):
    """复刻 nginx：= 精确优先；否则 ^~ 最长前缀胜（命中即不再查正则）。"""
    if path in eq:
        return "= " + path
    cand = [p for p in pref if path.startswith(p)]
    return ("^~ " + max(cand, key=len)) if cand else None


def test_conf_is_pure_crlf_and_new_block_is_well_formed():
    raw = CONF.read_bytes()
    assert raw.count(b"\r\n") >= 1389 + 13, "疑似删规则"
    text, _eq, pref = _locations()
    assert pref.count("/chanlun/") == 1
    assert "/chanlun" not in pref, "禁止出现不带斜杠的 ^~ /chanlun"
    assert "location = /chanlun {" in text
    # 花括号平衡（与 test_round7_closeout 同款最小结构校验，不替代 nginx -t）
    assert text.count("{") == text.count("}")
    # 施工单片段漏了 set $h；没有它 $h 未定义，nginx -t 必报 unknown variable
    block = text.split("location ^~ /chanlun/ {", 1)[1].split("}", 1)[0]
    assert "set $h host.docker.internal;" in block
    assert "proxy_pass http://$h:8091;" in block
    # 不引入 X-Portal-* 头：nginx proxy_set_header 非合并语义，声明即丢 server 级头
    assert "proxy_set_header" not in block


def test_new_domain_paths_go_to_new_block():
    _text, eq, pref = _locations()
    assert _choose(eq, pref, "/chanlun") == "= /chanlun"
    for path in ("/chanlun/", "/chanlun/radar", "/chanlun/action",
                 "/chanlun/learn"):
        assert _choose(eq, pref, path) == "^~ /chanlun/", path


def test_three_existing_dash_routes_keep_their_own_blocks():
    """§3.2 验收项 2/3 的离线部分：旧路由不得被新域吃掉。"""
    _text, eq, pref = _locations()
    cases = {
        "/chanlun-png": "^~ /chanlun-png",
        "/chanlun-anno": "^~ /chanlun-anno",
        "/chanlun-anno-report": "= /chanlun-anno-report",
        "/chanlun-learn-report/": "^~ /chanlun-learn-report/",
    }
    for path, want in cases.items():
        assert _choose(eq, pref, path) == want, path


def test_reason_recheck_no_slash_would_not_eat_those_three():
    """复核施工单 §3.2 的理由：错版（无斜杠）下三条旧路由仍归原块。

    结论：施工单写的「静默 502」按 nginx 最长前缀规则不成立；但带斜杠仍照做，
    理由见模块 docstring。此测试把事实固定下来，避免以后再把错误理由写进文档。
    """
    _text, eq, pref = _locations()
    broken = ["/chanlun" if p == "/chanlun/" else p for p in pref]
    for path, want in (("/chanlun-png", "^~ /chanlun-png"),
                       ("/chanlun-anno-report", "= /chanlun-anno-report"),
                       ("/chanlun-learn-report/", "^~ /chanlun-learn-report/")):
        assert _choose(eq, broken, path) == want, path
    # 但错版确实会误收「以 /chanlun 开头且无专门块」的未知路径 —— 这才是真风险
    assert _choose(eq, broken, "/chanlun-any-future-page") == "^~ /chanlun"
    assert _choose(eq, pref, "/chanlun-any-future-page") is None
