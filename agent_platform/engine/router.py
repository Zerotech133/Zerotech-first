# -*- coding: utf-8 -*-
"""SmartRouter 智能模型路由器（10-18 老田拍板：自动思考该用什么模型）

定位：binding 的"缺省决策者"——org.json/team_*.json 里的 llm_binding 是
人工注册的主事实源；本路由器负责两件事：
  1. route(task, ...) → 对一条任务/请求实时决定该用哪个 binding（可解释）
  2. auto_assign(roster) → 按分工策略给全军团生成建议绑定（预览 diff，人工确认）

设计原则（老张现场纪律）：
  - 规则打分优先：毫秒级、零 token、可解释；LLM 只做可选的兜底分类，不是必须
  - 负载与故障感知：本地 /api/ps 查在跑模型；云端通道带失败熔断计数
  - 席位等级（grade）是强信号：E3 判读/研究岗 → 云端重档；E1 调度岗 → flash/本地
"""
import json
import time
import urllib.request

# ---------------------------------------------------------------- 通道目录

CLOUD_MODELS = {
    "glm-5.3": {"tier": "heavy", "note": "思考型重档：判读/裁决/研究/走形检查"},
    "glm-5.3-flash": {"tier": "light", "note": "轻量快档：调度/分发/归集初筛"},
}

SPECIAL_ROUTES = {
    "nova/1234": "视觉/多模态（Qwen3.5-35B 视觉版 @1234）",
    "nova/1238": "结构化/JSON 质检（Qwen3-30B-Instruct @1238）",
}

LOCAL_ROLES = {
    "embed": "bge-m3",
    "distill_teacher": "qwen3.6:35b-a3b",
    "workhorse": "qwen3.5:9b",
}

# 任务类型关键词 → (binding, 权重)。顺序即优先级，命中即停。
TASK_RULES = [
    # —— 特种通道（最高优先：普通 LLM 干不了的活）——
    (("视觉", "图片", "图像", "截图", "看图", "image", "vision"), "nova/1234"),
    (("结构化", "json质检", "质检打分", "判分", "评分卡"), "nova/1238"),
    # —— 云端重档：要强推理的活 ——
    (("判读", "裁决", "复盘", "裁定", "走形", "渗透", "压测",
      "方法论", "研究", "尽调", "审计", "深挖", "归因", "战略"), "glm/glm-5.3"),
    # —— 云端轻档：调度类 ——
    (("调度", "分发", "归集", "汇总", "初筛", "跟踪", "台账", "晨会"), "glm/glm-5.3-flash"),
    # —— 本地重岗：训练/蒸馏/长生成 ——
    (("蒸馏", "训练", "微调", "sft", "数据集", "teacher"), "ollama/qwen3.6:35b-a3b"),
    # —— 本地主力：数据岗 ——
    (("治理", "抓取", "爬取", "清洗", "入库", "流转", "分配", "备份",
      "指标", "行情", "数据", "落库"), "ollama/qwen3.5:9b"),
    # —— 嵌入 ——
    (("嵌入", "向量", "检索", "相似", "embedding"), "ollama/bge-m3"),
]

GRADE_PREFERENCE = {
    # 席位等级 → 无关键词命中时的默认倾向（对齐 10-18 分工定案）
    "E3": "glm/glm-5.3",
    "E2": "ollama/qwen3.5:9b",
    "E1": "glm/glm-5.3-flash",
}


# ---------------------------------------------------------------- 熔断器

class ChannelHealth:
    """云端通道熔断：连续失败 N 次后冷却期内直接判不可用（不再打云端）"""

    def __init__(self, fail_threshold=3, cooldown=300):
        self.fail_threshold = fail_threshold
        self.cooldown = cooldown
        self._fails = 0
        self._last_fail = 0.0

    def record_fail(self):
        self._fails += 1
        self._last_fail = time.time()

    def record_ok(self):
        self._fails = 0

    @property
    def available(self):
        if self._fails < self.fail_threshold:
            return True
        return (time.time() - self._last_fail) > self.cooldown


CLOUD_HEALTH = ChannelHealth()


def local_loaded_models(host="http://127.0.0.1:11434", timeout=3):
    """查 Ollama 当前驻留模型（/api/ps），供负载感知；服务不可达返回 []"""
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(host.rstrip("/") + "/api/ps", timeout=timeout) as r:
            return [m.get("name", "") for m in json.load(r).get("models", [])]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- 核心决策

def _keyword_route(task):
    """关键词规则打分：返回 (binding, 命中词) 或 (None, None)"""
    t = (task or "").lower()
    for words, binding in TASK_RULES:
        for w in words:
            if w in t:
                return binding, w
    return None, None


def _fallback_for_grade(grade):
    return GRADE_PREFERENCE.get((grade or "").upper(), "ollama/qwen3.5:9b")


def route(task, agent_id=None, grade=None, task_type=None, respect_binding=None):
    """核心决策：返回 {binding, provider, reason, signals}

    respect_binding：该 Agent 已注册的 binding。路由哲学——
    注册绑定优先（前期分工配好就不折腾）；只有 binding 为空/"auto" 时才自动决策。
    task_type 显式指定时跳过关键词匹配（调用方已知任务类型）。
    """
    signals = []
    if respect_binding and respect_binding not in ("auto", ""):
        signals.append("尊重注册绑定(%s)" % respect_binding)
        return _pack(respect_binding, signals)

    # 1) 显式 task_type
    if task_type and task_type in SPECIAL_ROUTES:
        pass  # 落到关键词表无法表达，直接走下面特种映射

    # 2) 关键词规则
    binding, hit = _keyword_route(task)
    if task_type == "embed":
        binding, hit = "ollama/bge-m3", "task_type=embed"
    if binding:
        signals.append("关键词命中[%s]" % hit)
        # 云端熔断：命中云端但通道不可用 → 本地降档
        if binding.startswith("glm/") and not CLOUD_HEALTH.available:
            fb = "ollama/qwen3.5:9b" if "flash" not in binding else "ollama/qwen3.5:9b"
            signals.append("云端熔断中→本地降档")
            return _pack(fb, signals)
        return _pack(binding, signals)

    # 3) 等级倾向
    binding = _fallback_for_grade(grade)
    signals.append("无关键词命中→按席位等级 %s 默认" % (grade or "?"))
    if binding.startswith("glm/") and not CLOUD_HEALTH.available:
        signals.append("云端熔断中→本地降档")
        binding = "ollama/qwen3.5:9b"
    return _pack(binding, signals)


def _pack(binding, signals):
    prefix = binding.split("/", 1)[0]
    provider = {"glm": "cloud-zhipu", "nova": "special-novamax",
                "ollama": "local-ollama"}.get(prefix, prefix)
    return {"binding": binding, "provider": provider,
            "reason": "；".join(signals), "signals": signals}


# ---------------------------------------------------------------- 全军团自动适配

# 上云白名单：判读/研究/对抗型岗位（10-18 分工定案：要强推理的才吃云端）
CLOUD_HEAVY_ROLES = {
    "chief_of_staff",            # A0 田丰
    "ai_methodology_researcher",  # AI1 诸葛亮
    "security_lead",             # SEC2 方案走形/渗透
    "opt1_workflow_optimizer",   # OPT1
    "academy_director",          # ACAD1 研究院院长
}

# 本地工匠保护名单：即使标了 E3 也不上云（工程/数据/执行=本地纪律）
LOCAL_CRAFT_ROLES = {
    "code_engineer", "frontend_engineer", "ui_designer", "devops_engineer",
    "qa_engineer", "data_engineer", "tech_writer", "product_manager",
    "strategy_engineer", "backtest_engineer", "trade_executor",
    "t1_bull", "t2_bear", "n1_sentiment", "f1_fundamental", "d1_decision",
}


def auto_assign(roster, apply=False, write_fn=None):
    """按分工策略生成全军团建议绑定（默认只预览）。

    roster：[{agent_id, role_profile, grade, dept, binding}]（由服务层聚合）
    策略（10-18 分工定案直译）：
      vision/视觉 role              → nova/1234；json 质检 role → nova/1238
      上云白名单 role（判读/研究）  → glm/glm-5.3
      E1 参谋线调度岗(dept=chief)   → glm/glm-5.3-flash
      trainer/蒸馏 role             → ollama/qwen3.6:35b-a3b
      本地工匠保护名单（含 E3）     → ollama/qwen3.5:9b（不上云）
      其余 E3 非工匠               → glm/glm-5.3；剩余一律 → ollama/qwen3.5:9b
    write_fn(agent_id, binding, note)：应用回调（服务层负责备份+写回）。
    返回 [{agent_id, old, new, changed}]
    """
    out = []
    for a in roster:
        old = a.get("binding") or ""
        role = (a.get("role_profile") or "").lower()
        dept = a.get("dept") or ""
        if "vision" in role or "视觉" in role:
            new = "nova/1234"
        elif "json" in role and ("qa" in role or "质检" in role):
            new = "nova/1238"
        elif "trainer" in role or "distill" in role:
            new = "ollama/qwen3.6:35b-a3b"
        elif role in CLOUD_HEAVY_ROLES:
            new = "glm/glm-5.3"
        elif role in LOCAL_CRAFT_ROLES:
            new = "ollama/qwen3.5:9b"
        elif a.get("grade") == "E1" and dept == "chief":
            new = "glm/glm-5.3-flash"
        elif a.get("grade") == "E3":
            new = "glm/glm-5.3"
        else:
            new = "ollama/qwen3.5:9b"
        changed = old != new
        if apply and changed and write_fn:
            write_fn(a["agent_id"], new,
                     "auto_assign 10-18：按分工定案自动适配（原 %s）" % (old or "无"))
        out.append({"agent_id": a["agent_id"], "role": a.get("role_profile"),
                    "grade": a.get("grade"), "old": old, "new": new,
                    "changed": changed})
    return out
