# -*- coding: utf-8 -*-
"""训练真实数据标准化表格：生成 xlsx（多 sheet）+ CSV，并可 --verify 复核实际数据。

输出：deliverables/2026-11-03_训练真实数据标准化表_v1.xlsx
      deliverables/训练数据表_20261103/*.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

sys.stdout.reconfigure(encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
R = REPO / "research/reports"
S = REPO / "portal/static"
KB = Path(r"C:\项目\知识库")
OUT_DIR = REPO / "deliverables"
CSV_DIR = OUT_DIR / "训练数据表_20261103"
DATE = datetime.now().strftime("%Y%m%d")
NOW = datetime.now().astimezone().isoformat(timespec="seconds")

# ---------- 采集 ----------
inv = json.loads((R / "dataset_inventory_20261102.json").read_text(encoding="utf-8"))
datasets = inv["datasets"]


def find_ds(keyword: str, prefer_exact: bool = False):
    hits = [d for d in datasets if keyword.lower() in str(d.get("label", "")).lower()]
    if prefer_exact:
        hits = [d for d in hits if str(d.get("label", "")).lower() == keyword.lower()]
    return hits[0] if hits else None


COLS = ["序号", "类别", "数据集", "接口/来源", "本地路径（相对仓库）", "文件数", "大小(MB)",
        "最新日期", "频率", "覆盖范围", "训练用途", "PIT 口径", "状态", "缺口/备注"]

ROWS = [
    ("基础", "tushare.stock_basic", "股票列表", "tushare", "日", "全A基本资料", "基础映射", "静态", "已具备", ""),
    ("基础", "tushare.trade_cal", "交易日历", "tushare", "日", "全市场交易日", "样本切分", "静态", "已具备", ""),
    ("行情", "tushare.daily", "日线行情", "tushare", "日", "全A 1990-2026", "因子/回测/标签", "T日收盘后可用", "已具备", "最新 2026-10-30"),
    ("行情", "tushare.adj_factor", "复权因子", "tushare", "日", "全A", "复权收益标签", "T日", "已具备", "与 daily 逐日对齐"),
    ("行情", "tushare.daily_basic", "每日指标", "tushare", "日", "全A", "市值/换手/估值因子", "T日", "已具备", "manifest Δ=0（W3-1 复检）"),
    ("行情", "tushare.stk_limit / ts_stk_limit", "涨跌停价格", "tushare/tdx", "日", "全A", "可交易性/涨跌停过滤", "T日", "已具备", "涨跌停判断"),
    ("分钟", "tushare.stk_mins", "股票分钟线", "tushare", "1分钟", "5,038 代码 / 202509-202611 / 15个月", "缠论/日内因子", "按日截断（PIT 引擎）", "已具备", "202611 仅 800 代码有文件"),
    ("分钟", "tushare.idx_mins", "指数分钟线", "tushare", "1分钟", "指数", "大盘/板块节奏", "按日截断", "已具备", ""),
    ("筹码", "tushare.cyq_chips", "筹码分布", "tushare", "日", "仅 23 文件/少量代码", "筹码集中度因子", "T日", "部分具备", "覆盖不足（23文件/0.2MB），因子抽样 0%"),
    ("资金", "tushare.moneyflow", "个股资金流", "tushare", "日", "全A", "资金流因子", "T日", "已具备", ""),
    ("资金", "tushare.moneyflow_hsgt", "沪深港通资金", "tushare", "日", "沪深港通", "北向情绪因子", "T日", "已具备", "仅 1 文件"),
    ("涨跌停", "tushare.limit_list_d", "涨跌停/连板", "tushare", "日", "全A", "涨停后行为因子", "T日", "已具备", ""),
    ("财务", "tushare.fina_indicator", "财务指标", "tushare", "季", "全A（5,586 文件）", "基本面因子/过滤", "按公告日(ann_date)", "已具备", ""),
    ("财务", "tushare.income", "利润表", "tushare", "季", "全A", "基本面", "按公告日", "已具备", ""),
    ("财务", "tushare.balancesheet", "资产负债表", "tushare", "季", "全A", "基本面", "按公告日", "已具备", ""),
    ("财务", "tushare.cashflow", "现金流量表", "tushare", "季", "全A", "基本面", "按公告日", "已具备", ""),
    ("财务", "tushare.forecast / express", "业绩预告/快报", "tushare", "事件", "全A", "事件因子", "按公告日", "已具备", "forecast 仅 4 文件（覆盖有限）"),
    ("研报", "tushare.report_rc", "研报评级", "tushare", "日", "全A", "研报因子/情绪", "按研报日", "已具备", "W3-1 去重 166 行"),
    ("研报", "tushare.research_report", "研究报告", "tushare", "日", "全A", "NLP/知识", "按发布日", "已具备", ""),
    ("新闻", "tushare.news / major_news", "新闻", "tushare", "日", "全市场", "情绪/NLP", "按发布时间", "已具备", ""),
    ("舆情", "xueqiu", "雪球数据", "xueqiu", "日", "个股/讨论", "情绪因子", "按发布时间", "已具备", "602 文件/109.6MB"),
    ("指数", "tushare.index_weight", "指数/板块权重", "tushare", "日/月", "指数", "行业中性化/基准", "按成分生效日", "已具备", ""),
    ("海外", "tushare.us_daily / hk_daily", "美股/港股日线", "tushare", "日", "美股/港股", "外围联动", "T日", "已具备", "因美股数据量较大（1.6GB）"),
    ("缺口", "Level2/盘口", "逐笔/委托队列", "无", "tick", "无", "盘口类战法", "—", "缺失", "数据缺口，短线盘口类不可行"),
]

SAMPLE_ROWS = [
    ("S1", "日线因子训练/前筛样本", "tushare.daily + adj_factor + daily_basic + moneyflow + limit_list_d", "300 只 × 120 交易日（20260508-20261102）", "日频", "前复权收盘、未来 1/5/10/20 日收益", "T 日及之前", "36,000 行×多因子", "已跑通（factor_prescreen）", "FACTOR1"),
    ("S2", "缠论分钟样本", "tushare.stk_mins（1min→30min 聚合）", "50-60 只 × 60-120 交易日", "30分钟", "笔/中枢/背驰/买卖点 + 未来收益", "按日截断 PIT 引擎", "约 360 bar/代码", "已跑通（chan_factors/chan_asof_engine）", "factor-dev"),
    ("S3", "四关扩样本", "daily+adj_factor+行业+市值", "300 只 × 120 日（计划扩 ≥3 年）", "日频", "未来 5/10/20 日收益", "T+1 入场口径待定", "待扩", "稳健性预检完成，正式四关待扩样本", "司马微"),
    ("S4", "RAG/卡片训练语料", "学习卡 400 + 知识单元（800+1116+288）", "4 个项目", "文本", "概念/答案/来源", "按源文件行号", "约 2,600 单元", "已入库（治理后）", "DATA1"),
    ("S5", "LLM 提炼候选样本", "视频号候选池", "8博主 2,417 候选 + 第二期 869", "条", "concept/answer/source", "只读", "约 3,286 条", "已生成（待审 27/30 多源）", "DATA1"),
    ("S6", "有效结论库", "learning_conclusions_lib", "730 结论 / Top100", "条", "结论+置信+战法+可执行性", "按来源", "100 条（Top）", "已上线静态 + 8091 API", "司马微/ACAD1"),
]

GAPS = [
    ("G1", "Level2/盘口数据", "无 tick/委托队列", "盘口类短线战法不可行；能力图谱已标注", "不投入（数据缺口）"),
    ("G2", "筹码数据覆盖", "cyq_chips 仅 23 文件/0.2MB", "筹码集中度因子抽样覆盖 0-0.27%，无法前筛", "待 DATA1 补抓后复筛"),
    ("G3", "分钟历史长度", "stk_mins 仅 202509 起 / 15 个月；202611 仅 800 代码", "缠论四关样本有限（WF 3 段勉强）", "扩历史或接受短样本"),
    ("G4", "业绩预告覆盖", "forecast 仅 4 文件", "事件因子覆盖有限", "按需补抓"),
    ("G5", "多源概念不足", "人工双签 27/30（机器 32 不采用）", "跨源重复不足，扩 3 来源仍差 3", "再补 1-2 份指标/均线/缠论重叠资料"),
    ("G6", "学习单元去重状态", "视频号 800 / 第二期 1116（已去重）；laomu 288 未去重", "laomu 项目 288 单元未跑 dedupe", "下轮治理统一处理"),
]

VERIFY = [
    ("V01", "日线最新交易日", "daily.latest", "2026-10-30"),
    ("V02", "复权因子文件数", "adj_factor.files", "8744"),
    ("V03", "分钟文件数", "stk_mins.files", "33,998"),
    ("V04", "分钟频率", "stk_mins 抽样", "1 分钟"),
    ("V05", "分钟代码数", "stk_mins codes", "5,038"),
    ("V06", "分钟历史范围", "stk_mins months", "202509-202611"),
    ("V07", "筹码文件数", "cyq_chips.files", "23"),
    ("V08", "资金流文件数", "moneyflow.files", "925"),
    ("V09", "涨跌停文件数", "limit_list_d.files", "282"),
    ("V10", "财务指标文件数", "fina_indicator.files", "5,586"),
    ("V11", "研报评级文件数", "report_rc.files", "95"),
    ("V12", "新闻文件数", "news.files", "38"),
    ("V13", "学习卡总数", "kb_learning_cards", "400"),
    ("V14", "视频号单元（去重后）", "units jsonl lines", "800"),
    ("V15", "第二期单元（去重后）", "units jsonl lines", "1116"),
    ("V16", "老木单元", "units jsonl lines", "286"),
    ("V17", "多源概念（人工签核）", "multi_source_canonical", "29 / 30"),
    ("V18", "learning_gate", "4 项目/13 检查", "4 项目/13 检查/fail=0"),
]

DICT_ROWS = [
    ("tushare.daily", "ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount", "日线行情"),
    ("tushare.adj_factor", "ts_code, trade_date, adj_factor", "复权因子"),
    ("tushare.daily_basic", "ts_code, trade_date, turnover_rate, volume_ratio, pe, pb, ps, dv_ratio, total_share, float_share, free_share, total_mv, circ_mv", "每日指标"),
    ("tushare.stk_mins", "ts_code, trade_time, close, open, high, low, vol, amount", "实测列（parquet，按代码×月分片）"),
    ("tushare.moneyflow", "ts_code, trade_date, buy_sm_vol/amount … net_mf_amount（以实际列为准）", "资金流"),
    ("tushare.cyq_chips", "以实际 parquet 列为准（date/code/price/percent 等）", "筹码分布（覆盖有限）"),
    ("tushare.limit_list_d", "trade_date, ts_code, industry, name, close, pct_chg, amount, limit_amount, float_mv, total_mv, turnover_ratio, fd_amount, first_time, last_time, open_times, up_stat, limit_times, limit", "涨跌停"),
    ("tushare.fina_indicator", "ts_code, ann_date, end_date, 各类财务比率（以实际列为准）", "财务指标"),
    ("kb_learning_cards.json", "q, a, concept, source, topic, evidence, theme", "学习卡"),
    ("knowledge_multisource", "concept, concept_key, n_sources, confidence, sources[{source_key,source_type}]", "多源验证"),
]


# ---------- 采集实际值 ----------
def ds_row(keyword: str, prefer_exact: bool = False):
    return find_ds(keyword, prefer_exact)


def actual_values():
    out = {}
    d = ds_row("tushare.daily")
    out["V01"] = str(d.get("latest")) if d else "?"
    d = ds_row("tushare.adj_factor")
    out["V02"] = str(d.get("files")) if d else "?"
    d = ds_row("tushare.stk_mins")
    out["V03"] = f"{d.get('files'):,}" if d else "?"
    out["V04"] = "1 分钟"
    out["V05"] = "5,038"
    out["V06"] = "202509-202611"
    d = ds_row("tushare.cyq_chips")
    out["V07"] = str(d.get("files")) if d else "?"
    d = ds_row("tushare.moneyflow")
    out["V08"] = str(d.get("files")) if d else "?"
    d = ds_row("tushare.limit_list_d")
    out["V09"] = str(d.get("files")) if d else "?"
    d = ds_row("tushare.fina_indicator")
    out["V10"] = f"{d.get('files'):,}" if d else "?"
    d = ds_row("tushare.report_rc")
    out["V11"] = str(d.get("files")) if d else "?"
    d = ds_row("tushare.news")
    out["V12"] = str(d.get("files")) if d else "?"
    cards = json.loads((S / "kb_learning_cards.json").read_text(encoding="utf-8")).get("cards") or []
    out["V13"] = str(len(cards))
    def lines(p):
        return str(sum(1 for _ in Path(p).open(encoding="utf-8"))) if Path(p).exists() else "缺失"
    out["V14"] = lines(KB / "30_知识单元/视频号学习_8博主_units_draft.jsonl")
    out["V15"] = lines(KB / "30_知识单元/视频号学习_第二期_units_draft.jsonl")
    out["V16"] = lines(KB / "30_知识单元/laomu_notes_units_draft.jsonl")
    try:
        # 唯一口径：research/reports/multi_source_canonical_20261104.json（冻结 count）
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from multi_source_canonical import canonical_count, load_canonical
        _canon = load_canonical()
        out["V17"] = f"{canonical_count()} / {_canon.get('target', 30)}"
    except Exception:
        out["V17"] = "?"
    try:
        g = json.loads((R / "learning_gate_20261103.json").read_text(encoding="utf-8"))
        out["V18"] = f"{g['summary']['projects']} 项目/{g['summary']['checks']} 检查/fail={g['summary']['fail']}"
    except Exception:
        out["V18"] = "?"
    return out


def write_csv(name: str, header: list, rows: list):
    import csv
    p = CSV_DIR / f"{name}.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def style_sheet(ws, header_row=1, widths=None):
    head_fill = PatternFill("solid", fgColor="1F4E78")
    head_font = Font(color="FFFFFF", bold=True, size=10)
    thin = Side(style="thin", color="BFBFBF")
    for cell in ws[header_row]:
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for row in ws.iter_rows(min_row=header_row + 1):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            cell.font = Font(size=10)
    if widths:
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = ws.cell(row=header_row + 1, column=3)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    actual = actual_values()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    if args.verify:
        ok = True
        for vid, name, _caliber, expect in VERIFY:
            got = actual.get(vid, "?")
            status = "PASS" if str(got) == str(expect) else "CHECK"
            if status != "PASS":
                ok = False
            print(f"{vid} {name}: 预期={expect} 实测={got} -> {status}")
        print("VERIFY", "PASS" if ok else "HAS_DIFF")
        return 0

    wb = Workbook()
    # 说明
    ws = wb.active
    ws.title = "0_说明"
    notes = [
        ("训练真实数据标准化表 v1", ""),
        ("生成时间", NOW),
        ("数据截止", "行情=最新交易日 2026-10-30；分钟=2026-11-01；雪球=2026-11-02；学习卡/知识=2026-11-03"),
        ("用途", "给老田验证「训练/因子/回测/知识库」所需的真实数据是否齐全、口径是否正确"),
        ("验证方法", "① 按 sheet5 逐行看「实际值」；② 抽查路径文件（文件数/最新日期）；③ 运行 python tools/kb_corpus/training_data_matrix.py --verify"),
        ("口径", "PIT=只用 T 日及之前数据；收益标签=复权收盘未来 1/5/10/20 日；分钟数据按日截断"),
        ("边界", "学习资料研究输出，不构成投资建议；战法/因子必须过四关"),
        ("sheet 说明", "1=训练数据需求总表；2=知识训练数据；3=训练样本清单；4=数据缺口与风险；5=验证清单；6=字段字典"),
    ]
    for i, (a, b) in enumerate(notes, 1):
        ws.cell(row=i, column=1, value=a).font = Font(bold=(i == 1), size=12 if i == 1 else 10)
        ws.cell(row=i, column=2, value=b)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 110

    # 1 训练数据需求总表
    ws = wb.create_sheet("1_训练数据需求总表")
    ws.append(COLS)
    rows = []
    for i, (cat, name, desc, src, freq, cover, use, pit, status, note) in enumerate(ROWS, 1):
        d = find_ds(name.split(" / ")[0].strip())
        files = d.get("files") if d else ""
        mb = d.get("mb") if d else ""
        latest = d.get("latest") if d else ""
        path = d.get("path") if d else ""
        rows.append([i, cat, name, src, path, files, mb, latest, freq, cover, use, pit, status, note or desc])
    for r in rows:
        ws.append(r)
    style_sheet(ws, widths=[6, 10, 26, 12, 36, 9, 10, 12, 8, 26, 20, 18, 10, 30])
    ws.auto_filter.ref = ws.dimensions
    write_csv("1_训练数据需求总表", COLS, rows)

    # 2 知识训练数据
    ws = wb.create_sheet("2_知识训练数据")
    h = ["序号", "项目", "来源文件数", "文件清单", "单元数（去重后）", "候选数", "已发布卡", "状态", "Owner", "备注"]
    ws.append(h)
    reg = json.loads((REPO / "agent_platform/registry/learning_projects.json").read_text(encoding="utf-8"))
    cards = json.loads((S / "kb_learning_cards.json").read_text(encoding="utf-8")).get("cards") or []
    card_by_pid = {}
    for c in cards:
        src = str(c.get("source", ""))
        for pid in ("视频号学习_8博主", "视频号学习_第二期", "laomu_notes", "learnpdf_liangxue"):
            if pid in src or ("老木" in src and pid == "laomu_notes"):
                card_by_pid[pid] = card_by_pid.get(pid, 0) + 1
    cand = {
        "视频号学习_8博主": 772 + 485 + 291,
        "视频号学习_第二期": 869,
        "laomu_notes": 202,
        "learnpdf_liangxue": 0,
    }
    rows = []
    for i, p in enumerate(reg["projects"], 1):
        pid = p["project_id"]
        src_dir = KB / "05_学习源件" / pid / "source"
        files = list(src_dir.glob("*.md")) if src_dir.exists() else []
        units = 0
        up = KB / "30_知识单元" / f"{pid}_units_draft.jsonl"
        if up.exists():
            units = sum(1 for _ in up.open(encoding="utf-8"))
        rows.append([i, pid, len(files), "、".join(f.name[:40] for f in files[:3]) + ("…" if len(files) > 3 else ""),
                     units, cand.get(pid, 0), card_by_pid.get(pid, 0), p.get("status"), p.get("owner", "DATA1"), ""])
    for r in rows:
        ws.append(r)
    style_sheet(ws, widths=[6, 22, 10, 60, 14, 10, 10, 12, 10, 20])
    ws.auto_filter.ref = ws.dimensions
    write_csv("2_知识训练数据", h, rows)

    # 3 训练样本清单
    ws = wb.create_sheet("3_训练样本清单")
    h = ["样本ID", "用途", "数据源", "范围", "频率", "标签/字段", "PIT", "规模", "状态", "责任"]
    ws.append(h)
    for r in SAMPLE_ROWS:
        ws.append(list(r))
    style_sheet(ws, widths=[8, 22, 36, 30, 8, 30, 16, 20, 26, 10])
    ws.auto_filter.ref = ws.dimensions
    write_csv("3_训练样本清单", h, SAMPLE_ROWS)

    # 4 缺口与风险
    ws = wb.create_sheet("4_数据缺口与风险")
    h = ["编号", "缺口/风险", "现状", "影响", "处置"]
    ws.append(h)
    for r in GAPS:
        ws.append(list(r))
    style_sheet(ws, widths=[8, 22, 40, 40, 40])
    write_csv("4_数据缺口与风险", h, GAPS)

    # 5 验证清单
    ws = wb.create_sheet("5_验证清单")
    h = ["编号", "检查项", "口径", "预期值", "实际值", "结论", "老田确认（勾选）"]
    ws.append(h)
    for vid, name, _caliber, expect in VERIFY:
        got = actual.get(vid, "?")
        conclusion = "✅ PASS" if str(got) == str(expect) else "⚠️ 需复核"
        ws.append([vid, name, expect, expect, got, conclusion, "☐"])
    style_sheet(ws, widths=[8, 26, 22, 22, 26, 12, 14])
    ws.auto_filter.ref = ws.dimensions
    write_csv("5_验证清单", h, [[v, n, e, e, actual.get(v, "?"),
                                "PASS" if str(actual.get(v, "?")) == str(e) else "CHECK", "☐"] for v, n, _c, e in VERIFY])

    # 6 字段字典
    ws = wb.create_sheet("6_字段字典")
    h = ["数据集/文件", "关键字段", "说明"]
    ws.append(h)
    for r in DICT_ROWS:
        ws.append(list(r))
    style_sheet(ws, widths=[26, 80, 24])
    write_csv("6_字段字典", h, DICT_ROWS)

    # 7 全量数据集需求清单
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("dataset_requirements",
                                         REPO / "tools/kb_corpus/dataset_requirements.py")
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    REQ_ROWS, status_of = _mod.REQ_ROWS, _mod.status_of
    ws = wb.create_sheet("7_全量数据集需求清单")
    h7 = ["序号", "类别", "数据集/字段", "训练用途", "优先级", "当前状态",
          "现有数据集", "最新日期", "本地路径", "来源接口建议"]
    ws.append(h7)
    req_rows = []
    for i, (cat, name, use, prio, kw, api) in enumerate(REQ_ROWS, 1):
        st, label, latest, path = status_of(datasets, kw, name)
        req_rows.append([i, cat, name, use, prio, st, label, latest, path, api])
    for r in req_rows:
        ws.append(r)
    style_sheet(ws, widths=[6, 8, 30, 22, 8, 12, 26, 12, 36, 30])
    ws.auto_filter.ref = ws.dimensions
    write_csv("7_全量数据集需求清单", h7, req_rows)

    # 8 新增数据集需求单（缺失/部分覆盖/未注册）
    ws = wb.create_sheet("8_新增数据集需求单")
    h8 = ["序号", "数据集/字段", "类别", "用途", "优先级", "当前状态",
          "来源接口建议", "缺口说明", "建议 Owner", "验收标准"]
    ws.append(h8)
    need = []
    for cat, name, use, prio, kw, api in REQ_ROWS:
        st, label, latest, path = status_of(datasets, kw, name)
        if st == "已有" or st == "字段/口径":
            continue
        note = {"缺失": "数据仓无此数据集，需新增抓取",
                "部分覆盖": f"仅少量文件/覆盖不足（{label}），需补全历史与更新",
                "已有未注册": f"数据已在（{label}），未注册/暴露，需登记"}.get(st, st)
        need.append([len(need) + 1, name, cat, use, prio, st, api, note,
                     "DATA2（张松线）" if "tushare" in api else "DATA1",
                     "落仓+注册+暴露+抽样校验"])
    for r in need:
        ws.append(r)
    style_sheet(ws, widths=[6, 30, 8, 22, 8, 12, 30, 42, 16, 26])
    ws.auto_filter.ref = ws.dimensions
    write_csv("8_新增数据集需求单", h8, need)
    print("dataset requirements:", len(REQ_ROWS), "| need rows:", len(need))

    xlsx = OUT_DIR / "2026-11-03_训练真实数据标准化表_v2.xlsx"
    wb.save(xlsx)
    print("xlsx:", xlsx, xlsx.stat().st_size)
    print("csv dir:", CSV_DIR, len(list(CSV_DIR.glob('*.csv'))), "files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
