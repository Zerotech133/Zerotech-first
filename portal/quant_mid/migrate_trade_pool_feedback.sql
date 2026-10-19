-- K-QUANT-004: trade_pool 双向 Agent 反馈列迁移 (quant_system port 5442)
-- ⚠️ 执行须老田批准 —— 脚本只写不跑。WS3-FB 交付，批准后手工执行：
--    psql -h localhost -p 5442 -U quant_user -d quant_system -f 本文件
-- 幂等: information_schema 判断列/约束已存在则跳过，可重复执行，无副作用。
-- 语义来源: portal/quant_mid/trade_pool.py set_feedback() (HERMES 回流郭嘉反馈)
--   feedback_type ∈ {approve, reject, calibrate, skip, other}  可空
--   feedback_note TEXT                                          可空
--   feedback_score NUMERIC 0.000~1.000                          可空
--   feedback_at  TIMESTAMPTZ (应用层 NOW() 写入)                可空
-- 版本: v1.0 2026-10-19

BEGIN;

-- 1) 四列: 列已存在则跳过
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'trade_pool'
          AND column_name  = 'feedback_type'
    ) THEN
        ALTER TABLE trade_pool ADD COLUMN feedback_type VARCHAR(20);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'trade_pool'
          AND column_name  = 'feedback_note'
    ) THEN
        ALTER TABLE trade_pool ADD COLUMN feedback_note TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'trade_pool'
          AND column_name  = 'feedback_score'
    ) THEN
        ALTER TABLE trade_pool ADD COLUMN feedback_score NUMERIC(4,3);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'trade_pool'
          AND column_name  = 'feedback_at'
    ) THEN
        ALTER TABLE trade_pool ADD COLUMN feedback_at TIMESTAMPTZ;
    END IF;
END
$$;

-- 2) CHECK 约束: 同名约束已存在则跳过 (旧行全 NULL, CHECK 对 NULL 不生效, 安全)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema = 'public'
          AND table_name   = 'trade_pool'
          AND constraint_type = 'CHECK'
          AND constraint_name = 'trade_pool_feedback_type_check'
    ) THEN
        ALTER TABLE trade_pool ADD CONSTRAINT trade_pool_feedback_type_check
            CHECK (feedback_type IS NULL OR feedback_type IN
                   ('approve', 'reject', 'calibrate', 'skip', 'other'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema = 'public'
          AND table_name   = 'trade_pool'
          AND constraint_type = 'CHECK'
          AND constraint_name = 'trade_pool_feedback_score_check'
    ) THEN
        ALTER TABLE trade_pool ADD CONSTRAINT trade_pool_feedback_score_check
            CHECK (feedback_score IS NULL OR (feedback_score >= 0.0 AND feedback_score <= 1.0));
    END IF;
END
$$;

-- 3) 郭嘉校准查询索引: 只索引已反馈的行 (CREATE INDEX IF NOT EXISTS 本身幂等)
CREATE INDEX IF NOT EXISTS idx_trade_pool_feedback
    ON trade_pool(feedback_at)
    WHERE feedback_type IS NOT NULL;

COMMIT;
