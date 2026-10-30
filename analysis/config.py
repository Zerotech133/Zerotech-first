# -*- coding: utf-8 -*-
"""全局配置：路径、常量、评级与数据域定义。"""
import os
import logging

PKG = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG)  # 当前 worktree 根（代码所在）
MAIN_ROOT = os.environ.get("ANALYSIS_MAIN_ROOT", r"C:\项目\老田的AI助手")  # 主仓：数据/服务/工具


def _load_env():
    """从主仓 .env.local 读取凭据（XT_DATA_KEY 等），不覆盖已存在的环境变量。"""
    p = os.path.join(MAIN_ROOT, ".env.local")
    if not os.path.exists(p):
        return
    try:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "analysis/config.py", exc)


_load_env()
CURATED = os.path.join(MAIN_ROOT, "quant_backtest", "data", "tushare_curated")
WORK = os.environ.get("ANALYSIS_WORK", r"C:\项目\analysis_work")
DELIVER_ROOT = os.environ.get("ANALYSIS_DELIVER", r"C:\项目\deliverables")
DATA_CACHE = os.path.join(WORK, "cache")         # sync 缓存（不写 curated 仓库）
DB_PATH = os.path.join(WORK, "analysis.db")
SCHEMA_PATH = os.path.join(PKG, "schema", "stock_analysis.schema.json")
PROMPT_VERSION = "p0-20261026"

# 数据域：用于快照覆盖率与质量门
DOMAINS = {
    "quote":        {"table": "daily",            "required": True},
    "valuation":    {"table": "daily_basic",      "required": True},
    "financial":    {"table": "income+balancesheet+cashflow", "required": True},
    "moneyflow":    {"table": "moneyflow",        "required": True},
    "chips":        {"table": "cyq_perf",         "required": False},
    "margin":       {"table": "margin_detail",    "required": False},
    "announcement": {"table": "anns_d",           "required": True},
    "news":         {"table": "news.db",          "required": False},
    "research":     {"table": "research_report",  "required": False},
    "holders":      {"table": "top10_holders",    "required": False},
    "industry":     {"table": "sw_l1_member",     "required": False},
    "calendar":     {"table": "disclosure_date",  "required": False},
}

RATINGS = ["买入", "观察偏多", "观察", "事件观察", "回避"]
# G1 数据质量门：80% 为硬门槛（低于即不通过），90% 为目标（80-90 区间标黄告警）
COVERAGE_MIN = 80
COVERAGE_TARGET = 90
# P0N 打分 v2：五维权重（合计 1.0）与评级分档（P0O 回测后按分位校准）
SCORE_WEIGHTS = {"quality": 0.30, "growth": 0.20, "valuation": 0.20, "trend": 0.15, "event": 0.15}
SCORE_CUTS = {"buy": 82, "observe_up": 68, "observe": 50, "avoid": 32}
SPECIAL_TAGS = {
    "ST": "ST/*ST（风险警示）", "TUI": "退市风险", "TINGPAI": "停牌",
    "CHONGZHENG": "重整/预重整", "ZHONGZU": "重组/并购",
}

# 事件分类关键词（规则版，LLM 可选增强）；顺序敏感：重整/退市优先于一般类别
EVENT_RULES = [
    ("重整", r"重整|预重整|破产清算|债权人会议"),
    ("退市/ST", r"风险警示|退市风险|终止上市|\*ST"),
    ("停牌/控制权", r"停牌|复牌|控制权|要约收购"),
    ("重组并购", r"重组|并购|发行股份购买|收购|重大资产"),
    ("业绩", r"业绩预告|业绩快报|年度报告|半年度报告|季度报告|预增|预亏"),
    ("减持/增持", r"减持|增持|权益变动"),
    ("回购", r"回购"),
    ("质押", r"质押"),
    ("解禁", r"解除限售|限售股上市流通|解禁"),
    ("分红", r"利润分配|分红|权益分派"),
    ("诉讼", r"诉讼|仲裁|起诉|判决"),
    ("问询/监管", r"问询函|监管函|警示函|立案|处罚"),
    ("股权激励", r"限制性股票|股权激励|员工持股"),
    ("高管变动", r"辞职|辞任|离任|聘任|高级管理人员"),
    ("经营/订单", r"中标|订单|合同|战略合作|停产|复产|产能"),
    ("行业政策", r"政策|规划|指导意见|标准"),
    ("市场异动", r"异常波动|股票交易"),
    # 程序性/治理类文件（不是投研事件，预审建议驳回；保留类别以便统计）
    ("治理/程序", r"实施细则|管理制度|章程|独立董事|审计委员会|保荐|持续督导|法律意见书|"
                  r"会议材料|股东大会|会议通知|核查意见|述职报告|社会责任|内部控制|募集资金"),
]
