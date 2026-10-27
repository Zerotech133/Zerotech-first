# -*- coding: utf-8 -*-
"""军团任务路由器 v0.1（K-ORG-119 批1）

职责：任务 → 查三册 → 执行计划（谁 + 什么数据 + 什么接口 + 什么顺序）
原则：规则匹配非 LLM 路由（可解释/零幻觉/零成本）；只撮合不决策；匹配不唯一升级 A0 仲裁。

用法：
  python router.py "刻画 600337 的投研画像"
  python router.py --plan plans/xxx.json
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOGS = ROOT / "catalogs"


# ── 极简 YAML 读取（无外部依赖，独立体系原则）──
def load_seed(path: Path, key: str) -> list:
    """读种子 yaml 的 resources/services 列表（单层 list + 平铺字段）。"""
    items, cur = [], None
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("- id:"):
            cur = {"id": s[5:].strip()}
            items.append(cur)
        elif (
            cur is not None
            and re.match(r"^(\w[\w_]*):\s*(.+)$", s)
            and not s.startswith("#")
        ):
            m = re.match(r"^(\w[\w_]*):\s*(.+)$", s)
            k, v = m.group(1), m.group(2).strip().strip('"').strip("'")
            if k not in ("resources", "services"):
                cur[k] = v
    return items


DATA = load_seed(CATALOGS / "data_catalog.yaml", "resources")
SERVICES = load_seed(CATALOGS / "service_catalog.yaml", "services")

# ── 能力册种子（29 编制的 can_do/needs，先手工核心岗，批2全量回填）──
CAPABILITIES = [
    {
        "agent": "T1",
        "dept": "invest",
        "can_do": ["技术面分析", "多头论据", "趋势判断", "刻画画像", "weak跟踪"],
        "needs": ["ashare_kline", "kg_graph"],
    },
    {
        "agent": "T2",
        "dept": "invest",
        "can_do": ["技术面分析", "空头论据", "风险提示", "刻画画像"],
        "needs": ["ashare_kline", "kg_graph"],
    },
    {
        "agent": "N1",
        "dept": "invest",
        "can_do": ["情绪分析", "舆情判读", "新闻事件"],
        "needs": ["news_db", "kg_graph"],
    },
    {
        "agent": "F1",
        "dept": "invest",
        "can_do": ["基本面分析", "财报解读", "估值"],
        "needs": ["kg_graph"],
    },
    {"agent": "D1", "dept": "invest", "can_do": ["最终裁决", "综合决策"], "needs": []},
    {
        "agent": "DATA1",
        "dept": "data",
        "can_do": ["数据查询", "数据治理", "目录登记"],
        "needs": ["ashare_kline", "kg_graph", "news_db"],
    },
    {
        "agent": "A0",
        "dept": "chief",
        "can_do": ["任务仲裁", "汇报判读", "调度"],
        "needs": [],
    },
    {
        "agent": "R1",
        "dept": "invest",
        "can_do": ["风控辩论", "左侧交易评估", "激进仓位设计"],
        "needs": ["ashare_kline", "kg_graph"],
    },
    {
        "agent": "R2",
        "dept": "invest",
        "can_do": ["风控辩论", "右侧交易评估", "保守仓位设计"],
        "needs": ["ashare_kline", "kg_graph"],
    },
    {
        "agent": "R3",
        "dept": "invest",
        "can_do": ["风控仲裁", "风险参数裁定"],
        "needs": [],
    },
    {
        "agent": "ACAD1",
        "dept": "academy",
        "can_do": ["研究立项", "研究院评审", "策略统筹", "商业化统筹", "跨部门联评"],
        "needs": ["strategy_registry", "backtest_registry"],
    },
    {
        "agent": "STRAT1",
        "dept": "academy",
        "can_do": ["策略研究", "交易策略设计", "因子研究", "图形推演", "研究报告"],
        "needs": ["ashare_kline", "kg_graph"],
    },
    {
        "agent": "BACK1",
        "dept": "academy",
        "can_do": ["量化回测", "回测复现", "样本外验证", "Go/No-Go", "回测判据"],
        "needs": ["backtest_registry", "ashare_kline"],
    },
    {
        "agent": "COMM1",
        "dept": "academy",
        "can_do": ["商业化研究", "算法商品", "策略市场", "对外服务", "商业模式"],
        "needs": ["strategy_registry"],
    },
    {
        "agent": "INTEL1",
        "dept": "academy",
        "can_do": ["竞争情报", "行业对标", "开源社区研究", "竞争格局", "商业模式拆解"],
        "needs": ["news_db", "kg_graph"],
    },
    {
        "agent": "AI1",
        "dept": "academy",
        "can_do": ["AI方法论", "Agent能力抽象", "评测体系", "Prompt工程", "知识反哺"],
        "needs": ["strategy_registry"],
    },
    {
        "agent": "PROD1",
        "dept": "academy",
        "can_do": ["策略产品化", "需求定义", "验收标准", "试点设计", "交付清单"],
        "needs": ["strategy_registry"],
    },
    {
        "agent": "MKT1",
        "dept": "marketing",
        "can_do": ["商业化包装", "市场内容", "客户触达", "开源传播"],
        "needs": ["news_db"],
    },
    {
        "agent": "LEG1",
        "dept": "legal",
        "can_do": ["合规审查", "数据授权审查", "对外发布审查", "风险边界"],
        "needs": [],
    },
    {
        "agent": "ARCH1",
        "dept": "dev",
        "can_do": ["策略工程化", "系统架构", "接口设计", "技术评审"],
        "needs": ["strategy_registry"],
    },
]

# ── 任务意图识别（关键词规则）──
INTENTS = [
    (
        r"研究院|策略研究|交易策略|方法论|商业化|算法商品|策略市场|竞争情报|行业对标|产品化",
        "academy_research",
        [
            "ashare_kline",
            "kg_graph",
            "news_db",
            "backtest_registry",
            "strategy_registry",
        ],
    ),
    (
        r"刻画|画像|全维|分析.*股|投研",
        "xsec_profile",
        ["ashare_kline", "kg_graph", "news_db"],
    ),
    (r"辩论|多空|方向|看多看空", "invest_debate", ["ashare_kline", "kg_graph"]),
    (r"舆情|新闻|情绪", "sentiment_scan", ["news_db"]),
    (r"备份|备份数据", "backup", []),
    (r"推送|飞书", "push", []),
]


def route(task_desc: str) -> dict:
    # 1) 意图与资源需求
    intent, need_data = "general", []
    for pat, it, needs in INTENTS:
        if re.search(pat, task_desc):
            intent, need_data = it, needs
            break
    # 2) 数据册定位（含代码参数提取）
    code = re.search(r"\b(\d{6})\b", task_desc)
    data_plan = []
    for d in DATA:
        if d["id"] in need_data:
            entry = {
                "id": d["id"],
                "kind": d.get("kind"),
                "uri": d.get("uri"),
                "what": d.get("what", "")[:40],
            }
            if code:
                entry["uri"] = (
                    entry["uri"]
                    .replace("{code}", code.group(1))
                    .replace("{mkt}", "SH" if code.group(1)[0] == "6" else "SZ")
                )
                entry["param"] = {"code": code.group(1)}
            if d.get("status") == "read-only-archived":
                entry["note"] = "⚠️ 封存源，仅考古引用"
            data_plan.append(entry)
    # 3) 能力册匹配（谁能干）
    agents = [
        c
        for c in CAPABILITIES
        if intent.replace("_", "") in "".join(c["can_do"])
        or any(k in task_desc for k in c["can_do"])
    ]
    if not agents and intent == "xsec_profile":
        agents = [
            c for c in CAPABILITIES if c["agent"] in ("T1", "T2", "N1", "F1", "D1")
        ]
    if intent == "academy_research":
        agents = [c for c in CAPABILITIES if c["dept"] == "academy"]
    # 4) 执行计划
    plan = {
        "task": task_desc,
        "intent": intent,
        "data": data_plan,
        "agents": [{"agent": a["agent"], "dept": a["dept"]} for a in agents],
        "services": [{"id": "lark_push"}]
        if "推送" in task_desc or intent == "xsec_profile"
        else [],
        "sequence": (
            [
                "ACAD1审题立项",
                "STRAT1/INTEL1提出研究假设",
                "DATA1登记数据口径",
                "BACK1或专项方法验证",
                "COMM1/PROD1论证转化",
                "LEG1合规审查",
                "ACAD1评审→A0拍板",
                "知识入库/进入试点/产品化",
            ]
            if intent == "academy_research"
            else [
                "取数(按data册uri)",
                "生成三线报告(xsec_profile)",
                "投研辩论(T1⇄T2+N1/F1)",
                "裁决(D1)",
            ]
            if intent == "xsec_profile"
            else ["按意图执行"]
        ),
        "router": "rules-v1",
        "ts_rule": "E1岗需A0附署；匹配不唯一升级田丰仲裁",
    }
    return plan


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--plan":
        plan_path = Path(sys.argv[2])
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            if not payload:
                raise SystemExit("plan 文件不能为空")
            payload = payload[0]
        desc = (
            payload.get("task")
            or payload.get("description")
            or payload.get("desc")
            or payload.get("title")
        )
        if not desc:
            raise SystemExit("plan 文件必须包含 task/description/desc/title")
    elif len(sys.argv) > 1 and sys.argv[1] == "--plan":
        raise SystemExit("用法：python router.py --plan <plan.json>")
    else:
        desc = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "刻画 600337 的投研画像"
    print(json.dumps(route(desc), ensure_ascii=False, indent=2))
