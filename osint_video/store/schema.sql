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
