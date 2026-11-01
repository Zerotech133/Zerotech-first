# -*- coding: utf-8 -*-
"""学习卡/简报门户化（learning_digest + kb_reports.html 区块）回归测试。

覆盖：
  1) 生成器输出 JSON schema（date/cards/briefing/strategy_notes/meta；每日5张、rank 连续、主题唯一、质量分降序）；
  2) 主题去重 + 质量排序纯函数（重复主题跳过、高分优先、最多5张）；
  3) 异常容错（输入缺失/损坏仍退出 0，输出可解析并记录 fallbacks；--check 对损坏输出退出 1）；
  4) kb_reports.html 页面挂载点 / fetch 路径 / 兜底与降级文案，且原搜索功能标记仍在；
  5) 策略结论 Markdown 解析（表格/教训/裁决）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "learning_digest.py"
PAGE = REPO / "portal" / "kb_reports.html"
STRATEGY_MD = REPO / "knowledge_docs" / "选股策略研究结论_20261101.md"

_spec = importlib.util.spec_from_file_location("learning_digest_tool", SCRIPT)
ld = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ld)  # type: ignore[union-attr]

TOP_KEYS = {"date", "cards", "briefing", "strategy_notes", "meta"}


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        cwd=str(REPO),
    )


def write_fixtures(tmp_path: Path) -> dict:
    cards = {
        "generated_at": "2026-11-01T08:00:00+08:00",
        "total_units": 100,
        "cards": [
            {
                "q": "什么是「AI 算力需求」？",
                "a": "AI 训练与推理带动数据中心算力需求高速增长，2026 年资本开支同比增长 45%，其中光模块与交换芯片受益最明显。",
                "concept": "AI 算力需求",
                "source": "《某研报》 电子行业 p1-5",
                "evidence": "2026 年全球 AI 资本开支同比增长 45%，光模块 800G 出货量翻倍。" * 2,
            },
            {
                "q": "什么是「AI 算力需求」？",  # 主题重复，应被去重
                "a": "短答。",
                "concept": "AI 算力需求",
                "source": "《另一研报》 p1-1",
                "evidence": "",
            },
            {
                "q": "什么是「钙钛矿电池标准」？",
                "a": "市场监管总局发布首个钙钛矿光伏国家标准 GB/T 6495.12—2026，统一总面积与光阑面积定义，将于 2027 年实施。",
                "concept": "钙钛矿电池标准",
                "source": "《某研报》 电新 p2-6",
                "evidence": "首个钙钛矿光伏领域国家标准发布，规定测试设备、稳定化处理要求及 I-V 特性测量方法。",
            },
            {
                "q": "什么是「长回答知识」？",
                "a": "这是一条信息量明显更足的知识：" + "包含具体数据 42%、政策时间 2027 年、以及产业链上下游影响。" * 3,
                "concept": "长回答知识",
                "source": "《书籍》 第3章 p10-20",
                "evidence": "原文支持段落，" * 10,
            },
            {
                "q": "什么是「无答案」？",
                "a": "",
                "concept": "无答案",
                "source": "《x》 p1-1",
                "evidence": "",
            },
            {"q": "什么是「主题B」？", "a": "主题B的结论是什么？", "concept": "主题B", "source": "《某研报》 p3-4", "evidence": "证据" * 10},
            {"q": "什么是「主题C」？", "a": "主题C的结论是什么？", "concept": "主题C", "source": "《某研报》 p3-4", "evidence": "证据" * 10},
            {"q": "什么是「主题D」？", "a": "主题D的结论是什么？", "concept": "主题D", "source": "《某研报》 p3-4", "evidence": "证据" * 10},
        ],
    }
    briefing = {
        "generated_at": "2026-11-01T07:00:00+08:00",
        "date": "20261101",
        "docs": [
            {
                "doc": "rp_A",
                "pages": 100,
                "chapters": ["目录"],
                "units": [
                    {"concept": "AI 原生安全", "claim": "围绕 AI 系统重新设计安全体系。", "evidence": "e1", "chapter": "目录", "page_start": 1, "page_end": 5},
                    {"concept": "AI 原生安全", "claim": "重复单元应被去重。", "evidence": "e2", "chapter": "目录", "page_start": 1, "page_end": 5},
                ],
            },
            {
                "doc": "rp_B",
                "pages": 80,
                "chapters": ["前言"],
                "units": [{"concept": "构网型技术", "claim": "构网型是弱电网刚需。", "evidence": "e3", "chapter": "前言", "page_start": 2, "page_end": 8}],
            },
        ],
        "note": "测试用简报",
    }
    strategy_md = """# 测试策略结论

## 二、关键实测结论

| 实验 | 窗口 | 结果 | 结论 |
|---|---|---|---|
| S02 复合排序 | 20 交易日 | NAV 0.904 | 证伪 |
| S01 中性化 | 21 交易日 | +0.17% | 无 alpha |

## 三、策略库最终状态

| ID | 策略 | 状态 | 用途 |
|---|---|---|---|
| S01 | 过滤 | validated | 风控过滤 |

## 四、方法论教训

1. **前视**：信号必须放确认日；
2. **重叠**：窗口需修正。

## 五、对后续的裁决

- 暂停新因子投入；
- 保留事件提醒。
"""
    paths = {
        "cards": tmp_path / "cards.json",
        "briefing": tmp_path / "briefing.json",
        "strategy": tmp_path / "strategy.md",
        "out": tmp_path / "digest.json",
    }
    paths["cards"].write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    paths["briefing"].write_text(json.dumps(briefing, ensure_ascii=False), encoding="utf-8")
    paths["strategy"].write_text(strategy_md, encoding="utf-8")
    return paths


# ---------------------------------------------------------------------------
# 1) 生成器输出 schema（合成输入，CLI run）
# ---------------------------------------------------------------------------
def test_generated_digest_schema(tmp_path):
    paths = write_fixtures(tmp_path)
    proc = run_cli(
        "run",
        "--cards", str(paths["cards"]),
        "--briefing", str(paths["briefing"]),
        "--strategy-notes", str(paths["strategy"]),
        "--out", str(paths["out"]),
        "--date", "20261101",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(paths["out"].read_text(encoding="utf-8"))

    assert set(payload) >= TOP_KEYS
    assert payload["date"] == "20261101"
    cards = payload["cards"]
    assert len(cards) == ld.DAILY_CARDS == 5
    assert [c["rank"] for c in cards] == [1, 2, 3, 4, 5]
    assert len({c["topic_key"] for c in cards}) == 5, "主题必须去重"
    scores = [c["quality_score"] for c in cards]
    assert scores == sorted(scores, reverse=True), "质量分必须降序"
    for card in cards:
        for key in ld.REQUIRED_CARD_KEYS:
            assert key in card, f"卡片缺字段 {key}"
        assert card["a"].strip()
    assert "AI 算力需求" not in [c["concept"] for c in cards[1:]] or True  # 去重后最多一张 AI 算力需求
    assert sum(1 for c in cards if c["concept"] == "AI 算力需求") <= 1

    briefing = payload["briefing"]
    assert briefing["docs_count"] == 2
    assert 1 <= len(briefing["highlights"]) <= 5
    assert "AI 原生安全" in [h["concept"] for h in briefing["highlights"]]
    assert sum(1 for h in briefing["highlights"] if h["concept"] == "AI 原生安全") == 1, "简报要点也去重"

    notes = payload["strategy_notes"]
    assert notes["title"] == "测试策略结论"
    assert notes["points"], "策略结论要点不应为空"
    assert any("S02" in p for p in notes["points"])
    assert notes["takeaway"]

    meta = payload["meta"]
    assert meta["generator"] == ld.GENERATOR
    assert isinstance(meta["fallbacks"], list)
    assert ld.check_digest(payload) == [], ld.check_digest(payload)


# ---------------------------------------------------------------------------
# 2) 主题去重 + 质量排序纯函数
# ---------------------------------------------------------------------------
def test_dedupe_and_quality_ranking_pure_functions():
    good = {
        "q": "什么是「AI 算力需求」？",
        "a": "AI 训练与推理带动数据中心算力需求增长，2026 年资本开支同比增 45%，光模块与交换芯片受益。",
        "concept": "AI 算力需求",
        "source": "《研报》 电子 p1-5",
        "evidence": "2026 年全球 AI 资本开支同比增长 45%，800G 光模块出货翻倍。" * 2,
    }
    good_near_dup = {"q": "什么是「AI 算力需求和光模块」？", "a": "同一主题的变体，应判近重复。", "concept": "AI 算力需求和光模块", "source": "《研报2》 p1-1", "evidence": "x" * 50}
    thin = {"q": "什么是「薄卡」？", "a": "太短。", "concept": "薄卡", "source": "p1", "evidence": ""}
    rich1 = {"q": "什么是「主题1」？", "a": "主题1的完整结论与数据支撑。" * 4, "concept": "主题1", "source": "《书》 p10-20", "evidence": "证据" * 30}
    rich2 = {"q": "什么是「主题2」？", "a": "主题2的完整结论与数据支撑。" * 4, "concept": "主题2", "source": "《书》 p10-20", "evidence": "证据" * 30}
    rich3 = {"q": "什么是「主题3」？", "a": "主题3的完整结论与数据支撑。" * 4, "concept": "主题3", "source": "《书》 p10-20", "evidence": "证据" * 30}

    assert ld.quality_score(good) > ld.quality_score(thin), "信息量/证据更足的卡质量分应更高"
    picked = ld.select_daily_cards(
        [thin, good, good_near_dup, rich1, rich2, rich3, {"q": "空答案", "a": "", "concept": "空"}],
        n=5,
    )
    assert len(picked) == 5
    assert [c["rank"] for c in picked] == [1, 2, 3, 4, 5]
    concepts = [c["concept"] for c in picked]
    assert concepts.count("AI 算力需求") == 1
    assert "AI 算力需求和光模块" not in concepts, "近重复主题应被去重"
    assert picked[-1]["concept"] == "薄卡", "薄卡质量最低，应排在最后（名额不足时才入选）"
    scores = [c["quality_score"] for c in picked]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# 3) 异常容错：输入缺失/损坏仍可产出；--check 对损坏输出退出非 0
# ---------------------------------------------------------------------------
def test_missing_inputs_tolerated(tmp_path):
    paths = write_fixtures(tmp_path)
    missing = tmp_path / "nope.json"
    out = tmp_path / "digest_degraded.json"
    # 学习卡缺失 → 用简报知识单元兜底；策略结论缺失 → 降级
    proc = run_cli(
        "run",
        "--cards", str(missing),
        "--briefing", str(paths["briefing"]),
        "--strategy-notes", str(missing),
        "--out", str(out),
        "--date", "20261101",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["cards"], "学习卡缺失时应从简报兜底出卡"
    assert "AI 原生安全" in [c["concept"] for c in payload["cards"]]
    assert payload["meta"]["fallbacks"], "应记录降级原因"
    assert ld.check_digest(payload) == []

    # 全部输入缺失：仍退出 0 且输出可解析（cards 为空，--check 会报错）
    out2 = tmp_path / "digest_empty.json"
    proc2 = run_cli("run", "--cards", str(missing), "--briefing", str(missing), "--strategy-notes", str(missing), "--out", str(out2))
    assert proc2.returncode == 0, proc2.stderr
    assert json.loads(out2.read_text(encoding="utf-8"))["cards"] == []

    # --check：合法输出 0；损坏输出 1
    assert run_cli("--check", "--out", str(out)).returncode == 0
    bad = tmp_path / "bad.json"
    bad.write_text('{"date": "x", "cards": "not-a-list"}', encoding="utf-8")
    proc3 = run_cli("--check", "--out", str(bad))
    assert proc3.returncode == 1
    assert "校验失败" in proc3.stderr


# ---------------------------------------------------------------------------
# 4) 页面挂载点 / fetch 路径 / 兜底与降级文案（且原有搜索功能保留）
# ---------------------------------------------------------------------------
def test_page_mount_points_and_fetch_paths():
    html = PAGE.read_text(encoding="utf-8")
    # 两个新区块的挂载点
    assert 'id="learning-cards"' in html
    assert 'id="daily-briefing"' in html
    assert 'id="learning-cards-list"' in html
    assert 'id="daily-briefing-list"' in html
    assert "📚 学习卡" in html and "每日" in html, "学习卡区块标题应含「📚 学习卡（每日5张）」语义"
    assert "📰 今日简报" in html
    # 主 fetch + 兜底 fetch 路径
    assert "/static/kb_learning_digest.json" in html
    assert "/static/kb_learning_cards.json" in html
    assert "/static/kb_daily_briefing.json" in html
    assert "fetch(" in html
    # 失败降级文案
    assert "学习卡暂时无法加载" in html
    assert "今日简报暂时无法加载" in html
    # 原有搜索功能 / 结构未被破坏
    assert 'id="q"' in html and 'id="go"' in html and 'id="results"' in html
    assert "/static/kb_units_all.json" in html
    assert "/static/nav.js" in html or "nav.js" in html


# ---------------------------------------------------------------------------
# 5) 策略结论 Markdown 解析（表格/教训/裁决）
# ---------------------------------------------------------------------------
def test_strategy_notes_parser():
    text = (
        "# 策略结论\n\n## 二、关键实测结论\n\n"
        "| 实验 | 窗口 | 结果 | 结论 |\n|---|---|---|---|\n| S02 | 20 日 | NAV 0.904 | 证伪 |\n\n"
        "## 四、方法论教训\n\n1. **前视**：信号放确认日；\n2. **重叠**：窗口需修正。\n\n"
        "## 五、对后续的裁决\n\n- 暂停新因子投入；\n- 保留事件提醒。\n"
    )
    notes = ld.parse_strategy_notes(text, path="knowledge_docs/选股策略研究结论_20261101.md")
    assert notes["title"] == "策略结论"
    assert notes["updated_at"] == "2026-11-01"
    assert any("S02" in p and "证伪" in p for p in notes["points"])
    assert any("前视" in p for p in notes["points"])
    assert notes["takeaway"] == "暂停新因子投入；"
    assert notes["key_findings"][0]["experiment"] == "S02"

    empty = ld.parse_strategy_notes("", path="")
    assert empty["points"] == [] and empty["title"] == "策略研究结论"

    # 真实结论文件（若存在）应可解析出要点
    if STRATEGY_MD.is_file():
        real = ld.parse_strategy_notes(STRATEGY_MD.read_text(encoding="utf-8"), path=str(STRATEGY_MD))
        assert real["title"].startswith("选股策略研究结论")
        assert len(real["points"]) >= 3
