-- ============================================================================
-- strategy schema：情报层置信账本（数据→信息→情报三级血缘）
-- 纯增量 DDL（IF NOT EXISTS），绝不 DROP / ALTER 现有对象。
-- 执行：docker exec quant-pg psql -U quant_user -d quant_system -f - < schema.sql
-- 编码：UTF-8 无 BOM
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS strategy;

-- 汇总信号表：各策略信号融合后的最终产出
-- contributions 结构示例：
--   {"kg_boost": {"score": 78.5, "weight": 0.5}, "chan_monitor": {"score": 65.0, "weight": 0.5}}
CREATE TABLE IF NOT EXISTS strategy.signals_combined (
    trade_date   text        NOT NULL,
    symbol       text        NOT NULL,
    action       text        NOT NULL,
    score        numeric     NOT NULL,
    confidence   numeric     NOT NULL,
    contributions jsonb      NOT NULL DEFAULT '{}',
    divergence   boolean     NOT NULL DEFAULT false,
    rationale    text,
    created_at   timestamptz DEFAULT now(),
    PRIMARY KEY (trade_date, symbol)
);

-- 反馈表：人工/AI 复核对信号的 verdict（accept/reject/mute）
CREATE TABLE IF NOT EXISTS strategy.feedback (
    id         bigserial   PRIMARY KEY,
    trade_date text        NOT NULL,
    symbol     text        NOT NULL,
    strategy   text,
    verdict    text        NOT NULL CHECK (verdict IN ('accept','reject','mute')),
    created_at timestamptz DEFAULT now()
);

-- 策略权重表：动态调整各策略在融合中的权重
CREATE TABLE IF NOT EXISTS strategy.weights (
    strategy_id text        PRIMARY KEY,
    weight      numeric     NOT NULL DEFAULT 0.5,
    updated_at  timestamptz DEFAULT now()
);

-- ============================================================================
-- 种子数据：初始策略权重（幂等，重复执行不覆盖）
-- ============================================================================
INSERT INTO strategy.weights (strategy_id, weight) VALUES
    ('kg_boost', 0.5),
    ('chan_monitor', 0.5)
ON CONFLICT (strategy_id) DO NOTHING;

-- P2 upsert 改造（2026-10-17）：docker exec \copy 兜底的中转暂存表
CREATE TABLE IF NOT EXISTS strategy._stg_combined (
    trade_date text, symbol text, action text, score numeric,
    confidence numeric, contributions jsonb, divergence boolean,
    rationale text);
