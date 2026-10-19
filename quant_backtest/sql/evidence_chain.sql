-- ============================================================================
-- B1 证据链库 DDL（M0 计划 §五 B1 / 路线图 P0：入池/出池/授权/结果四态）
--
-- ⚠️ 执行须老田批准：本文件仅为交付件，任何建表动作（SQLite 或 PG5442）
--    均须经老田批准后另行实施。工位只产代码件，未连任何真实库。
--
-- 栈选型：与 quant_backtest/db.py 同栈（SQLite, WAL, 单写者串行）。
-- 未来迁 PG5442 时按文末【PG 迁移注记】调整方言，语义不变。
--
-- 设计要点：
--   · evidence_events   append-only 事件账本（每次跃迁一行，禁 UPDATE/DELETE，
--                       SQLite 用触发器强制；PG 侧用 RULE/触发器等价实现）
--   · evidence_state    当前状态表（可由 events 全量重建，仅供 O(1) 读）
--   · 幂等键            对齐平台 approvals 账本判例「域:主体:内容哈希」：
--                       evidence:<entry_id>:<to_state>:<payload_hash 前 16 位>
--                       payload_hash = sha256(canonical JSON)，同键同 payload
--                       重放返回首次结果，同键不同 payload 视为 conflict。
--   · 四态状态机（线性，非法跃迁由应用层抛 IllegalTransitionError）：
--       入池(pool_in) → 授权(authorized) → 出池(pool_out) → 结果(result)
--       起始态为 NULL（仅允许 pool_in 入账）。
-- ============================================================================

CREATE TABLE IF NOT EXISTS evidence_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,   -- PG: BIGSERIAL
    idempotency_key TEXT NOT NULL UNIQUE,                -- 域:主体:态:哈希
    entry_id        TEXT NOT NULL,                       -- 主体 strategy:trade_date:symbol
    from_state      TEXT,                                -- NULL=首跃迁（入池）
    to_state        TEXT NOT NULL
                    CHECK (to_state IN
                        ('pool_in','authorized','pool_out','result')),
    payload_json    TEXT NOT NULL,                       -- canonical JSON（sorted/无空白/禁 NaN）
    payload_hash    TEXT NOT NULL,                       -- sha256 hex 全长
    trace_id        TEXT,                                -- 贯穿平台 trace（可空）
    actor           TEXT NOT NULL DEFAULT 'system',      -- 跃迁发起方
    created_at      TEXT NOT NULL                        -- ISO8601 带时区；PG: TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ec_events_entry ON evidence_events(entry_id, id);
CREATE INDEX IF NOT EXISTS idx_ec_events_state ON evidence_events(to_state);
CREATE INDEX IF NOT EXISTS idx_ec_events_trace ON evidence_events(trace_id);

CREATE TABLE IF NOT EXISTS evidence_state (
    entry_id     TEXT PRIMARY KEY,
    state        TEXT NOT NULL
                 CHECK (state IN
                     ('pool_in','authorized','pool_out','result')),
    symbol       TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    strategy     TEXT NOT NULL,
    last_key     TEXT NOT NULL,          -- 最近一次跃迁的幂等键
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ec_state_date ON evidence_state(trade_date);
CREATE INDEX IF NOT EXISTS idx_ec_state_sym  ON evidence_state(symbol);

-- ── append-only 强制（SQLite 触发器）─────────────────────────────────────
CREATE TRIGGER IF NOT EXISTS trg_ec_events_no_update
BEFORE UPDATE ON evidence_events
BEGIN
    SELECT RAISE(ABORT, 'evidence_events is append-only: UPDATE denied');
END;

CREATE TRIGGER IF NOT EXISTS trg_ec_events_no_delete
BEFORE DELETE ON evidence_events
BEGIN
    SELECT RAISE(ABORT, 'evidence_events is append-only: DELETE denied');
END;

-- ============================================================================
-- 【PG 迁移注记】（迁 PG5442 时执行须另行批准，本段不随 SQLite DDL 生效）
--   · INTEGER PRIMARY KEY AUTOINCREMENT → BIGSERIAL（或 IDENTITY）
--   · TEXT 时间列 → TIMESTAMPTZ；created_at 默认 now()
--   · SQLite 触发器 → CREATE RULE 或 AFTER 触发器 + RAISE EXCEPTION
--   · UNIQUE 约束照搬；索引照搬（可按查询计划加部分索引）
-- ============================================================================
