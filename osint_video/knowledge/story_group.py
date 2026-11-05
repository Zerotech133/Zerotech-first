"""事件层聚类算子（P0）：把事实（knowledge_unit）聚成事件（story）。

## 为什么需要这一层

现有链路 ``resource → knowledge_unit(fact) → strategy_evidence`` 是**单文档**
链路：能回答"这篇报道说了什么"，回答不了"财联社/东财/雪球说的是不是同一
件事"。缺了横向聚合，下游 ``kg_brief`` / ``event_graph`` 只能消费散点事实
——这正是 2026-10-17 报告 F2「采了没人吃」的物理成因。

## 判定口径（参照 AIHOT 的 article→fact→story，MIT，仅取口径不引运行时）

1. **四关系而非二分类**：``same_story`` / ``progress`` / ``unrelated`` /
   ``roundup``。依据是其 370 对标注样本实测结论——"是非题 + 倾向否"的
   二分类会拒掉约一半的真合并。
2. **两层防错**：余弦 ≥ ``RECHECK_SIMILARITY`` 可直接判同；落在
   ``[RECALL_SIMILARITY, RECHECK_SIMILARITY)`` 区间的必须再过一道判定器
   （生产接 LLM，缺省用可解释规则），判定结果按实际判定方标记 ``llm`` 或
   ``rule``——标签必须指向真正下结论的那一方，否则事后无法追责。
3. **人工优先**：``decided_by='manual'`` 的归属边重算时一律跳过。
4. **独立来源数**：按媒体域名去重，同一家发 5 篇仍算 1 个来源；它是热度
   的分母，与 ``fact_count`` 必须分开统计。
5. **热度**：``独立来源数 × exp(-Δ小时 / 48)``，48 小时时间常数。

## 幂等性

``osint_story_member`` 以 ``(unit_key, unit_version)`` 作主键，一条事实只能
属于一个事件；写入走 ``ON CONFLICT DO NOTHING``，已归属的单元在取数阶段即被
``NOT EXISTS`` 排除。因此整算子可重复执行而不改变结果。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence

from common import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 常量 --

#: 向量召回阈值。低于此值不进入判定（AIHOT 14 天窗口同款起点）。
#:
#: 2026-11-04 用 bge-m3 在本项目语料 10 对样本上实测：
#: 同事件组 min=0.765 mean=0.816，无关组 max=0.434 mean=0.336，
#: **干净可分**，0.60 处 recall=1.00 / false_hit=0.00，取值安全。
#: 复现：``python scripts/calibrate_story_threshold.py``
RECALL_SIMILARITY: float = 0.60
#: 高置信阈值：余弦达到它即直接判同，不再过第二层判定器。
#:
#: **2026-11-04 由 0.85 下调为 0.75**，依据是本项目 42 条真值语料
#: （``tests/fixtures/verify_corpus.json``）上的端到端对照：
#:
#: | 阈值 | 事件数 | 拆散 | 误合并 | 独立来源数正确 |
#: |---|---|---|---|---|
#: | 0.85 | 34 | 5 | 0 | 9/9 |
#: | 0.75 | 29 | 1 | 1 | 13/13 |
#:
#: 0.85 的失效形态不是抽象的"recall 低"，而是**具体事件从日报里消失**：
#: 英伟达、台积电这类中英双语报道被拆散后，各自只剩 1 个来源，够不上
#: ``independent_sources >= 2`` 的入报门槛，读者看不到这件事发生过。
#: 0.75 引入的 1 处误合并已定位到具体配对并记在交付文档里——它是可观测、
#: 可事后补的代价；而"事件消失"不可观测，两者危害不对称，故取 0.75。
#:
#: 覆盖方式：环境变量 ``STORY_RECHECK_SIMILARITY``（见
#: :func:`resolve_recheck_similarity`）。需要回退到旧口径时不必改代码。
RECHECK_SIMILARITY: float = 0.75

#: 文本口径的直判同阈值（字符二元组 Jaccard）。
#:
#: **必须与 :data:`RECHECK_SIMILARITY` 分开**，两者不可比：余弦衡量的是
#: bge-m3 语义空间里的夹角，Jaccard 衡量的是字符二元组共现；同一条线在两种
#: 度量下含义完全不同。这个缺陷是 2026-11-04 下调阈值时被测试逼出来的——
#: 把 0.85 统一降到 0.75 后，一对 Jaccard=0.786 的文本（近乎同句改写）直接
#: 被判同、跳过了判定器，``judge_failures`` 从 1 变 0，降级原因随之丢失。
#:
#: 取值沿用原 0.85：Jaccard 到 0.85 基本已是同句改写，此时直判同无风险；
#: 落在 ``[RULE_JACCARD(0.35), 0.85)`` 的仍交给判定器，不会漏。
RULE_DIRECT_SIMILARITY: float = 0.85
#: 默认时间窗（天）。
DEFAULT_WINDOW_DAYS: int = 14
#: 单次召回候选上限。
CANDIDATE_LIMIT: int = 20
#: 热度时间常数（秒）：48 小时。
HEAT_TAU_SEC: float = 172800.0
#: 本地倒排召回的桶内上限（防止单 topic 爆炸）。
BUCKET_LIMIT: int = 50
#: 规则判定器的字符二元组 Jaccard 阈值。
#: 实体重合曾单独构成判定条件（RULE_ENTITY_OVERLAP），因在带真值语料上独立
#: 制造 6 处误合并已被移除，详见 ``RuleJudge`` docstring 的 v2 修订说明。
RULE_JACCARD: float = 0.35

#: 各相似度口径对应的**候选召回**门槛（进入判定环节的最低相似度）。
#:
#: 两个口径的分布不可比，不能共用一个数：余弦 0.60 处同事件 recall=1.00，
#: 而字符 Jaccard 在"同一事件、不同表述"上常只有 0.1–0.3，
#: ``compute`` 出来的 0.60 几乎只有同句改写才够得到——若沿用 0.60，嵌入降级
#: 那一轮会把绝大多数真候选挡在门外，合并率断崖式下跌且不报错。
#:
#: 定义在这里而不是 ``story_recall``：算子是消费方，探针要与算子同口径，
#: 故由算子持有常量、探针引用（见 ``story_recall.SIMILARITY_GATES``）。
SIMILARITY_GATES: dict[str, float] = {
    "vector": RECALL_SIMILARITY,
    "text": RULE_JACCARD,
}

#: 「临界未合并」的观测带宽：低于直判门槛多少以内仍值得记录。
#:
#: 存在的理由：``[门槛-0.05, 门槛)`` 这段是**沉默地带**——既没合进去，也没
#: 留下任何痕迹。运维只看到"事件数"，看不到"有多少对差一点就合"。而调阈值
#: 这件事的全部依据就在这里：若这段里挤满真同事件，说明门槛偏高；若全是无关
#: 事实，说明门槛偏低。没有这个数，调阈值只能靠猜。
#:
#: 2026-11-04 实测：本语料该带内有 ``s11-01``/``s11-02``（cos=0.7133，真同
#: 事件，被拆散）与 ``s04-01``/``solo-05``（cos=0.7203，不同事件）——两类都
#: 在里面，这正是"余弦无法区分同话题延续与同事件"的直接证据。
BORDERLINE_MARGIN: float = 0.05

#: 临界样本最多保留多少条。只留样本供人看，全量计数走 ``borderline``。
#: 上限的存在是为了让 ``dry_run`` 在十万级语料上也不会把内存撑爆。
BORDERLINE_SAMPLE_LIMIT: int = 20

#: 配对级观测的条数上限（``StoryGrouper(trace_pairs=True)`` 时生效）。
#:
#: 这份观测与 ``borderline`` 的区别：后者只记录落在**当前**带宽内且未合并的
#: 配对，只能回答"现在的门槛是否合适"；前者记录**每一对**被算过相似度的候选
#: 及其判定结论，可以回答"如果带宽改成 X 会怎样"——这是标定 ``BORDERLINE_MARGIN``
#: 唯一的数据来源，否则只能反复改代码重跑。
#:
#: 上限 5000 对应真机 50 条事实的百倍余量，十万级语料上会截断（截断由
#: ``pairs_observed_truncated`` 显式标记，绝不静默少给数据——标定结论依赖
#: 样本完整性，静默截断会让"某带宽下 0 对真该合"这种结论完全失真）。
PAIR_TRACE_LIMIT: int = 5000

#: 临界带复核判定写库时使用的 ``decided_by`` 取值。
#:
#: 与普通 ``llm`` 区分开是**必须**的，否则事后无法回答"临界带开 LLM 到底救回
#: 了多少"——两者混在同一个 ``llm`` 标签下，统计口径就没了。``store/schema.sql``
#: 的 ``osint_story_member_decided_by_check`` 早已包含该取值，无需改表。
BORDERLINE_RECHECK_DECIDED_BY: str = "llm_recheck"


def gate_for(mode: str) -> float:
    """返回给定相似度口径的候选召回门槛；未知口径按文本口径（更保守）处理。

    未知口径落到 ``text`` 而不是 ``vector``：向量门槛 0.60 更高，用错会让
    不该合并的配对进入判定；文本门槛更低，误入的配对还有判定器兜住。
    """
    return SIMILARITY_GATES.get(mode, RULE_JACCARD)

# ------------------------------------------------------ 跨窗口召回护栏 --
#
# 背景：算子按 ``window_days`` 滚动取数，同一件事跨月发酵（立案 → 处罚，间隔
# 18 天）会被切成两个事件，各自 ``independent_sources`` 只有一半，热度被系统
# 性低估。跨窗口召回是解法，但**放宽召回必然引入误合并**，而误合并会污染
# ``independent_sources`` 且事后几乎无法发现——所以必须带护栏，且默认关闭。

#: 跨窗口 anchor 的取数倍数：范围是 ``window_days * 本值``（默认 14×3 = 42 天）。
#:
#: 取 3 的依据见 ``knowledge.story_recall.SPLIT_FAR_MULTIPLIER``：2 倍（28 天）
#: 刚够覆盖自然月跨越，但"立案 → 听证 → 处罚"常拖到 5-6 周；3 倍是"跨月发酵
#: 仍属同一事件"的经验上限，也远小于 90 天回溯跨度。
CROSS_WINDOW_DAYS_MULTIPLIER: int = 3

#: 跨窗口合并的余弦下限。**严于窗内的 ``RECALL_SIMILARITY``(0.60)**。
#:
#: 2026-11-04 在本项目 42 条真值语料上实测 6 对跨窗高相似配对，余弦分布：
#:
#: - 真同一事件 5 对：0.757 / 0.797 / 0.904 / 0.923 / 0.932
#: - 假阳 1 对：**0.642** ——「隆基绿能公告董事长增持」vs「隆基绿能上调硅片
#:   报价」，同一公司、不同事件
#:
#: 0.70 = 两者中点（0.6995 上取整）。它同时满足"保留全部真分裂"（>0.642）
#: 与"排除假阳"（<0.757），两侧余量各约 0.06。
#:
#: ⚠️ 关键教训：**实体重叠排不掉这条假阳**——假阳恰恰是实体重合的那一对，
#: 而 5 条真分裂也都有实体重合。这与 ``RuleJudge`` 删除 ``ent_overlap`` 分支
#: 是同一个坑：单实体集合的重合率恒为 1.0，是必要条件而非充分条件。所以
#: 实体交集只作准入门槛，真正的判别力在余弦。
CROSS_WINDOW_MIN_SIMILARITY: float = 0.70

#: 跨窗口召回单轮 anchor 上限（窗口外已归属单元）。与 ``CANDIDATE_LIMIT`` 分开：
#: anchor 是"历史池"，量级比窗内候选大一个数量级，但每条只比一次。
CROSS_WINDOW_ANCHOR_LIMIT: int = 2000


def cross_window_admit(
    shared_entities: set[str], similarity: float, mode: str
) -> tuple[bool, str]:
    """判断一对跨窗配对是否准入合并，返回 ``(是否准入, 通道名)``。

    准入条件（三选三，缺一不可）：

    1. 口径为 ``vector``——0.70 是为余弦标定的，字符 Jaccard 与它不可比；
       跨月表述差异会让 Jaccard 系统偏低，用它判定只会得到"看似没分裂"的
       假象。宁可漏合；
    2. **有共同主体**（``shared_entities`` 非空）；
    3. 余弦 >= :data:`CROSS_WINDOW_MIN_SIMILARITY`。

    ## 一段已撤回的放宽（留档，勿重犯）

    2026-11-04 真机验证时曾加过第二条"高相似免实体"通道（余弦 >= 0.90 即
    免实体条件），理由是查库看到 ``SMIC``/``中芯国际``、``隆基绿能股份有限
    公司``/``隆基绿能``、``欧盟``/``欧盟委员会`` 三对实体字面不一致。**这个
    依据是错的**：查的是 ``osint_knowledge_unit.entities`` 的原始值，而算子
    在 :meth:`StoryGrouper._entity_keys_for` 里已经过别名归一，真机日志显示
    两侧都是 ``['smic']``/``['longi']``/``['eu']``——实体门根本没拦住它们。

    结论：放宽护栏的前提不成立，通道已删除。教训是**别拿库里的原始字段
    推断运行时行为**，中间可能隔着一层归一。

    判定抽成纯函数是为了让准入逻辑能被单测逐条钉住——写进 ``run()`` 的 if
    里就只能靠端到端跑，出问题时分不清是护栏错还是取数错。
    """
    if mode != "vector":
        return False, "text_mode_not_allowed"
    if not shared_entities:
        return False, "no_shared_entity"
    if similarity < CROSS_WINDOW_MIN_SIMILARITY:
        return False, "below_min_similarity"
    return True, "entity+similarity"


def resolve_recheck_similarity() -> float:
    """解析高置信直判同阈值：配置优先，配置层不可用时回落 :data:`RECHECK_SIMILARITY`。

    为什么不让调用方直接读常量：阈值是**运营口径**，不是代码常量。上线后
    发现误合并抬头时要能在不改代码、不发版的前提下回退到 0.85，所以走
    ``config.settings``（环境变量 ``STORY_RECHECK_SIMILARITY``）。

    为什么不放进模块级一次性读取：``get_settings`` 有 ``lru_cache``，重复调用
    几乎零成本；而模块级读取会把配置错误固化进 import 阶段——一旦阈值被配成
    非法值，整个进程都起不来。这里选择**降级而非崩溃**：配置读不到就用常量，
    并留 warn，聚类不该因为一个阈值配置项而停摆。

    越界值（<0 或 >1）同样回落常量：``ge=0/le=1`` 已在 pydantic 校验，但
    ``get_settings`` 可能被绕过（直接构造 Settings 或测试替身），故二次兜底。
    """
    try:
        from config.settings import get_settings  # 局部导入：算子不为配置层硬依赖

        value = float(get_settings().story_recheck_similarity)
    except Exception as exc:  # noqa: BLE001 - 配置不可用不阻断聚类
        logger.debug("读取 story_recheck_similarity 失败，改用模块常量：%s", exc)
        return RECHECK_SIMILARITY
    if not 0.0 <= value <= 1.0:
        logger.warning(
            "story_recheck_similarity=%s 越界（应在 0~1），改用模块常量 %s",
            value,
            RECHECK_SIMILARITY,
        )
        return RECHECK_SIMILARITY
    return value


def resolve_borderline_margin() -> float:
    """解析临界复核带宽：配置优先，配置层不可用时回落 :data:`BORDERLINE_MARGIN`。

    为什么必须可配：带宽是**语料属性**，不是代码常量。2026-11-04 在 42 条真值
    语料上标定出 0.05，但该结论只对当前语料成立——语料换成以"同一主体多类
    事项"为主时，唯一那对真同事件（cos=0.7133）离门槛只有 0.0367，余量仅
    0.0133；一旦新语料出现更低的真同事件对，带宽就得跟着放宽，而那不该等
    一次发版。

    **不 clamp 到可达上限**：上限是 ``直判门槛 - RECALL_SIMILARITY``，门槛本身
    也可被 ``STORY_RECHECK_SIMILARITY`` 覆盖，在这里算出来的上限可能与运行时
    不同。clamp 会悄悄改掉用户填的数，让人以为"配了 0.20 就覆盖到 0.55"。
    改为 warn：明确指出超出部分无效，值仍按用户填的用——多出的部分自然无效，
    因为更低的相似度根本进不了判定环节。

    越界（<0 或 >0.5）同样回落常量并 warn：与 :func:`resolve_recheck_similarity`
    同一套"降级而非崩溃"口径，聚类不该因为一个带宽配置项停摆。
    """
    try:
        from config.settings import get_settings  # 局部导入：算子不为配置层硬依赖

        value = float(get_settings().story_borderline_margin)
    except Exception as exc:  # noqa: BLE001 - 配置不可用不阻断聚类
        logger.debug("读取 story_borderline_margin 失败，改用模块常量：%s", exc)
        return BORDERLINE_MARGIN
    if not 0.0 <= value <= 0.5:
        logger.warning(
            "story_borderline_margin=%s 越界（应在 0~0.5），改用模块常量 %s",
            value,
            BORDERLINE_MARGIN,
        )
        return BORDERLINE_MARGIN
    reachable = RECHECK_SIMILARITY - RECALL_SIMILARITY
    if value > reachable + 1e-9:
        logger.warning(
            "story_borderline_margin=%.3f 超过当前可达上限 %.3f"
            "（直判门槛 %.2f - 召回门 %.2f），超出部分无效："
            "相似度低于召回门的配对在进判定环节前已被拦掉",
            value,
            reachable,
            RECHECK_SIMILARITY,
            RECALL_SIMILARITY,
        )
    return value


# ------------------------------------------------------------ 数据结构 --
@dataclass(frozen=True, slots=True)
class Verdict:
    """一次归属判定的结果。

    ``failed`` 是判定器的**故障信号**，不是判定结论：为 True 时 ``relation``
    字段无意义，调用方必须回落到下一层判定器并把本轮标记为 degraded。

    这条约束来自一次真实故障形态——判定后端掉线时若返回 ``unrelated``，
    外部表现就是"事件莫名变少"，与嵌入层降级完全同形，运维无法区分
    "今天没新闻"和"判定器挂了"。
    """

    relation: str  # same_story / progress / unrelated / roundup
    decided_by: str  # vector / llm / llm_recheck / rule / manual
    rationale: str
    failed: bool = False


@dataclass
class GroupResult:
    """一次聚类运行的统计回报（供日志与验收断言使用）。

    ``degraded`` 是**必须监控**的字段：嵌入层不可用时会静默降级为文本相似
    度，合并率会显著下降但不报错。若无此标志，运维只会看到"事件变少了"，
    误判为数据不足而非故障。
    """

    units_scanned: int = 0
    stories_created: int = 0
    #: 复用既有 story（同 ``story_key`` 已存在）的次数。删成员边重算时应当
    #: 全落到这里，而 ``stories_created`` 保持 0——两者分开才看得出"重算"。
    stories_reused: int = 0
    members_added: int = 0
    units_skipped_existing: int = 0
    unrelated: int = 0
    judge_calls: int = 0
    judge_failures: int = 0
    #: 跨窗口召回加载的锚点数（窗口外已归属单元）。为 0 说明跨窗口未开启，
    #: 或窗外 42 天内确实没有已归属历史——两者要靠这个数字区分。
    anchors_loaded: int = 0
    #: 本轮经跨窗口召回并入既有事件的事实数。
    cross_window_merged: int = 0
    #: 落在 ``[门槛-BORDERLINE_MARGIN, 门槛)`` 且最终**未合并**的配对数。
    #:
    #: 这是调阈值唯一靠得住的依据：该带内真同事件多 → 门槛偏高；无关事实多 →
    #: 门槛偏低。只看 ``unrelated`` 总数是分不出来的。
    borderline: int = 0
    #: 临界配对的样本（上限 ``BORDERLINE_SAMPLE_LIMIT``），字段见
    #: :meth:`note_borderline`。
    borderline_samples: list[dict[str, Any]] = field(default_factory=list)
    #: 临界带内**真正送去 LLM 复核**的配对数（去重后）。为 0 有两种可能：
    #: 临界带为空，或本轮没开 ``borderline_judge``——两者要靠 ``borderline``
    #: 与 ``borderline_rechecked`` 一起看才分得清。
    borderline_rechecked: int = 0
    #: 复核后被改判为同事件并**实际合并**的配对数。这就是"临界带开 LLM"
    #: 的直接收益，分母是 ``borderline_rechecked``。
    borderline_promoted: int = 0
    #: 被复核提升的配对样本（上限 ``BORDERLINE_SAMPLE_LIMIT``）。供人工确认：
    #: 这份清单是"LLM 越过门槛合了，但还在门槛下方 0.05 内"的全部配对，
    #: 也是误合并风险最集中的地方。
    borderline_promoted_samples: list[dict[str, Any]] = field(default_factory=list)
    #: 人工裁决命中的配对数（台账命中，无论合还是不合）。非 0 说明本轮结果
    #: 已被人工干预过，与"纯自动跑出来的"不可直接对比。
    manual_overrides: int = 0
    #: 配对级观测：**每一对**被算过相似度并进入判定环节的候选，字段见
    #: :meth:`note_pair`。仅在 ``StoryGrouper(trace_pairs=True)`` 时收集，
    #: 否则恒为空——这不是"省点内存"，而是刻意让默认路径不产生任何额外对象，
    #: 十万级语料下每轮多存几万条 dict 会把常驻内存推高一个量级。
    pairs_observed: list[dict[str, Any]] = field(default_factory=list)
    #: ``pairs_observed`` 是否被 :data:`PAIR_TRACE_LIMIT` 截断。**必须外露**：
    #: 标定脚本据此拒绝给结论，否则截断后的样本会被当成全量样本算出错误的
    #: "带宽 X 下真该合对数"。
    pairs_observed_truncated: bool = False
    #: 已记录的配对（无序对），用于去重。**必须去重**：同一对事实会在双向各
    #: 被评估一次（A 作为 pivot 时见过 B，B 作为 pivot 时又见 A），不去重会把
    #: 临界规模记成两倍，运维据此判断"门槛偏低"就会做出反向决策。
    _borderline_seen: set[tuple[str, str]] = field(
        default_factory=set, repr=False, compare=False
    )
    #: 已记录的"复核提升"配对（无序对），用途同上，但记的是另一类事件：
    #: 这对**合了**，不能进 ``_borderline_seen``（那里只记未合并的）。
    _promoted_seen: set[tuple[str, str]] = field(
        default_factory=set, repr=False, compare=False
    )
    dry_run: bool = False
    elapsed_ms: int = 0
    degraded: bool = False
    degrade_reason: str | None = None

    def tally_story(self, created: bool) -> None:
        """记一次"建成或复用事件"。两者分开计数：删成员边重跑时应当全是复用。"""
        if created:
            self.stories_created += 1
        else:
            self.stories_reused += 1

    def note_borderline(
        self,
        *,
        unit: Mapping[str, Any],
        target: Mapping[str, Any],
        similarity: float,
        threshold: float,
        mode: str,
        decided_by: str,
        rationale: str,
    ) -> None:
        """记录一对"差一点就合并"的临界配对。

        只在**最终未合并**时调用——已合并的不叫临界。样本里同时保留两侧标题与
        判定来源，因为看这条清单的人要回答的问题是"这对该不该合"，只给相似度
        是没法判断的。

        计数与样本分开：``borderline`` 是真实总数，``borderline_samples`` 受
        上限约束。只看样本条数会系统性低估临界规模。

        同一对**只记一次**：一对事实会在双向各被评估一遍（A 作 pivot 时见过
        B，B 作 pivot 时又见 A），不去重就会把临界规模记成两倍。
        """
        left = str(unit.get("unit_key") or "")
        right = str(target.get("unit_key") or "")
        pair_key = unordered_pair(left, right)
        if pair_key in self._borderline_seen:
            return
        self._borderline_seen.add(pair_key)

        self.borderline += 1
        if len(self.borderline_samples) >= BORDERLINE_SAMPLE_LIMIT:
            return
        self.borderline_samples.append(
            {
                "left": left,
                "right": right,
                "similarity": round(similarity, 4),
                "threshold": round(threshold, 4),
                "gap": round(threshold - similarity, 4),
                "mode": mode,
                "decided_by": decided_by,
                "rationale": rationale,
                "left_title": str(unit.get("title") or "")[:60],
                "right_title": str(target.get("title") or "")[:60],
            }
        )

    def note_promoted(
        self,
        *,
        unit: Mapping[str, Any],
        target: Mapping[str, Any],
        similarity: float,
        threshold: float,
        mode: str,
        rationale: str,
    ) -> None:
        """记录一对"被临界复核抬过门槛、最终合并了"的配对。

        与 :meth:`note_borderline` 互为镜像：那边记"差一点没合"，这边记"差一点
        没合、但被 LLM 抬进去了"。**两份清单都要有**——只看未合并的那份，会
        得出"门槛偏高"的结论；只看合并的这份，会得出"门槛偏低"的结论。真实
        情况必须两边一起看才判得准。

        这份清单是误合并风险最集中的地方：它在数学上明确低于直判门槛，是被
        判定器"越权"拉进去的。生产环境应当由人过一遍，把结论写进台账。

        同样按无序对去重，理由与 :meth:`note_borderline` 一致。
        """
        left = str(unit.get("unit_key") or "")
        right = str(target.get("unit_key") or "")
        pair_key = unordered_pair(left, right)
        if pair_key in self._promoted_seen:
            return
        self._promoted_seen.add(pair_key)

        # 计数放在样本上限之外：样本只有 20 条，计数必须全量，否则临界带的
        # 真实规模会被上限压成 20，据此判断门槛方向一定是错的。
        self.borderline_promoted += 1
        if len(self.borderline_promoted_samples) >= BORDERLINE_SAMPLE_LIMIT:
            return
        self.borderline_promoted_samples.append(
            {
                "left": left,
                "right": right,
                "similarity": round(similarity, 4),
                "threshold": round(threshold, 4),
                "gap": round(threshold - similarity, 4),
                "mode": mode,
                "decided_by": BORDERLINE_RECHECK_DECIDED_BY,
                "rationale": rationale,
                "left_title": str(unit.get("title") or "")[:60],
                "right_title": str(target.get("title") or "")[:60],
            }
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "units_scanned": self.units_scanned,
            "stories_created": self.stories_created,
            "stories_reused": self.stories_reused,
            "members_added": self.members_added,
            "units_skipped_existing": self.units_skipped_existing,
            "unrelated": self.unrelated,
            "judge_calls": self.judge_calls,
            "judge_failures": self.judge_failures,
            "anchors_loaded": self.anchors_loaded,
            "cross_window_merged": self.cross_window_merged,
            "borderline": self.borderline,
            "borderline_samples": self.borderline_samples,
            "borderline_rechecked": self.borderline_rechecked,
            "borderline_promoted": self.borderline_promoted,
            "borderline_promoted_samples": self.borderline_promoted_samples,
            "manual_overrides": self.manual_overrides,
            "pairs_observed": len(self.pairs_observed),
            "pairs_observed_truncated": self.pairs_observed_truncated,
            "dry_run": self.dry_run,
            "elapsed_ms": self.elapsed_ms,
            "degraded": self.degraded,
            "degrade_reason": self.degrade_reason,
        }


class EmbedderPort(Protocol):
    """嵌入端口（``knowledge.embedder.Embedder`` 满足该协议）。"""

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


# ------------------------------------------------------------ 文本工具 --

_NORM_RE = re.compile(r"[\s\u3000]+")
_PUNCT_RE = re.compile(
    r"[，。！？、；：“”‘’（）《》【】…—·,.!?;:\"'()\[\]{}<>|/\\@#$%^&*_+=~`\-]+"
)
_DIGIT_RE = re.compile(r"\d+")


def normalize_text(text: str | None) -> str:
    """归一化文本：全角空白折叠、标点剥离、数字归一为 ``#``。

    数字归一是有意为之：同一事件里"涨 3.2%"与"涨 3.5%"应视为高度相似，
    若保留原数字会把两个二元组拆散，降低 Jaccard。
    """
    if not text:
        return ""
    value = _PUNCT_RE.sub("", _NORM_RE.sub(" ", text)).strip().lower()
    return _DIGIT_RE.sub("#", value)


def char_bigrams(text: str) -> set[str]:
    """字符二元组集合。中文无需分词即可获得可用的相似度信号。"""
    norm = normalize_text(text)
    if len(norm) < 2:
        return {norm} if norm else set()
    return {norm[i : i + 2] for i in range(len(norm) - 1)}


def jaccard(left: set[str], right: set[str]) -> float:
    """集合 Jaccard 相似度；任一为空时返回 0.0。"""
    if not left or not right:
        return 0.0
    inter = len(left & right)
    union = len(left | right)
    return inter / union if union else 0.0


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """余弦相似度（纯 Python，避免为此引入 numpy 依赖）。"""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    lnorm = 0.0
    rnorm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        lnorm += a * a
        rnorm += b * b
    if lnorm <= 0.0 or rnorm <= 0.0:
        return 0.0
    return dot / (math.sqrt(lnorm) * math.sqrt(rnorm))


def _as_list(value: Any) -> list[Any]:
    """把 JSONB / 字符串 / None 统一成 list（PG 驱动可能返回 str）。"""
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []
    return list(value) if isinstance(value, (list, tuple, set)) else []


def entity_keys(unit: Mapping[str, Any]) -> set[str]:
    """从 entities JSONB 抽出可比较的实体键。

    兼容两种形态：``["宁德时代", ...]`` 与 ``[{"name": "宁德时代"}, ...]``。
    """
    keys: set[str] = set()
    for item in _as_list(unit.get("entities")):
        if isinstance(item, str):
            candidate = item
        elif isinstance(item, Mapping):
            candidate = str(
                item.get("name") or item.get("canonical") or item.get("id") or ""
            )
        else:
            candidate = ""
        norm = normalize_text(candidate)
        if norm:
            keys.add(norm)
    return keys


def topic_keys(unit: Mapping[str, Any]) -> set[str]:
    """从 topics JSONB 抽出主题键（用于本地倒排召回）。"""
    keys: set[str] = set()
    for item in _as_list(unit.get("topics")):
        if isinstance(item, str):
            norm = normalize_text(item)
        elif isinstance(item, Mapping):
            norm = normalize_text(str(item.get("name") or item.get("id") or ""))
        else:
            norm = ""
        if norm:
            keys.add(norm)
    return keys


def unordered_pair(left: Any, right: Any) -> tuple[str, str]:
    """把两个 ``unit_key`` 归一成**无序对**，作为配对级去重与人工台账的键。

    为什么必须无序：同一对事实会在两个方向各被评估一次（A 作 pivot 时见过 B，
    B 作 pivot 时又见 A）。用有向对做键，``borderline`` 会记成两倍、台账会命中
    一半，运维据此判断门槛方向就会得出反向结论——2026-11-04 临界观测就是先
    踩了这个坑才加的 ``_borderline_seen``。

    排序用字符串字典序而非哈希：必须**跨进程稳定**，否则台账文件换个解释器
    就跑不通（``hash()`` 受 ``PYTHONHASHSEED`` 影响）。
    """
    a, b = str(left or ""), str(right or "")
    return (a, b) if a <= b else (b, a)


def pair_record(
    unit: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    similarity: float,
    threshold: float,
    mode: str,
    relation: str,
    decided_by: str,
) -> dict[str, Any]:
    """构造一条配对级观测记录（``trace_pairs=True`` 时使用）。

    与 ``note_borderline`` 的样本是**互补而非重复**的两份数据：那个是"此刻
    带宽内未合并的配对"，这个是"每一次 sim→verdict 的全集"。标定带宽只能用
    后者——只有拿到全集，才能回答"把带宽从 0.05 放到 0.08 会新增哪些配对、
    其中几对真该合"，而不必改一次常量重跑一次库。

    条目里同时存两侧 ``unit_key`` 与标题：前者让标定脚本能 join 真值表，后者
    让人在明细里一眼看出是哪两条新闻——只有 key 的话，看到"某对 sim=0.68 真
    该合"根本无从判断是不是标注错误。

    键强制成无序对：同一对会在两个方向各判一次，有向键会让标定把同一对算两遍，
    进而把"该带宽下真该合 2 对"报成 4 对。
    """
    a, b = unordered_pair(unit.get("unit_key"), target.get("unit_key"))
    return {
        "left": a,
        "right": b,
        "similarity": round(similarity, 4),
        "threshold": round(threshold, 4),
        "mode": mode,
        "relation": relation,
        "decided_by": decided_by,
        "left_title": str(unit.get("title") or "")[:60],
        "right_title": str(target.get("title") or "")[:60],
    }


def extract_source_id(unit: Mapping[str, Any]) -> str:
    """推导来源身份：媒体域名优先，其次 blogger_key，兜底 ``unknown``。

    "同一家媒体发 5 篇算 5 个来源"是独立来源数最常见的失真来源，所以必须
    先按域名收敛（去掉 ``www.`` 与 ``m.`` 前缀），再退回账号维度。
    """
    raw_url = str(unit.get("source_url") or unit.get("source_link") or "")
    if raw_url:
        match = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", raw_url.strip())
        host = (match.group(1) if match else raw_url.split("/")[0]).lower()
        host = host.split(":")[0]
        for prefix in ("www.", "m.", "wap."):
            if host.startswith(prefix):
                host = host[len(prefix) :]
                break
        if host and "." in host:
            return host
    blogger = str(unit.get("blogger_key") or "").strip()
    if blogger:
        return f"blogger:{blogger}"
    return "unknown"


def compute_heat(independent_sources: int, last_seen: datetime, now: datetime) -> float:
    """热度 = 独立来源数 × exp(-Δ小时 / 48)。"""
    age_sec = max((now - last_seen).total_seconds(), 0.0)
    return round(independent_sources * math.exp(-age_sec / HEAT_TAU_SEC), 4)


# -------------------------------------------------------------- 判定器 --


class RelationJudge(Protocol):
    """归属判定端口。生产可接 LLM，缺省用 :class:`RuleJudge`。"""

    name: str

    def judge(
        self,
        unit: Mapping[str, Any],
        target: Mapping[str, Any],
        similarity: float,
    ) -> Verdict: ...


class RuleJudge:
    """可解释的规则判定器（零依赖、确定性、可单测）。

    仅用于余弦落在复核区间的样本。**判定依据只看文本证据**（字符二元组
    Jaccard），达不到阈值一律 ``unrelated``。

    关于实体重合（v2 修订，2026-11-04）
    ------------------------------------
    v1 曾在"实体交集比例 ≥ :data:`RULE_ENTITY_OVERLAP`"时直接判 ``same_story``，
    注释声称"宁可漏合不可误合"，实测行为恰好相反：该比例的分母是**较小实体集
    的大小**，于是单实体事实恒为 ``1/1 = 1.0``，规则退化成"只要共享任一实体就
    是同一件事"。

    在 50 条带真值的验收语料上，这一条独立制造了 6 处误合并（同一家公司的不
    同事件被并成一个事件：英伟达显卡发布并进财报、茅台提价并进三季报、特斯拉
    召回并进高管减持……）。这 6 对的文本 Jaccard 全部只有 0.05–0.14，证据极
    弱，却被实体规则强行拉进同一事件，并直接把 ``independent_sources`` 抬高。

    故 v2 起实体重合**只用于候选召回**（见 ``StoryGrouper._candidates``），
    绝不单独构成判定结论。判不了就返回 ``unrelated``，把判断权交给 LLM 判定器
    或人工复核——漏合可以补，误合并几乎无法事后发现。
    """

    name = "rule-v2"

    def judge(
        self,
        unit: Mapping[str, Any],
        target: Mapping[str, Any],
        similarity: float,
    ) -> Verdict:
        left_text = f"{unit.get('title') or ''} {unit.get('content') or ''}"
        right_text = f"{target.get('title') or ''} {target.get('content') or ''}"
        text_sim = jaccard(char_bigrams(left_text), char_bigrams(right_text))

        left_entities = entity_keys(unit)
        right_entities = entity_keys(target)
        smaller = min(len(left_entities), len(right_entities))
        ent_overlap = (
            len(left_entities & right_entities) / smaller if smaller else 0.0
        )

        # 只有文本证据够硬才判同事件。实体重合写进理由仅供人工复核时参考，
        # 不作为判定条件——理由见类 docstring 的 v2 修订说明。
        if text_sim >= RULE_JACCARD:
            return Verdict(
                "same_story",
                "rule",
                f"rule:text_jaccard={text_sim:.3f}>={RULE_JACCARD}"
                f"(entity_overlap={ent_overlap:.3f})",
            )
        return Verdict(
            "unrelated",
            "rule",
            f"rule:text_jaccard={text_sim:.3f}<{RULE_JACCARD}"
            f",entity_overlap={ent_overlap:.3f}(不单独构成同事件)",
        )


# ------------------------------------------------------------------ SQL --

_SELECT_UNITS = """
SELECT u.unit_key,
       u.unit_version,
       u.title,
       u.content,
       u.blogger_key,
       u.resource_key,
       u.source_url,
       u.source_link,
       u.entities,
       u.topics,
       u.review_status,
       u.created_at
  FROM osint_knowledge_unit u
 WHERE u.status = 'active'
   AND (u.unit_type IS NULL OR u.unit_type = 'fact')
   AND u.created_at >= now() - make_interval(days => :days)
   AND (CAST(:only_reviewed AS boolean) IS NOT TRUE OR u.review_status = 'reviewed')
   AND NOT EXISTS (
         SELECT 1
           FROM osint_story_member m
          WHERE m.unit_key = u.unit_key
            AND m.unit_version = u.unit_version
       )
 ORDER BY u.created_at ASC, u.unit_key ASC
 LIMIT :lim
"""

_SELECT_ANCHORS = """
SELECT u.unit_key,
       u.unit_version,
       u.title,
       u.content,
       u.blogger_key,
       u.resource_key,
       u.source_url,
       u.source_link,
       u.entities,
       u.topics,
       u.created_at,
       m.story_id
  FROM osint_knowledge_unit u
  JOIN osint_story_member m
    ON m.unit_key = u.unit_key
   AND m.unit_version = u.unit_version
 WHERE u.status = 'active'
   AND (u.unit_type IS NULL OR u.unit_type = 'fact')
   AND u.created_at >= now() - make_interval(days => :far_days)
   AND u.created_at <  now() - make_interval(days => :days)
   AND u.unit_key > :last_key
 ORDER BY u.unit_key ASC
 LIMIT :lim
"""

_SELECT_STORY_BY_UNIT_KEYS = """
SELECT m.unit_key, m.unit_version, m.story_id
  FROM osint_story_member m
 WHERE m.unit_key = ANY(CAST(:keys AS text[]))
"""

_SELECT_MEMBER_UNIT = """
SELECT u.unit_key, u.unit_version, u.title, u.content, u.source_url, u.source_link,
       u.blogger_key, u.entities, u.topics
  FROM osint_knowledge_unit u
 WHERE u.unit_key = ANY(CAST(:keys AS text[]))
"""

_INSERT_STORY = """
INSERT INTO osint_story (
    story_key, title, summary, first_seen_at, last_seen_at,
    independent_sources, source_names, fact_count, heat, stage,
    entities, topics, confidence, model_version
) VALUES (
    :story_key, :title, :summary, :first_seen_at, :last_seen_at,
    1, CAST(:source_names AS jsonb), 1, 0, 'emerging',
    CAST(:entities AS jsonb), CAST(:topics AS jsonb), :confidence, :model_version
)
ON CONFLICT (story_key) DO UPDATE
   SET title         = EXCLUDED.title,
       summary       = EXCLUDED.summary,
       first_seen_at = EXCLUDED.first_seen_at,
       last_seen_at  = EXCLUDED.last_seen_at,
       entities      = EXCLUDED.entities,
       topics        = EXCLUDED.topics,
       model_version = EXCLUDED.model_version,
       updated_at    = now()
RETURNING story_id, (xmax = 0) AS inserted
"""

_INSERT_MEMBER = """
INSERT INTO osint_story_member (
    unit_key, unit_version, story_id, source_id, relation, similarity, decided_by, rationale
) VALUES (
    :unit_key, :unit_version, :story_id, :source_id, :relation, :similarity, :decided_by, :rationale
)
ON CONFLICT (unit_key, unit_version) DO NOTHING
"""

_REFRESH_STATS = """
UPDATE osint_story s
   SET fact_count          = agg.cnt,
       independent_sources = agg.src_cnt,
       first_seen_at       = agg.first_at,
       last_seen_at        = agg.last_at,
       heat                = ROUND(
           (agg.src_cnt * exp(
               -GREATEST(EXTRACT(EPOCH FROM (now() - agg.last_at))::double precision, 0)
               / 172800.0
           ))::numeric, 4),
       stage = CASE
           WHEN agg.last_at < now() - interval '7 days'  THEN 'closed'
           WHEN agg.last_at < now() - interval '48 hours' THEN 'fading'
           WHEN agg.cnt > 1 AND agg.src_cnt >= 3
                AND agg.last_at > now() - interval '24 hours' THEN 'peak'
           WHEN agg.cnt > 1 THEN 'developing'
           ELSE 'emerging'
       END,
       updated_at = now()
  FROM (
        SELECT m.story_id,
               count(*)                                            AS cnt,
               count(DISTINCT COALESCE(NULLIF(m.source_id, ''), 'unknown')) AS src_cnt,
               min(u.created_at)                                   AS first_at,
               max(u.created_at)                                   AS last_at
          FROM osint_story_member m
          JOIN osint_knowledge_unit u
            ON u.unit_key = m.unit_key
           AND u.unit_version = m.unit_version
         WHERE m.story_id = ANY(CAST(:story_ids AS bigint[]))
         GROUP BY m.story_id
       ) agg
 WHERE s.story_id = agg.story_id
"""


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _row_ts(row: Mapping[str, Any]) -> datetime:
    """取行的 ``created_at`` 并统一成带时区的 datetime，缺失时返回纪元起点。

    为什么要统一：驱动给的可能是 ``aware`` 也可能是 ``naive``，跨窗口候选要按
    时间挑"最新成员"，naive 与 aware 直接比较会抛 ``TypeError``。缺失时间戳
    的行走纪元起点——它只影响"谁是代表"的选择，不影响是否合并。
    """
    value = row.get("created_at")
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return _EPOCH


def _story_key(source_id: str, unit_key: str) -> str:
    """由种子单元推导稳定事件键（同一种子重跑得到同一 key）。"""
    digest = hashlib.sha1(f"{source_id}|{unit_key}".encode("utf-8")).hexdigest()
    return f"st_{digest[:16]}"


# ------------------------------------------------------------ 主算子 --


@dataclass
class _UnitRef:
    """算子内部使用的单元视图（已预算向量与派生属性）。"""

    key: str
    version: int
    row: Mapping[str, Any]
    source_id: str
    vector: list[float] | None = None
    entities: set[str] = field(default_factory=set)
    topics: set[str] = field(default_factory=set)
    assigned_story: int | None = None


class StoryGrouper:
    """把窗口内尚未归属的事实聚成事件。

    典型用法::

        grouper = StoryGrouper()               # 生产：自动装配 embedder/index
        result = grouper.run(window_days=14, only_reviewed=True)
        print(result.as_dict())
    """

    def __init__(
        self,
        *,
        embedder: EmbedderPort | None = None,
        judge: RelationJudge | None = None,
        fallback_judge: RelationJudge | None = None,
        borderline_judge: RelationJudge | None = None,
        borderline_overrides: Mapping[tuple[str, str], bool] | None = None,
        alias_map: Mapping[str, str] | None = None,
        recheck_similarity: float | None = None,
        borderline_margin: float | None = None,
        cross_window: bool = False,
        trace_pairs: bool = False,
    ) -> None:
        self._embedder = embedder
        self.judge: RelationJudge = judge or RuleJudge()
        #: 判定器故障时的回落目标。恒定是规则判定——它零依赖、确定性，
        #: 且偏向"宁可漏合不可误合"，用作兜底不会污染独立来源数。
        self.fallback_judge: RelationJudge = fallback_judge or RuleJudge()
        #: **只在临界带** ``[门槛-BORDERLINE_MARGIN, 门槛)`` 调用的复核判定器。
        #:
        #: 与 ``judge`` 分开而不是复用同一个，是因为两者的调用量差了整整一个
        #: 数量级：``judge`` 覆盖 ``[召回门, 门槛)`` 全域（真机 ~120 对），
        #: ``borderline_judge`` 只覆盖最上面 0.05 那一段（真机 2 对）。把复核
        #: 能力压在这 2 对上，是本机 iGPU 与生产机纯 CPU 都付得起的代价；全区间
        #: 开 LLM 在生产机要 40-60 分钟，只能离线做。
        #:
        #: 为 None 时临界带沿用 ``judge`` 的结论（P0/P1 默认行为，零额外调用）。
        self.borderline_judge: RelationJudge | None = borderline_judge
        #: 人工裁决台账 ``{无序对: 是否同事件}``。**只对临界带生效**。
        #:
        #: 刻意不放开到全域：``sim>=门槛`` 的配对已由余弦直判，``sim<门槛-0.05``
        #: 的配对证据太弱，人工一个笔误就能把明显无关的两条事实并成一个事件，
        #: 而误合并会直接污染 ``independent_sources`` 且事后极难发现。人工只在
        #: "机器也拿不准"的那条窄带上拥有否决权，这与复核本身的意义一致。
        #:
        #: 构造时**强制归一成无序对**：调用方（脚本/单测/台账文件）很可能按
        #: 自己看到的顺序传 ``("b","a")``，而运行时配对是 ``("a","b")``。不归一
        #: 的话台账只在一半情况下命中，外部表现是"人工改的规则时灵时不灵"——
        #: 这种故障没有任何报错，只能靠人反复试出来。
        self.borderline_overrides: Mapping[tuple[str, str], bool] = self._normalize_overrides(
            borderline_overrides
        )
        #: 本轮临界复核的结论缓存 ``{无序对: Verdict}``。
        #:
        #: 三个作用：① 去重计数（同一对会在双向各评估一次）；② 复用结论，
        #: 避免同一对打两次 LLM；③ 让 ``borderline_rechecked`` 与
        #: ``borderline_promoted`` 两个数口径一致（同一批配对）。
        self._recheck_outcomes: dict[tuple[str, str], Verdict] = {}
        #: 送去复核过的配对（含失败）。计数用，与 ``_recheck_outcomes`` 分开是
        #: 因为失败对不缓存结论，但**必须算进复核量**。
        self._recheck_seen: set[tuple[str, str]] = set()
        #: 复核器已确认失效的配对。只用于避免重复白等一个超时——不是判定结论。
        self._recheck_failed: set[tuple[str, str]] = set()
        #: 本轮命中人工台账的配对数。
        self._manual_hits = 0
        #: 已命中台账的配对（去重）。同一对双向各评估一次，不去重会记成两倍。
        self._manual_seen: set[tuple[str, str]] = set()
        self._embedder_ready = embedder is not None
        self._judge_failures = 0
        self._judge_failure_reason: str | None = None
        #: 实体别名映射 ``{alias_norm: canonical_id}``。显式传入便于单测；
        #: 传 None 时在首轮惰性从 ``osint_entity_alias`` 装载。
        self._alias_map: Mapping[str, str] | None = alias_map
        #: 高置信直判同阈值。``None`` = 每轮运行时从配置解析（默认 0.75，
        #: 可由 ``STORY_RECHECK_SIMILARITY`` 覆盖）。显式传值仅用于标定脚本
        #: 与单测，生产不要传——否则配置项会被静默忽略。
        self._recheck_similarity: float | None = recheck_similarity
        #: 临界复核带宽。``None`` = 每轮从配置解析（默认 0.05，可由
        #: ``STORY_BORDERLINE_MARGIN`` 覆盖）。显式传值仅用于标定与单测，
        #: 生产不要传——否则配置项会被静默忽略。
        self._borderline_margin: float | None = borderline_margin
        #: 跨窗口召回开关。默认关闭：放宽召回必然引入误合并，而误合并会污染
        #: ``independent_sources`` 且事后极难发现（详见 :meth:`_cross_candidates`）。
        #: 打开前必须先在真值语料上跑 ``scripts/eval_story_quality.py`` 确认
        #: 拆散下降幅度大于误合并增幅。
        self.cross_window: bool = bool(cross_window)
        #: 配对级观测开关。**默认关闭**，关闭时不多存一个 dict，生产路径零影响。
        #:
        #: 只有标定带宽（``scripts/calibrate_borderline_margin.py``）与排查
        #: "某对明明很像却没合"时才打开。打开后 ``GroupResult.pairs_observed``
        #: 会积累本轮全部候选对，上限 :data:`PAIR_TRACE_LIMIT`。
        self.trace_pairs: bool = bool(trace_pairs)
        #: 配对观测缓冲区。**每轮开始清空**，收尾时整体交给 ``GroupResult``。
        #: 放在算子上而不是 ``GroupResult`` 上，是因为 :meth:`_decide` 拿不到
        #: result——它被两路召回各调一次，签名里塞 result 会污染判定链路。
        self._pair_trace: list[dict[str, Any]] = []
        self._pair_trace_truncated: bool = False

    # ---------------------------------------------------- 惰性装配 --

    @staticmethod
    def _normalize_overrides(
        overrides: Mapping[tuple[str, str], bool] | None,
    ) -> dict[tuple[str, str], bool]:
        """把台账键归一成无序对；非法键记 warn 后跳过，不影响其余条目。

        不抛异常是刻意的：台账是手写 JSON，一条写坏不该让整轮聚类跑不起来。
        这一点与 ``knowledge.borderline_review.load_overrides`` 的分工一致——
        那边负责"文件级损坏必须报错"，这边负责"单条键写坏不要连坐"。
        """
        normalized: dict[tuple[str, str], bool] = {}
        for pair, merge in (overrides or {}).items():
            try:
                left, right = pair  # type: ignore[misc]
            except (TypeError, ValueError):
                logger.warning("忽略一条格式非法的台账键（非二元组）：%r", pair)
                continue
            normalized[unordered_pair(left, right)] = bool(merge)
        return normalized

    def _get_alias_map(self) -> Mapping[str, str]:
        """惰性装载实体别名表；不可用时返回空映射，不影响主流程。

        别名归一是**增强**而非前置条件：表缺失/读失败时退回按原文实体聚类
        （P0 行为），只降低合并率，不会误合并——因此不置 degraded。但必须
        留 warn，否则别名链路坏掉后只会表现为"来源数偏低"，无从追查。
        """
        if self._alias_map is not None:
            return self._alias_map
        try:
            # 局部导入：knowledge.entity_alias 反向依赖本模块的 entity_keys，
            # 模块级互引会形成循环。
            from knowledge.entity_alias import load_alias_map_from_db

            self._alias_map = load_alias_map_from_db()
        except Exception as exc:  # noqa: BLE001
            logger.warning("实体别名表不可用，按原文实体聚类：%s", exc)
            self._alias_map = {}
        return self._alias_map

    def _entity_keys_for(self, row: Mapping[str, Any]) -> set[str]:
        """抽实体键并做别名归一（同实体不同写法收敛到同一 canonical）。"""
        keys = entity_keys(row)
        alias_map = self._get_alias_map()
        if not keys or not alias_map:
            return keys
        from knowledge.entity_alias import resolve  # 同上的循环依赖规避

        return resolve(keys, alias_map)

    def _get_embedder(self) -> EmbedderPort | None:
        """惰性装配嵌入器；不可用时返回 None 并降级为纯文本判定。"""
        if self._embedder_ready:
            return self._embedder
        self._embedder_ready = True
        try:
            from knowledge.embedder import Embedder  # 局部导入，避免硬依赖

            self._embedder = Embedder()
        except Exception as exc:  # noqa: BLE001
            logger.warning("嵌入器不可用，降级为纯文本判定：%s", exc)
            self._embedder = None
        return self._embedder

    # -------------------------------------------------------- 取数 --

    def _load_units(
        self, window_days: int, only_reviewed: bool, limit: int
    ) -> list[_UnitRef]:
        rows = db.fetch_all(
            _SELECT_UNITS,
            {
                "days": int(window_days),
                "only_reviewed": bool(only_reviewed),
                "lim": int(limit),
            },
        )
        refs: list[_UnitRef] = []
        seen: set[tuple[str, int]] = set()
        for row in rows:
            key = str(row["unit_key"])
            version = int(row.get("unit_version") or 1)
            if (key, version) in seen:
                # 同一单元在结果集中重复出现（视图联表放大 / 上层重复投递）时
                # 只处理一次。否则第二次会因 pivot 未标记归属而再开一个事件，
                # 而 member 插入又被主键挡掉，最终留下没有成员边的孤儿事件。
                logger.warning("跳过重复单元：%s v%s", key, version)
                continue
            seen.add((key, version))
            refs.append(
                _UnitRef(
                    key=key,
                    version=version,
                    row=row,
                    source_id=extract_source_id(row),
                    entities=self._entity_keys_for(row),
                    topics=topic_keys(row),
                )
            )
        return refs

    def _load_anchors(self, window_days: int, limit: int) -> list[_UnitRef]:
        """加载窗口外、**已归属**的单元，作为跨窗口召回的锚点。

        只取已归属的：窗口外的未归属单元没有 story 可挂，拿来做锚点毫无意义
        （它们本该在窗口内那轮就处理掉；落在窗外说明当时被 ``limit`` 截断或
        未复核，属于另一类问题，不能用跨窗口召回掩盖）。

        按 ``unit_key`` 游标分页而不是 ``OFFSET``：窗口外历史是十万级表的
        主要部分，``OFFSET`` 翻页会随页码线性变慢。任何读失败都向上抛——跨
        窗口召回是显式开启的增强路径，静默降级成"没锚点"会让人以为"开启了
        但没效果"，比直接报错更难查。
        """
        far_days = int(window_days) * CROSS_WINDOW_DAYS_MULTIPLIER
        cap = max(0, int(limit))
        anchors: list[_UnitRef] = []
        last_key = ""
        while len(anchors) < cap:
            want = min(500, cap - len(anchors))
            rows = db.fetch_all(
                _SELECT_ANCHORS,
                {
                    "days": int(window_days),
                    "far_days": far_days,
                    "last_key": last_key,
                    "lim": want,
                },
            )
            if not rows:
                break
            last_key = str(rows[-1]["unit_key"])
            for row in rows:
                ref = _UnitRef(
                    key=str(row["unit_key"]),
                    version=int(row.get("unit_version") or 1),
                    row=row,
                    source_id=extract_source_id(row),
                    entities=self._entity_keys_for(row),
                    topics=topic_keys(row),
                )
                ref.assigned_story = int(row["story_id"])
                anchors.append(ref)
            if len(rows) < want:
                break
        return anchors

    def _cross_candidates(
        self, anchors: Sequence[_UnitRef], pivot: _UnitRef
    ) -> list[_UnitRef]:
        """从窗口外锚点里挑跨窗口候选：**只去重，不在这里筛实体**。

        实体交集曾经写在这一层做硬过滤，2026-11-04 真机运行时证明那是错的：
        三对真分裂（``SMIC``/``中芯国际``、``隆基绿能股份有限公司``/
        ``隆基绿能``、``欧盟``/``欧盟委员会``）全部因字面不一致被挡，而它们
        的余弦都在 0.90 以上。准入判断必须拿到余弦之后才能做，所以实体条件
        下沉到 :func:`cross_window_admit`，与本层的"取哪个代表"解耦。

        同 story 只保留**最新**的一个成员作代表：锚点上限 2000 条，若逐条全
        比，42 条 pivot × 2000 锚点 ≈ 8.4 万次余弦（纯 Python 约 5 秒/轮），
        而同一事件的成员彼此高度相似，取其最新者（信息最全）作代表是成本与
        精度的合理折中。
        """
        if not anchors:
            return []
        best_per_story: dict[int, _UnitRef] = {}
        for anchor in anchors:
            story_id = anchor.assigned_story
            if story_id is None:
                continue
            current = best_per_story.get(story_id)
            if current is None or _row_ts(anchor.row) > _row_ts(current.row):
                best_per_story[story_id] = anchor
        return list(best_per_story.values())

    # ---------------------------------------------------- 向量计算 --

    def _embed_units(self, refs: Sequence[_UnitRef]) -> tuple[bool, str | None]:
        """批量为单元算向量；不可用时保持 ``None``（降级为纯文本判定）。

        Returns:
            ``(是否降级, 降级原因)``。降级不抛异常——单轮失败不应中断整批，
            但必须向上回报，否则会表现为"事件数莫名变少"的静默故障。
        """
        embedder = self._get_embedder()
        if embedder is None or not refs:
            return True, "embedder_unavailable"
        texts = [self._unit_text(ref.row) for ref in refs]
        try:
            vectors = embedder.embed_texts(texts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("批量嵌入失败，本轮降级为纯文本判定：%s", exc)
            return True, f"embed_failed:{type(exc).__name__}"
        if len(vectors) != len(refs):
            logger.warning(
                "嵌入返回数量不符（%d != %d），本轮降级为纯文本判定",
                len(vectors),
                len(refs),
            )
            return True, "count_mismatch"
        for ref, vec in zip(refs, vectors):
            ref.vector = [float(item) for item in vec]
        return False, None

    @staticmethod
    def _unit_text(row: Mapping[str, Any]) -> str:
        """构造用于嵌入的文本：标题权重靠前置，正文截断控制成本。"""
        title = str(row.get("title") or "").strip()
        content = str(row.get("content") or "").strip()
        return f"{title}\n{content[:2000]}".strip()

    # ------------------------------------------------------ 召回 --

    def _candidates(
        self, refs: Sequence[_UnitRef], pivot: _UnitRef, *, assigned: bool
    ) -> list[_UnitRef]:
        """候选池：按归属状态分流，用 topic/实体倒排剪枝。

        两条召回路径是分开的：
        - ``assigned=True``：找已归属的单元，把新事实挂到既有事件上（保持
          事件连续性，避免同一件事被反复开新事件）。
        - ``assigned=False``：找另一个未归属单元，两者合并成新事件。

        当单元既无 topic 也无实体时无法倒排，退化为按时间邻近取桶内上限，
        否则冷启动阶段（实体抽取未覆盖）会完全聚不起来。
        """
        loose = not (pivot.topics or pivot.entities)
        pool: list[_UnitRef] = []
        for other in refs:
            if other.key == pivot.key:
                continue
            if (other.assigned_story is not None) != assigned:
                continue
            if not loose and not (
                pivot.topics & other.topics or pivot.entities & other.entities
            ):
                continue
            pool.append(other)
            if len(pool) >= BUCKET_LIMIT:
                break
        return pool

    def _best_candidate(
        self, pool: Sequence[_UnitRef], pivot: _UnitRef
    ) -> tuple[_UnitRef | None, float, str]:
        """在候选池中取相似度最高者，返回 ``(候选, 相似度, 口径)``。

        口径必须一起返回：向量余弦与字符 Jaccard 的阈值不可互换（见
        :data:`RULE_DIRECT_SIMILARITY`），只回数值会让调用方用错门槛。
        """
        best: _UnitRef | None = None
        best_sim = 0.0
        best_mode = "text"
        for other in pool:
            sim, mode = self._similarity_detail(pivot, other)
            if sim > best_sim:
                best, best_sim, best_mode = other, sim, mode
        return best, best_sim, best_mode

    def _similarity(self, left: _UnitRef, right: _UnitRef) -> float:
        """只要数值时的薄封装（口径见 :meth:`_similarity_detail`）。"""
        return self._similarity_detail(left, right)[0]

    def _similarity_detail(
        self, left: _UnitRef, right: _UnitRef
    ) -> tuple[float, str]:
        """优先用向量余弦；任一缺向量时退回字符二元组 Jaccard。

        返回 ``(相似度, 口径)``，口径为 ``'vector'`` 或 ``'text'``。
        """
        if left.vector and right.vector:
            return cosine(left.vector, right.vector), "vector"
        return (
            jaccard(
                char_bigrams(self._unit_text(left.row)),
                char_bigrams(self._unit_text(right.row)),
            ),
            "text",
        )

    # ------------------------------------------------------ 判定 --

    def _direct_threshold(self, mode: str) -> float:
        """直判同阈值按相似度口径取：向量用配置值，文本用独立常量。"""
        if mode != "vector":
            return RULE_DIRECT_SIMILARITY
        return (
            self._recheck_similarity
            if self._recheck_similarity is not None
            else resolve_recheck_similarity()
        )

    def _margin(self) -> float:
        """当前临界复核带宽：显式传值优先，其次配置，最后模块常量。

        两条链路（:meth:`_decide_routed` 判"要不要进复核"、:meth:`_decide_borderline`
        判"是否落在带内"）**必须取同一个数**。两处各写一次 ``BORDERLINE_MARGIN``
        时，配置覆盖只对其中一处生效，表现是"配了 0.10，带内却仍是 0.05"——
        没有任何报错，只能靠人比对两次跑批的调用数才能发现。
        """
        if self._borderline_margin is not None:
            return self._borderline_margin
        return resolve_borderline_margin()

    def _decide(
        self, unit: _UnitRef, target: _UnitRef, sim: float, mode: str = "vector"
    ) -> Verdict:
        """判定入口：取路由结论，按需记一条配对观测。

        包装一层而不是在 :meth:`_decide_routed` 的每条 return 处埋点——那边有
        5 条返回路径（直判同 / 临界复核 / 主判定器 / 故障回落 / 复核失败回落），
        分散埋点必然漏一条，而漏掉的那条恰好可能是标定最关心的（例如"故障回落
        的对，其相似度分布如何"）。
        """
        verdict = self._decide_routed(unit, target, sim, mode)
        if self.trace_pairs and len(self._pair_trace) < PAIR_TRACE_LIMIT:
            self._pair_trace.append(
                pair_record(
                    unit.row,
                    target.row,
                    similarity=sim,
                    threshold=self._direct_threshold(mode),
                    mode=mode,
                    relation=verdict.relation,
                    decided_by=verdict.decided_by,
                )
            )
        elif self.trace_pairs:
            self._pair_trace_truncated = True
        return verdict

    def _decide_routed(
        self, unit: _UnitRef, target: _UnitRef, sim: float, mode: str = "vector"
    ) -> Verdict:
        """按两层防错策略给出归属判定。

        判定器返回 ``failed=True`` 时（后端不可用 / 回复不可解析 / 预算用尽），
        **回落规则判定并计数**，绝不把故障当"判为无关"。计数在 ``run()`` 末尾
        汇总成 ``degraded``，与嵌入降级共用同一条告警通道。

        ``mode`` 决定直判同用哪个阈值——两个口径的阈值不可互换，混用会让
        文本口径在低阈值下悄悄短路判定器（丢失降级信号）。

        临界带 ``[门槛-0.05, 门槛)`` 走单独一条链路（见
        :meth:`_decide_borderline`）：先查人工台账，再交给可选的复核判定器。
        该链路返回 ``None`` 时（没配台账也没配复核器）落回本文的常规路径，
        行为与 P1 之前完全一致。
        """
        threshold = self._direct_threshold(mode)
        if sim >= threshold:
            label = "cosine" if mode == "vector" else "text_jaccard"
            decided = "vector" if mode == "vector" else "rule"
            return Verdict(
                "same_story", decided, f"{label}={sim:.3f}>={threshold}(direct)"
            )

        if sim >= threshold - self._margin():
            borderline = self._decide_borderline(unit, target, sim, threshold, mode)
            if borderline is not None:
                return borderline

        verdict = self.judge.judge(unit.row, target.row, sim)
        if not verdict.failed:
            return verdict

        self._judge_failures += 1
        if self._judge_failure_reason is None:
            self._judge_failure_reason = verdict.rationale
        return self.fallback_judge.judge(unit.row, target.row, sim)

    def _decide_borderline(
        self,
        unit: _UnitRef,
        target: _UnitRef,
        sim: float,
        threshold: float,
        mode: str,
    ) -> Verdict | None:
        """临界带专用判定：人工台账优先，其次复核判定器，都没有则交回上层。

        返回 ``None`` 的语义是"本算子没有复核能力"，调用方据此落回
        ``self.judge``——这样默认配置下行为逐字节不变，不会因为新增这条链路
        而改变任何既有跑批结果。

        三层优先级及其理由：

        1. **人工台账最高**。人已经看过这一对并拍过板，机器再算一遍只是在
           覆盖人的判断。台账命中率在真机上很低（临界带本身就窄），但每次
           命中都是"人纠正机器"，必须让它赢。
        2. **复核判定器次之**。它替换的是 ``self.judge`` 在这一小段上的结论。
           真机临界带只有 2 对，本机 iGPU 单次 1.5-6 秒，代价可忽略；生产机
           纯 CPU 单次 20-30 秒，2 对约 1 分钟，也在 cron 轮次内。
        3. **都没有 → 交回上层**。

        复核器**故障**时不写结论缓存、也不冒充 unrelated：回落规则判定、计入
        ``judge_failures``，与常规链路同一套降级语义。唯一区别是失败的对不会
        重试——``OllamaRelationJudge`` 内部熔断已让重试变成毫秒级空转，重试
        只会把 ``borderline_rechecked`` 计数刷高，反而看不出真实规模。
        """
        pair = unordered_pair(unit.key, target.key)

        override = self.borderline_overrides.get(pair)
        if override is not None:
            self._manual_seen.add(pair)
            self._manual_hits = len(self._manual_seen)
            relation = "same_story" if override else "unrelated"
            return Verdict(
                relation,
                "manual",
                "manual_override:%s(cosine=%.3f,threshold=%.2f)"
                % ("merge" if override else "keep_separate", sim, threshold),
            )

        if self.borderline_judge is None:
            return None

        cached = self._recheck_outcomes.get(pair)
        if cached is not None:
            return cached
        if pair in self._recheck_failed:
            # 已确认复核器对这一对失效，别再打一次；直接回落规则。
            return self.fallback_judge.judge(unit.row, target.row, sim)

        # 计数放在调用前：失败对也算"复核过"，否则运维会以为复核量比实际小。
        self._recheck_seen.add(pair)
        verdict = self.borderline_judge.judge(unit.row, target.row, sim)
        if verdict.failed:
            self._recheck_failed.add(pair)
            self._judge_failures += 1
            if self._judge_failure_reason is None:
                self._judge_failure_reason = verdict.rationale
            logger.warning(
                "临界复核失败（%s vs %s, cos=%.3f），回落规则判定：%s",
                unit.key,
                target.key,
                sim,
                verdict.rationale,
            )
            return self.fallback_judge.judge(unit.row, target.row, sim)

        decided = Verdict(
            verdict.relation,
            BORDERLINE_RECHECK_DECIDED_BY,
            ("borderline_llm:%s" % verdict.rationale)[:500],
        )
        self._recheck_outcomes[pair] = decided
        return decided

    def _note_borderline(
        self,
        result: GroupResult,
        unit: _UnitRef,
        target: _UnitRef | None,
        sim: float,
        mode: str,
        verdict: Verdict,
    ) -> None:
        """把"差一点就合"的配对记进 ``result``——**只观测，不改任何判定结果**。

        观测带是 ``[直判门槛 - BORDERLINE_MARGIN, 直判门槛)``。门槛按口径取
        （向量 0.75 / 文本 0.85），因为这两个数本来就不可比；统一用一个固定
        数值会把文本口径的临界带算错。

        为什么值得记：本语料里 ``s11-01``/``s11-02``（cos=0.7133，真同事件，
        被拆散）与 ``s04-01``/``solo-05``（cos=0.7203，不同事件，正确未合）
        同时落在这个带里。**同一个带内两类混杂**，正是"余弦分不开同话题延续
        与同事件"的直接证据——这个事实只有记录下来才能被看见。
        """
        if target is None:
            return
        threshold = self._direct_threshold(mode)
        if sim < threshold - self._margin():
            return
        result.note_borderline(
            unit=unit.row,
            target=target.row,
            similarity=sim,
            threshold=threshold,
            mode=mode,
            decided_by=verdict.decided_by,
            rationale=verdict.rationale,
        )

    def _decide_cross(
        self, unit: _UnitRef, anchor: _UnitRef, sim: float, channel: str
    ) -> Verdict:
        """跨窗口归属判定：准入即判同，结论里留痕走过哪条通道。

        **为什么不再叠一层判定器**：窗内那两层防错的前提是"两条事实处在同一
        时间窗、可比性高"，跨窗配对恰恰相反——同一个事件跨 30 天发酵，标题
        与正文表述差异天然很大，字符二元组 Jaccard 会系统性偏低，
        ``RuleJudge`` 的 ``RULE_JACCARD=0.35`` 会把真分裂成批拒掉（本语料 5 条
        真分裂的文本相似度全部低于该线）。再叠它等于用一套与余弦口径相冲突的
        度量去否决余弦的结论，只会把跨窗口召回直接废掉。

        ``channel`` 由 :func:`cross_window_admit` 给出，写进 ``rationale``：
        事后要能区分"因为实体对上了才合"和"因为太像、免实体才合"，两者的
        风险等级不同，误合并排查时第一个要看的字段就是它。
        """
        shared = sorted(unit.entities & anchor.entities)
        return Verdict(
            "same_story",
            "vector",
            f"cross_window[{channel}]:cosine={sim:.3f}"
            f",shared_entities={','.join(shared[:3]) or '-'}",
        )

    # ------------------------------------------------------ 写库 --

    def _create_story(self, seed: _UnitRef, now: datetime) -> tuple[int, bool]:
        """建事件（或复用同 key 的既有事件），返回 ``(story_id, 是否新建)``。

        返回值里带 ``是否新建`` 是因为 ``ON CONFLICT`` 让"建"与"复用"走了同
        一条语句：PostgreSQL 的 ``xmax = 0`` 只在真正 INSERT 时为真，靠它区分。
        不区分的话，"删成员边重跑"会让 ``stories_created`` 虚报成又建了一批
        新事件，运维无法从日志判断这轮到底发生了什么。
        """
        payload = {
            "story_key": _story_key(seed.source_id, seed.key),
            "title": str(seed.row.get("title") or "")[:500] or "未命名事件",
            "summary": str(seed.row.get("content") or "")[:1000] or None,
            "first_seen_at": seed.row.get("created_at") or now,
            "last_seen_at": seed.row.get("created_at") or now,
            "source_names": json.dumps([seed.source_id], ensure_ascii=False),
            "entities": json.dumps(sorted(seed.entities), ensure_ascii=False),
            "topics": json.dumps(sorted(seed.topics), ensure_ascii=False),
            "confidence": None,
            "model_version": self.judge.name,
        }
        # 必须走 insert_returning：这是写入 + RETURNING，fetch_all 会回滚事务，
        # 拿到的 story_id 来自序列 nextval（不受回滚影响），行却不存在，
        # 故障会推迟到下一条成员边插入时才以外键冲突的形式爆出来。
        #
        # ON CONFLICT (story_key) 不是可选优化：``osint_story.story_key`` 有唯一
        # 约束，而 key 由种子事实唯一确定。任何"删掉成员边重算归属"的操作
        # （修误合并、调阈值后重跑、人工重置）都会让同一条种子事实再次走到
        # 这里——2026-11-04 调阈值后重跑就是撞在 ``osint_story_story_key_key``
        # 上直接崩掉的。复用既有 story 并刷新元数据，才是"重算"应有的语义。
        rows = db.insert_returning(_INSERT_STORY, payload)
        row = rows[0]
        return int(row["story_id"]), bool(row.get("inserted", True))

    def _add_member(self, unit: _UnitRef, story_id: int, verdict: Verdict, sim: float) -> int:
        return db.execute(
            _INSERT_MEMBER,
            {
                "unit_key": unit.key,
                "unit_version": unit.version,
                "story_id": story_id,
                "source_id": unit.source_id,
                "relation": verdict.relation,
                "similarity": round(float(sim), 6),
                "decided_by": verdict.decided_by,
                "rationale": verdict.rationale[:500],
            },
        )

    # ------------------------------------------------------ 主流程 --

    def run(
        self,
        *,
        window_days: int = DEFAULT_WINDOW_DAYS,
        only_reviewed: bool = True,
        limit: int = 2000,
        dry_run: bool = False,
    ) -> GroupResult:
        """执行一轮聚类。

        Args:
            window_days: 时间窗（天），只处理窗内事实。
            only_reviewed: 为 True 时只聚类 ``review_status='reviewed'`` 的
                事实。首轮建议 True——绕开未复核噪声，也顺带对齐"pending 不
                算已判读"的口径。
            limit: 单轮处理上限，防止一次拉爆内存。
            dry_run: 只计算不落库，用于上线前评估分组效果。

        跨窗口召回由构造函数开关 :attr:`cross_window` 控制（默认关）。打开后
        本方法额外做三件事：加载窗外 ``window_days *
        CROSS_WINDOW_DAYS_MULTIPLIER`` 天内已归属的锚点、给锚点补算向量、在
        窗内两路都失败后尝试跨窗挂载。启用前必须先在真值语料上验证，见
        :data:`CROSS_WINDOW_MIN_SIMILARITY` 的实测依据。
        """
        started = time.monotonic()
        result = GroupResult(dry_run=dry_run)

        # 每轮复位判定器预算与熔断：否则一次网络抖动会让该进程后续所有轮次
        # 永久放弃 LLM 判定，且外部只看到"合并率一直很低"。
        self._judge_failures = 0
        self._judge_failure_reason = None
        # 复核缓存必须每轮清空：不清的话第二轮会把上一轮的结论当成"本轮复核
        # 结果"，而那两个数（rechecked/promoted）正是要用来判断本轮效果的。
        self._recheck_outcomes.clear()
        self._recheck_seen.clear()
        self._recheck_failed.clear()
        self._manual_hits = 0
        self._manual_seen.clear()
        # 配对观测同理：不清会跨轮累加，标定脚本按"本轮"统计就全错了。
        self._pair_trace.clear()
        self._pair_trace_truncated = False
        _reset = getattr(self.judge, "reset_budget", None)
        if callable(_reset):
            _reset()
        if self.borderline_judge is not None and self.borderline_judge is not self.judge:
            _reset_borderline = getattr(self.borderline_judge, "reset_budget", None)
            if callable(_reset_borderline):
                _reset_borderline()

        refs = self._load_units(window_days, only_reviewed, limit)
        result.units_scanned = len(refs)
        if not refs:
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            logger.info("事件聚类：窗内无待归属事实，跳过")
            return result

        anchors: list[_UnitRef] = []
        if self.cross_window:
            anchors = self._load_anchors(window_days, CROSS_WINDOW_ANCHOR_LIMIT)
            result.anchors_loaded = len(anchors)
            logger.info(
                "跨窗口召回开启：窗内 %d 条待归属，窗外 %d 条锚点（%d 天带）",
                len(refs),
                len(anchors),
                int(window_days) * CROSS_WINDOW_DAYS_MULTIPLIER,
            )

        # 锚点与窗内单元一起算向量：跨窗口判定只靠余弦，缺向量的锚点会退回
        # 字符二元组口径，在跨月表述差异面前几乎必然判不同（等于该锚点失效）。
        result.degraded, result.degrade_reason = self._embed_units(refs + anchors)
        if result.degraded:
            logger.error(
                "事件聚类降级为纯文本判定（原因=%s）：合并率将显著下降，请排查嵌入链路",
                result.degrade_reason,
            )

        now = datetime.now(timezone.utc)
        # 按 created_at 升序处理：先出现的事实优先作种子，同输入得到同分组。
        for pivot in refs:
            if pivot.assigned_story is not None:
                result.units_skipped_existing += 1
                continue

            # 第一路：挂到已有事件（优先，保证同一件事不被反复开新事件）
            target, sim, mode = self._best_candidate(
                self._candidates(refs, pivot, assigned=True), pivot
            )
            if target is not None and sim >= gate_for(mode):
                verdict = self._decide(pivot, target, sim, mode)
                if verdict.relation != "unrelated":
                    if verdict.decided_by == BORDERLINE_RECHECK_DECIDED_BY:
                        result.note_promoted(
                            unit=pivot.row,
                            target=target.row,
                            similarity=sim,
                            threshold=self._direct_threshold(mode),
                            mode=mode,
                            rationale=verdict.rationale,
                        )
                    if dry_run:
                        pivot.assigned_story = target.assigned_story
                        result.members_added += 1
                    else:
                        assert target.assigned_story is not None
                        inserted = self._add_member(
                            pivot, target.assigned_story, verdict, sim
                        )
                        pivot.assigned_story = target.assigned_story
                        result.members_added += inserted
                    continue
                result.unrelated += 1
                self._note_borderline(result, pivot, target, sim, mode, verdict)

            # 第二路：与另一个未归属单元合并，开新事件并同时挂两条
            partner, psim, pmode = self._best_candidate(
                self._candidates(refs, pivot, assigned=False), pivot
            )
            if partner is not None and psim >= gate_for(pmode):
                verdict = self._decide(pivot, partner, psim, pmode)
                if verdict.relation != "unrelated":
                    if verdict.decided_by == BORDERLINE_RECHECK_DECIDED_BY:
                        result.note_promoted(
                            unit=pivot.row,
                            target=partner.row,
                            similarity=psim,
                            threshold=self._direct_threshold(pmode),
                            mode=pmode,
                            rationale=verdict.rationale,
                        )
                    if dry_run:
                        pivot.assigned_story = -1
                        partner.assigned_story = -1
                        result.stories_created += 1
                        result.members_added += 2
                    else:
                        story_id, created = self._create_story(pivot, now)
                        self._add_member(
                            pivot,
                            story_id,
                            Verdict("same_story", "vector", "seed"),
                            1.0,
                        )
                        self._add_member(partner, story_id, verdict, psim)
                        pivot.assigned_story = story_id
                        partner.assigned_story = story_id
                        result.tally_story(created)
                        result.members_added += 2
                    continue
                result.unrelated += 1
                self._note_borderline(result, pivot, partner, psim, pmode, verdict)

            # 第三路：跨窗口挂到窗口外的既有事件（默认关闭，见 CROSS_WINDOW_*）。
            # 放在窗内两路之后：窗内证据时间更近、可比性更高，能合就不该越窗。
            if anchors:
                anchor, asim, amode = self._best_candidate(
                    self._cross_candidates(anchors, pivot), pivot
                )
                # 准入判定见 cross_window_admit：向量口径 + 共同主体 + 余弦 >= 0.70。
                admit, channel = (
                    cross_window_admit(pivot.entities & anchor.entities, asim, amode)
                    if anchor is not None
                    else (False, "no_anchor")
                )
                if admit:
                    story_id = anchor.assigned_story
                    assert story_id is not None
                    verdict = self._decide_cross(pivot, anchor, asim, channel)
                    if dry_run:
                        pivot.assigned_story = story_id
                        result.members_added += 1
                    else:
                        inserted = self._add_member(pivot, story_id, verdict, asim)
                        pivot.assigned_story = story_id
                        result.members_added += inserted
                    result.cross_window_merged += 1
                    continue

            # 第四路：无任何可信近邻 → 独立事件
            if dry_run:
                pivot.assigned_story = -2
                result.stories_created += 1
                result.members_added += 1
                continue
            story_id, created = self._create_story(pivot, now)
            self._add_member(
                pivot, story_id, Verdict("same_story", "vector", "seed"), 1.0
            )
            pivot.assigned_story = story_id
            result.tally_story(created)
            result.members_added += 1

        if not dry_run:
            # 只重算本轮涉及的事件。全表 UPDATE 在 story 表长大后（十万级）
            # 会成为每轮首要瓶颈，且与增量聚类的定位相矛盾。
            touched = sorted(
                {
                    ref.assigned_story
                    for ref in refs
                    if ref.assigned_story is not None and ref.assigned_story > 0
                }
            )
            if touched:
                db.execute(_REFRESH_STATS, {"story_ids": touched})

        result.judge_calls = int(getattr(self.judge, "calls", 0) or 0)
        # 复核器与主判定器通常不是同一实例（主判定器走规则、复核器走 LLM），
        # 只报 self.judge.calls 会把复核那部分调用漏掉，成本核算直接算错。
        if self.borderline_judge is not None and self.borderline_judge is not self.judge:
            result.judge_calls += int(
                getattr(self.borderline_judge, "calls", 0) or 0
            )
        result.judge_failures = self._judge_failures
        result.borderline_rechecked = len(self._recheck_seen)
        result.manual_overrides = self._manual_hits
        # 观测数据整体转移给 result。用切片而不是重新构造：标定脚本要的是原始
        # 顺序，且这里的元素在 run 期间已不再被追加。
        result.pairs_observed = list(self._pair_trace)
        result.pairs_observed_truncated = self._pair_trace_truncated
        if self._judge_failures:
            judge_reason = "judge_failed(%d/%d, last=%s)" % (
                self._judge_failures,
                result.judge_calls,
                self._judge_failure_reason,
            )
            if result.degraded:
                result.degrade_reason = f"{result.degrade_reason}; {judge_reason}"
            else:
                result.degraded = True
                result.degrade_reason = judge_reason
            logger.error(
                "判定器故障，已回落规则判定：%s —— 本次事件的独立来源数可能被低估",
                judge_reason,
            )

        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info("事件聚类完成：%s", result.as_dict())
        return result


__all__ = [
    "BORDERLINE_MARGIN",
    "BORDERLINE_RECHECK_DECIDED_BY",
    "BORDERLINE_SAMPLE_LIMIT",
    "CANDIDATE_LIMIT",
    "CROSS_WINDOW_ANCHOR_LIMIT",
    "CROSS_WINDOW_DAYS_MULTIPLIER",
    "CROSS_WINDOW_MIN_SIMILARITY",
    "DEFAULT_WINDOW_DAYS",
    "GroupResult",
    "PAIR_TRACE_LIMIT",
    "RECALL_SIMILARITY",
    "RECHECK_SIMILARITY",
    "RULE_DIRECT_SIMILARITY",
    "SIMILARITY_GATES",
    "RelationJudge",
    "RuleJudge",
    "StoryGrouper",
    "Verdict",
    "char_bigrams",
    "compute_heat",
    "cosine",
    "cross_window_admit",
    "entity_keys",
    "extract_source_id",
    "gate_for",
    "jaccard",
    "normalize_text",
    "pair_record",
    "resolve_borderline_margin",
    "resolve_recheck_similarity",
    "topic_keys",
    "unordered_pair",
]
