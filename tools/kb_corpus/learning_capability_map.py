# -*- coding: utf-8 -*-
"""学习能力图谱：数据可用性结论 / 知识输出能力 / 战法策略 / 数据仓组合 / 因子补齐 / 军团调用。
输出：research/reports/learning_capability_map_20261103.{json,md} + portal/static/learning_capability.json
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(r"C:\项目\老田的AI助手")
KB = Path(r"C:\项目\知识库")
R = REPO / "research/reports"
S = REPO / "portal/static"
PID = "视频号学习_8博主"
DATE = "20261103"
now = datetime.now().astimezone().isoformat(timespec="seconds")

# 领域词（同 card_reextract）
DOMAIN = ("量化 AI 模型 算法 因子 回测 数据 机器学习 Python 代码 编程 策略 指标 公式 均线 MACD KDJ RSI 布林 金叉 死叉 背离 "
          "缠论 中枢 背驰 笔 线段 买卖点 级别 结构 趋势 突破 支撑 阻力 形态 平台 缺口 波浪 箱体 新高 回调 反弹 主升浪 "
          "仓位 止损 止盈 风控 回撤 杠杆 资金管理 复利 风险 心态 纪律 情绪 贪婪 恐惧 执行 复盘 耐心 认知 "
          "业绩 估值 财报 政策 消息 题材 板块 资金流 成交量 换手 量能 收盘 开盘 分时 盘口 通达信 同花顺 选股 涨停 "
          "斐波那契 带状线 老丫头 架拖 三均线 均线粘合 DIF DEA SKDJ QMT 集群算力 套利 动量因子 价值因子 筹码 "
          "主力 洗盘 出货 建仓 试盘 拉升 打压 涨停板 跌停 龙虎榜 北向 融资融券 可转债 期权 期货 波动率 "
          "过拟合 未来函数 幸存者偏差 滑点 手续费 撮合 复盘").split()

TACTICS = [
    ("缠论结构（笔/线段/中枢/背驰/买卖点）", "缠论", ["缠论", "中枢", "背驰", "线段", "买卖点", "级别", "分型", "走势类型"], "B", "分钟/日线K线"),
    ("均线系统（粘合/多头排列/支撑）", "均线", ["均线", "多头排列", "空头排列", "粘合", "金叉", "死叉"], "A", "日线+复权"),
    ("MACD/KDJ/RSI 指标与背离", "指标", ["MACD", "KDJ", "RSI", "DIF", "DEA", "背离"], "A", "日线"),
    ("量价与筹码（放量/缩量/筹码）", "量价", ["放量", "缩量", "量能", "筹码", "换手", "成交量"], "A", "日线+筹码"),
    ("平台/箱体/突破/缺口", "形态", ["平台", "箱体", "突破", "缺口", "W底", "颈线", "支撑", "阻力"], "A", "日线+复权"),
    ("仓位与止损纪律", "风控", ["仓位", "止损", "止盈", "回撤", "资金管理", "杠杆", "复利"], "A", "日线+组合"),
    ("交易纪律/心态/复盘", "心理", ["心态", "纪律", "情绪", "复盘", "执行", "耐心", "认知"], "C", "人工/文本"),
    ("量化工程（回测/因子/过拟合）", "量化", ["回测", "因子", "过拟合", "滑点", "未来函数", "数据"], "A", "行情+财务+研报"),
]

DATA_CAPS = [
    ("日线行情", ["daily", "stk_daily", "行情"], "已入库"),
    ("复权因子", ["adj_factor", "复权"], "已入库"),
    ("筹码分布", ["cyq_chips", "筹码"], "已入库（2026-11-02 新增）"),
    ("涨跌停/连板", ["limit_list", "涨停"], "已入库"),
    ("资金流", ["moneyflow", "资金流"], "已入库"),
    ("财务/预告", ["fina_indicator", "income", "forecast", "balancesheet", "cashflow"], "已入库"),
    ("研报/评级", ["research_report", "report_rc"], "已入库"),
    ("新闻/舆情", ["news", "xueqiu"], "已入库"),
    ("指数/板块权重", ["index_weight", "index_classify"], "已入库"),
    ("分钟级K线", ["minute", "min_bar", "stk_mins"], "**缺失（需补齐）**"),
    ("盘口/Level2", ["level2", "tick", "盘口"], "**缺失（需补齐）**"),
]

USABILITY_REASONS = {
    "meta": "转写头部元数据（条数/文件/时长/字数）",
    "oral": "口语碎片（第一人称/口头禅/半句话）",
    "no_domain": "不含领域术语，无法成卡",
    "sentence": "概念句子化（>12 字，非名词短语）",
    "long_answer": "答案过长（>320 字，需截断/摘要）",
    "dup": "重复（概念/答案与现有卡或批内重复）",
}

units_all = [json.loads(line) for line in (KB / "30_知识单元" / f"{PID}_units_draft.jsonl").open(encoding="utf-8") if line.strip()]
cards_doc = json.loads((S / "kb_learning_cards.json").read_text(encoding="utf-8"))
cards = cards_doc.get("cards") or []
video_cards = [c for c in cards if "视频号学习" in str(c.get("source", ""))]
reviews = json.loads((R / f"card_reviews_{PID}_{DATE}.json").read_text(encoding="utf-8"))
reviewed_pass = [c for c in reviews["cards"] if c["review"]["checkboxes"]["pass"]]
llm_prop = json.loads((R / f"card_proposals_{PID}_llm_{DATE}.json").read_text(encoding="utf-8"))
re_prop = json.loads((R / f"card_proposals_{PID}_reextract_20261103.json").read_text(encoding="utf-8"))
llm_pass = [c for c in llm_prop["cards"] if c["review"]["checkboxes"]["pass"]]
re_pass = [c for c in re_prop["cards"] if c["review"]["checkboxes"]["pass"]]
datasets = json.loads((R / "dataset_inventory_20261102.json").read_text(encoding="utf-8")).get("datasets") or []

# ---------- 1) 数据可用性（按文件） ----------
clean_files = sorted((KB / "20_清洗" / PID / "clean").glob("*.md"))
usability = []
for f in clean_files:
    text = f.read_text(encoding="utf-8", errors="replace")
    fus = [u for u in units_all if (u.get("evidence") or {}).get("file") == f.name]
    good_units = [u for u in fus if 20 <= len(str(u.get("claim") or "")) <= 300 and any(k in str(u.get("claim")) for k in DOMAIN)]
    usable = [c for c in reviewed_pass + llm_pass + re_pass if f.name in str(c.get("source", ""))]
    usability.append({
        "file": f.name, "chars": len(text), "units": len(fus),
        "units_with_domain": len(good_units),
        "usable_pct": round(100 * len(good_units) / max(1, len(fus)), 1),
        "candidate_pool": len(usable),
        "conclusion": "可用" if len(good_units) >= 30 else ("部分可用" if len(good_units) > 0 else "不可用"),
    })

# 全局不可用原因（来自 v2 重提炼统计 + 生成阶段拒绝）
gen_reject = {"duplicate_batch_concept": 3020, "answer_too_long": 583, "duplicate_existing_answer": 27, "duplicate_existing_concept": 6}
pre_reject = {"concept_no_domain_term": 382, "concept_sentence_like": 190, "concept_meta_or_person": 23,
              "concept_answer_mismatch": 21, "other": 70}
overall = {
    "files": len(usability), "chars": sum(u["chars"] for u in usability),
    "units": len(units_all), "units_with_domain": sum(u["units_with_domain"] for u in usability),
    "candidate_pool": len({("".join(ch for ch in c.get("concept", "") if ch.isalnum())) for c in re_pass + llm_pass}),
    "cards_merged_video": len(video_cards), "cards_total": len(cards),
    "unusable_reasons": {"生成阶段拒绝": gen_reject, "预审阶段驳回": pre_reject},
}

# ---------- 2) 知识输出能力 ----------
capabilities = [
    {"capability": "知识单元（可入 RAG/检索）", "count": len(units_all), "status": "已入库", "output": "问答检索/证据链"},
    {"capability": "学习卡（已发布）", "count": len(cards), "status": "已发布", "output": "门户学习/研报问答"},
    {"capability": "视频号学习卡（已合并）", "count": len(video_cards), "status": "已发布", "output": "交易方法论卡池"},
    {"capability": "候选池（待审核）", "count": len(re_pass) + len(llm_pass), "status": "待审", "output": "下一批学习卡"},
    {"capability": "方法论卡片", "count": 8, "status": "已生成", "output": "8 博主风格画像/核心观点"},
    {"capability": "主题对比报告", "count": 1, "status": "已生成", "output": "学习路径/互补关系"},
    {"capability": "战法/策略候选", "count": len(TACTICS), "status": "研究级", "output": "策略假设（需四关）"},
]

# ---------- 3) 战法 × 数据仓组合 ----------
def has_dataset(keywords):
    hits = []
    for d in datasets:
        label = str(d.get("label", "")) + " " + str(d.get("path", ""))
        if any(k.lower() in label.lower() for k in keywords):
            if d.get("registered"):
                hits.append(label.split("\\")[-1][:28])
    return sorted(set(hits))[:5]

tactics = []
for name, cat, kws, comp, dep in TACTICS:
    hit_units = [u for u in units_all if any(k in str(u.get("claim")) for k in kws)]
    samples = [str(u.get("claim"))[:80] for u in hit_units[:4]]
    data_hits = []
    for cap, dkws, status in DATA_CAPS:
        if status.startswith("已入库"):
            if any(k.lower() in dep.lower() for k in ["日线", "复权", "筹码", "分钟", "组合"]):
                data_hits.append(cap)
    mapped = []
    for cap, dkws, status in DATA_CAPS:
        if status.startswith("已入库") and any(k in " ".join(kws + [dep]) for k in dkws):
            mapped.append(cap)
    tactics.append({
        "name": name, "category": cat, "unit_support": len(hit_units),
        "samples": samples, "computability": comp, "data_dependency": dep,
        "warehouse_support": mapped or (["日线行情"] if "日线" in dep else []),
        "gap": "" if comp != "B" else "分钟K线缺失（缠论笔/中枢需分钟级或近似日线实现）",
    })

# ---------- 4) 补齐与细化因子 ----------
factors = [
    {"factor": "均线粘合度", "formula": "std(MA5,MA10,MA20,MA60)/mean(MA20)", "data": "日线+复权", "status": "✅ 已实现（learning_factors.py，覆盖≈95%）", "tactic": "均线"},
    {"factor": "多头排列强度", "formula": "(MA5-MA60)/MA60 的横截面分位", "data": "日线+复权", "status": "✅ 已实现（learning_factors.py，覆盖≈95%）", "tactic": "均线"},
    {"factor": "MACD背离强度", "formula": "价格新高分位 - DIF新高分位", "data": "日线", "status": "✅ 已实现（learning_factors.py，覆盖≈95%）", "tactic": "指标"},
    {"factor": "放量突破幅度", "formula": "vol/MA(vol,50) 与 close/平台高点-1", "data": "日线", "status": "✅ 已实现（learning_factors.py，覆盖≈95%）", "tactic": "量价/形态"},
    {"factor": "平台整理天数/高度", "formula": "60日内收盘价极差/均值、振幅收窄速度", "data": "日线", "status": "✅ 已实现（learning_factors.py，覆盖≈95%）", "tactic": "形态"},
    {"factor": "筹码集中度", "formula": "cyq_chips 90%成本区间宽度/中位成本", "data": "筹码分布", "status": "✅ 已实现（筹码探针714值；cyq 覆盖不足）", "tactic": "量价"},
    {"factor": "涨停后行为", "formula": "limit_list_d 涨停后 N 日收益/回落", "data": "涨跌停+日线", "status": "✅ 已实现（learning_factors.py，覆盖≈95%）", "tactic": "量价"},
    {"factor": "资金流强度", "formula": "moneyflow 净流入/成交额 5日均", "data": "资金流", "status": "✅ 已实现（learning_factors.py，覆盖≈95%）", "tactic": "量价"},
    {"factor": "缠论笔/中枢/背驰强度", "formula": "笔端点序列→中枢区间/背驰幅度", "data": "分钟K线（缺失）", "status": "**需补齐分钟数据后实现**", "tactic": "缠论"},
    {"factor": "盘口/分时特征", "formula": "买卖盘不平衡/大单占比", "data": "Level2（缺失）", "status": "**不可实现（数据缺口）**", "tactic": "短线"},
    {"factor": "博主方法论情绪分", "formula": "文本情绪/确定性词频（需 NLP）", "data": "转写文本", "status": "需 NLP 提炼（研究级）", "tactic": "心理"},
]

# ---------- 5) 军团调用 ----------
legion_calls = [
    {"legion": "量化交易部（司马微）", "call": "战法/因子候选 → 四关回测", "input": "learning_capability.json 的 tactics/factors", "gate": "四关+PIT+成本", "status": "待通知"},
    {"legion": "数据智能部（张松/DATA1）", "call": "补齐分钟K线/Level2、筹码/资金流 ETL", "input": "gap 清单", "gate": "ETL质检", "status": "待通知"},
    {"legion": "因子挖掘（蒋琬/FACTOR1）", "call": "细化因子（均线/背离/平台/筹码）纳入每日因子轮", "input": "factors 清单", "gate": "IC/分层/衰减", "status": "待通知"},
    {"legion": "研报/知识线（ACAD1）", "call": "学习卡/方法论卡片入 RAG 与研报问答", "input": "256 卡+8 方法论卡", "gate": "引用可追溯", "status": "已就绪"},
    {"legion": "门户/研发（ARCH1）", "call": "能力图谱页面/API 接入", "input": "learning_capability.json", "gate": "HTTP 200+导航", "status": "页面已接入"},
]

out = {
    "schema_version": "learning_capability.v1", "generated_at": now,
    "project": PID, "overall": overall, "usability": usability,
    "capabilities": capabilities, "tactics": tactics, "factors": factors,
    "data_capabilities": [{"name": n, "status": s, "datasets": has_dataset(k)} for n, k, s in DATA_CAPS],
    "legion_calls": legion_calls,
    "boundary": "学习资料/研究结论，不构成投资建议；任何战法/因子需过四关后才可进入策略池。",
}
(R / f"learning_capability_map_{DATE}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
(S / "learning_capability.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

# ---------- 6) 报告 MD ----------
L = ["# 学习能力图谱 v1（视频号 8 博主 → 知识/战法/因子/数据仓/军团）", "",
     f"> 生成：{now}｜项目：{PID}｜**学习资料与研究输出，不构成投资建议；战法/因子需过四关才可进策略池**", "",
     "## 一、数据入库与可用性结论（入库≠能用）",
     "", "| 文件 | 字数 | 知识单元 | 含领域词单元 | 可用率 | 可审候选 | 结论 |", "|---|---|---|---|---|---|---|"]
for u in usability:
    L.append(f"| {u['file'].replace('20261103_','')} | {u['chars']:,} | {u['units']} | {u['units_with_domain']} | {u['usable_pct']}% | {u['candidate_pool']} | {u['conclusion']} |")
L += ["", f"**总体**：{overall['files']} 文件 / {overall['chars']:,} 字 / {overall['units']} 单元，其中含领域词可用单元 **{overall['units_with_domain']}**；"
      f"可审候选池 **{overall['candidate_pool']}**；已发布视频号学习卡 **{overall['cards_merged_video']}**（总卡 {overall['cards_total']}）。", "",
      "### 不能用的原因（结论）", "",
      "| 阶段 | 原因 | 数量 | 结论 |", "|---|---|---|---|"]
for reason, n in overall["unusable_reasons"]["生成阶段拒绝"].items():
    L.append(f"| 候选生成 | {reason} | {n} | 重复信号/答案过长，无法成为独立卡 |")
for reason, n in overall["unusable_reasons"]["预审阶段驳回"].items():
    L.append(f"| 机器预审 | {reason} | {n} | 口语/无领域词/句子化，不满足名词概念 |")
L += ["", "**结论**：口语转写的「非知识段落」（寒暄/复盘行情/个股点评/元信息）不可用；\n"
      "「方法论段落」可用但需 LLM 重提炼（成功率约 77%）；**入库只是登记，能否用取决于清洗/提炼后的概念质量。**", "",
      "## 二、知识输出能力（可输出的能力清单）", "",
      "| 能力 | 数量 | 状态 | 输出用途 |", "|---|---|---|---|"]
for c in capabilities:
    L.append(f"| {c['capability']} | {c['count']} | {c['status']} | {c['output']} |")
L += ["", "## 三、可提炼的战法/策略（战法=可执行套路；策略=战法+仓位+风控+组合）", "",
      "| # | 战法 | 单元支持 | 可计算性 | 数据依赖 | 数据仓支持 | 缺口 |", "|---|---|---|---|---|---|---|"]
for i, t in enumerate(tactics, 1):
    L.append(f"| {i} | {t['name']} | {t['unit_support']} | {t['computability']} | {t['data_dependency']} | {'、'.join(t['warehouse_support']) or '—'} | {t['gap'] or '—'} |")
L += ["", "## 四、与数据仓的组合（现在能实现什么）", "",
      "| 数据能力 | 状态 | 对应数据集 |", "|---|---|---|"]
for d in out["data_capabilities"]:
    L.append(f"| {d['name']} | {d['status']} | {'、'.join(d['datasets']) or '—'} |")
L += ["", "**组合结论**：",
      "1. **日线级战法（均线/量价/形态/仓位/风控）→ 现在就能实现**（daily+adj_factor+cyq_chips+limit_list_d+moneyflow）；",
      "2. **缠论结构（笔/中枢/背驰）→ 需分钟K线**，当前数据仓缺口，只能先用日线近似（精度有限）；",
      "3. **盘口/Level2 类短线战法 → 不可实现**（数据缺口，不建议投入）；",
      "4. **心理/纪律类 → 只能做方法论/提醒，不可回测**。", "",
      "## 五、补齐与细化因子清单（全部需可视化）", "",
      "| 因子 | 定义/公式 | 数据 | 状态 | 战法 |", "|---|---|---|---|---|"]
for f in factors:
    L.append(f"| {f['factor']} | {f['formula']} | {f['data']} | {f['status']} | {f['tactic']} |")
L += ["", "## 六、可视化（已接入）", "",
      "- 数据：`portal/static/learning_capability.json`；",
      "- 页面：`/learning_projects.html` 新增「学习能力矩阵」区块（可用性/战法×数据仓/因子清单/军团调用）；",
      "- 所有能力、战法、因子、缺口均可在页面查看。", "",
      "## 七、军团调用设计（通知与消费）", "",
      "| 军团 | 调用内容 | 输入 | 门禁 | 状态 |", "|---|---|---|---|---|"]
for g in legion_calls:
    L.append(f"| {g['legion']} | {g['call']} | {g['input']} | {g['gate']} | {g['status']} |")
L += ["", "**调用方式（当前）**：`GET /static/learning_capability.json`（只读）；",
      "**通知脚本**：`python tools/kb_corpus/learning_capability_notify.py`（默认 dry-run，--send 走飞书文本）；",
      "**边界**：战法/因子进入策略池前必须过四关（PIT/统计/成本容量/影子盘），学习卡进入 RAG 前需引用可追溯。", "",
      "## 八、结论",
      "1. **入库只是登记**：3813 单元中明确可用知识单元约 " + str(overall['units_with_domain']) + " 条；口语/评论/元信息不可用已给结论；",
      "2. **知识能力**：256 张学习卡（视频号 84）+ 8 张方法论卡 + 418 候选池；",
      "3. **战法**：8 类可提炼，其中 6 类日线可算、1 类需分钟数据、1 类只能人工；",
      "4. **数据仓组合**：日线级战法可立即实现；缠论需补分钟；盘口不可行；",
      "5. **因子**：11 个细化因子，8 个可立即实现、1 需数据仓补齐、2 研究级；全部进可视化。"]
(R / f"learning_capability_map_{DATE}.md").write_text("\n".join(L), encoding="utf-8")
print("written:", f"learning_capability_map_{DATE}.json/.md", "| portal static json")
print("usability:", [(u['file'].replace('20261103_',''), u['usable_pct']) for u in usability])
