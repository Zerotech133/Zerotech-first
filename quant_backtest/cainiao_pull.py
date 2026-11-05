# -*- coding: utf-8 -*-
"""CainiaoPull：菜鸟远端 thunder_model 九表 → 本地镜像的每日增量拉取。

2026-11-05 重建：换机后 wrapper 与计划任务一并丢失（git 历史、四份备份归档、
C:\\Temp\\cainiao_7z_list.txt 均无留存）。本脚本按 10-08 三资产文档 §五 P0
与 10-13 QMT 文档的「六表→九表」口径重写。

数据流
------
  源  ：远端 MySQL thunder_model，只读账号 CAINIAO_MYSQL_DSN
        （复用 xmap_sync.get_cainiao_conn / parse_dsn，会话级 READ ONLY）
  目标：本地 docker 容器 quant-cainiao-mysql（127.0.0.1:3307）的 thunder_model
        全库镜像。备份由 ops/db_dump_daily.py 承担，但该脚本 2026-11-05 复核时
        **没有任何计划任务调用**（backups/db 的 10-29 / 11-05 两次均为人工跑），
        排班仍需 L2 批准；不要据此认为"已有每日备份"。
  水位：目标库 _cainiao_pull_state（本脚本自建，下划线前缀区分源表）
  写入：INSERT ... ON DUPLICATE KEY UPDATE，按主键 id 幂等；每表独立事务。

九表（六表 + 10-13 P0 新增的源头层三表）
----------------------------------------
  stock_trade_member_fund_anomaly / position_daily / account_daily
  stock_realtime_monitor_record / stock_trade_member_instruction
  stock_trade_member_real_order
  stock_trade_strategy / stock_trade_strategy_instruction
  stock_trade_strategy_pool

口径说明（与 10-08 文档 §六对齐）
--------------------------------
* 不拉 bak 表：只按上表白名单逐表拉，天然排除 *_20260722 / *_20260811。
* deleted 位不做过滤：走的是原表直读而非 /page，保留逻辑删除行以维持镜像
  保真；下游按 deleted 自行过滤（与旧六表镜像一致）。
* tenant_id 不做硬过滤：源表租户值未在本脚本里硬编码，避免误丢数据。
* update_time 为 NULL 的行每次增量都会重放（幂等 upsert，无副作用），
  保证不会因空水位永久漏数。

用法
----
  python -X utf8 cainiao_pull.py                        # dryrun：只报每张表待拉行数
  python -X utf8 cainiao_pull.py --apply               # 实际增量写入
  python -X utf8 cainiao_pull.py --apply --full        # 忽略水位，全量重放
  python -X utf8 cainiao_pull.py --apply --tables stock_trade_strategy

安全护栏：--apply 会拒绝向非本机（127.0.0.1/localhost）目标写库。
退出码：0 全表成功；1 有表失败；3 凭据/驱动/连接缺失。
"""

import argparse
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import pymysql  # noqa: E402
import pymysql.cursors  # noqa: E402
import xmap_sync as xs  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 九表白名单：任何表名都只能来自这里，杜绝 SQL 注入面
TABLES = (
    "stock_trade_member_fund_anomaly",
    "stock_trade_member_position_daily",
    "stock_trade_member_account_daily",
    "stock_realtime_monitor_record",
    "stock_trade_member_instruction",
    "stock_trade_member_real_order",
    "stock_trade_strategy",
    "stock_trade_strategy_instruction",
    "stock_trade_strategy_pool",
)

LOCAL_DB = "thunder_model"
LOCAL_CONTAINER = "quant-cainiao-mysql"
STATE_TABLE = "_cainiao_pull_state"
RECEIPT_F = os.path.join(HERE, "data", "cainiao_pull", "receipt.jsonl")
BATCH = 500
OVERLAP = timedelta(minutes=5)

EXIT_MISSING = 3


def _env_local_key(key):
    p = os.path.join(HERE, ".env.local")
    if not os.path.isfile(p):
        return ""
    with io.open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith(key + "=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return ""


def local_connect():
    """目标连接凭据链：显式 DSN > .env.local > docker inspect 容器环境变量。

    不把 root 密码写进源码或日志。docker inspect 只在本地容器上取，
    凭据仅存在于进程内存。
    """
    dsn = os.environ.get("CAINIAO_LOCAL_MYSQL_DSN", "").strip() \
        or _env_local_key("CAINIAO_LOCAL_MYSQL_DSN")
    if dsn:
        cfg = xs.parse_dsn(dsn)
    else:
        try:
            out = subprocess.check_output(
                ["docker", "inspect", "-f",
                 "{{range .Config.Env}}{{println .}}{{end}}", LOCAL_CONTAINER],
                text=True, timeout=25, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("无法读取本地容器环境变量：%s" % exc)
        env = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
        pwd = env.get("MYSQL_ROOT_PASSWORD")
        if not pwd:
            raise RuntimeError("容器 %s 未暴露 MYSQL_ROOT_PASSWORD" % LOCAL_CONTAINER)
        cfg = dict(host=os.environ.get("CAINIAO_LOCAL_HOST", "127.0.0.1"),
                   port=int(os.environ.get("CAINIAO_LOCAL_PORT", "3307")),
                   user="root", password=pwd, database=LOCAL_DB,
                   charset="utf8mb4")
    if cfg["host"] not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("拒绝连接非本机目标：%s（防止误写远端生产库）" % cfg["host"])
    return pymysql.connect(
        **cfg, connect_timeout=10, autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _cols(cur, table):
    cur.execute(
        "SELECT COLUMN_NAME AS cn, COLUMN_KEY AS ck "
        "FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
        (LOCAL_DB, table),
    )
    return [(r["cn"], r["ck"]) for r in cur.fetchall()]


def ensure_state(cur):
    cur.execute(
        "CREATE TABLE IF NOT EXISTS `%s` ("
        "  `table_name` varchar(64) NOT NULL,"
        "  `wm` datetime NULL,"
        "  `rows_last` int NOT NULL DEFAULT 0,"
        "  `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP "
        "    ON UPDATE CURRENT_TIMESTAMP,"
        "  PRIMARY KEY (`table_name`)"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 "
        "COMMENT='CainiaoPull internal watermark; not a source table'" % STATE_TABLE
    )


def state_exists(cur):
    cur.execute(
        "SELECT COUNT(*) AS n FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
        (LOCAL_DB, STATE_TABLE),
    )
    return cur.fetchone()["n"] > 0


def get_watermarks(cur):
    if not state_exists(cur):
        return {}
    cur.execute("SELECT table_name, wm FROM `%s`" % STATE_TABLE)
    return {r["table_name"]: r["wm"] for r in cur.fetchall()}


def q(ident):
    """白名单外的标识符一律拒绝（表名来自 TABLES，列名来自 information_schema）。"""
    if not ident.replace("_", "").isalnum():
        raise ValueError("unsafe identifier: %r" % ident)
    return "`%s`" % ident


def where_clause(wm):
    """增量窗口：update_time > 水位（含 5 分钟回看，由调用方减好）。

    update_time 为 NULL 的行每次重放，避免永久漏数（幂等 upsert 兜底）。
    """
    if wm is None:
        return "", ()
    return " WHERE (`update_time` > %s OR `update_time` IS NULL)", (wm,)


def upsert_sql(table, cols, pk):
    collist = ", ".join(q(c) for c in cols)
    ph = ", ".join(["%s"] * len(cols))
    upd = ", ".join("%s=VALUES(%s)" % (q(c), q(c)) for c in cols if c not in pk)
    if not upd:  # 单列主键表：只做幂等插入
        return "INSERT INTO %s (%s) VALUES (%s) ON DUPLICATE KEY UPDATE `%s`=`%s`" \
               % (q(table), collist, ph, pk[0], pk[0])
    return "INSERT INTO %s (%s) VALUES (%s) ON DUPLICATE KEY UPDATE %s" \
           % (q(table), collist, ph, upd)


def run_table(src, dst, table, wm, apply):
    with src.cursor() as sc, dst.cursor() as dc:
        src_cols = [(r["cn"], r["ck"]) for r in _read_src_cols(sc, table)]
        dst_cols = _cols(dc, table)
        if not src_cols:
            raise RuntimeError("源表不存在或无列：%s" % table)
        if not dst_cols:
            raise RuntimeError("目标表不存在：%s（先恢复本地镜像）" % table)
        src_names = {c for c, _k in src_cols}
        dst_names = {c for c, _k in dst_cols}
        common = [c for c, _k in src_cols if c in dst_names]
        dropped = [c for c, _k in src_cols if c not in dst_names]
        pk = [c for c, k in dst_cols if k == "PRI"] or ["id"]
        # 缺 update_time 的源表无法做水位窗口，退回全量重放（upsert 幂等）。
        if "update_time" not in src_names:
            wm = None
        # keyset 分段键：优先 id，其次目标主键；都没有才退回一次性读取。
        key = "id" if "id" in common else (pk[0] if pk[0] in common else None)
        where, args = where_clause(wm)
        info = {"table": table, "cols": len(common),
                "wm_before": str(wm) if wm else None,
                "dropped_cols": dropped, "pk": pk}
        # dryrun 只数行，绝不把远端全表拉回本地（九表合计 8.6 万行 × 30 列
        # 走内网/跳板会把一轮巡检拖到分钟级，没必要）。
        if not apply:
            sc.execute("SELECT COUNT(*) AS n FROM %s%s" % (q(table), where), args)
            info["rows"] = sc.fetchone()["n"]
            return info

        # 2026-11-05 P1 修复：pymysql 默认缓冲游标在 execute() 阶段就把整表
        # 结果集读进内存，fetchmany() 只是本地切片——27252 行 × 32 列含长文本
        # 的单条大查询会被中间链路以 WinError 10053 掐断，且共用源连接失效后
        # 后 5 张表连锁 InterfaceError。改为按主键分段，每条查询只取 BATCH 行，
        # 读一段写一段，单条查询始终短小可控。
        stmt = upsert_sql(table, common, pk)
        collist = ", ".join(q(c) for c in common)
        total = 0
        if key is None:
            sql = "SELECT %s FROM %s%s" % (collist, q(table), where)
            sc.execute(sql, args)
            chunk = sc.fetchall()
            if chunk:
                dc.executemany(stmt, [tuple(r[c] for c in common) for r in chunk])
                total = len(chunk)
        else:
            last_key = None
            while True:
                parts, seg_args = [], []
                if wm is not None:
                    parts.append("(`update_time` > %s OR `update_time` IS NULL)")
                    seg_args.append(wm)
                if last_key is not None:
                    parts.append("%s > %%s" % q(key))
                    seg_args.append(last_key)
                seg_sql = "SELECT %s FROM %s" % (collist, q(table))
                if parts:
                    seg_sql += " WHERE " + " AND ".join(parts)
                # BATCH 是模块内 int 常量，不经外部输入，直接内插无注入面。
                seg_sql += " ORDER BY %s LIMIT %d" % (q(key), BATCH)
                sc.execute(seg_sql, seg_args)
                chunk = sc.fetchall()
                if not chunk:
                    break
                dc.executemany(stmt, [tuple(r[c] for c in common) for r in chunk])
                total += len(chunk)
                last_key = chunk[-1][key]
                if len(chunk) < BATCH:
                    break
        info["rows"] = total
        info["written"] = total
        return info


def _read_src_cols(cur, table):
    cur.execute(
        "SELECT COLUMN_NAME AS cn, COLUMN_KEY AS ck "
        "FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s "
        "ORDER BY ORDINAL_POSITION",
        (table,),
    )
    return list(cur.fetchall())


def main():
    p = argparse.ArgumentParser(description="CainiaoPull 九表增量拉取")
    p.add_argument("--apply", action="store_true", help="实际写库（默认 dryrun）")
    p.add_argument("--full", action="store_true", help="忽略水位，全量重放")
    p.add_argument("--tables", help="逗号分隔的表名子集（必须在九表白名单内）")
    args = p.parse_args()

    tables = list(TABLES)
    if args.tables:
        want = {t.strip() for t in args.tables.split(",") if t.strip()}
        bad = want - set(TABLES)
        if bad:
            print("[exit 1] 不在九表白名单：%s" % sorted(bad), file=sys.stderr)
            sys.exit(1)
        tables = [t for t in TABLES if t in want]

    try:
        src = xs.get_cainiao_conn()   # 凭据/驱动缺失时 sys.exit(3)
        dst = local_connect()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print("[exit 3] 连接失败：%s" % exc, file=sys.stderr)
        sys.exit(EXIT_MISSING)

    results, failed = [], []
    try:
        with src.cursor() as sc:
            sc.execute("SELECT NOW() AS now_v")
            mnow = sc.fetchone()["now_v"]
            if not isinstance(mnow, datetime):
                mnow = datetime.strptime(str(mnow)[:19], "%Y-%m-%d %H:%M:%S")
        with dst.cursor() as dc:
            if args.apply:
                ensure_state(dc)
                dst.commit()
            wms = get_watermarks(dc)
        print("=== CainiaoPull %s | 九表 %d 张 | 源时钟 %s | 目标 %s ==="
              % ("APPLY" if args.apply else "DRYRUN", len(tables),
                 mnow.strftime("%F %T"), LOCAL_DB))

        for table in tables:
            wm = None if args.full else wms.get(table)
            if wm:
                wm = wm - OVERLAP   # 5 分钟回看，边界行靠 upsert 幂等兜底
            # 单表最多 2 次：连接类故障先重连源库再重跑，避免一张表断连后
            # 共用连接失效、后 5 张表连锁 InterfaceError。整表 upsert 幂等，
            # 中途失败已 rollback，重试不会产生重复行。
            for attempt in range(2):
                try:
                    if src is None:
                        src = xs.get_cainiao_conn()
                    info = run_table(src, dst, table, wm, args.apply)
                    if args.apply:
                        with dst.cursor() as dc:
                            dc.execute(
                                "INSERT INTO `%s` (table_name, wm, rows_last) "
                                "VALUES (%%s, %%s, %%s) ON DUPLICATE KEY UPDATE "
                                "wm=VALUES(wm), rows_last=VALUES(rows_last)" % STATE_TABLE,
                                (table, mnow, info["rows"]),
                            )
                        dst.commit()
                    info["status"] = "ok"
                    results.append(info)
                    print("  %-42s 行=%-7d 列=%-3d 水位=%-19s %s"
                          % (table, info["rows"], info["cols"],
                             str(wm) if wm else "(全量)",
                             ("写入 %d" % info["written"]) if args.apply else "(未写)"))
                    if info["dropped_cols"]:
                        print("    [warn] 目标表缺列，已跳过：%s" % info["dropped_cols"])
                    break
                except Exception as exc:  # noqa: BLE001
                    try:
                        dst.rollback()
                        dst.ping(reconnect=True)
                    except Exception:  # noqa: BLE001
                        pass
                    conn_err = isinstance(
                        exc, (pymysql.err.OperationalError, pymysql.err.InterfaceError)
                    )
                    if conn_err and attempt == 0:
                        try:
                            src.close()
                        except Exception:  # noqa: BLE001
                            pass
                        src = None
                        print("    [retry] 源连接断开，重连后重跑 %s：%s"
                              % (table, str(exc)[:90]))
                        continue
                    failed.append(table)
                    results.append({"table": table, "status": "fail",
                                    "error": "%s: %s" % (type(exc).__name__, exc)})
                    print("  %-42s FAIL %s: %s" % (table, type(exc).__name__, exc))
                    break

        if args.apply:
            os.makedirs(os.path.dirname(RECEIPT_F), exist_ok=True)
            with io.open(RECEIPT_F, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": datetime.now().strftime("%F %T"),
                    "mode": "full" if args.full else "incr",
                    "source_clock": mnow.strftime("%F %T"),
                    "tables": results, "failed": failed,
                }, ensure_ascii=False, default=str) + "\n")
        print("=== 结束：成功 %d/%d，失败 %d，rc=%d ==="
              % (len(tables) - len(failed), len(tables), len(failed),
                 1 if failed else 0))
        sys.exit(1 if failed else 0)
    finally:
        if src is not None:
            src.close()
        dst.close()


if __name__ == "__main__":
    main()
