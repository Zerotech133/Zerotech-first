-- K-QUANT-001: trade_pool 建表 SQL (quant_system port 5442)
-- 流水线: 选股Agent → pending_research → 投研 → approved → 建仓 → active_watch → 盯盘 → watch_alert → closed_*
-- 字段对齐 stock_basic(symbol VARCHAR/text, 市场 SH/SZ)
-- 版本: v1.0 2026-10-19

BEGIN;

CREATE TABLE IF NOT EXISTS trade_pool (
    id              SERIAL PRIMARY KEY,
    stock_code      VARCHAR(10)  NOT NULL,   -- 000001.SZ / 600000.SH (与 stock_basic.symbol 同格式)
    stock_name      VARCHAR(20)  NOT NULL DEFAULT '',
    signal          VARCHAR(10)  NOT NULL,   -- BUY / SELL / HOLD / WATCH
    confidence      NUMERIC(4,3)  NOT NULL,   -- 0.000 ~ 1.000
    entry_price     NUMERIC(10,3),           -- 建仓价 (投研填)
    entry_date      DATE,                    -- 建仓日
    stop_loss       NUMERIC(10,3),           -- 埋点① 止损价
    take_profit     NUMERIC(10,3),           -- 埋点② 止盈价
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending_research',
    source_agent    VARCHAR(40)  NOT NULL,   -- 选股Agent标识 (quant_a4 / chan_monitor / ...)
    rationale       TEXT,                    -- 选股理由
    position_pct    NUMERIC(5,2),            -- 建仓仓位占比 %
    alert_triggered BOOLEAN      NOT NULL DEFAULT FALSE,
    alert_detail    TEXT,                    -- 异动详情 (盯盘写)
    closed_price    NUMERIC(10,3),           -- 平仓价 (闭环追踪)
    closed_at       TIMESTAMPTZ,             -- 平仓时间
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT trade_pool_signal_check
        CHECK (signal IN ('BUY', 'SELL', 'HOLD', 'WATCH')),
    CONSTRAINT trade_pool_confidence_check
        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CONSTRAINT trade_pool_status_check
        CHECK (status IN (
            'pending_research',     -- 选股Agent推池，待投研处理
            'researching',          -- 投研评估中
            'approved',             -- 投研批准建仓
            'rejected',             -- 投研否决
            'building_position',    -- 建仓中
            'active_watch',         -- 持仓中，盯盘监控
            'watch_alert',          -- 异动已触发
            'closed_profit',        -- 止盈平仓
            'closed_loss',          -- 止损平仓
            'closed_other'          -- 其他平仓
        ))
);

-- 状态机索引: 投研轮询、盯盘轮询
CREATE INDEX IF NOT EXISTS idx_trade_pool_status  ON trade_pool(status);
CREATE INDEX IF NOT EXISTS idx_trade_pool_code    ON trade_pool(stock_code);
CREATE INDEX IF NOT EXISTS idx_trade_pool_active  ON trade_pool(status)
    WHERE status IN ('active_watch', 'watch_alert', 'pending_research');

-- updated_at 自动维护触发器
CREATE OR REPLACE FUNCTION _trade_pool_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trade_pool_touch_updated_at ON trade_pool;
CREATE TRIGGER trade_pool_touch_updated_at
    BEFORE UPDATE ON trade_pool
    FOR EACH ROW
    EXECUTE FUNCTION _trade_pool_touch_updated_at();

COMMIT;
