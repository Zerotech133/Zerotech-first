"""Ollama 关系判定器（事件层第二层判定，替代 P0 的可解释规则兜底）。

职责边界：
- 只做**两条事实 → 一个关系标签**的判定，不写库、不建事件、不改归属边；
  归属边落库仍由 :class:`knowledge.story_group.StoryGrouper` 负责，本模块
  返回 :class:`~knowledge.story_group.Verdict` 即交权；
- 只在余弦落在复核区间 ``[RECALL_SIMILARITY, RECHECK_SIMILARITY)`` 时被调用
  （见 ``StoryGrouper._decide``），高置信区间的合并**不经过**本模块；
- 后端为 Ollama HTTP 接口 ``POST {OLLAMA_URL}/api/chat``，与
  :mod:`knowledge.embedder` 同一传输层、同一 ``ollama_url`` 配置；
- 支持注入 client，单测离线运行不依赖真实 Ollama 服务。

## 为什么是四分类而不是"是否同一事件"

AIHOT 在 370 对标注样本上实测：把提示词写成"是/否同一事件"且语气偏保守的
二分类，会拒掉约一半真合并。原因是语言模型在证据不足时倾向选"否"，而
"否"在我们这里的代价是**独立来源数被系统性低估**——同一件事被拆成多个
事件，每条各算一个来源，热度直接算错。

因此提示词强制四选一（``same_story`` / ``progress`` / ``roundup`` /
``unrelated``），并在指令里明确"不确定也不许选 unrelated"。四个标签的含义
互斥且覆盖全部情形，模型无需靠回避来表达不确定。

## 失败必须显式，绝不可伪装成 unrelated

判定后端不可用（未启动 / 超时 / 非 2xx / 回复无法解析）时，本模块**不**返回
``unrelated``，而是返回 ``Verdict(..., failed=True)``。调用方据此回落到规则
判定并在 ``GroupResult`` 上置 ``degraded``。

这条约束是 P0 阶段 ``EXIT_DEGRADED=2`` 契约的延伸：如果 LLM 掉线被当成
"全都判无关"，外部表现就是"事件突然变少"，与嵌入层降级完全同形，运维无法
区分"今天没新闻"和"判定器挂了"。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Mapping

try:  # httpx 为核心依赖；缺失时降级为运行时异常，不阻断模块导入
    import httpx
except ImportError:  # pragma: no cover - 取决于部署环境
    httpx = None  # type: ignore[assignment]

from config.settings import get_settings
from knowledge.story_group import Verdict, extract_source_id

logger = logging.getLogger(__name__)


class JudgeUnavailableError(RuntimeError):
    """判定后端不可用（未启动 / 网络失败 / 超时 / 非 2xx / 回复不可解析）。

    调用方应**回落**到 :class:`knowledge.story_group.RuleJudge` 并把本轮标记
    为 degraded，而不是把失败当成 ``unrelated``。
    """


#: 允许的关系标签。与 ``osint_story_member.relation`` 的 CHECK 约束一致，
#: 但多出 ``unrelated``——它表示"判定为不相关"，此时**不落库**，故表里没有。
JUDGE_RELATIONS: tuple[str, ...] = (
    "same_story",
    "progress",
    "roundup",
    "unrelated",
)

#: 判为这些标签时不写 ``osint_story_member``。
NON_MEMBER_RELATIONS: frozenset[str] = frozenset({"unrelated"})

#: 单轮 LLM 调用上限的兜底值（正常情况取 ``settings.judge_max_calls``）。
#:
#: 实测：本机 iGPU 热态单次约 2-6 秒、冷加载额外约 50 秒；生产机为 20 vCPU
#: 纯 CPU 无 GPU，9B 模型单次约 20-30 秒。120 次在两种环境下分别约 10 分钟
#: 与 40-60 分钟，已是 cron 轮次的忍耐上限。超限后熔断并按失败计，由调用方
#: 标 degraded——宁可本轮少合并一些，也不能让一轮跑不完卡住下一轮。
DEFAULT_MAX_CALLS: int = 120

#: 标题/正文截断长度。判定只需判断"是不是同一件事"，不需要全文；
#: 截断还能把单次请求压在 16k 上下文内（``OLLAMA_CONTEXT_LENGTH=16384``）。
TITLE_CHARS: int = 200
BODY_CHARS: int = 400

#: 理由长度上限，落库字段 ``rationale`` 截 500，这里先收到 120。
REASON_CHARS: int = 120

#: 预热/探活用的极短提示词（模型名写死在提示里没有意义，只求走通链路）。
_WARMUP_PROMPT = '只输出 {"relation":"unrelated","reason":"ping"}'

#: 预热超时下限（秒）。冷加载实测约 46 秒，留一倍余量；与常规判定超时取大。
WARMUP_TIMEOUT_SEC: float = 120.0

#: 模型驻留时长。整轮聚类最长不过数十分钟，2 小时足以覆盖并跨轮复用。
KEEP_ALIVE: str = "2h"

_TROUBLESHOOT_HINT = (
    "排障指引：1) 确认 Ollama 已启动（curl http://127.0.0.1:11434/api/tags）；"
    "2) 拉取模型：ollama pull qwen3.5:9b；"
    "3) 检查项目根 .env 的 OLLAMA_URL / JUDGE_MODEL / JUDGE_TIMEOUT_SEC"
)

# ---------------------------------------------------------------- 提示词 --

_PERSONA = (
    "你是开源情报系统的新闻事件归并判定器。你的唯一任务：判断两条事实是否"
    "属于同一个事件。你只输出 JSON，不输出任何解释性文字。"
)

_PROMPT_V1 = """两条事实已被向量检索筛为"可能相关"，请判断它们的关系。

【事实 A】来源：{src_a}；发布：{ts_a}
标题：{title_a}
正文：{body_a}

【事实 B】来源：{src_b}；发布：{ts_b}
标题：{title_b}
正文：{body_b}

在下列四个选项中选一个，必须选，不许回避：
- same_story：同一件事的同一阶段。两条在讲同一起事件、同一进展，只是报道方不同。
- progress：同一件事的不同阶段。主体相同、事态向前推进了（如"立案"→"起诉"→"判决"）。
- roundup：综述/聚合报道。一条是清单式汇总，另一条是其中某个具体事件。
- unrelated：不相关。主体不同，或主体相同但确实是两件不同的事。

判定须知：
1. 不要因为"证据不足"或"不确定"就选 unrelated。只有在你能指出两条事实的
   主体或事件确实不同时，才选 unrelated。
2. 正文可能被截断，以标题和可见部分为准，不要因为残缺就选 unrelated。
3. 同一主体（公司/机构/人物）出现在两件事里，不等于同一事件。要看事件本身。
4. 时间相隔较远但事态连续（如一个月前的立案与今天的判决），选 progress。

只输出一个 JSON 对象，格式严格如下，不要加代码块标记：
{{"relation": "same_story|progress|roundup|unrelated", "reason": "不超过 60 字的中文理由"}}"""

#: v2：收紧 ``progress`` 判据。
#:
#: 动因（技术债 #16）：v1 下"同一主体、同一时期、两项并列事项"会被随机判成
#: progress。真机四对同形样本里三对判 unrelated、一对判 progress，**同样的推理
#: 只在一对上发生**——这不是采样抖动（``temperature=0`` 已钉死），而是 v1 的
#: progress 定义只写了"主体相同、事态推进"，没写"必须是同一个具体事项在推进"。
#:
#: v2 的改动是三处，都针对"模型凭什么说是同一件事"：
#: 1. 定义里加"**只有一个具体事项**在推进"，把"两项并列事项"排除出去；
#: 2. 给出三条并列的判据，其中第 3 条直接点名"同一主体的两项不同动作"不算；
#: 3. 要求 progress 额外输出 ``stage`` 字段写明"前一阶段→后一阶段"。写不出
#:    具体阶段，就说明它其实不是同一件事的两个阶段。
#:
#: 反例刻意选**域外**结构同形样本（省级年度经济数据 vs 新高铁通车），不写
#: 真机语料里的央行那对——写进去就成了背答案，标定失效。
_PROMPT_V2 = """两条事实已被向量检索筛为"可能相关"，请判断它们的关系。

【事实 A】来源：{src_a}；发布：{ts_a}
标题：{title_a}
正文：{body_a}

【事实 B】来源：{src_b}；发布：{ts_b}
标题：{title_b}
正文：{body_b}

在下列四个选项中选一个，必须选，不许回避：
- same_story：同一件事的同一阶段。两条在讲同一起具体事件、同一进展，只是报道方不同。
- progress：同一件事的不同阶段。**只有一个具体事项**在推进，A 是它的前一阶段、B 是它的后一阶段。
- roundup：综述/聚合报道。一条是清单式汇总，另一条是其中某个具体事件。
- unrelated：不相关。主体不同，或主体相同但确实是两件不同的事。

progress 必须同时满足三条，缺一条就不是 progress：
1. 两条讲的是**同一个具体事项**，不是同一主体在同一时期做的两件并列事项；
2. B 相对 A 有明确的状态推进，你能说清"A 停在哪一步、B 推进到哪一步"
   （如"提交上市申请"→"通过上市委审议"）；
3. 若两条只是同一主体的两项不同动作，即使时间接近、同属一个政策或业务
   范畴，也不是 progress，选 unrelated。
   反例：同一周"某省发布年度经济数据"与"某省开通新高铁线路"，是两件并列
   的事，不是一件事的两个阶段。

判定须知：
1. 不要因为"证据不足"或"不确定"就选 unrelated。只有在你能指出两条事实的
   主体或事件确实不同时，才选 unrelated。
2. 正文可能被截断，以标题和可见部分为准，不要因为残缺就选 unrelated。
3. 同一主体（公司/机构/人物）出现在两件事里，不等于同一事件。要看事件本身。
4. 时间相隔较远但事态连续（如一个月前的立案与今天的判决），选 progress。

若选 progress，必须写出 "stage" 字段，用"A：… → B：…"点明前后两个阶段；
写不出具体阶段，说明它不是同一件事的两个阶段，改选 unrelated。

只输出一个 JSON 对象，格式严格如下，不要加代码块标记：
{{"relation": "same_story|progress|roundup|unrelated", "stage": "仅 progress 填写前后阶段，否则留空", "reason": "不超过 60 字的中文理由"}}"""

#: 可用提示词版本。**对外契约是"版本名 → 完整提示词"，不是"当前提示词"**：
#: 判定结论会写进 ``osint_story.model_version``，事后必须能凭版本名复现同一批
#: 事件的判据。改提示词 = 加新版本，绝不原地改旧版本。
PROMPT_VARIANTS: dict[str, str] = {
    "v1": _PROMPT_V1,
    "v2": _PROMPT_V2,
}

#: 默认版本。**改动默认值必须附真机标定数据**（见 ``scripts/calibrate_judge_prompt.py``）：
#: 提示词是事件层唯一的语义判据，凭直觉换版本会让历史 ``model_version`` 失去可比性。
#:
#: 2026-11-04 由 v1 改为 v2，依据（24 对真机探针集，均 3 轮/2 轮零翻转）：
#: v1 在 16 对"同一主体、不同事项"的硬负例上误合并 **4 对**（25%），理由清一色
#: 是"主体相同 + 时间先后 ⇒ progress"；v2 误合并 **0 对**、漏合 **0 对**（8 对
#: 正例全中）。同时真机 42 事实端到端结果与 v1 完全一致（28 事件 / 拆散 0 /
#: 误合并 0 / 来源数 14-14），即当日**零回归**。
#:
#: 回滚：``JUDGE_PROMPT_VARIANT=v1``（v1 提示词原样保留，不是删了重建）。
DEFAULT_PROMPT_VARIANT: str = "v2"

#: 兼容旧引用。指默认版本，不要在新代码里用。
_PROMPT_TEMPLATE = _PROMPT_V1


def _clean(value: Any, limit: int) -> str:
    """压平空白并截断，避免正文里的换行撑爆提示词结构。"""
    text = " ".join(str(value or "").split())
    return text[:limit]


def _parse_ts(value: Any) -> datetime | None:
    """宽松解析时间戳（datetime / ISO 字符串 / None）。"""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _fmt_ts(value: Any) -> str:
    parsed = _parse_ts(value)
    if parsed is None:
        return "未知"
    return parsed.strftime("%Y-%m-%d %H:%M")


def resolve_prompt_variant(name: str | None = None) -> str:
    """把版本名解析成**确定可用的**版本名。

    未知版本**不抛异常**，回落 :data:`DEFAULT_PROMPT_VARIANT` 并告警：聚类是
    cron 长跑任务，一个拼错的配置项不该让整轮判定停摆；但必须留下日志，否则
    "配了 v3 却没生效"会变成查不出来的幽灵问题。
    """
    wanted = str(name or "").strip().lower()
    if not wanted:
        wanted = DEFAULT_PROMPT_VARIANT
    if wanted in PROMPT_VARIANTS:
        return wanted
    logger.warning(
        "未知的判定提示词版本 %r，回落 %s（可用：%s）",
        name,
        DEFAULT_PROMPT_VARIANT,
        "/".join(sorted(PROMPT_VARIANTS)),
    )
    return DEFAULT_PROMPT_VARIANT


def build_judge_prompt(
    unit: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    title_chars: int = TITLE_CHARS,
    body_chars: int = BODY_CHARS,
    variant: str | None = None,
) -> str:
    """构造判定提示词（纯函数，便于单测与留痕）。

    刻意**不**把余弦值写进提示词：模型会把它当成答案的锚点，反而放大阈值
    本身的偏差。提示词只告诉它"已被筛为可能相关"这一事实。

    :param variant: 提示词版本名，``None`` 取默认版本；未知版本回落默认并告警。
    """

    def _pair(item: Mapping[str, Any]) -> dict[str, str]:
        # 来源身份对判定很关键：不同媒体各自报道同一进展才是同一事件的证据，
        # 同一家媒体连发两篇则更可能是系列稿。story_group 已实现域名归一
        # （去 www./m./wap. 前缀），这里直接复用，避免两处口径漂移。
        source = str(
            item.get("source_id")
            or item.get("source_name")
            or extract_source_id(item)
            or ""
        )
        if not source or source == "unknown":
            # extract_source_id 对无 URL/无 blogger_key 的单元返回 "unknown"，
            # 直接喂给模型会被读成一个真实媒体名，改用显式占位。
            source = "未知来源"
        return {
            "src": _clean(source, 60) or "未知来源",
            "ts": _fmt_ts(item.get("created_at") or item.get("published_at")),
            "title": _clean(item.get("title"), title_chars) or "（无标题）",
            "body": _clean(item.get("content"), body_chars) or "（无正文）",
        }

    left, right = _pair(unit), _pair(target)
    template = PROMPT_VARIANTS[resolve_prompt_variant(variant)]
    return template.format(
        src_a=left["src"],
        ts_a=left["ts"],
        title_a=left["title"],
        body_a=left["body"],
        src_b=right["src"],
        ts_b=right["ts"],
        title_b=right["title"],
        body_b=right["body"],
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_judge_reply_ex(text: str | None) -> tuple[str, str, str]:
    """从模型回复里抽出 ``(relation, reason, stage)``。

    ``stage`` 只有 v2 提示词会要求，v1 回复里没有 → 返回空串。它**不参与**判定：
    写不出阶段只说明模型没遵守指令，不能据此推断关系。留在 ``rationale`` 里是
    为了事后能看出"这次 progress 是凭什么阶段判的"——错了才知道错在哪一步。

    宁可抛异常也不猜：标签非法时抛 :class:`JudgeUnavailableError`，调用方
    回落规则判定。把非法标签当成 ``unrelated`` 会静默丢掉真合并。
    """
    raw = str(text or "").strip()
    if not raw:
        raise JudgeUnavailableError("模型回复为空")

    fenced = _FENCE_RE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()

    matched = _JSON_RE.search(raw)
    if not matched:
        raise JudgeUnavailableError("模型回复中找不到 JSON：%s" % raw[:120])

    try:
        payload = json.loads(matched.group(0))
    except ValueError as exc:
        raise JudgeUnavailableError("模型回复 JSON 解析失败：%s" % exc) from exc
    if not isinstance(payload, dict):
        raise JudgeUnavailableError("模型回复 JSON 不是对象")

    relation = str(payload.get("relation") or "").strip().lower()
    if relation not in JUDGE_RELATIONS:
        raise JudgeUnavailableError(
            "模型给出的 relation 不在词表内：%r（允许：%s）"
            % (relation, "/".join(JUDGE_RELATIONS))
        )
    reason = _clean(payload.get("reason"), REASON_CHARS) or "（模型未给理由）"
    stage = _clean(payload.get("stage"), REASON_CHARS)
    return relation, reason, stage


def parse_judge_reply(text: str | None) -> tuple[str, str]:
    """``(relation, reason)`` 二元组，兼容旧调用方。"""
    relation, reason, _stage = parse_judge_reply_ex(text)
    return relation, reason


# -------------------------------------------------------------- 判定器 --


class OllamaRelationJudge:
    """Ollama 后端的关系判定器，满足 ``story_group.RelationJudge`` 协议。

    用法::

        judge = OllamaRelationJudge()
        verdict = judge.judge(unit_row, target_row, 0.72)
        if verdict.failed:          # 后端不可用，调用方应回落规则判定
            ...

    :param client: 可注入的 HTTP 客户端（需提供 ``post(url, json=..., timeout=...)``）；
                   传 ``None`` 时内部惰性创建 ``httpx.Client``。
    :param model: 模型名，默认取 ``settings.judge_model``。
    :param base_url: Ollama 地址，默认取 ``settings.ollama_url``。
    :param timeout: 单次请求超时（秒），默认取 ``settings.judge_timeout_sec``。
    :param max_calls: 单轮调用预算，默认 :data:`DEFAULT_MAX_CALLS`；用尽后
                      熔断并按失败计。
    :param prompt_variant: 提示词版本名（``PROMPT_VARIANTS`` 的键），默认取
                      ``settings.judge_prompt_variant``；未知版本回落默认并告警。
    """

    #: 判定器族名。实例化时会带上提示词版本（``llm-ollama-v2``），写入
    #: ``osint_story.model_version``——**换个提示词就是换了个判定器**，若都写
    #: 同一个名字，事后无法分辨某批事件是哪套判据产出的，也没法做 A/B 回溯。
    name = "llm-ollama"

    def __init__(
        self,
        client: Any = None,
        *,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        max_calls: int | None = None,
        prompt_variant: str | None = None,
    ) -> None:
        settings = get_settings()
        self.model = model or getattr(settings, "judge_model", "qwen3.5:9b")
        self.base_url = (base_url or settings.ollama_url).rstrip("/")
        #: 提示词版本（已解析，必为 ``PROMPT_VARIANTS`` 的键）。
        self.prompt_variant = resolve_prompt_variant(
            prompt_variant
            if prompt_variant is not None
            else getattr(settings, "judge_prompt_variant", DEFAULT_PROMPT_VARIANT)
        )
        self.name = "llm-ollama-%s" % self.prompt_variant
        self.timeout = float(
            timeout
            if timeout is not None
            else getattr(settings, "judge_timeout_sec", 120.0)
        )
        budget = (
            max_calls
            if max_calls is not None
            else getattr(settings, "judge_max_calls", DEFAULT_MAX_CALLS)
        )
        self.max_calls = max(1, int(budget))
        self._client = client
        self._calls = 0
        #: 传输层已确认不可用时置位，后续调用直接短路，避免每次白等一个超时。
        self._broken = False
        self._broken_reason: str | None = None

    # ------------------------------------------------------- 传输 --

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if httpx is None:  # pragma: no cover - 取决于部署环境
            raise JudgeUnavailableError("httpx 未安装，无法调用 Ollama")
        self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def _post(self, prompt: str, *, timeout: float | None = None) -> str:
        if self._broken:
            raise JudgeUnavailableError(self._broken_reason or "判定后端已熔断")
        if self._calls >= self.max_calls:
            raise JudgeUnavailableError(
                "判定调用预算用尽（max_calls=%d）" % self.max_calls
            )

        url = f"{self.base_url}/api/chat"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _PERSONA},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
            # 必须关掉思考链。qwen3.5:9b 是思考型模型，实测开思考时 160 token
            # 预算会被 thinking 段全部吃掉，message.content 返回空串、单次耗时
            # 48 秒；关掉后 content 直接给出 JSON、推理仅 1.5 秒。四分类任务
            # 不需要思维链，开思考只会让整批跑不完。
            "think": False,
            # 显式钉住模型驻留，别依赖服务端 OLLAMA_KEEP_ALIVE 环境变量。
            # 实测冷加载 50 秒 vs 计算 1.5 秒：cron 间隔一旦超过服务端保活窗口，
            # 每轮第一对事实就要白付 50 秒，且该延迟落在超时判定里极易被误判为
            # 后端故障。钉住后整轮只在启动时付一次加载代价。
            "keep_alive": KEEP_ALIVE,
            # 220 而非 160：v2 多要一个 stage 字段（"A：… → B：…"）。这是**上限**
            # 不是目标，正常回复仍在 60-80 token 停下；抬高只为避免长阶段描述被
            # 截断成非法 JSON——那会被判成判定故障、回落规则并整体标 degraded。
            "options": {"temperature": 0, "num_predict": 220},
        }
        self._calls += 1
        try:
            resp = self._get_client().post(
                url, json=body, timeout=timeout or self.timeout
            )
        except Exception as exc:  # noqa: BLE001 - 网络层异常一律归一
            self._broken = True
            self._broken_reason = "连接 Ollama 失败：%s: %s" % (
                type(exc).__name__,
                exc,
            )
            raise JudgeUnavailableError(
                "%s；%s" % (self._broken_reason, _TROUBLESHOOT_HINT)
            ) from exc

        status = getattr(resp, "status_code", 200)
        if status >= 400:
            # 4xx 多为模型名/参数错，属配置问题；同样熔断，避免刷屏。
            self._broken = True
            self._broken_reason = "Ollama 返回 HTTP %s" % status
            raise JudgeUnavailableError(
                "%s；%s" % (self._broken_reason, _TROUBLESHOOT_HINT)
            )

        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise JudgeUnavailableError("Ollama 响应不是 JSON：%s" % exc) from exc

        message = payload.get("message") if isinstance(payload, dict) else None
        if isinstance(message, dict):
            content = str(message.get("content") or "").strip()
            if content:
                return content
            # 兜底：某些 Ollama 版本不认 think=false，仍会把结果写进 thinking。
            # 与其判失败，不如从 thinking 里捞 JSON——标签校验仍会兜住乱码。
            thinking = str(message.get("thinking") or "").strip()
            if thinking:
                logger.warning("模型未返回 content，改从 thinking 段解析")
                return thinking
        # 兼容 /api/generate 形态（部分代理会把 chat 转成 generate）
        if isinstance(payload, dict) and payload.get("response"):
            return str(payload["response"])
        raise JudgeUnavailableError("Ollama 响应缺 message.content 字段")

    # ------------------------------------------------------- 端口 --

    def judge(
        self,
        unit: Mapping[str, Any],
        target: Mapping[str, Any],
        similarity: float,
    ) -> Verdict:
        """给出归属判定。失败时返回 ``failed=True``，**绝不**冒充 unrelated。"""
        prompt = build_judge_prompt(unit, target, variant=self.prompt_variant)
        try:
            relation, reason, stage = parse_judge_reply_ex(self._post(prompt))
        except JudgeUnavailableError as exc:
            logger.warning("LLM 判定失败，回落规则判定：%s", exc)
            return Verdict(
                relation="unrelated",
                decided_by="llm",
                rationale="llm_failed: %s" % str(exc)[:200],
                failed=True,
            )

        detail = "llm:%s | %s" % (relation, reason)
        if stage:
            # 只对 progress 有意义，但不必校验 relation——模型在别的标签上多写
            # 一句阶段描述无害，丢掉反而少了一条排查线索。
            detail += " | stage:%s" % stage
        return Verdict(
            relation=relation,
            decided_by="llm",
            rationale=detail,
        )

    # ------------------------------------------------------- 运维 --

    def warmup(self) -> bool:
        """预热模型：把首次加载的代价挪到循环之外，绝不抛异常。

        实测本机 iGPU 上 ``qwen3.5:9b`` 冷加载约 46 秒、真实推理仅 0.36 秒。
        若不预热，第一对事实会独自吃掉整个超时预算；预热后单次约 1 秒。

        预热失败**不保留熔断**：连接被拒的调用本身是毫秒级失败，让真实调用
        再试一次比直接放弃整轮更划算。
        """
        try:
            self._post(_WARMUP_PROMPT, timeout=max(self.timeout, WARMUP_TIMEOUT_SEC))
            return True
        except Exception:  # noqa: BLE001 - 预热只回报，不抛出
            logger.warning("判定模型预热失败，首对事实可能触发冷加载")
            return False
        finally:
            self._calls = 0
            self._broken = False
            self._broken_reason = None

    def health(self) -> bool:
        """探活：真发一次请求（含冷加载），绝不抛异常，**不改变任何内部状态**。

        纯观测语义：探活失败不应让后续真实调用短路，否则运维跑一次探活就把
        整轮判定废掉了。
        """
        saved = (self._broken, self._broken_reason, self._calls)
        self._broken, self._broken_reason = False, None
        try:
            self._post(_WARMUP_PROMPT, timeout=max(self.timeout, WARMUP_TIMEOUT_SEC))
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            self._broken, self._broken_reason, self._calls = saved

    @property
    def calls(self) -> int:
        """本轮已消耗的调用次数（供 GroupResult 与日志使用）。"""
        return self._calls

    def reset_budget(self) -> None:
        """复位调用预算与熔断状态（每轮 ``run()`` 前调用）。"""
        self._calls = 0
        if not self._broken:
            return
        # 熔断只在传输层失败时置位；新一轮允许再试一次，否则一次网络抖动
        # 会让该进程后续所有轮次永久放弃 LLM 判定。
        self._broken = False
        self._broken_reason = None


__all__ = [
    "DEFAULT_MAX_CALLS",
    "DEFAULT_PROMPT_VARIANT",
    "JUDGE_RELATIONS",
    "JudgeUnavailableError",
    "KEEP_ALIVE",
    "NON_MEMBER_RELATIONS",
    "PROMPT_VARIANTS",
    "OllamaRelationJudge",
    "build_judge_prompt",
    "parse_judge_reply",
    "parse_judge_reply_ex",
    "resolve_prompt_variant",
]
