# -*- coding: utf-8 -*-
"""审计线门户状态面（M6 最小接线，2026-10-08 定位拍板 C 混合态）。

单一数据源纪律：本模块是 docs 台账的**投影**，不是第二个事实源——
  · M0-M6 行文        ← docs/ai_operator/overall-progress.md（唯一进度台账）
  · 挂账条件/复活触发  ← docs/positioning-decision.md（决议单一事实源）
两处不一致时以文档为准并修此处。每个数字带「值@时点 + 来源指针」。

最小接线边界（拍板原文）：只读、无鉴权、全体条目标 unverified，
让七批 439 项测试资产可见；鉴权/每日对账/持久调度/告警等
企业级要求全部缓行（positioning-decision.md §2）。

用法：
  python -m ai_operator.portal_status   # CLI 自测，打印 JSON
"""
import json
import time

DECISION_DOC = "quant_backtest/docs/positioning-decision.md"
PROGRESS_DOC = "quant_backtest/docs/ai_operator/overall-progress.md"

# M0-M6 里程碑投影（照录 overall-progress.md 表格 @2026-10-08；
# M6 行为本次接线后的终态，台账同步回填）
MILESTONES = [
    {"stage": "M0 资料与契约",
     "delivered": "离线 ZIP/目录核查、内容哈希与发布包边界",
     "pending": "两个原始 ZIP、正式 Schema、规则 ID/evidence、V011/V012 台账与审核同步"},
    {"stage": "M1 记忆与审计",
     "delivered": "SQLite WAL、幂等/修订、增量旧记录导入、时点召回、outbox 完整导出和 CLI；持久接收链；审核追加事件和历史回放；Ed25519 归档策略验证器及本人离线签名工具",
     "pending": "本人公钥/角色独立登记、可信授权策略固定摘要及应用接线；CLI 审核保持关闭；正式运行调度"},
    {"stage": "M2 结构与规则",
     "delivered": "既有本地简化算法逐根回放、首次观测/撤销、120 根存量样本工程验收；当前接收来源快照/字节哈希、300 根存量日线联调",
     "pending": "标准算法定义、四组结构对照、供应商/价格口径审核及可信历史行情、校准及成本后时间外报告"},
    {"stage": "M3 多 AI 与快速模型",
     "delivered": "统一影子事实包/建议审计、白名单特征与可重复数据集切分",
     "pending": "教师观点/超时/限流、可信训练数据、模型训练/注册和性能评测"},
    {"stage": "M4 独立 paper",
     "delivered": "尚未开发本阶段账户与执行模块",
     "pending": "指令状态机、组合硬风控、T+1/现金/整手、成交/拒绝/未知回执对账"},
    {"stage": "M5 复盘与进化",
     "delivered": "H5/H20 标签计算、purge/embargo/传递分组、描述性摘要；来源/日历快照、身份与收盘校验；持久审核缺口；人工批准/拒绝/撤销工程接口及 as_of 回放；官方原文/回执；455 天独立文本日历推导与真实逐日对照零差异",
     "pending": "规则条件生效首日佐证、临时停市完整检索、真实供应商/口径/修订/历史到达与复权认证、实际人审、正式持续采集、可信成熟结果、逐笔归因、教师蒸馏、champion/challenger 晋升与回滚"},
    {"stage": "M6 门户与常驻",
     "delivered": "审计状态页已接线（portal /audit 页 + /api/audit_status，只读无鉴权，全体 unverified）@2026-10-08",
     "pending": "任务入口核实、持久调度、重启补偿、鉴权、告警和每日对账（拍板缓行，挂复活触发）"},
]

# 七批测试资产终态（overall-progress.md 第七批段落，251fe70 回填口径）
TEST_ASSETS = {
    "batches": 7,
    "collected": 439, "passed": 438, "skipped": 1, "failed": 0,
    "subtests_passed": 869, "duration_s": 485.98,
    "as_of": "2026-10-08 08:40",
    "env": "管理员环境（pytest tests_ai_operator）",
    "log": r"C:\Temp\pytest_full.log",
    "skip_reason": "唯一 skip=test_private_permissions（行为级探测确认本机安全层"
                   "拦截句柄删除标记，代码按设计禁止路径清理，该用例显式跳过）",
    "baseline_note": "非管理员环境快照 435 通过/4 跳过 @2026-10-07（两套环境预期均已台账注记）",
}

# 认证保留条件（positioning-decision.md §2：挂账不销，等待复活触发）
AUTH_CONDITIONS = [
    {"item": "私钥登记（本人公钥/角色独立登记）", "stage": "M1", "done": False},
    {"item": "2023 首日佐证（规则条件生效首日佐证）", "stage": "M5", "done": False},
    {"item": "临时停市全集（临时停市完整检索）", "stage": "M5", "done": False},
]

# 平台复活触发条件（positioning-decision.md §4：满足其一起评 M6 全集）
REVIVAL_TRIGGERS = [
    "涪陵 OPC 或任一企业场景出现真实付费/部署需求",
    "北理工联合实验室产出需要对外演示审计级证据链",
    "ARM 服务器客户明确要求数据可追溯能力交付",
]

DECISION = {
    "mode": "C 混合态（2026-10-08 老田拍板）",
    "summary": "个人助手为主线；审计线冻结扩批（不再启动第八批）；"
               "M6 只做最小接线（审计状态页进门户）；认证保留条件挂账不销",
    "doc": DECISION_DOC,
}


def status():
    """审计线状态 dict（portal_api /api/audit_status 直接返回本值）。"""
    return {
        "ok": True,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "verified": False,  # 全体条目 unverified 总开关（挂账未销）
        "banner": "全体条目 unverified：认证保留条件挂账未销，本页仅提供"
                  "可见性，不构成任何认证声明（positioning-decision.md §2）",
        "decision": DECISION,
        "milestones": MILESTONES,
        "test_assets": TEST_ASSETS,
        "auth_conditions": AUTH_CONDITIONS,
        "revival_triggers": REVIVAL_TRIGGERS,
        "sources": {"progress": PROGRESS_DOC, "decision": DECISION_DOC},
    }


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=1))
