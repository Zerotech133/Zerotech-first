"""幂等注册 CHAN_V7 策略与版本（STAGING，不实盘下单）。

运行方式：
    PYTHONPATH=src .venv/bin/python scripts/seed_chan_v7.py

多次运行安全（ON CONFLICT DO NOTHING）。
"""
import psycopg2

from zhice_os_v2.config.settings import get_settings

conn = psycopg2.connect(get_settings().db_main_url)
cur = conn.cursor()

# 注册策略主表行
cur.execute("""
INSERT INTO v2_strategy (strategy_id, strategy_name, market, strategy_type,
                         status, description, created_at, updated_at)
VALUES ('v1-cn-strat-4-v7', '[V7] 缠论增强版', 'CN', 'CHAN_V7', 'ACTIVE',
        'v1几何选股+周线门+大盘regime门+30分钟确认+5%止损+15交易日时间止损+中枢下沿动态出场',
        now(), now())
ON CONFLICT (strategy_id) DO NOTHING;
""")

# 注册版本行（STAGING：A/B 观察，不实盘下单）
# spec='{}' 与其他简单 registry 策略保持一致（规则由 Python exit_policies 管理，非 spec JSON）
cur.execute("""
INSERT INTO v2_strategy_version (strategy_version_id, strategy_id, version_no,
                                 market, status, spec, created_at)
VALUES ('v1-cn-sv-7', 'v1-cn-strat-4-v7', 'v7.0.0', 'CN', 'STAGING', '{}', now())
ON CONFLICT (strategy_version_id) DO NOTHING;
""")

conn.commit()
print("CHAN_V7 seeded (idempotent).")
cur.close()
conn.close()
