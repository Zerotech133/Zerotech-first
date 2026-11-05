"""全局配置模块（pydantic-settings，架构 §7.4）。

约定：
- 配置来源：环境变量 + 项目根 ``.env`` 文件（与老田现有产线惯例一致，大小写不敏感）；
- 限速参数带**硬下限校验**：``COLLECT_INTERVAL_SEC`` 禁止配置为 <30 秒（人速护栏，只可收紧不可放宽）；
- 所有模块统一通过 :func:`get_settings` 获取单例，禁止各自读环境变量。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 采集间隔硬下限（秒）。合规护栏常量，任何配置不得低于该值。
MIN_COLLECT_INTERVAL_SEC: int = 30


class Settings(BaseSettings):
    """osint_video 全局配置。

    字段名小写，环境变量大写自动映射（如 ``pg_host`` ← ``PG_HOST``）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- PostgreSQL（队列 / 结构化入库，架构 §2.1 §2.5） ----------
    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_db: str = "osint"

    # ---------- Qdrant（本地可选派生索引；PG 始终为事实源） ----------
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "osint_knowledge_units_v1"
    qdrant_vector_size: int = 384

    # ---------- Ollama 嵌入（bge-m3，1024 维；knowledge/embedder.py） ----------
    # 注意：qdrant_vector_size=384 是旧 collection 的维度，新建 collection 时
    # 由调用方显式传 embed_dim（1024），二者不得混用。
    ollama_url: str = Field(
        default="http://127.0.0.1:11434", description="Ollama 服务根地址"
    )
    embed_model: str = Field(default="bge-m3", description="嵌入模型名（Ollama）")
    embed_dim: int = Field(default=1024, description="嵌入向量维度；bge-m3 为 1024")
    embed_timeout_sec: float = Field(
        default=30.0, description="单次嵌入请求超时秒数（首次加载模型可能较慢）"
    )
    embed_batch_size: int = Field(
        default=32, description="单批嵌入文本条数，用于切分批量请求"
    )

    # ---------- 事件层聚类阈值（knowledge/story_group.py） ----------
    # 高置信直判同阈值。低于它但超过 RECALL_SIMILARITY 的配对必须过第二层
    # 判定器。**做成可覆盖配置而非只留常量**：阈值是"多报还是漏报"的口径
    # 选择，不同语料下最优值不同，上线后需要能不改代码灰度回退。
    story_recheck_similarity: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description=(
            "余弦直接判同阈值。0.75（2026-11-04 在 42 条真值语料上标定："
            "0.85 拆散 5/误合并 0，0.75 拆散 1/误合并 1）；回退填 0.85"
        ),
    )
    story_cross_window: bool = Field(
        default=False,
        description=(
            "跨窗口召回开关：窗内两路都没挂上时，再到窗外 "
            "window_days×3 天内**已归属**的锚点里找一次。"
            "解决同一件事跨月发酵（立案→处罚间隔 18 天）被切成两个事件、"
            "各自独立来源数只有一半的问题。"
            "护栏是三重与（必须 vector 口径 + 必须有共同主体 + 余弦≥0.70），"
            "其中 0.70 由 6 对跨窗高相似配对标定：真同事件最低 0.757、"
            "唯一假阳 0.642，取中点。"
            "**默认关**：放宽召回必然引入误合并，而误合并直接污染 "
            "independent_sources 且事后几乎无法发现。生产开启前须先 "
            "dry-run 比对 cross_window_merged 明细与误合并数"
        ),
    )

    # ---------- Ollama 关系判定（事件层第二层，knowledge/llm_judge.py） ----------
    # 模型锁死本机 iGPU 甜点 qwen3.5:9b：35B 级权重塞不进共享显存，会 OOM 或
    # 回退 CPU 推理拖垮整机。需要更强判定力时改走云端 API，**不要**在本地换大模型。
    judge_model: str = Field(
        default="qwen3.5:9b", description="关系判定模型名（Ollama，本机 iGPU 甜点）"
    )
    judge_timeout_sec: float = Field(
        default=120.0,
        description=(
            "单次判定请求超时秒数。iGPU 上 qwen3.5:9b 冷加载约 46 秒、"
            "热推理约 1.5 秒，故默认留足冷启动余量；预热后不会用到这么多"
        ),
    )
    judge_max_calls: int = Field(
        default=120,
        description=(
            "单轮判定调用上限。生产机为纯 CPU（无 GPU），9B 模型单次约 20-30 秒，"
            "120 次约 40-60 分钟已是 cron 轮次上限；超限熔断并按降级处理"
        ),
    )
    judge_prompt_variant: str = Field(
        default="v2",
        description=(
            "判定提示词版本（knowledge/llm_judge.py:PROMPT_VARIANTS 的键）。"
            "版本会写进 osint_story.model_version，换版本后历史事件不可直接对比；"
            "未知值回落默认并告警。改动默认值必须有真机标定数据支撑："
            "v2 相对 v1 在同一主体硬负例上误合并 4→0，其余指标不变"
        ),
    )

    # ---------- 临界带复核（knowledge/story_group.py:_decide_borderline） ----------
    # 只作用于 [直判门槛-BORDERLINE_MARGIN, 直判门槛) 这条窄带。真机实测该带
    # 仅 2 对，所以这里开 LLM 的调用量与全域开 LLM 差一个数量级：本机 iGPU
    # 数秒、生产机纯 CPU 约 1 分钟，都付得起。
    story_borderline_judge: str = Field(
        default="off",
        description=(
            "临界带复核判定器开关：off=沿用主判定器结论（P0/P1 默认）；"
            "llm=临界带改由 Ollama 复核，结论落 decided_by='llm_recheck'。"
            "开之前先用 --dry-run 确认 borderline 规模"
        ),
    )
    story_borderline_margin: float = Field(
        default=0.05,
        ge=0.0,
        le=0.5,
        description=(
            "临界复核带宽：落在 [直判门槛-margin, 直判门槛) 的配对改由复核器判。"
            "2026-11-04 在 42 条真值语料上标定（scripts/calibrate_borderline_margin.py）："
            "0.05 是'漏合归零的最小带宽'——0.03 会漏掉 cos=0.7133 的唯一真同事件，"
            "0.08 起每放宽 0.01 约多 1 对噪声而收益为 0。"
            "**上限由召回门决定**：门槛-RECALL_SIMILARITY 以上的取值永远无效，"
            "低于召回门的配对在进判定环节前就被拦掉了"
        ),
    )
    story_borderline_queue: bool = Field(
        default=True,
        description=(
            "是否把被 LLM 复核提升的配对写入待确认队列（供人工二审）。"
            "关掉后仍会计数，但不会留下可复核的明细"
        ),
    )
    story_borderline_overrides: str = Field(
        default="data/story_borderline_overrides.json",
        description=(
            "人工裁决台账路径（相对项目根）。文件不存在=未启用；"
            "存在但解析失败时会拒绝运行，不会静默当成空台账"
        ),
    )
    story_borderline_pending: str = Field(
        default="data/story_borderline_pending.jsonl",
        description="待人工确认队列路径（只追加的 JSONL，按配对去重）",
    )
    story_review_backlog_max: int = Field(
        default=20,
        ge=0,
        description=(
            "待确认队列积压告警下限（条）。0=关闭该维度。"
            "与滞留阈值分开：这个管'人跟不上机器产出'，下面那个管'人根本没在看'"
        ),
    )
    story_review_max_age_days: float = Field(
        default=14.0,
        ge=0,
        description=(
            "待确认队列最老一条的滞留告警天数。0=关闭该维度。"
            "超过说明 LLM 提升的合并在日报里生效两周仍无人复核——"
            "提升会直接改变独立来源数，放着不看等于默认接受"
        ),
    )
    story_run_ledger: str = Field(
        default="data/story_run_ledger.jsonl",
        description=(
            "轮次台账路径（只追加的 JSONL，每轮一行）。"
            "没有它，'事件数昨天 29 今天 31'与'积压是否持续上涨'都只能靠人记得上次输出"
        ),
    )
    story_run_record: bool = Field(
        default=True,
        description=(
            "是否默认把每轮结果写入台账。纯追加、不改聚类行为，因此默认开；"
            "标定库演练等会产生噪声的场合用 --no-record 单轮关掉"
        ),
    )

    # ---------- 飞书机器人（未配置时降级为日志输出，不报错） ----------
    feishu_webhook_url: str = ""

    # ---------- 采集限速（架构 §2.2，人速护栏） ----------
    collect_interval_sec: int = Field(
        default=120,
        description="两次采集最小间隔秒数，默认 120（1 视频/2 分钟）；硬下限 30s",
    )
    collect_night_start_hour: int = Field(
        default=23, description="夜间静默开始小时（含），该窗口内不采集"
    )
    collect_night_end_hour: int = Field(
        default=7, description="夜间静默结束小时（不含）"
    )

    # ---------- 安全 ----------
    fernet_key: str = Field(
        default="",
        description="Cookie Fernet 加密密钥（base64，64 字节）；空则 Cookie 写入接口拒绝服务",
    )

    # ---------- 日志 ----------
    log_level: str = "INFO"

    # -------------------------------------------------- 校验器 --
    @field_validator("collect_interval_sec")
    @classmethod
    def _validate_collect_interval(cls, value: int) -> int:
        """限速硬下限：间隔禁止低于 30 秒（防止误配成机器速）。"""
        if value < MIN_COLLECT_INTERVAL_SEC:
            raise ValueError(
                f"collect_interval_sec={value}s 低于硬下限 {MIN_COLLECT_INTERVAL_SEC}s："
                "采集必须保持人速，禁止放宽；如需调整只可增大该值（收紧）。"
            )
        return value

    @field_validator("collect_night_start_hour", "collect_night_end_hour")
    @classmethod
    def _validate_hour(cls, value: int) -> int:
        """夜间静默小时必须在 0~23 范围内。"""
        if not 0 <= value <= 23:
            raise ValueError(f"夜间静默小时必须在 0~23 之间，当前为 {value}")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """日志级别白名单校验。"""
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.strip().upper()
        if upper not in allowed:
            raise ValueError(f"log_level 必须是 {sorted(allowed)} 之一，当前为 {value}")
        return upper

    # -------------------------------------------------- 派生属性 --
    @property
    def pg_dsn_plain(self) -> str:
        """原生 psycopg 连接串（libpq keyword/value 格式，scripts/init_db.py 使用）。

        密码含空格等特殊字符时 libpq 格式天然支持，无需 URL 转义。
        """
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_db} "
            f"user={self.pg_user} password={self.pg_password} connect_timeout=5"
        )


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例（进程内缓存）。"""
    return Settings()
