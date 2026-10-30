#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
X2 实体 ID 映射同步脚本（零界 PG schema xmap）
设计稿：docs/plans/2026-10-08_X2实体ID映射_设计稿.md

子命令：
  init       全量对齐菜鸟 stock_basic → xmap.entity_map（需菜鸟 MySQL 凭据）
  incr       watermark 增量拉取（update_time > 水位，水位存 xmap.sync_watermark）
  mock-test  内置 50 条样例（47 正常 + 3 故意问题）走完整流程，独立 schema xmap_test，
             跑完输出统计并 drop，不连菜鸟、不动 xmap 真表

菜鸟连接：环境变量 CAINIAO_MYSQL_DSN，例：
  mysql://readonly_user:pass@rookie-host:3306/thunder_model?charset=utf8mb4
凭据缺失/驱动缺失时干净退出（exit 3），不抛栈。

PG 连接：默认 127.0.0.1:5442 quant_user/quant_system（Docker 容器 quant-pg，
同容器还挂 quant_meta 库；2026-10-16 勘误：5442 并非残留占用，系 quant-pg 容器
真实映射，此前误判曾短暂切 5443 已回滚），
可用环境变量 XMAP_PG_HOST / XMAP_PG_PORT / XMAP_PG_USER / XMAP_PG_PASSWORD / XMAP_PG_DB 覆盖。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
import logging

try:  # Windows 控制台 GBK 兜底
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception as exc:
    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/xmap_sync.py", exc)

import psycopg2
import psycopg2.extras

# ── 常量 ──────────────────────────────────────────────────────────────
MAIN_SCHEMA = "xmap"
TEST_SCHEMA = "xmap_test"

# 设计稿 §3.2 代码格式映射规则表（rule 引擎唯一依据）
MARKET_SUFFIX = {"SH": ".SH", "SZ": ".SZ", "BJ": ".BJ"}
# 兜底前缀推导：market 缺失/未知时
PREFIX_SUFFIX = (
    ("92", ".BJ"),
    ("4", ".BJ"),
    ("8", ".BJ"),
    ("6", ".SH"),
    ("5", ".SH"),
    ("11", ".SH"),
    ("0", ".SZ"),
    ("3", ".SZ"),
    ("12", ".SZ"),
    ("1", ".SZ"),
)

EXIT_MISSING_CREDENTIAL = 3


# ── PG 连接 ───────────────────────────────────────────────────────────
def _load_env_local_pg_password():
    """XMAP_PG_PASSWORD 回落链：env 未设时读同目录 .env.local（止血 2026-10-16：
    移除源码明文兜底，缺密码 fail-closed，同 _load_env_local_dsn 模式，只填空不覆盖显式 env）。"""
    if os.environ.get("XMAP_PG_PASSWORD", "").strip():
        return
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, ".env.local")
    if not os.path.isfile(p):
        return
    try:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line.startswith("XMAP_PG_PASSWORD=") and not line.startswith("#"):
                os.environ["XMAP_PG_PASSWORD"] = line.split("=", 1)[1].strip()
                return
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/xmap_sync.py", exc)


def pg_connect():
    _load_env_local_pg_password()
    pwd = os.environ.get("XMAP_PG_PASSWORD", "").strip()
    if not pwd:
        raise RuntimeError(
            "XMAP_PG_PASSWORD 未配置（env 或 quant_backtest/.env.local）"
        )
    return psycopg2.connect(
        host=os.environ.get("XMAP_PG_HOST", "127.0.0.1"),
        port=int(os.environ.get("XMAP_PG_PORT", "5442")),
        user=os.environ.get("XMAP_PG_USER", "quant_user"),
        password=pwd,
        dbname=os.environ.get("XMAP_PG_DB", "quant_system"),
        connect_timeout=5,
    )


# ── 设计稿 §2 DDL（schema 参数化，主库 xmap 与测试库 xmap_test 共用） ──
DDL_TEMPLATE = """
CREATE SCHEMA IF NOT EXISTS {s};

CREATE TABLE IF NOT EXISTS {s}.entity_map (
    id              bigserial PRIMARY KEY,
    entity_type     text        NOT NULL,
    src_system      text        NOT NULL DEFAULT 'rookie',
    src_ns          text        NOT NULL DEFAULT '',
    src_id          text        NOT NULL,
    dst_system      text        NOT NULL DEFAULT 'lingjie',
    dst_ns          text        NOT NULL DEFAULT '',
    dst_id          text        NOT NULL,
    match_method    text        NOT NULL,
    confidence      numeric(4,3) NOT NULL DEFAULT 1.000,
    status          text        NOT NULL DEFAULT 'matched',
    src_payload     jsonb       NULL,
    reviewed_by     text        NULL,
    reviewed_at     timestamptz NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_xmap_fwd
    ON {s}.entity_map (entity_type, src_system, src_ns, src_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_xmap_rev
    ON {s}.entity_map (entity_type, dst_system, dst_ns, dst_id)
    WHERE status = 'matched';
CREATE INDEX IF NOT EXISTS idx_xmap_status
    ON {s}.entity_map (entity_type, status);

CREATE TABLE IF NOT EXISTS {s}.unmatched_queue (
    id              bigserial PRIMARY KEY,
    entity_type     text        NOT NULL,
    src_system      text        NOT NULL DEFAULT 'rookie',
    src_ns          text        NOT NULL DEFAULT '',
    src_id          text        NOT NULL,
    src_payload     jsonb       NULL,
    reason          text        NOT NULL,
    attempt_count   int         NOT NULL DEFAULT 0,
    status          text        NOT NULL DEFAULT 'open',
    resolved_map_id bigint      REFERENCES {s}.entity_map(id),
    detected_at     timestamptz NOT NULL DEFAULT now(),
    resolved_at     timestamptz NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_xmap_unq
    ON {s}.unmatched_queue (entity_type, src_system, src_ns, src_id)
    WHERE status = 'open';

CREATE TABLE IF NOT EXISTS {s}.map_audit (
    id              bigserial PRIMARY KEY,
    map_id          bigint      NOT NULL REFERENCES {s}.entity_map(id),
    action          text        NOT NULL,
    old_row         jsonb       NULL,
    new_row         jsonb       NULL,
    actor           text        NOT NULL DEFAULT 'system',
    acted_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_xmap_audit_map
    ON {s}.map_audit (map_id, acted_at DESC);

CREATE TABLE IF NOT EXISTS {s}.sync_watermark (
    entity_type     text PRIMARY KEY,
    wm              timestamptz NOT NULL
);
"""


def create_objects(cur, schema):
    cur.execute(DDL_TEMPLATE.format(s=schema))


# ── 设计稿 §3.2 规则推导 ──────────────────────────────────────────────
def rule_derive_ts_code(stock_code, market):
    """按规则表推导 ts_code；推导不出返回 None。"""
    code = (stock_code or "").strip()
    if not code:
        return None
    mkt = (market or "").strip().upper()
    if mkt in MARKET_SUFFIX:
        return code + MARKET_SUFFIX[mkt]
    for prefix, suffix in PREFIX_SUFFIX:
        if code.startswith(prefix):
            return code + suffix
    return None


# ── 设计稿 §3.1 对齐规则（init 与 incr 与 mock 共用同一函数） ─────────
def align_rows(rows, securities):
    """rows: [{'stock_code','market','ts_code','stock_name','list_status'},...]
    securities: 零界证券全集 S（ts_code 集合）
    返回 (matched_list, unmatched_list)"""
    matched, unmatched = [], []
    for r in rows:
        code = (r.get("stock_code") or "").strip()
        ts_src = (r.get("ts_code") or "").strip() or None
        derived = rule_derive_ts_code(code, r.get("market"))
        payload = {
            "stock_name": r.get("stock_name"),
            "market": r.get("market"),
            "ts_code_src": ts_src,
            "ts_code_derived": derived,
            "list_status": r.get("list_status"),
        }
        item = {"src_id": code, "payload": payload}
        if ts_src and ts_src in securities:  # ① ts_code_confirm
            item.update(
                dst_id=ts_src, method="ts_code_confirm", conf="1.000", status="matched"
            )
            matched.append(item)
        elif not ts_src and derived and derived in securities:  # ② rule 推导
            item.update(dst_id=derived, method="rule", conf="0.990", status="matched")
            matched.append(item)
        elif ts_src and derived and ts_src != derived and derived in securities:
            # ③a 错码自愈（复盘 A1）：自存 ts_code 不在 S 但推导码在 S
            # → 推导码为准自动修复，audit 留痕（method='rule_fix'）。
            item.update(
                dst_id=derived, method="rule_fix", conf="0.950", status="matched"
            )
            matched.append(item)
        elif ts_src and derived and ts_src != derived:  # ③ 双码打架且推导也不在 S
            item["reason"] = "ts_code_mismatch"
            unmatched.append(item)
        elif derived and not ts_src:  # ③/④ 推导码不在 S
            item["reason"] = "dst_not_found"
            unmatched.append(item)
        elif ts_src:  # ④ ts_code 非空但不在 S
            item["reason"] = "dst_not_found"
            unmatched.append(item)
        else:  # 规则表"其他"→直接入队
            item["reason"] = "dst_not_found"
            unmatched.append(item)
    return matched, unmatched


# ── 写库：entity_map upsert + map_audit 记账 ──────────────────────────
def upsert_entity(cur, schema, m, entity_type="stock", actor="xmap_sync"):
    """原子 upsert（复盘 A2）：单条 CTE 完成「读旧行 → INSERT ON CONFLICT
    DO UPDATE → 取回新行」，消除先 SELECT 后 UPDATE 的并发竞态窗口；
    audit 记账行为与原实现逐字段一致（insert/update 双动作 + old/new 留痕）。
    xmax=0 表示本事务新插入的行（PG heap 特性），用于区分 insert/update。
    注意：不可在此再加 FOR UPDATE——同一语句内 ON CONFLICT 改过的元组
    带当前命令 ID，对自身快照不可见，FOR UPDATE 顺链后会整行丢失
    （2026-10-10 探针实锤）；行锁由 ON CONFLICT DO UPDATE 原子持有。"""
    cur.execute(
        f"""  # nosec B608 - 值参数化/内部标识符（已抽查）
        WITH prev AS (
            SELECT id, to_jsonb(t) AS old_row
            FROM {schema}.entity_map t
            WHERE entity_type=%s AND src_system=%s AND src_ns=%s AND src_id=%s
        ), up AS (
            INSERT INTO {schema}.entity_map
                (entity_type, src_system, src_ns, src_id, dst_system, dst_ns,
                 dst_id, match_method, confidence, status, src_payload)
            VALUES (%s, 'rookie', '', %s, 'lingjie', '', %s, %s, %s, %s, %s)
            ON CONFLICT (entity_type, src_system, src_ns, src_id) DO UPDATE
                SET dst_id       = EXCLUDED.dst_id,
                    match_method = EXCLUDED.match_method,
                    confidence   = EXCLUDED.confidence,
                    status       = EXCLUDED.status,
                    src_payload  = EXCLUDED.src_payload,
                    updated_at   = now()
            RETURNING id, (xmax = 0) AS is_insert, to_jsonb(entity_map) AS new_row
        )
        SELECT up.id, up.is_insert, up.new_row, prev.old_row
        FROM up LEFT JOIN prev ON true
        """,
        (
            entity_type,
            "rookie",
            "",
            m["src_id"],
            entity_type,
            m["src_id"],
            m["dst_id"],
            m["method"],
            m["conf"],
            m["status"],
            psycopg2.extras.Json(m["payload"]),
        ),
    )
    map_id, is_insert, new_row, old_row = cur.fetchone()
    cur.execute(
        f"INSERT INTO {schema}.map_audit (map_id, action, old_row, new_row, actor) "  # nosec B608 - 值参数化/内部标识符（已抽查）
        "VALUES (%s,%s,%s,%s,%s)",
        (
            map_id,
            "insert" if is_insert else "update",
            psycopg2.extras.Json(old_row) if old_row is not None else None,
            psycopg2.extras.Json(new_row),
            actor,
        ),
    )
    return map_id, "insert" if is_insert else "update"


def enqueue_unmatched(cur, schema, u, entity_type="stock"):
    """同一实体只排一次队（部分唯一索引 ON CONFLICT DO NOTHING）。"""
    cur.execute(
        f"INSERT INTO {schema}.unmatched_queue "  # nosec B608 - 值参数化/内部标识符（已抽查）
        "(entity_type, src_system, src_ns, src_id, src_payload, reason) "
        "VALUES (%s, 'rookie', '', %s, %s, %s) "
        "ON CONFLICT (entity_type, src_system, src_ns, src_id) "
        "WHERE status = 'open' DO NOTHING",
        (entity_type, u["src_id"], psycopg2.extras.Json(u["payload"]), u["reason"]),
    )


def write_results(cur, schema, matched, unmatched):
    n_ins = n_upd = 0
    for m in matched:
        _, action = upsert_entity(cur, schema, m)
        n_ins += action == "insert"
        n_upd += action == "update"
    for u in unmatched:
        enqueue_unmatched(cur, schema, u)
    return n_ins, n_upd


# ── 零界证券全集 S ────────────────────────────────────────────────────
def load_securities(cur):
    """S = 零界证券全集 ts_code 集合。
    基准优先级：PG public.stock_basic.symbol（ts_code 风格，为空则按规则补后缀）
    → 为空时回退 Neo4j Security.ts_code（kg_client，本轮骨架不强制可用）。"""
    s = set()
    cur.execute("SELECT symbol FROM public.stock_basic")
    for (sym,) in cur.fetchall():
        sym = (sym or "").strip()
        if not sym:
            continue
        if "." in sym:
            s.add(sym)
        else:
            d = rule_derive_ts_code(sym, None)
            if d:
                s.add(d)
    if s:
        return s
    try:  # 回退 Neo4j（可选依赖，失败不阻塞——init 真跑时需确保可达）
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from kg_client import KgClient  # noqa

        kg = KgClient()
        s = {
            r["ts_code"]
            for r in kg.query(
                "MATCH (n:Security) WHERE n.ts_code IS NOT NULL RETURN n.ts_code AS ts_code"
            )
        }
        kg.close()
    except Exception as e:  # noqa
        print(
            f"[warn] 零界证券全集获取失败（PG stock_basic 空 且 Neo4j 不可用）：{e}",
            file=sys.stderr,
        )
    return s


# ── 菜鸟 MySQL（拉取式只读） ──────────────────────────────────────────
def _load_env_local_dsn():
    """CAINIAO_MYSQL_DSN 回落链：env 未设时读同目录 .env.local（与
    rps_resonance 的 XT_DATA_KEY 同模式），只填空不覆盖显式 env。"""
    if os.environ.get("CAINIAO_MYSQL_DSN", "").strip():
        return
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, ".env.local")
    if not os.path.isfile(p):
        return
    try:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line.startswith("CAINIAO_MYSQL_DSN=") and not line.lstrip().startswith(
                "#"
            ):
                os.environ["CAINIAO_MYSQL_DSN"] = line.split("=", 1)[1].strip()
                return
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/xmap_sync.py", exc)


def parse_dsn(dsn):
    u = urlsplit(dsn)
    return dict(
        host=u.hostname,
        port=u.port or 3306,
        user=u.username,
        password=u.password or "",
        database=(u.path or "/").strip("/"),
        charset="utf8mb4",
    )


def get_cainiao_conn():
    _load_env_local_dsn()
    dsn = os.environ.get("CAINIAO_MYSQL_DSN", "").strip()
    if not dsn:
        print(
            "[exit 3] 缺少菜鸟凭据：请设置环境变量 CAINIAO_MYSQL_DSN"
            "（mysql://user:pass@host:port/thunder_model?charset=utf8mb4）。"
            "本轮全量初始化暂缓，待凭据到位。",
            file=sys.stderr,
        )
        sys.exit(EXIT_MISSING_CREDENTIAL)
    try:
        import pymysql  # 延迟导入：未安装同样干净退出
    except ImportError:
        print("[exit 3] 缺少驱动：pip install pymysql 后重试。", file=sys.stderr)
        sys.exit(EXIT_MISSING_CREDENTIAL)
    # pymysql 无 read_only 连接参数（mysql-connector 专属 API）——
    # 只读防线两层：账号权限（xmap_ro 仅 SELECT）为真闸 + 会话级只读为兜底。
    try:
        conn = pymysql.connect(
            **parse_dsn(dsn),
            connect_timeout=10,
            cursorclass=__import__(
                "pymysql.cursors", fromlist=["DictCursor"]
            ).DictCursor,
        )
    except Exception as e:
        print(
            f"[exit 3] 菜鸟 MySQL 不可达：{e}（host 见 DSN；"
            "内网 192.168.203.x 需 VPN/跳板，自动化重跑即可）",
            file=sys.stderr,
        )
        sys.exit(EXIT_MISSING_CREDENTIAL)
    try:
        with conn.cursor() as c:
            c.execute("SET SESSION TRANSACTION READ ONLY")
    except Exception:
        conn.close()
        raise
    return conn


def fetch_cainiao_stocks(cn_conn, watermark=None):
    """只读拉取菜鸟 stock_basic；watermark 传 timestamptz 时按 update_time > wm 增量。"""
    sql = (
        "SELECT stock_code, market, ts_code, stock_name, list_status, update_time "
        "FROM stock_basic"
    )
    args = None
    if watermark is not None:
        sql += " WHERE update_time > %s"
        args = (watermark,)
    with cn_conn.cursor() as c:
        c.execute(sql, args)
        # 连接已用 DictCursor，fetchall() 直接返回 dict 列表；
        # 若再 dict(zip(cols,row)) 会拿 dict 的键当值（全列名自映射→对齐全 miss）。
        return list(c.fetchall())


# ── 子命令：init（全量对齐） ──────────────────────────────────────────
def cmd_init(_args):
    cn = get_cainiao_conn()  # 凭据缺失 → exit 3
    try:
        conn = pg_connect()
    except Exception as e:
        print(f"[error] 零界 PG 连接失败：{e}", file=sys.stderr)
        sys.exit(1)
    try:
        with conn.cursor() as cur:
            create_objects(cur, MAIN_SCHEMA)
            securities = load_securities(cur)
        if not securities:
            print(
                "[error] 零界证券全集 S 为空，中止 init（先跑零界 stock_basic 同步）",
                file=sys.stderr,
            )
            sys.exit(1)
        rows = fetch_cainiao_stocks(cn)
        matched, unmatched = align_rows(rows, securities)
        with conn.cursor() as cur:
            n_ins, n_upd = write_results(cur, MAIN_SCHEMA, matched, unmatched)
        conn.commit()
        print(
            f"[init] 菜鸟 {len(rows)} 行 | 对齐 {len(matched)}（新插 {n_ins} 更新 {n_upd}）"
            f" | 入队 {len(unmatched)}"
        )
        from collections import Counter

        print(
            "[init] unmatched 原因分布：", dict(Counter(u["reason"] for u in unmatched))
        )
    finally:
        conn.close()
        cn.close()


# ── 子命令：incr（watermark 增量） ────────────────────────────────────
def _mysql_clock(cn_conn):
    """取菜鸟 MySQL 挂钟 now() 与其对 UTC 的偏移。

    返回 (mnow_naive, offset)：mnow_naive=MySQL 本地挂钟 naive datetime；
    offset=MySQL 挂钟 − UTC 的 timedelta。两侧时钟域不同
    （PG now()=UTC aware、MySQL update_time=本地 DATETIME），pymysql
    传参按裸字符串拼接不转时区，故增量窗口必须显式换算到 MySQL 挂钟域。
    """
    with cn_conn.cursor() as c:
        # 别名 + 双形态取值：连接默认 DictCursor（dict 按别名取键），
        # 若调用方换普通 Cursor 则退回元组解包——两形态都不再把键名当值。
        c.execute("SELECT NOW() AS now_v, TIMEDIFF(NOW(), UTC_TIMESTAMP()) AS off_v")
        row = c.fetchone()
    if isinstance(row, dict):
        mnow, off = row["now_v"], row["off_v"]
    else:
        mnow, off = row
    if isinstance(off, timedelta):
        offset = off
    else:  # 某些驱动回字符串 '08:00:00'
        h, m, s = (int(x) for x in str(off).split(":"))
        offset = timedelta(hours=h, minutes=m, seconds=s)
    if not isinstance(mnow, datetime):
        mnow = datetime.strptime(str(mnow)[:19], "%Y-%m-%d %H:%M:%S")
    return mnow.replace(tzinfo=None), offset


OVERLAP = timedelta(minutes=5)  # 水位回看重叠：边界行靠 upsert 幂等兜底


def cmd_incr(_args):
    cn = get_cainiao_conn()  # 凭据缺失 → exit 3
    try:
        conn = pg_connect()
    except Exception as e:
        print(f"[error] 零界 PG 连接失败：{e}", file=sys.stderr)
        sys.exit(1)
    # X3 血缘登记（best-effort，lineage.job_run job='xmap_sync'；失败不拦业务）
    run_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lineage.job_run (job, step, started_at, status, detail) "
                "VALUES ('xmap_sync', 'incr', now(), 'running', %s) RETURNING id",
                (
                    json.dumps(
                        {
                            "host": parse_dsn(os.environ["CAINIAO_MYSQL_DSN"])["host"]
                            if os.environ.get("CAINIAO_MYSQL_DSN")
                            else None
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            run_id = cur.fetchone()[0]
        conn.commit()
    except Exception as e:
        print(f"[warn] lineage 登记失败（不拦业务）：{e}", file=sys.stderr)
        conn.rollback()
    try:
        with conn.cursor() as cur:
            create_objects(cur, MAIN_SCHEMA)
            cur.execute("SELECT wm FROM xmap.sync_watermark WHERE entity_type='stock'")
            row = cur.fetchone()
            wm = row[0] if row else None
            securities = load_securities(cur)
        # 时钟域统一：水位(UTC aware) → MySQL 挂钟 naive，带 5 分钟回看重叠
        mnow, offset = _mysql_clock(cn)
        if wm is not None:
            wm_mysql = (wm.astimezone(timezone.utc) + offset).replace(tzinfo=None)
            wm_mysql -= OVERLAP
            print(
                f"[incr] stock 水位：{wm} → MySQL 挂钟 {wm_mysql + OVERLAP}"
                f"（回看 {OVERLAP}）"
            )
        else:
            wm_mysql = None
            print("[incr] stock 水位：（无，全量）")
        rows = fetch_cainiao_stocks(cn, watermark=wm_mysql)
        matched, unmatched = align_rows(rows, securities)
        # 退市（list_status='D'）→ disabled，历史映射保留（设计稿 §4）
        for m in matched:
            if (m["payload"].get("list_status") or "").upper() == "D":
                m["status"] = "disabled"
        # 新水位 = 本次 MySQL 挂钟 mnow，转回 UTC aware 存 timestamptz
        wm_utc = (mnow - offset).replace(tzinfo=timezone.utc)
        with conn.cursor() as cur:
            n_ins, n_upd = write_results(cur, MAIN_SCHEMA, matched, unmatched)
            cur.execute(
                "INSERT INTO xmap.sync_watermark (entity_type, wm) "
                "VALUES (%s, %s) ON CONFLICT (entity_type) DO UPDATE "
                "SET wm = EXCLUDED.wm",
                ("stock", wm_utc),
            )
        conn.commit()
        print(
            f"[incr] 拉取 {len(rows)} 行 | 新插 {n_ins} 更新 {n_upd} | 入队 {len(unmatched)}"
            f" | 水位已推进至 {wm_utc.isoformat()}"
        )
        if run_id is not None:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE lineage.job_run SET status='ok', ended_at=now(), "
                        "rows_written=%s WHERE id=%s",
                        (len(rows), run_id),
                    )
                conn.commit()
            except Exception as e:
                print(f"[warn] lineage 收尾失败：{e}", file=sys.stderr)
                conn.rollback()
        # strategy / user 增量按设计稿 §4 定调后续接入（策略精选制、用户 mobile 归并）
    except Exception:
        if run_id is not None:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE lineage.job_run SET status='fail', ended_at=now() "
                        "WHERE id=%s",
                        (run_id,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
        raise
    finally:
        conn.close()
        cn.close()


# ── 子命令：mock-test（内置样例全流程，独立 schema，不连菜鸟） ────────
def _mock_rows():
    """50 条样例：40 条 ts_code 非空（38 正常 + 2 问题）、10 条 ts_code 空（9 正常 + 1 问题）。"""
    rows = []
    # 38 条正常：ts_code 非空且在 S（沪样例避开 600519，那是后缀打架坏样例的码）
    n = 0
    i = 1
    while n < 20:
        code = f"6005{i:02d}"
        if code != "600519":
            rows.append(
                dict(
                    stock_code=code,
                    market="SH",
                    ts_code=code + ".SH",
                    stock_name=f"沪样例{n + 1}",
                    list_status="L",
                )
            )
            n += 1
        i += 1
    for i in range(1, 19):
        code = f"0005{i:02d}"
        rows.append(
            dict(
                stock_code=code,
                market="SZ",
                ts_code=code + ".SZ",
                stock_name=f"深样例{i}",
                list_status="L",
            )
        )
    # 9 条正常：ts_code 为空，走规则推导（market 明示 2 条 + market 为空 7 条）
    rows.append(
        dict(
            stock_code="688001",
            market="SH",
            ts_code=None,
            stock_name="科样例1",
            list_status="L",
        )
    )
    rows.append(
        dict(
            stock_code="300001",
            market="SZ",
            ts_code=None,
            stock_name="创样例1",
            list_status="L",
        )
    )
    for i, code in enumerate(
        ["601398", "601988", "000002", "600036", "000651", "002594", "430047"], start=1
    ):
        rows.append(
            dict(
                stock_code=code,
                market=None,
                ts_code=None,
                stock_name=f"推导样例{i}",
                list_status="L",
            )
        )
    # 3 条故意问题
    rows.append(
        dict(
            stock_code="900001",
            market=None,
            ts_code=None,  # 规则表"其他"
            stock_name="坏样例-无法推导",
            list_status="L",
        )
    )  # → dst_not_found
    rows.append(
        dict(
            stock_code="600519",
            market="SH",  # 自存 ts_code 与推导打架
            ts_code="600519.SZ",
            stock_name="坏样例-后缀打架",
            list_status="L",
        )
    )  # → ts_code_mismatch
    rows.append(
        dict(
            stock_code="301999",
            market="SZ",  # 合法码但零界 S 无
            ts_code="301999.SZ",
            stock_name="坏样例-零界缺",
            list_status="L",
        )
    )  # → dst_not_found
    # 复盘 A1 自愈样例：自存 ts_code 错后缀（不在 S），推导码在 S → rule_fix 修复
    rows.append(
        dict(
            stock_code="000520",
            market="SZ",
            ts_code="000520.SH",
            stock_name="自愈样例-错后缀",
            list_status="L",
        )
    )  # → rule_fix matched
    return rows


# 故意问题样例的 stock_code：构造 mock 零界证券全集 S 时必须排除
# （000520 属 A1 自愈样例：仅手工注入其正确推导码 000520.SZ 进 S，
#   防其自存错码 000520.SH 被自动收进 S 走 ts_code_confirm 而测不到自愈分支）
MOCK_BAD_CODES = {"900001", "600519", "301999", "000520"}


def cmd_mock_test(_args):
    rows = _mock_rows()
    # mock 零界证券全集 S：只由正常样例构造（排除故意问题样例）
    securities = set()
    for r in rows:
        if r["stock_code"] in MOCK_BAD_CODES:
            continue
        if r["ts_code"]:
            securities.add(r["ts_code"])
        d = rule_derive_ts_code(r["stock_code"], r["market"])
        if d:
            securities.add(d)
    securities.add("000519.SH")  # 噪声，验证规则不误配
    securities.add("000520.SZ")  # A1 自愈样例的正确推导码（手工注入 S）

    try:
        conn = pg_connect()
    except Exception as e:
        print(f"[error] 零界 PG 不可达，mock-test 无法入表：{e}", file=sys.stderr)
        sys.exit(1)
    schema = TEST_SCHEMA
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            create_objects(cur, schema)
        matched, unmatched = align_rows(rows, securities)
        with conn.cursor() as cur:
            n_ins, n_upd = write_results(cur, schema, matched, unmatched)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(f"SELECT status, count(*) FROM {schema}.entity_map GROUP BY 1")  # nosec B608 - 值参数化/内部标识符（已抽查）
            by_status = dict(cur.fetchall())
            cur.execute(
                f"SELECT reason, count(*) FROM {schema}.unmatched_queue "  # nosec B608 - 值参数化/内部标识符（已抽查）
                "WHERE status='open' GROUP BY 1"
            )
            by_reason = dict(cur.fetchall())
            cur.execute(
                f"SELECT count(*) FROM {schema}.map_audit WHERE action='insert'"  # nosec B608 - 值参数化/内部标识符（已抽查）
            )
            audit_ins = cur.fetchone()[0]
            cur.execute(f"SELECT count(*) FROM {schema}.unmatched_queue")  # nosec B608 - 值参数化/内部标识符（已抽查）
            q_total = cur.fetchone()[0]
            # 验收查询示例（设计稿 §7）：正向 + 反向（选正常样例 600501 演示）
            cur.execute(
                f"SELECT dst_id FROM {schema}.entity_map WHERE entity_type='stock'"  # nosec B608 - 值参数化/内部标识符（已抽查）
                " AND src_id='600501' AND status='matched'"
            )
            fwd = cur.fetchone()
            cur.execute(
                f"SELECT src_id FROM {schema}.entity_map WHERE entity_type='stock'"  # nosec B608 - 值参数化/内部标识符（已抽查）
                " AND dst_id='600501.SH'"
            )
            rev = cur.fetchone()
        print("=" * 62)
        print(f"mock-test 统计（schema={schema}）")
        print(f"  输入样例        : {len(rows)}")
        print(f"  对齐(matched)   : {len(matched)}  by_status={by_status}")
        print(f"    新插/更新     : {n_ins} / {n_upd}")
        print(f"  入队(unmatched) : {q_total}  by_reason={by_reason}")
        print(f"  map_audit insert: {audit_ins}")
        print(f"  §7 正向查询 600501 → {fwd[0] if fwd else None}")
        print(f"  §7 反向查询 600501.SH → src_id={rev[0] if rev else None}")
        print("=" * 62)
        rule_fix = sum(1 for m in matched if m.get("method") == "rule_fix")
        ok = (
            len(matched) == 48
            and q_total == 3
            and audit_ins == 48
            and by_status.get("matched") == 48
            and rule_fix == 1
        )
        print(
            f"断言：48 对齐（含 1 条 A1 自愈 rule_fix）+ 3 入队 + 48 audit"
            f" → {'PASS' if ok else 'FAIL'}"
        )
        conn.commit()
    except Exception as e:
        print(f"[error] mock-test 执行异常（不挂死，清理后退出）：{e}", file=sys.stderr)
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            conn.commit()
            print(f"[cleanup] 已 drop schema {schema}")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/xmap_sync.py", exc)
        sys.exit(1)
    finally:
        try:
            conn.close()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/xmap_sync.py", exc)
    # 正常收尾：清理测试数据
    try:
        conn2 = pg_connect()
        with conn2.cursor() as cur:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn2.commit()
        conn2.close()
        print(f"[cleanup] 已 drop schema {schema}（测试数据已清理，xmap 真表未动）")
    except Exception as e:
        print(
            f"[warn] 清理 {schema} 失败：{e}（可手工 DROP SCHEMA {schema} CASCADE）",
            file=sys.stderr,
        )


# ── 入口 ──────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="X2 实体 ID 映射同步")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="全量对齐（需 CAINIAO_MYSQL_DSN）")
    sub.add_parser("incr", help="watermark 增量拉取（需 CAINIAO_MYSQL_DSN）")
    sub.add_parser("mock-test", help="内置 50 条样例全流程自测（不连菜鸟）")
    args = p.parse_args()
    {"init": cmd_init, "incr": cmd_incr, "mock-test": cmd_mock_test}[args.cmd](args)


if __name__ == "__main__":
    main()
