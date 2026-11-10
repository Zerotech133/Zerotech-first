# -*- coding: utf-8 -*-
"""Step 3 / 判例 #30 门禁：前端 KG 能力探测与降级链路（2026-11-09 计划 A）。

背景：`/api/jev/kg` 在 8091 换血前会返回 **HTTP 200 但内容是 `/api/jev/`
兜底**（`ok:true` + `schema_version=jev.v2`）。只看状态码会让 L2 拿到空图且
毫无报错。因此 `jev_intel.html` 的 `kgUsable()` 必须逐项校验 `ok / !schema_version
/ source∈{graph_cache,snapshot} / rels 非空 / cover / n_rel`，不满足就降级到
Step 1 的本地 `compileRelations()` 并在页脚标 hot。

本门禁两层：
  ① 静态锁「降级接线」——KG 请求被包在 try/catch、kg=local 不发请求、
     失败一定走到 compileRelations 且 kgMode 回落 local；
  ② **行为实跑**——`fixtures/jev_kg/kg_contract_probe.js` 用 Node vm 真跑
     `kgUsable / kgFromServer / compileRelations / kgBadge / kgApiUrl`，
     含 13 类伪造响应、同形状比对、6 位码兜底、徽标与深链。
     `node` 不在 PATH 时跳过行为层（静态层仍生效）。

纯离线：不联网、不碰 8091、不改任何文件。
"""
import io
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INTEL = os.path.join(ROOT, "portal", "jev_intel.html")
FIX = os.path.join(ROOT, "portal", "tests", "fixtures", "jev_kg")
PROBE = os.path.join(FIX, "kg_contract_probe.js")
GRAPH_FIXTURE = os.path.join(FIX, "graph_industry.json")


@pytest.fixture(scope="module")
def html():
    with io.open(INTEL, encoding="utf-8", newline="") as f:
        return f.read()


def test_graph_fixture_is_a_real_response():
    """行为层必须喂真响应，不能用空 dict 造绿。"""
    with io.open(GRAPH_FIXTURE, encoding="utf-8") as f:
        d = json.load(f)
    assert str(d.get("day")) == "20261106"
    assert (d.get("network") or {}).get("nodes")
    assert d.get("boards")


def test_kg_usable_checks_every_required_field(html):
    """判例 #30：静默成功比 404 危险，六项判据一项都不能少。"""
    m = re.search(r"function kgUsable\(o\)\s*\{([\s\S]*?)\n\}", html)
    assert m, "kgUsable() 不存在"
    body = m.group(1)
    for needle in ("o.ok === true", "!o.schema_version",
                   "o.source === 'graph_cache'", "o.source === 'snapshot'",
                   "Array.isArray(o.rels)", "o.rels.length > 0",
                   "Array.isArray(o.cover)", "o.n_rel > 0"):
        assert needle in body, "kgUsable 漏判据: " + needle


def test_kg_request_is_guarded_and_skippable(html):
    assert "if (KG_MODE !== 'local')" in html, "kg=local 应完全不发 KG 请求"
    assert "else kgNote = 'kg=local 强制';" in html
    assert "if (kgUsable(ko)) kgOut = ko;" in html, "接受响应前必须过能力探测"
    assert "jev.v2 兜底，非 KG" in html, "兜底要给出可读原因，不能静默"
    assert "catch (e2) { kgNote = '请求异常 '" in html, "网络异常必须被吞成降级"
    assert "await fetch(kgApiUrl(kgBoard)" in html


def test_fallback_always_reaches_local_compiler(html):
    for needle in ("S.kg = kgFromServer(kgOut);",
                   "S.kgMode = 'server'; S.kgSource = kgOut.source;",
                   "S.kg = compileRelations(d);",
                   "S.kgMode = 'local'; S.kgSource = null;",
                   "S.kgNote = kgNote; S.kgMembers = null;"):
        assert needle in html, "降级接线缺失: " + needle
    # server 分支必须在 local 分支之前，否则永远不会走服务端
    assert html.index("S.kg = kgFromServer(kgOut);") < html.index("S.kg = compileRelations(d);")


def test_badge_and_snapshot_deeplink(html):
    assert "KG_SOURCES = { graph_cache: 'graph 缓存', snapshot: '落盘快照' }" in html
    assert "if (KG_SRC === 'snapshot') u += '&src=snapshot';" in html
    assert "KG 本地编译 · 服务端待重启', 'hot'" in html
    assert "KG_SOURCES[S.kgSource]" in html
    assert "'ok']" in html, "server 徽标必须是 ok 样式"


def test_l1_still_reads_graph_only(html):
    """L1 总览不能被 KG 接管（判例 #30 / Step 3 约束）。"""
    assert "let u = '/api/jev/graph?day='" in html
    assert "S.overview = d" in html


def test_default_source_is_auto_not_snapshot(html):
    assert "(new URLSearchParams(location.search).get('src') || 'auto').toLowerCase()" in html
    assert "u += '&src=snapshot'" in html  # 只有显式深链才追加该参数
    assert "if (KG_SRC === 'snapshot')" in html, "不得无条件强制快照"


def test_probe_js_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH")
    p = subprocess.run([node, "--check", PROBE], capture_output=True,
                       encoding="utf-8", errors="replace", timeout=60)
    assert p.returncode == 0, p.stderr


def test_probe_behavior_all_cases_pass():
    """行为层：真实跑一遍能力探测 / 适配器 / 徽标，任何一项红都算回归。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不在 PATH（静态门禁仍生效）")
    p = subprocess.run([node, PROBE, INTEL, GRAPH_FIXTURE],
                       capture_output=True, encoding="utf-8", errors="replace",
                       timeout=120)
    assert p.returncode == 0, "探针崩溃: " + (p.stderr or "")[:400]
    line = [x for x in p.stdout.splitlines() if x.strip().startswith("{")]
    assert line, "探针没输出 JSON: " + p.stdout[:300]
    d = json.loads(line[-1])
    bad = [c for c in d["cases"] if not c["ok"]]
    assert not bad, "行为用例失败: " + " | ".join(
        "%s(%s)" % (c["name"], c["detail"][:120]) for c in bad)
    assert d["total_fail"] == 0
    assert len(d["cases"]) >= 30, "行为用例数异常: %d" % len(d["cases"])
