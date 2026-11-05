# -*- coding: utf-8 -*-
"""R5-D · P0-D「需维护窗口」三处口径修正 · 静态契约测试（不依赖浏览器 / 服务 / 网络）。

唯一 spec：`reports/impl_round5_contract_20261106.md` §5（含 §1 表格第 1 条对 `233` 的关键纠正）。
背景裁定：`reports/p0d_caliber_closure_20261106.md` §1（口径）/ §3（四级分级表）。

本轮 4 处改动：
1. `api_dataops.py:380` `_catalog_summary` docstring：`233 表` → `258 表`（纯注释；函数体动态聚合
   `catalog.json` 的 `tables`，无硬编码）。
2. `api_dataops.py:851` 拓扑节点 `pool:warehouse` 展示名：`行情仓 233 表` → `行情仓（catalog 池）`。
   ★ 233 是「行情仓」**子集**表数，`catalog 258 = 行情仓 + 雪球库 + 量化库 + 其它` 分池；
   拓扑里各池是并列节点，把这里写成 258 就是造假 → 故**去数字**，让 `dataops.html`
   的「catalog 258 表」口径卡片作唯一权威数字。这是本轮**唯一**的行为变化。
3. `api_dw_registry.py:97` `_catalog_alignment` docstring：`304 表` → `306 表`
   （`warehouse_registry.json.physical_count=306`，物理资产视图，纯注释）。
4. `briefing.html:116` `f-blockers` placeholder：`例：314 数据集口径未核` → `例：数据集口径未核`
   （314 是 11-02 盘点快照值，去数字最干净，不会再次过时）。

风格对齐 `portal/tests/test_round2_p1c.py`：pytest 直接收集的纯 assert 函数，不 import pytest；
断言一律做**函数头切片**（不用整文件 grep），避免别处的巧合数字造成误判 / 误伤。
"""
import subprocess
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]


def _wip_present(rel):
    """该 worktree 里 rel 是否存在未提交改动（被守卫的本地 WIP 是否存在）。

    守卫针对的是「他人未提交改动」——只存在于主工作区；分支/linked worktree 按定义
    clean，无物可守。此函数让同一份测试在主工作区照常断言、在分支 gate 中自动跳过，
    避免分支快检因主区 WIP 不可见而假红。
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", rel],
            cwd=str(PORTAL), capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return True
    if out.returncode != 0:
        return True
    return bool(out.stdout.strip())
DATAOPS = (PORTAL / "api_dataops.py").read_text(encoding="utf-8")
DWREG = (PORTAL / "api_dw_registry.py").read_text(encoding="utf-8")
BRIEF = (PORTAL / "briefing.html").read_text(encoding="utf-8")

DATAOPS_B = DATAOPS.encode("utf-8")
DWREG_B = DWREG.encode("utf-8")
BRIEF_B = BRIEF.encode("utf-8")

# 换行风格必须看**磁盘原始字节**：`read_text()` 默认做通用换行归一（CRLF → LF），
# 用它断言 CRLF 会恒失败，故单独 read_bytes。
RAW = {name: (PORTAL / name).read_bytes()
       for name in ("api_dataops.py", "api_dw_registry.py", "briefing.html")}


def func_block(src, header):
    """从 `header` 起到下一个顶层 `def ` 之前切片（含 docstring + 函数体）。

    只切本函数，避免整文件 `in` 把别处的数字误判成本函数的（本轮两处 anchor 都在函数头，
    另一处是模块级 NODES 常量，故两类都用精确定位而非全文搜索）。
    """
    assert src.count(header) == 1, "函数头锚点应唯一: %s" % header
    i = src.index(header)
    j = src.find("\ndef ", i + len(header))
    assert j > i, "找不到 %s 的下一个顶层 def" % header
    return src[i:j]


def node_line(src, node_id):
    """取 NODES 里指定 id 的那一整行（唯一命中）。"""
    hits = [ln for ln in src.splitlines() if '"id": "%s"' % node_id in ln]
    assert len(hits) == 1, "%s 节点行应唯一，实际 %d 行" % (node_id, len(hits))
    return hits[0]


# ── 1. api_dataops.py · _catalog_summary docstring 233 → 258 ─────────────────

def test_catalog_summary_docstring_258_not_233():
    """docstring 写 258 表，且不再出现 233 表；函数体逻辑原封不动。"""
    blk = func_block(DATAOPS, "def _catalog_summary(errors):")
    head = blk.split("try:", 1)[0]                     # 仅 docstring 段
    assert "258 表" in head, "_catalog_summary docstring 应写 258 表"
    assert "233 表" not in head, "_catalog_summary docstring 仍含过时的 233 表"
    assert "× source × layout，登记真源" in head, "docstring 其余措辞被改"


def test_catalog_summary_body_still_dynamic():
    """守卫：防止为了改注释误删/篡改函数体（仍是动态读 catalog.json 聚合 tables）。"""
    blk = func_block(DATAOPS, "def _catalog_summary(errors):")
    # 「catalog.json」也出现在 hint 文案里，故断言**读取语句**本身，防逻辑被删而文案仍在
    assert 'os.path.join(DATA, "catalog.json")' in blk, "函数体丢了 catalog.json 读取语句"
    assert 'cat.get("tables", {})' in blk, "函数体丢了 tables 读取"
    assert "table_count" in blk and "by_source" in blk, "函数体丢了聚合返回字段"
    assert "len(tables)" in blk, "table_count 应仍由 len(tables) 动态派生"
    # 断言函数体里**没有**任何写死的表数（233/258 都不许出现在 body）
    body = blk.split('"""', 2)[2]
    assert "233" not in body and "258" not in body, "函数体不应出现硬编码表数"


# ── 2. api_dataops.py · 拓扑节点 pool:warehouse 去数字 ───────────────────────

def test_warehouse_node_name_no_hardcoded_number():
    """展示名等于「行情仓（catalog 池）」，且该行不含 258（防有人把子集口径改成全表数）。"""
    ln = node_line(DATAOPS, "pool:warehouse")
    assert '"name": "行情仓（catalog 池）"' in ln, "节点展示名不符: %r" % ln
    assert "258" not in ln, "行情仓节点不得写 258（233 是子集口径，改 258 即造假）"
    assert "233" not in ln, "行情仓节点仍含硬编码 233"
    assert "表" not in ln.split('"name"')[1].split(",")[0], "展示名不应再带「表」数字口径"


def test_warehouse_node_other_keys_intact():
    """只改 name 值：id / layer / x / y 与缩进、逗号保持原样。"""
    ln = node_line(DATAOPS, "pool:warehouse")
    assert ln.startswith('    {"id": "pool:warehouse", "layer": "数据池", "name": '), ln
    assert ln.endswith(',  "x": 400, "y": 300},'), "x/y/双空格/逗号被改动: %r" % ln


def test_no_fake_warehouse_258_table_label():
    """守卫：全文件不得出现「行情仓 258 表」这种造假写法。"""
    assert "行情仓 258 表" not in DATAOPS
    assert DATAOPS.count("行情仓（catalog 池）") == 1


def test_peer_pool_nodes_untouched():
    """并列分池节点仍带各自子集表数（说明 233 属行情仓子集，不可合并成 258）。"""
    assert '"name": "雪球库 3 表"' in node_line(DATAOPS, "pool:xueqiu")
    assert '"name": "量化库 6 表"' in node_line(DATAOPS, "pool:quant")


# ── 3. api_dw_registry.py · _catalog_alignment docstring 304 → 306 ───────────

def test_catalog_alignment_docstring_306_not_304():
    blk = func_block(DWREG, "def _catalog_alignment() -> dict:")
    head = blk.split("out = {}", 1)[0]
    assert "306 表" in head, "_catalog_alignment docstring 应写 306 表"
    assert "304 表" not in head, "_catalog_alignment docstring 仍含过时的 304 表"
    assert "/api/dw/stats" in head and "/api/dataops" in head, "docstring 语义被改"


def test_catalog_alignment_body_reads_registry():
    """守卫：函数体仍从 catalog / warehouse_registry 现算，未被注释改动波及。"""
    blk = func_block(DWREG, "def _catalog_alignment() -> dict:")
    assert 'out["catalog_tables"] = len(cat.get("tables") or {})' in blk
    assert 'out["registry_physical"] = reg.get("physical_count")' in blk


# ── 4. briefing.html · f-blockers placeholder 去数字 ─────────────────────────

def test_blockers_placeholder_without_number():
    hits = [ln for ln in BRIEF.splitlines() if 'id="f-blockers"' in ln]
    assert len(hits) == 1, "f-blockers 应唯一"
    ln = hits[0]
    assert 'placeholder="例：数据集口径未核"' in ln, "placeholder 未按 spec 去数字: %r" % ln
    assert "314" not in ln, "placeholder 仍含 11-02 快照值 314"
    assert ln.startswith('      <textarea id="f-blockers"') and ln.endswith("</textarea>"), \
        "该行其它属性/缩进被破坏: %r" % ln


def test_briefing_other_placeholders_untouched():
    """邻近 placeholder 未被波及（同一批举例文案保持原样）。"""
    assert 'placeholder="例：数据资源台账首版完成"' in BRIEF
    assert 'placeholder="例：数据资源权限分级由谁拍板"' in BRIEF


# ── 5. 冻结文件守卫（证明没碰他人未提交改动 / 原型页）─────────────────────────

def test_dw_snapshot_frozen_v39():
    if not _wip_present("static/dw_snapshot.json"):
        return  # 分支/linked worktree 无此本地 WIP，跳过（主区照常断言）
    raw = (PORTAL / "static" / "dw_snapshot.json").read_bytes()
    assert b'"version": 39' in raw, "dw_snapshot.json 的 v39 不见了（他人 M 状态被破坏）"
    assert raw.count(b'"version"') == 1


def test_dw_v2_prototype_untouched():
    raw = (PORTAL / "static" / "dw_v2.html").read_bytes()
    assert b"DW_DATA" in raw, "dw_v2.html 原型页内嵌 DW_DATA 被破坏"
    assert len(raw) > 200000, "dw_v2.html 体积异常（单行内嵌数据疑似被删）"


# ── 6. 换行风格 / 数字原始字节复核 ──────────────────────────────────────────

def test_crlf_style_preserved():
    """三文件均保持原 CRLF 风格，无孤立 LF（字节替换未引入换行漂移）。"""
    for name, raw in RAW.items():
        assert raw.count(b"\r\n") > 0, name
        assert raw.count(b"\n") == raw.count(b"\r\n"), name + " 出现孤立 LF"


def test_numbers_verified_by_raw_bytes():
    """数字结论用原始字节 + ord() 复核（不凭肉眼看打印）。"""
    d, g, b = RAW["api_dataops.py"], RAW["api_dw_registry.py"], RAW["briefing.html"]
    i = d.index("258 表".encode("utf-8"))
    assert list(d[i:i + 3]) == [50, 53, 56], "258 码点不符"
    j = g.index("306 表".encode("utf-8"))
    assert list(g[j:j + 3]) == [51, 48, 54], "306 码点不符"
    assert b.count(b"314") == 0
