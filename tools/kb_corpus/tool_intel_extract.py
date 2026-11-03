# -*- coding: utf-8 -*-
"""第二期工具情报库结构化（task-158 / P1）：3 份 clean 文本 + 二期 units → 工具情报 JSON/MD/CSV。

规则（不调模型、不联网、纯正则+人工筛选）：
  - 工具词典（人工整理，含别名/类别/已知地址/一句话提示）在文本中匹配；
  - 描述：取该工具首次出现所在句子（清理营销话术后截断）；
  - 能力：词典关键词 + 上下文关键词（数据/回测/指标/绘图/Agent/研报/行情/因子）；
  - 地址：仅当原文出现域名/网址才记录；
  - 来源：3 份 clean 文件 + 行号；units 中提及次数；
  - 去重：同名/别名合并，记录来源文件数与置信；
  - 置信：高=明确介绍句（叫/是/开源/一款）+（多来源或地址）；中=明确提及；低=仅顺带提及；
  - 营销话术剔除：报告命中的营销词及被剔除句（描述不采用营销句）。

输出：research/reports/tool_intel_20261103.json/.md + deliverables/2026-11-03_第二期工具情报库.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CLEAN = Path(r"C:\项目\知识库\20_清洗\视频号学习_第二期\clean")
UNITS = Path(r"C:\项目\知识库\30_知识单元\视频号学习_第二期_units_draft.jsonl")
R = REPO / "research" / "reports"
DELIV = REPO / "deliverables"
OUT_JSON = R / "tool_intel_20261103.json"
OUT_MD = R / "tool_intel_20261103.md"
OUT_CSV = DELIV / "2026-11-03_第二期工具情报库.csv"
DATE = "20261103"
CATEGORIES = ("行情", "研报", "量化", "回测", "绘图", "Agent")

# 人工整理工具词典（name, category, aliases, address_hint, capability, description_hint）
LEXICON = [
    # ---- Agent / AI 工具 ----
    ("Codex", "Agent", ["OpenAI Codex", "Codex CLI"], "github.com/openai/codex", ["代码", "AI编程"],
     "OpenAI 的终端 AI 编程代理（Codex CLI），可读写代码库并执行命令。"),
    ("Claude Code", "Agent", ["Claude", "Cloud Code", "ClaudeCode"], "", ["AI编程", "终端Agent"],
     "Anthropic 的终端 AI 编程代理（Claude Code），面向代码任务与自动化。"),
    ("ChatGPT", "Agent", ["GPT", "ChatGPT"], "chatgpt.com", ["对话", "LLM"], "OpenAI 对话式 AI 助手。"),
    ("Gemini", "Agent", ["Google Gemini"], "gemini.google.com", ["对话", "LLM"], "Google 的多模态 AI 助手。"),
    ("DeepSeek", "Agent", ["深度求索"], "deepseek.com", ["对话", "LLM", "代码"], "DeepSeek 大模型对话/推理服务。"),
    ("Cursor", "Agent", ["Cursor IDE"], "cursor.com", ["AI编程", "IDE"], "AI 原生代码编辑器（Cursor）。"),
    ("Perplexity", "Agent", [], "perplexity.ai", ["AI搜索", "研究"], "AI 搜索引擎（Perplexity）。"),
    ("GrokBot", "Agent", [], "", ["Agent", "自动化"], "GrokBot 自动化 Agent 工具（原文为 hack 展示）。"),
    ("Composio", "Agent", [], "composio.dev", ["Agent", "工具集成"], "为 Agent 提供工具集成的开源平台。"),
    ("RDAgent", "Agent", ["微软 RDAgent"], "github.com/microsoft/RD-Agent", ["AI研究", "因子挖掘"],
     "微软开源的研究 Agent，可把研究想法转成可验证实验并迭代因子/模型代码。"),
    ("Gbrain", "Agent", ["Gbrain AI 第二脑"], "", ["知识管理", "AI"], "AI 第二脑/知识关联工具（原文介绍其分层用法）。"),
    ("MCP", "Agent", ["Model Context Protocol", "TradingView MCP"], "modelcontextprotocol.io", ["协议", "工具接入"],
     "Model Context Protocol：让 AI 通过标准协议调用外部工具/数据。"),
    ("刺猬投研", "研报", ["刺猬投研AI"], "", ["AI投研", "研究"], "AI 投研软件/Web 端（原文为工具推广演示）。"),
    ("韭菜盘GCP AI", "Agent", [], "", ["多Agent", "投研桌面"], "Go/React 开源桌面应用，多 Agent 投研团队协作分析。"),
    ("Obsidian", "Agent", ["Obsidian 笔记"], "obsidian.md", ["知识管理", "笔记"], "本地 Markdown 知识管理工具。"),
    ("Notion", "Agent", [], "notion.so", ["知识管理", "协作"], "协作式笔记/数据库工具。"),
    ("飞书", "Agent", ["Lark", "Feishu"], "feishu.cn", ["协作", "推送提醒"], "飞书协作平台（原文用于推送提醒示例）。"),
    # ---- 行情 / 数据 ----
    ("TradingView MCP", "Agent", ["tradingview-mcp", "TradingView MCP"], "github.com",
     ["MCP", "技术分析", "AI接入"], "开源的 TradingView MCP 工具，让 AI 直接调用 TradingView 做技术分析。"),
    ("TradingView", "行情", ["TV", "TradingView 图表"], "tradingview.com", ["行情图表", "指标", "Pine脚本"],
     "全球行情图表与指标社区平台（TradingView）。"),
    ("Pine Script", "绘图", ["PineScript", "Pine"], "tradingview.com/pine-script-docs", ["指标脚本", "策略"],
     "TradingView 的 Pine Script 指标/策略脚本语言。"),
    ("通达信", "行情", ["TDX"], "", ["行情", "指标公式", "选股"], "国内行情交易终端（通达信）。"),
    ("同花顺", "行情", ["iFinD"], "", ["行情", "研报", "选股"], "同花顺行情/数据终端。"),
    ("东方财富", "行情", ["Choice"], "", ["行情", "研报", "资金流"], "东方财富行情与数据服务。"),
    ("Wind", "行情", ["万得"], "wind.com.cn", ["金融数据", "研报"], "万得金融数据终端。"),
    ("Finviz", "行情", [], "finviz.com", ["行情", "筛选器"], "美股行情与筛选器（Finviz）。"),
    ("Polygon.io", "行情", ["Polygon"], "polygon.io", ["行情API"], "美股行情数据 API。"),
    ("Alpha Vantage", "行情", ["AlphaVantage", "alphavantage"], "alphavantage.co", ["行情API", "基本面"],
     "免费/付费行情与基本面数据 API。"),
    ("yfinance", "行情", ["Y-Finance", "Yahoo Finance"], "github.com/ranaroussi/yfinance", ["行情数据", "Python"],
     "Yahoo Finance 非官方 Python 数据接口。"),
    ("StockData", "行情", [], "github.com", ["A股数据", "多数据源"], "A 股多源数据工具包（原文称 10 层架构/43 端点）。"),
    ("EasyTDX", "行情", [], "github.com", ["通达信协议", "行情", "Python"], "通过通达信协议直连行情数据的 Python 工具。"),
    ("FreeStockDB", "量化", ["FreeStockDB 本地量化引擎"], "github.com", ["本地数据", "增量更新", "回测数据"],
     "A 股本地量化数据引擎（日K/分钟K/ETF，增量更新与校验）。"),
    ("Instock", "行情", ["InStock", "Me Hubstock", "Instock Me Hubstock"], "github.com", ["A股数据", "指标", "选股"],
     "A 股全能数据工具（原文称抓东方财富数据/技术指标/形态识别/选股/回测）。"),
    ("TikFlow Stock Panel", "量化", ["TikFlow"], "", ["自托管", "选股", "回测", "监控"],
     "自托管 A 股量化工作台（选股/监控/回测，LLM 辅助写策略）。"),
    ("雪球", "行情", [], "xueqiu.com", ["行情", "社区"], "股票行情与投资社区。"),
    # ---- 量化 / 回测 ----
    ("Qlib", "量化", ["QLib", "微软 Qlib"], "github.com/microsoft/qlib", ["量化研究", "因子", "回测"],
     "微软开源量化研究平台（数据→因子→模型→回测）。"),
    ("FinRL", "量化", ["FinRL-Trading", "FinRL-X", "AI4Finance"], "github.com/AI4Finance-Foundation/FinRL", ["强化学习", "交易"],
     "AI4Finance 开源强化学习量化交易框架。"),
    ("GSQuant", "量化", ["高盛 GSQuant"], "github.com/goldmansachs/gsquant", ["衍生品", "风险", "量化开发"],
     "高盛开源的量化金融开发框架（定价/风险/组合）。"),
    ("QuantWiki", "研报", ["quant-wiki", "QuantWiki 中文量化百科"], "quant-wiki.com", ["量化百科", "知识库"],
     "中文量化百科/知识库（quant-wiki.com）。"),
    ("QuantQuester", "量化", [], "", ["刷题", "量化面试"], "量化知识练习/刷题工具（原文与 QuantWiki 配套）。"),
    ("QSTrader", "回测", [], "github.com/mhallsmoore/qstrader", ["回测", "事件驱动"], "事件驱动回测框架（QSTrader）。"),
    ("TA4J", "回测", ["ta4j"], "github.com/ta4j/ta4j", ["技术指标", "Java"], "Java 技术指标/策略回测库。"),
    ("Riskfolio-Lib", "量化", ["Riskfolio"], "github.com/dcajasn/Riskfolio-Lib", ["组合优化", "风险"],
     "Python 组合优化与风险管理库。"),
    ("TensorTrade", "回测", [], "github.com/tensortrade-org/tensortrade", ["强化学习", "回测"],
     "Python 强化学习交易框架（模拟交易所/奖励函数）。"),
    ("OpenBB", "研报", ["OpenBB Terminal"], "openbb.co", ["数据终端", "研究", "基本面"],
     "开源投资研究终端（多数据源统一接口）。"),
    ("QuantConnect", "回测", ["LEAN"], "quantconnect.com", ["回测", "实盘", "多资产"], "在线量化回测/实盘平台（LEAN 引擎）。"),
    ("Vela", "绘图", ["LuxAlgo Vela"], "github.com/luxalgo", ["金融图表", "可视化", "可嵌入"],
     "LuxAlgo 开源的金融图表库 Vela（K线/画线/指标/多图联动）。"),
    # ---- 研报 / 信息 ----
    ("Finviz", "行情", [], "finviz.com", ["筛选", "行情"], "美股筛选与行情工具。"),
    ("慧博", "研报", ["慧博投研"], "hibor.com.cn", ["研报", "数据"], "研报数据平台（慧博）。"),
    ("萝卜投研", "研报", [], "robo.datayes.com", ["研报", "数据"], "研报/数据平台（萝卜投研）。"),
    ("韭研公社", "研报", ["韭菜公社"], "", ["研报", "社区"], "投研内容社区。"),
]

MARKETING = ["卧槽", "兄弟们", "家人们", "暴涨", "翻倍", "稳赚", "躺赚", "神器", "割韭菜", "免费领取", "限时",
             "扫码", "私信", "关注", "点赞", "评论区", "福利", "0 元", "0元", "必赚", "上车", "干货", "暴富",
             "AI 神器", "AI神器", "吃灰", "狠角色", "杀疯了", "炸裂", "王炸", "财富自由"]
BAD_DOMAINS = {"aapl.co", "size.ai", "amara.org", "close.app", "ret.co", "data2.co", "wiki.com",
               "return.me", "sum.app", "cumsum.app", "returns.me", "gmail.com", "apl.app"}
GOOD_DOMAINS = {"tradingview.com", "alphavantage.co", "polygon.io", "finviz.com", "quant-wiki.com",
                "xueqiu.com", "wind.com.cn", "hibor.com.cn", "openbb.co", "quantconnect.com",
                "obsidian.md", "notion.so", "feishu.cn", "chatgpt.com", "gemini.google.com",
                "deepseek.com", "cursor.com", "perplexity.ai", "composio.dev", "modelcontextprotocol.io",
                "github.com", "robo.datayes.com", "tradingview.com/pine-script-docs"}
CAP_MAP = {"回测": "回测", "数据": "数据", "指标": "指标", "绘图": "绘图", "可视化": "可视化", "图表": "图表",
           "Agent": "Agent", "agent": "Agent", "LLM": "LLM", "因子": "因子", "研报": "研报", "行情": "行情",
           "强化学习": "强化学习", "知识管理": "知识管理"}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_units(path: Path) -> list[str]:
    claims = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            u = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(u, dict) and u.get("claim"):
            claims.append(str(u["claim"]))
    return claims


def clean_text(s: str) -> str:
    s = s.strip().replace("`", "").replace("文件：", "").replace("文件:", "")
    s = re.sub(r"^[#>\-\*\s]+", "", s)
    s = re.sub(r"^20\d{6}[_\s]+\d{0,6}[_\s]*", "", s)
    # 去掉营销词/夸张前缀
    for m in MARKETING:
        s = s.replace(m, "")
    s = re.sub(r"^[，,。．.、；;：:\s]+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s


def _bad_sentence(sentence: str) -> bool:
    st = sentence.strip()
    return (st.startswith("#") or "__" in st or "mp4" in st.lower()
            or bool(re.match(r"^20\d{6}[_\s]", st)) or len(st) < 8)


def find_sentence(text_lines: list[str], idx: int, alias: str) -> str:
    """取包含别名的句子：优先定义句/正文行（排除标题、日期、代码碎行），清洗后截断 160 字。"""
    candidates = []
    for offset in (0, 1, -1, 2, -2):
        j = idx + offset
        if not (0 <= j < len(text_lines)):
            continue
        line = text_lines[j]
        if line.strip().startswith("#"):
            continue
        for sentence in re.split(r"(?<=[。！？!?；;])", line):
            if alias.lower() not in sentence.lower() or _bad_sentence(sentence):
                continue
            cleaned = clean_text(sentence)
            if not cleaned:
                continue
            score = 0
            if re.search(r"(叫|叫做|是一个|是一款|是[一]?个|开源|平台|工具|框架|库|终端|软件)", cleaned):
                score += 2
            if len(cleaned) >= 20:
                score += 1
            if offset == 0:
                score += 1
            candidates.append((score, cleaned))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1][:160]


def contains_hype(s: str) -> bool:
    return any(m in s for m in MARKETING)


def build() -> dict:
    files = sorted(CLEAN.glob("*.md"))
    texts = {f.name: f.read_text(encoding="utf-8", errors="replace").splitlines() for f in files}
    units = load_units(UNITS)
    unit_text = "\n".join(units)
    hype_hits = Counter()
    for fname, lines in texts.items():
        for line in lines:
            for m in MARKETING:
                if m in line:
                    hype_hits[m] += 1
    tools: dict[str, dict] = {}
    for name, category, aliases, addr_hint, capability, hint in LEXICON:
        names = [name] + aliases
        occurrences = []
        for fname, lines in texts.items():
            for i, line in enumerate(lines, 1):
                for alias in names:
                    if alias and re.search(rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])", line):
                        if name == "TradingView" and "TradingView MCP" in line:
                            continue
                        occurrences.append({"file": fname, "line": i})
                        break
        if not occurrences:
            continue
        # 描述：定义句优先，其次首次出现句
        description = ""
        for alias in names:
            for fname in texts:
                for i, line in enumerate(texts[fname], 1):
                    if alias and alias.lower() in line.lower() and re.search(
                            r"(叫|叫做|是一个|是一款|是[一]?个|开源|平台|工具|框架|库|终端|软件)", line):
                        description = find_sentence(texts[fname], i - 1, alias)
                        if description:
                            break
                if description:
                    break
            if description:
                break
        if not description:
            for alias in names:
                for fname in texts:
                    for i, line in enumerate(texts[fname], 1):
                        if alias and alias.lower() in line.lower():
                            description = find_sentence(texts[fname], i - 1, alias)
                            break
                    if description:
                        break
                if description:
                    break
        if not description:
            description = hint
        # 地址：原文域名优先，其次已知地址
        address = ""
        for fname in texts:
            for i, line in enumerate(texts[fname], 1):
                for alias in names:
                    if alias and alias.lower() in line.lower():
                        dom = re.findall(r"(?<![\w/])(?:[a-z0-9][a-z0-9\-]{1,30}\.(?:com|cn|io|ai|app|net|org|co|me|dev))(?:/[^\s，。；)）]*)?",
                                         line, re.I)
                        if dom:
                            d0 = dom[0].lower().split("/")[0]
                            name_key = re.sub(r"[^a-z0-9]", "", name.lower())
                            if (d0 in BAD_DOMAINS) or not (d0 in GOOD_DOMAINS or name_key[:6] in d0):
                                continue
                            address = dom[0].lower()
                            break
                if address:
                    break
            if address:
                break
        if not address and addr_hint:
            address = addr_hint
        # 能力：词典 + 描述/上下文关键词
        caps = list(capability)
        context_text = description + " " + " ".join(hint for hint in [hint])
        for kw, cap in CAP_MAP.items():
            if kw in context_text and cap not in caps:
                caps.append(cap)
        units_mentions = sum(1 for c in units if any(a and a in c for a in names))
        explicit_def = bool(re.search(r"(叫|叫做|是一个|是一款|开源|平台|工具|框架|库|终端|软件)", description))
        sources = len({o["file"] for o in occurrences})
        confidence = "高" if (explicit_def and (sources >= 2 or address)) else ("中" if occurrences else "低")
        tools[name] = {
            "name": name, "category": category, "aliases": aliases,
            "description": description, "capabilities": caps, "address": address,
            "source_files": sorted({o["file"] for o in occurrences}),
            "source_lines": occurrences[:20],
            "sources_count": sources, "mentions": len(occurrences),
            "units_mentions": units_mentions, "confidence": confidence,
            "marketing_removed": contains_hype(description),
        }
    # 去重：若别名被其他工具名覆盖（如 GPT 归入 ChatGPT），保留 canonical 项
    items = sorted(tools.values(), key=lambda x: (-x["mentions"], x["name"]))
    payload = {
        "schema_version": "tool_intel.v1", "date": DATE,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generator": "tools/kb_corpus/tool_intel_extract.py（纯规则+人工筛选，不调模型/不联网）",
        "task": "P1-第二期降级为工具情报库（3 份 clean + 二期 units）",
        "sources": {
            "clean_dir": str(CLEAN),
            "files": [{"name": f.name, "sha256": sha(f), "lines": len(texts[f.name])} for f in files],
            "units": {"path": str(UNITS), "sha256": sha(UNITS), "records": len(units)},
        },
        "policy": {
            "categories": list(CATEGORIES),
            "method": "工具词典匹配（名称/别名）+ 定义句抽取 + 域名/行号来源；纯规则+人工筛选，不调模型、不联网",
            "dedupe": "同名/别名合并为一个工具条目，记录来源数与提及数",
            "confidence": {"高": "明确介绍句（叫/是/开源/平台等）+（多来源或地址）",
                           "中": "明确提及但介绍信息有限", "低": "仅顺带提及"},
            "marketing_removed": MARKETING,
        },
        "summary": {
            "tools_total": len(items),
            "by_category": dict(Counter(t["category"] for t in items)),
            "by_confidence": dict(Counter(t["confidence"] for t in items)),
            "files": len(files),
            "units": len(units),
            "marketing_hypes": dict(hype_hits.most_common(20)),
            "marketing_sentences": sum(hype_hits.values()),
        },
        "tools": items,
        "marketing_policy": {
            "note": "二期 3 份资料以 AI 工具推广为主（46 有效候选终审 0 通过）；本库仅提取工具事实信息，"
                    "描述句已剔除营销话术（见 marketing_removed 词表与命中统计），不采信收益承诺/夸张宣传。",
            "stripped_terms": MARKETING,
            "hits": dict(hype_hits.most_common(20)),
        },
        "credibility": {
            "level": "中（规则抽取+人工筛选）",
            "notes": [
                "工具条目来自文本显式提及（名称/别名+定义句/域名/行号），非联网抓取，不保证工具当前可用性；",
                "描述为原文句子清理营销话术后截断，未做事实核验；地址仅当原文出现域名/已知主页时才填；",
                "同名/别名已合并；source_lines 记录前 20 处提及；units_mentions 为二期 units 中提及次数；",
                "置信为『证据强度』而非工具质量评级；低置信条目建议人工复核后再用。",
            ],
        },
        "disclaimer": "工具情报为原文结构化整理，不构成工具推荐或投资建议；使用前请自行核验。",
    }
    return payload


def render_md(p: dict) -> str:
    s = p["summary"]
    lines = [
        "# 第二期工具情报库（2026-11-03）", "",
        f"> 来源：3 份 clean 文本（{s['files']} 文件）+ 二期 units（{s['units']} 条）· "
        f"**工具 {s['tools_total']} 个** · 纯规则+人工筛选（不调模型/不联网）",
        f"> 类别分布：{json.dumps(s['by_category'], ensure_ascii=False)} · "
        f"置信：{json.dumps(s['by_confidence'], ensure_ascii=False)}",
        "> **营销话术已剔除**（词表/命中见 §四）；工具情报不构成推荐。", "",
        "## 一、工具清单", "",
        "| # | 工具 | 类别 | 一句话描述 | 关键能力 | 地址 | 来源数/提及 | 置信 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, t in enumerate(p["tools"], 1):
        lines.append(f"| {i} | **{t['name']}** | {t['category']} | {t['description'][:80]} | "
                     f"{'、'.join(t['capabilities'][:4])} | {t['address'] or '—'} | "
                     f"{t['sources_count']}/{t['mentions']} | {t['confidence']} |")
    lines += ["", "## 二、来源文件", ""]
    for f in p["sources"]["files"]:
        lines.append(f"- `{f['name']}`（{f['lines']} 行，sha256 `{f['sha256'][:12]}…`）")
    lines += [f"- 二期 units：`{p['sources']['units']['path']}`（{p['sources']['units']['records']} 条）",
              "", "## 三、方法与置信口径", ""]
    for k, v in p["policy"]["confidence"].items():
        lines.append(f"- **{k}**：{v}")
    lines += ["- " + p["policy"]["method"],
              "", "## 四、营销话术剔除说明", "",
              f"> {p['marketing_policy']['note']}", ""]
    for term, n in p["marketing_policy"]["hits"].items():
        lines.append(f"- `{term}`：命中 {n} 次（描述中已剔除/不采信）")
    lines += ["", "## 五、可信度说明", "", f"- 总体：**{p['credibility']['level']}**"]
    for note in p["credibility"]["notes"]:
        lines.append(f"- {note}")
    lines += ["", "## 六、消费提示", "",
              "- 量化/回测/行情类工具可用于军团（司马微/张松/FACTOR1）调研；Agent 类工具仅供参考，不替代审核流程；",
              "- 地址未填者=原文未给域名/地址；低置信条目建议人工复核后再登记；",
              "- 本库为静态 JSON/CSV，后续可扩为页面/API（契约先行）。",
              "", f"> {p['disclaimer']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass
    parser = argparse.ArgumentParser(description="第二期工具情报库结构化（不调模型/不联网）")
    parser.add_argument("command", nargs="?", default="build", choices=["build"])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    p = build()
    if args.write:
        OUT_JSON.write_text(json.dumps(p, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        OUT_MD.write_text(render_md(p), encoding="utf-8")
        DELIV.mkdir(parents=True, exist_ok=True)
        with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["name", "category", "description", "capabilities", "address",
                        "source_files", "source_lines", "sources_count", "mentions", "units_mentions", "confidence"])
            for t in p["tools"]:
                w.writerow([t["name"], t["category"], t["description"], "|".join(t["capabilities"]),
                            t["address"], "|".join(t["source_files"]),
                            "|".join(f"{o['file']}:{o['line']}" for o in t["source_lines"]),
                            t["sources_count"], t["mentions"], t["units_mentions"], t["confidence"]])
        p["_outputs"] = {"json": str(OUT_JSON), "md": str(OUT_MD), "csv": str(OUT_CSV)}
    s = p["summary"]
    if args.json:
        print(json.dumps({k: v for k, v in p.items() if k != "_outputs"}, ensure_ascii=False, indent=2))
    else:
        print(f"[tool_intel] 工具 {s['tools_total']} 个 | 类别 {json.dumps(s['by_category'], ensure_ascii=False)} | "
              f"置信 {json.dumps(s['by_confidence'], ensure_ascii=False)}")
        print(f"  来源：{s['files']} 文件 / {s['units']} units | 营销命中 {s['marketing_sentences']} 处")
        if p.get("_outputs"):
            for k, v in p["_outputs"].items():
                print(f"  输出({k})：{v}")
        else:
            print("  （未写盘；加 --write）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
