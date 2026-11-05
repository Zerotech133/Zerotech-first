-- ============================================================
-- osint_video 全部 DDL（架构 §2.1 队列两表 + §2.5 osint_video 预留 + v2 增量两表）
-- 执行方式：python scripts/init_db.py（幂等，可重复执行）
-- 命名约定：osint_ 前缀独立体系，与 news_collector 同构不冲突
-- ============================================================

-- ① 链接任务表（状态机核心，架构 §2.1）
CREATE TABLE IF NOT EXISTS osint_link_task (
    id            BIGSERIAL PRIMARY KEY,
    sph_url       TEXT NOT NULL,              -- 原始分享链接
    url_hash      TEXT NOT NULL,              -- SHA1(规范化sph_url)，投递去重
    status        TEXT NOT NULL DEFAULT 'pending',
        -- pending → parsed → downloaded → decrypted → extracted → done
        -- 任意态 → failed（可重试）→ dead（死信，超限）
        -- 特殊: waiting_cookie（Cookie过期挂起，人工更新后自动回 pending）
    route        TEXT NOT NULL DEFAULT 'D',   -- D / B / AUTO(D失败转B)
    video_id     TEXT,                        -- 解析成功后回填
    parse_result JSONB,                       -- {mediaUrl, urlToken, decode_key, author, ...}
    file_md5     TEXT,
    raw_path     TEXT,                        -- 明文mp4落盘路径
    retry_count  INT NOT NULL DEFAULT 0,
    max_retry    INT NOT NULL DEFAULT 3,
    next_retry_at TIMESTAMPTZ,
    error_code   TEXT,                        -- COOKIE_EXPIRED / PARSE_FAIL / DOWNLOAD_FAIL / DECRYPT_FAIL / EXTRACT_FAIL
    error_msg    TEXT,
    priority     SMALLINT NOT NULL DEFAULT 5, -- 数字小先采
    created_by   TEXT DEFAULT 'feishu',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 独立导入任务：不可混入仅支持 sph 的链接 worker 队列。
CREATE TABLE IF NOT EXISTS osint_import_task (
    id BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL CHECK (source_type IN ('link', 'text', 'pdf', 'image', 'video')),
    source_name TEXT NOT NULL,
    source_url TEXT,
    status TEXT NOT NULL CHECK (status IN ('processing', 'pending_processing', 'completed', 'failed')),
    file_path TEXT,
    sha256 TEXT,
    content_text TEXT,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_link_url_hash ON osint_link_task(url_hash);
CREATE INDEX IF NOT EXISTS idx_link_status ON osint_link_task(status, priority, next_retry_at);

-- ①-b 人工重试幂等记录（跨进程/重启持久化）
-- 仅保存受控响应快照与审计元数据；禁止写入 Cookie、Token、raw_path 等敏感载荷。
CREATE TABLE IF NOT EXISTS osint_retry_idempotency (
    idempotency_id  BIGSERIAL PRIMARY KEY,
    task_id         BIGINT NOT NULL REFERENCES osint_link_task(id) ON DELETE CASCADE,
    action          TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_id      TEXT,
    reason          VARCHAR(500) NOT NULL,
    operator        VARCHAR(128) NOT NULL DEFAULT 'admin-ui',
    response        JSONB,
    status          TEXT NOT NULL DEFAULT 'processing',
    response_status SMALLINT,
    processing_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (action = 'retry'),
    CHECK (length(btrim(reason)) BETWEEN 1 AND 500),
    CHECK (status IN ('processing', 'completed')),
    CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
    UNIQUE (task_id, action, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_retry_idempotency_created
    ON osint_retry_idempotency(created_at DESC);
-- Upgrade path for installations created before processing timestamps existed.
ALTER TABLE osint_retry_idempotency ADD COLUMN IF NOT EXISTS processing_at TIMESTAMPTZ;
ALTER TABLE osint_retry_idempotency ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
UPDATE osint_retry_idempotency
SET processing_at = COALESCE(processing_at, created_at),
    updated_at = COALESCE(updated_at, created_at)
WHERE processing_at IS NULL OR updated_at IS NULL;
ALTER TABLE osint_retry_idempotency ALTER COLUMN processing_at SET DEFAULT now();
ALTER TABLE osint_retry_idempotency ALTER COLUMN processing_at SET NOT NULL;
ALTER TABLE osint_retry_idempotency ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE osint_retry_idempotency ALTER COLUMN updated_at SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_retry_idempotency_processing
    ON osint_retry_idempotency(status, processing_at);

-- ② 凭证热更新表（架构 §2.1：Cookie/Token Fernet 加密存储）
CREATE TABLE IF NOT EXISTS osint_credential (
    name        TEXT PRIMARY KEY,             -- 'yuanbao_cookie' / 'collector_b_token'
    value_enc   TEXT NOT NULL,                -- AES-GCM(Fernet) 加密存储
    status      TEXT NOT NULL DEFAULT 'active', -- active / expired
    updated_by  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ③ v2 监控对象注册表（osint_v2_requirements_and_roadmap.md §2）
--    滚雪球机制：投递链接 → 解析出作者元数据 → 不存在则自动入表（source_type='link'）
CREATE TABLE IF NOT EXISTS osint_target (
    target_id     BIGSERIAL PRIMARY KEY,
    platform      TEXT NOT NULL DEFAULT 'wx_channels',  -- 预留跨平台
    nickname      TEXT NOT NULL,
    finder_id     TEXT,                 -- 账号唯一标识
    monitor_level SMALLINT DEFAULT 2,   -- 1核心/2常规/3观察
    source_type   TEXT DEFAULT 'manual',-- manual=人工录入 / link=链接发现(滚雪球) / referral=账号推荐
    source_link   TEXT,                 -- link 型来源的首条价值链接（溯源）
    whitelist     BOOLEAN DEFAULT FALSE,
    cookie_ref    TEXT,                 -- 门户Cookie归属
    last_feed_check TIMESTAMPTZ,
    note          TEXT,
    enabled       BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_target_finder_id ON osint_target(platform, finder_id);
CREATE INDEX IF NOT EXISTS idx_target_enabled ON osint_target(enabled, monitor_level);

-- 链接驱动博主运营状态：从一次性发现升级为历史回溯与每日增量扫描。
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS operation_state TEXT NOT NULL DEFAULT 'link_received';
ALTER TABLE osint_target ALTER COLUMN operation_state SET DEFAULT 'link_received';
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS history_cursor TEXT;
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS last_success_cursor TEXT;
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS last_success_at TIMESTAMPTZ;
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS last_summary_version INT NOT NULL DEFAULT 0;
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS last_summary_hash TEXT;
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS summary_updated_at TIMESTAMPTZ;
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS scan_interval_minutes INT NOT NULL DEFAULT 1440;
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS next_scan_at TIMESTAMPTZ;
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS last_scan_error TEXT;
-- Durable scheduler lease: expired claims can be recovered after worker crashes.
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS claim_token TEXT;
ALTER TABLE osint_target ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_target_scan_lease ON osint_target(lease_until, claim_token);
ALTER TABLE osint_target DROP CONSTRAINT IF EXISTS osint_target_operation_state_check;
ALTER TABLE osint_target ADD CONSTRAINT osint_target_operation_state_check CHECK (operation_state IN ('link_received','creator_resolved','profile_created','history_backfill_running','profile_summarized','daily_monitoring','incremental_update','retryable','dead_letter'));
CREATE INDEX IF NOT EXISTS idx_target_scan_due ON osint_target(enabled, operation_state, next_scan_at);

-- 每次历史回溯/每日扫描均有独立运行记录，支持暂停、恢复、重试与审计。
CREATE TABLE IF NOT EXISTS osint_creator_run (
    run_id BIGSERIAL PRIMARY KEY,
    target_id BIGINT NOT NULL REFERENCES osint_target(target_id) ON DELETE CASCADE,
    run_type TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    input_cursor TEXT,
    output_cursor TEXT,
    items_seen INT NOT NULL DEFAULT 0,
    items_succeeded INT NOT NULL DEFAULT 0,
    items_failed INT NOT NULL DEFAULT 0,
    summary_version INT,
    error_code TEXT,
    error_detail TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (run_type IN ('history_backfill','profile_summary','daily_scan','incremental_update')),
    CHECK (state IN ('pending','running','succeeded','failed','cancelled','dead_letter')),
    CHECK (items_seen >= 0 AND items_succeeded >= 0 AND items_failed >= 0)
);
CREATE INDEX IF NOT EXISTS idx_creator_run_target ON osint_creator_run(target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_creator_run_state ON osint_creator_run(state, run_type);

-- 博主总结是版本化派生知识，不覆盖旧总结。
CREATE TABLE IF NOT EXISTS osint_creator_summary (
    summary_id BIGSERIAL PRIMARY KEY,
    target_id BIGINT NOT NULL REFERENCES osint_target(target_id) ON DELETE CASCADE,
    summary_version INT NOT NULL,
    summary_text TEXT NOT NULL,
    topics JSONB NOT NULL DEFAULT '[]'::JSONB,
    viewpoints JSONB NOT NULL DEFAULT '[]'::JSONB,
    evidence_count INT NOT NULL DEFAULT 0,
    input_cursor TEXT,
    model_version TEXT,
    snapshot_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(target_id, summary_version),
    UNIQUE(target_id, snapshot_hash),
    CHECK (evidence_count >= 0)
);
CREATE INDEX IF NOT EXISTS idx_creator_summary_target ON osint_creator_summary(target_id, summary_version DESC);

-- ④ v2 评论表（osint_v2_requirements_and_roadmap.md §2；T07 CommentCollector 写入）
CREATE TABLE IF NOT EXISTS osint_comment (
    id          BIGSERIAL PRIMARY KEY,
    video_id    TEXT NOT NULL,          -- 关联 osint_video.video_id（T05 建表后可加 FK）
    comment_id  TEXT,                   -- 平台评论ID去重键
    author_name TEXT,                   -- PII: 入库前脱敏策略同v1
    author_id   TEXT,
    content     TEXT NOT NULL,
    like_count  INT,
    reply_count INT,
    published_at TIMESTAMPTZ,
    sentiment   TEXT,                   -- pos/neu/neg (analyzer回填)
    sentiment_conf FLOAT,
    collected_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(video_id, comment_id)
);
CREATE INDEX IF NOT EXISTS idx_comment_video ON osint_comment(video_id, collected_at DESC);

-- ⑤ osint_video 结构化入库表（架构 §2.5，T05 启用；随本批一并建好，
--    PoC 期 StructuredDoc 以 JSON 文件形态存在于 data/work/{video_id}/）
CREATE TABLE IF NOT EXISTS osint_video (
    video_id        TEXT PRIMARY KEY,          -- sph exportId；无则 file_md5
    author          TEXT,
    author_id       TEXT,
    publish_time    TIMESTAMPTZ,
    source_url      TEXT,                      -- sph 链接
    duration_sec    FLOAT,
    file_md5        TEXT UNIQUE,               -- 视频级去重键
    raw_path        TEXT,                      -- 明文mp4（保留策略见架构§8-7）
    asr_text        TEXT,                      -- 全文（脱敏后）
    ocr_text        TEXT,
    aligned_segments JSONB,                    -- §3 segments 数组
    entities        JSONB,                     -- LLM/规则抽取的公司/人名/产品
    topics          JSONB,
    compliance      JSONB,                     -- {whitelisted, pii_masked, minhash, quarantine}
    metrics         JSONB,                     -- {asr_conf, ocr_conf, coverage}
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_video_author ON osint_video(author);
CREATE INDEX IF NOT EXISTS idx_video_publish ON osint_video(publish_time DESC);

-- ⑥ 持久化知识单元：视频只是临时输入，结构化结果为核心资产。
CREATE TABLE IF NOT EXISTS osint_knowledge_unit (
    unit_id         BIGSERIAL PRIMARY KEY,
    unit_key        TEXT NOT NULL,
    unit_version    INT NOT NULL DEFAULT 1,
    unit_type       TEXT NOT NULL DEFAULT 'fact',
    title           TEXT,
    content         TEXT NOT NULL DEFAULT '',
    source_channel  TEXT NOT NULL DEFAULT 'manual',
    source_video_id TEXT,
    source_url      TEXT,
    source_link     TEXT,
    start_sec       DOUBLE PRECISION,
    end_sec         DOUBLE PRECISION,
    content_hash    TEXT,
    review_status   TEXT NOT NULL DEFAULT 'unreviewed',
    blogger_key     TEXT,
    resource_key    TEXT,
    facts           JSONB NOT NULL DEFAULT '{}'::JSONB,
    entities        JSONB NOT NULL DEFAULT '[]'::JSONB,
    topics          JSONB NOT NULL DEFAULT '[]'::JSONB,
    evidence_refs   JSONB NOT NULL DEFAULT '[]'::JSONB,
    quality         JSONB NOT NULL DEFAULT '{}'::JSONB,
    status          TEXT NOT NULL DEFAULT 'active',
    truth_status    TEXT NOT NULL DEFAULT 'unknown',
    extension       JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (source_channel IN ('asr', 'ocr', 'subtitle', 'manual')),
    CHECK (review_status IN ('unreviewed', 'reviewed', 'rejected')),
    CHECK (start_sec IS NULL OR (start_sec NOT IN ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision) AND start_sec >= 0)),
    CHECK (end_sec IS NULL OR (end_sec NOT IN ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision) AND end_sec >= 0)),
    CHECK (start_sec IS NULL OR end_sec IS NULL OR end_sec > start_sec),
    CHECK (source_channel = 'manual' OR source_url IS NOT NULL OR source_link IS NOT NULL OR source_video_id IS NOT NULL),
    CHECK (unit_version >= 1),
    CHECK (truth_status IN ('unknown', 'correct', 'incorrect', 'contested')),
    UNIQUE (unit_key, unit_version),
    UNIQUE (unit_key, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_unit_key ON osint_knowledge_unit(unit_key, unit_version DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_unit_blogger ON osint_knowledge_unit(blogger_key);
CREATE INDEX IF NOT EXISTS idx_knowledge_unit_video ON osint_knowledge_unit(source_video_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_unit_type ON osint_knowledge_unit(unit_type, status);

-- ⑦ 博主历史整理：history_key 由采集/整理层生成，保证重复观察幂等。
CREATE TABLE IF NOT EXISTS osint_blogger_history (
    history_id      BIGSERIAL PRIMARY KEY,
    blogger_key     TEXT NOT NULL,
    history_key     TEXT NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL,
    event_type      TEXT NOT NULL DEFAULT 'snapshot',
    summary         TEXT,
    attributes      JSONB NOT NULL DEFAULT '{}'::JSONB,
    source_video_id TEXT,
    source_unit_id  BIGINT REFERENCES osint_knowledge_unit(unit_id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    observation_hash TEXT NOT NULL DEFAULT '',
    UNIQUE (blogger_key, history_key, observation_hash)
);
CREATE INDEX IF NOT EXISTS idx_blogger_history_time ON osint_blogger_history(blogger_key, observed_at DESC);

-- ⑧ 策略与知识单元的证据关系：独立边表便于审计、评分和撤销。
CREATE TABLE IF NOT EXISTS osint_strategy_evidence (
    evidence_id       BIGSERIAL PRIMARY KEY,
    strategy_key      TEXT NOT NULL,
    knowledge_unit_key TEXT NOT NULL,
    knowledge_unit_version INT NOT NULL DEFAULT 1,
    evidence_key      TEXT NOT NULL UNIQUE,
    FOREIGN KEY (knowledge_unit_key, knowledge_unit_version)
      REFERENCES osint_knowledge_unit(unit_key, unit_version) ON DELETE CASCADE,
    evidence_role     TEXT NOT NULL DEFAULT 'supporting',
    quote             TEXT,
    relevance         DOUBLE PRECISION,
    confidence        DOUBLE PRECISION,
    quote_start_sec   DOUBLE PRECISION,
    quote_end_sec     DOUBLE PRECISION,
    evidence_status   TEXT NOT NULL DEFAULT 'draft',
    snapshot_hash     TEXT,
    metadata          JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (relevance IS NULL OR (relevance >= 0 AND relevance <= 1)),
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    CHECK (quote_start_sec IS NULL OR (quote_start_sec NOT IN ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision) AND quote_start_sec >= 0)),
    CHECK (quote_end_sec IS NULL OR (quote_end_sec NOT IN ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision) AND quote_end_sec >= 0)),
    CHECK (quote_start_sec IS NULL OR quote_end_sec IS NULL OR quote_end_sec > quote_start_sec),
    CHECK (evidence_status <> 'valid' OR (quote IS NOT NULL AND length(btrim(quote)) > 0)),
    CHECK (evidence_status <> 'valid' OR snapshot_hash IS NOT NULL),
    CHECK (knowledge_unit_version >= 1),
    CHECK (evidence_status IN ('draft', 'valid', 'rejected', 'superseded'))
);
CREATE INDEX IF NOT EXISTS idx_strategy_evidence_strategy ON osint_strategy_evidence(strategy_key);
CREATE INDEX IF NOT EXISTS idx_strategy_evidence_unit ON osint_strategy_evidence(knowledge_unit_key);

-- Upgrade path for installations created by the previous five-table schema.
ALTER TABLE osint_knowledge_unit DROP CONSTRAINT IF EXISTS osint_knowledge_unit_unit_key_key;
ALTER TABLE osint_knowledge_unit ADD COLUMN IF NOT EXISTS unit_version INT NOT NULL DEFAULT 1;
ALTER TABLE osint_knowledge_unit ADD COLUMN IF NOT EXISTS source_channel TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE osint_knowledge_unit ADD COLUMN IF NOT EXISTS source_link TEXT;
ALTER TABLE osint_knowledge_unit ADD COLUMN IF NOT EXISTS start_sec DOUBLE PRECISION;
ALTER TABLE osint_knowledge_unit ADD COLUMN IF NOT EXISTS end_sec DOUBLE PRECISION;
ALTER TABLE osint_knowledge_unit ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uk_knowledge_unit_version ON osint_knowledge_unit(unit_key, unit_version);
CREATE UNIQUE INDEX IF NOT EXISTS uk_knowledge_unit_content ON osint_knowledge_unit(unit_key, content_hash);
ALTER TABLE osint_knowledge_unit ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'unreviewed';
ALTER TABLE osint_knowledge_unit ADD COLUMN IF NOT EXISTS resource_key TEXT;
ALTER TABLE osint_knowledge_unit ADD COLUMN IF NOT EXISTS truth_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE osint_knowledge_unit ADD COLUMN IF NOT EXISTS extension JSONB NOT NULL DEFAULT '{}'::JSONB;
ALTER TABLE osint_blogger_history DROP CONSTRAINT IF EXISTS osint_blogger_history_blogger_key_history_key_key;
ALTER TABLE osint_blogger_history ADD COLUMN IF NOT EXISTS observation_hash TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS uk_blogger_observation ON osint_blogger_history(blogger_key, history_key, observation_hash);
ALTER TABLE osint_strategy_evidence ADD COLUMN IF NOT EXISTS knowledge_unit_version INT NOT NULL DEFAULT 1;
ALTER TABLE osint_strategy_evidence ADD COLUMN IF NOT EXISTS quote_start_sec DOUBLE PRECISION;
ALTER TABLE osint_strategy_evidence ADD COLUMN IF NOT EXISTS quote_end_sec DOUBLE PRECISION;
ALTER TABLE osint_strategy_evidence ADD COLUMN IF NOT EXISTS evidence_status TEXT NOT NULL DEFAULT 'draft';
ALTER TABLE osint_strategy_evidence ADD COLUMN IF NOT EXISTS snapshot_hash TEXT;

-- Durable resource metadata; media path is temporary and may be cleared by retention.
CREATE TABLE IF NOT EXISTS osint_knowledge_resource (
    resource_id BIGSERIAL PRIMARY KEY,
    resource_key TEXT NOT NULL UNIQUE,
    blogger_key TEXT,
    source_url TEXT,
    platform_content_id TEXT,
    published_at TIMESTAMPTZ,
    duration_sec DOUBLE PRECISION,
    status TEXT NOT NULL DEFAULT 'candidate',
    temporary_media_path TEXT,
    media_expires_at TIMESTAMPTZ,
    asr_text TEXT,
    ocr_text TEXT,
    subtitle_text TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (duration_sec IS NULL OR (duration_sec NOT IN ('NaN'::double precision, 'Infinity'::double precision, '-Infinity'::double precision) AND duration_sec >= 0)),
    CHECK (status IN ('candidate', 'reviewed', 'published', 'deprecated', 'quarantined'))
);
CREATE INDEX IF NOT EXISTS idx_knowledge_resource_blogger ON osint_knowledge_resource(blogger_key);
CREATE INDEX IF NOT EXISTS idx_knowledge_resource_expiry ON osint_knowledge_resource(media_expires_at);
ALTER TABLE osint_knowledge_resource ADD COLUMN IF NOT EXISTS asr_text TEXT;
ALTER TABLE osint_knowledge_resource ADD COLUMN IF NOT EXISTS ocr_text TEXT;
ALTER TABLE osint_knowledge_resource ADD COLUMN IF NOT EXISTS subtitle_text TEXT;

-- Processing run audit: PG facts remain authoritative across retries/failures.
CREATE TABLE IF NOT EXISTS osint_processing_run (
    run_id BIGSERIAL PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    pipeline_version TEXT NOT NULL DEFAULT 'v1',
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_processing_run_status ON osint_processing_run(status, updated_at);

-- Media lifecycle references are disposable; durable resource/unit/evidence rows are not.
CREATE TABLE IF NOT EXISTS osint_media_asset (
    asset_id BIGSERIAL PRIMARY KEY,
    asset_key TEXT NOT NULL UNIQUE,
    resource_key TEXT REFERENCES osint_knowledge_resource(resource_key) ON DELETE SET NULL,
    run_key TEXT REFERENCES osint_processing_run(run_key) ON DELETE SET NULL,
    media_path TEXT,
    media_status TEXT NOT NULL DEFAULT 'active',
    deleted_at TIMESTAMPTZ,
    deletion_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (media_status IN ('active', 'expired', 'deleted', 'quarantined'))
);
CREATE INDEX IF NOT EXISTS idx_media_asset_resource ON osint_media_asset(resource_key, media_status);

-- Outbox-like index fan-out state; PG remains authoritative when external indexes fail.
CREATE TABLE IF NOT EXISTS osint_index_task (
    task_id BIGSERIAL PRIMARY KEY,
    resource_key TEXT NOT NULL REFERENCES osint_knowledge_resource(resource_key) ON DELETE CASCADE,
    target TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    processing_run_key TEXT,
    error_message TEXT,
    attempt_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(resource_key, target),
    CHECK (target IN ('pg', 'qdrant', 'graph')),
    CHECK (status IN ('pending', 'running', 'succeeded', 'failed'))
);
CREATE INDEX IF NOT EXISTS idx_index_task_status ON osint_index_task(status, updated_at);

-- ============================================================
-- ⑨ 事件层（story）：事实的横向聚合（P0 增量，向后兼容，不破坏既有契约）
--
-- 背景：resource → knowledge_unit(fact) → strategy_evidence 是一条"单文档"
--   链路，能回答"这篇报道说了什么"，但回答不了"财联社/东财/雪球说的是
--   不是同一件事"。缺了横向聚合层，下游（kg_brief / event_graph）只能
--   消费散点事实，这正是 2026-10-17 报告 F2「采了没人吃」的物理成因。
--
-- 建模参照 AIHOT 的 article→fact→story 三层（MIT 许可，仅取表结构与判定
--   口径，不引入其 Node/TypeScript 运行时）：article 对应
--   osint_knowledge_resource，fact 对应 osint_knowledge_unit，本批补 story。
-- ============================================================

-- 事件主体：一个 story = 一件真实发生的事 + 它的直接进展。
CREATE TABLE IF NOT EXISTS osint_story (
    story_id            BIGSERIAL PRIMARY KEY,
    story_key           TEXT NOT NULL UNIQUE,     -- 稳定业务键，供下游长期引用
    title               TEXT NOT NULL,            -- 归一化后的中文标题
    summary             TEXT,
    first_seen_at       TIMESTAMPTZ NOT NULL,
    last_seen_at        TIMESTAMPTZ NOT NULL,
    -- 独立来源数：按媒体/账号去重后计数，是热度的分母。
    -- 同一家媒体发 5 篇不等于 5 个来源，二者必须分开统计。
    independent_sources INT NOT NULL DEFAULT 1,
    source_names        JSONB NOT NULL DEFAULT '[]'::JSONB,
    fact_count          INT NOT NULL DEFAULT 1,   -- 归属事实条数（含重复报道）
    heat                DOUBLE PRECISION NOT NULL DEFAULT 0,  -- 独立来源数 + 时间衰减
    stage               TEXT NOT NULL DEFAULT 'emerging',
    entities            JSONB NOT NULL DEFAULT '[]'::JSONB,
    topics              JSONB NOT NULL DEFAULT '[]'::JSONB,
    confidence          DOUBLE PRECISION,
    review_status       TEXT NOT NULL DEFAULT 'unreviewed',
    model_version       TEXT,                     -- 判定所用模型，便于回溯
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (independent_sources >= 1),
    CHECK (fact_count >= 1),
    CHECK (independent_sources <= fact_count),
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    CHECK (stage IN ('emerging', 'developing', 'peak', 'fading', 'closed')),
    CHECK (review_status IN ('unreviewed', 'reviewed', 'rejected'))
);
-- 消费侧主入口：按热度出日报
CREATE INDEX IF NOT EXISTS idx_story_heat ON osint_story(heat DESC, last_seen_at DESC);
-- 算子主入口：按时间窗召回
CREATE INDEX IF NOT EXISTS idx_story_window ON osint_story(last_seen_at DESC, stage);
CREATE INDEX IF NOT EXISTS idx_story_review ON osint_story(review_status, last_seen_at DESC);

-- 归属边：(unit_key, unit_version) 作主键 = 一条事实只能属于一个事件。
-- 该约束同时保证聚类算子可幂等重跑，且不会因重复归属把独立来源数算高。
CREATE TABLE IF NOT EXISTS osint_story_member (
    unit_key     TEXT NOT NULL,
    unit_version INT NOT NULL DEFAULT 1,
    story_id     BIGINT NOT NULL REFERENCES osint_story(story_id) ON DELETE CASCADE,
    -- 来源身份（媒体域名优先，其次 blogger_key，兜底 'unknown'）。
    -- 落库而非运行时计算，独立来源数才能用 SQL 聚合，且历史口径可复现。
    source_id    TEXT,
    relation     TEXT NOT NULL,
        -- same_story 同一件事 / progress 同一事件的直接进展 / roundup 多话题盘点
        -- （unrelated 不入表：不相关就不该产生归属边）
    similarity   DOUBLE PRECISION,   -- 召回余弦，用于事后复核与阈值调优
    decided_by   TEXT NOT NULL,
        -- vector 直接由余弦判定 / llm 模型判定 / llm_recheck 低置信换模复核
        -- rule 规则判定器（无 LLM 时的兜底）/ manual 人工
        -- 标签必须指向真正下结论的一方：规则判的不能标成 llm_recheck，
        -- 否则事后出误合并时无从追责（2026-11-04 由实测事故驱动补 rule）
        -- 重算时 decided_by='manual' 的行必须跳过，人工优先于模型
    rationale    TEXT,               -- 判定理由，供审计与争议复盘
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (unit_key, unit_version),
    CHECK (relation IN ('same_story', 'progress', 'roundup')),
    CONSTRAINT osint_story_member_decided_by_check
        CHECK (decided_by IN ('vector', 'llm', 'llm_recheck', 'rule', 'manual')),
    CHECK (similarity IS NULL OR (similarity >= -1 AND similarity <= 1)),
    CHECK (unit_version >= 1)
);
CREATE INDEX IF NOT EXISTS idx_story_member_story ON osint_story_member(story_id);
CREATE INDEX IF NOT EXISTS idx_story_member_manual ON osint_story_member(decided_by)
    WHERE decided_by = 'manual';

-- 存量库迁移：v1 的 CHECK 不含 'rule'，而规则判定器现在按实际判定方标记
-- decided_by='rule'。旧库若不改约束，写入会直接被 CHECK 拒绝。
-- DROP 后再 ADD 是幂等的（表为空时开销为零；十万级行也只是一次顺序扫描），
-- 且不依赖 pg_constraint 的自动命名规则，避免"约束名猜错导致迁移静默跳过"。
ALTER TABLE IF EXISTS osint_story_member
    DROP CONSTRAINT IF EXISTS osint_story_member_decided_by_check;
ALTER TABLE IF EXISTS osint_story_member
    ADD CONSTRAINT osint_story_member_decided_by_check
    CHECK (decided_by IN ('vector', 'llm', 'llm_recheck', 'rule', 'manual'));

-- 日报消费视图：外部消费方（kg_brief / event_graph / 外部 BI）直接读它，
-- 不必知道事件层的表结构。视图名与列名即为对外契约，改动需走版本化。
-- 口径：近 24 小时、至少 2 家独立来源、排除已否决事件，按热度倒序。
CREATE OR REPLACE VIEW v_story_brief AS
SELECT s.story_key,
       s.title,
       s.summary,
       s.first_seen_at,
       s.last_seen_at,
       s.independent_sources,
       s.fact_count,
       s.heat,
       s.stage,
       s.source_names,
       s.topics
  FROM osint_story s
 WHERE s.last_seen_at >= now() - interval '24 hours'
   AND s.independent_sources >= 2
   AND s.review_status <> 'rejected'
 ORDER BY s.heat DESC, s.last_seen_at DESC;

-- ============================================================
-- ⑳ 实体别名归一（P1-B）：把同一实体的多种写法收敛到一个 canonical_id。
--
-- 为什么需要：osint_knowledge_unit.entities 由 LLM/规则抽取，同一家公司在
--   不同报道里可能写成 "宁德时代" / "CATL" / "宁德时代新能源科技股份有限
--   公司"。事件层算子（knowledge/story_group.py）的候选召回是
--   `pivot.entities & other.entities` 的集合交集，三种写法即三个不同实体，
--   彼此选不中 → 同一件事被拆成三个事件 → 独立来源数被系统性低估，
--   而独立来源数正是热度的分母。
--
-- 归一方向：只做「别名 → 唯一 canonical_id」的单向收敛。算子读取本表后在
--   内存中把实体键映射为 canonical（见 knowledge/entity_alias.py），
--   **不**回写 entities 列——原文是审计依据，改写会破坏可追溯性。
--
-- 二义性处置（本表的硬约束，务必不要放宽）：
--   约束是 UNIQUE (alias)，而**不是** UNIQUE (alias, canonical_id)。
--   后者允许同一别名同时挂到两个 canonical 上，归一会立刻变成二义：
--   "苹果" 既可指水果也可指公司，一旦把它同时映射到 apple_inc 与
--   apple_fruit，`resolve()` 的返回值就取决于读表顺序，独立来源数会被
--   随机污染，且故障表现为"数字偶尔偏大"，极难定位。
--   因此有歧义的别名**根本不允许入表**：宁可漏归一（事件仍旧拆开，
--   可事后补录），不可错归一（错误合并不可逆地抬高热度）。
--   同一 canonical 可以有多行（这是"反查全部别名"的正常形态）。
-- ============================================================

CREATE TABLE IF NOT EXISTS osint_entity_alias (
    alias_id      BIGSERIAL PRIMARY KEY,
    -- 别名原文。保留原样（大小写、全半角、法定后缀）便于人工核对；
    -- 比较时由 knowledge/entity_alias.py 统一过 normalize_text()。
    alias         TEXT NOT NULL,
    -- 归一后 ID（slug，如 'catl'）。指向"实体"而非本表某一行，
    -- 因此不需要自引用外键：一个 canonical 没有别名行时仍是合法实体。
    canonical_id  TEXT NOT NULL,
    alias_type    TEXT NOT NULL DEFAULT 'variant',
        -- abbr 英文缩写 / en 英文全称 / cn_full 中文全称 / former_name 曾用名
        -- / product 产品名 / variant 其他书写变体
    source        TEXT NOT NULL DEFAULT 'manual',
        -- manual 人工录入 / backfill 存量回填 / llm 模型推导。
        -- 区分来源是为了让"机器推导错了"能批量回滚（WHERE source='llm'），
        -- 也避免人工修正被下一轮回填覆盖。
    confidence    DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        -- 人工录入默认 1.0；机器推导应 < 1.0，供审计时按阈值筛。
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 别名全局唯一：一个写法只能有一个归属，从物理上排除二义（见上方说明）。
    UNIQUE (alias),
    CHECK (alias <> ''),
    CHECK (canonical_id <> ''),
    CHECK (alias_type IN ('abbr', 'en', 'cn_full', 'former_name', 'product', 'variant')),
    CHECK (source IN ('manual', 'backfill', 'llm')),
    CHECK (confidence >= 0 AND confidence <= 1)
);

-- 大小写变体也不允许并存：UNIQUE(alias) 是大小写敏感的，
-- 'CATL' 与 'catl' 会同时通过校验，但读表归一后是同一个键，dict 会互相覆盖。
-- 该唯一索引把这类冲突提前到写入阶段暴露（宁可插入失败，不可静默覆盖）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_alias_lower ON osint_entity_alias (lower(alias));
-- 反查入口：给定 canonical_id 列出它的全部写法（人工复核 / 前端展示）
CREATE INDEX IF NOT EXISTS idx_entity_alias_canonical ON osint_entity_alias(canonical_id);
