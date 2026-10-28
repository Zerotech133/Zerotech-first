# -*- coding: utf-8 -*-
"""G3 订阅全流程演练脚本（P3.10-lite · O-C 工位 · WO-FS-133）。

定位：上线检查清单 G3 段的执行载体——在**临时库沙盒**里把
「建库 → 开通 → 查询 → 判定 → 到期提醒 → 清扫 → 撤销（替身）→ 勾稽 →
去重状态」九步各演练一次，产出逐步 pass/fail/skip 摘要；任一步 fail
整体 fail。

硬性纪律（收编 grep 可校验）：
- 不发送任何消息：零网络调用，不发卡片不发文本不碰任何出向通道；
- 不写真实库：唯一落点是显式指定的临时演练库与临时去重状态文件；
- 不接排程：一次性命令行工具，无守护、无循环、无计划任务；
- 「撤销」是替身操作：store 尚无 revoke 能力（方案 a/b 待拍板），演练用
  sqlite 直接把订阅行 status 置为 expired 并回写 end（end 取当下前一秒，
  保持账本勾稽自洽）。这是「撤销能力上线前的替身」，不代表真 revoke 已
  实现；未来真 revoke 若采用独立状态字面量（如 revoked）或保留原 end，
  与本替身行为存在差异，差异由真实现落地时的工单收口。

隔离检查（硬门槛，绝不碰真实库）：
- ``SUBSCRIPTION_DB`` 与 ``RENEWAL_STATE_PATH`` 两者必须显式给出（env 或
  --db / --state），任一缺省 → 立即退出 2，不执行任何演练步骤；
- 即使显式给出，也拒绝指向真实默认路径（portal/work/subscription.sqlite3、
  portal/work/renewal_state.json）以及 portal/work/ 目录内的任何位置；
- 通过检查后把两个 env 回写为解析出的临时路径：subscription_store 与
  renewal_state 都是**调用时读 env、不缓存**（见各自模块注释），故后续
  import 进来的账本与状态读写全部落在沙盒内；
- 演练器内部对临时库的直连一律走 sqlite URI ``mode=rw``（文件不存在即
  抛错，绝不新建文件），只读校验走 ``mode=ro``；建库只有
  subscription_store.ensure_schema 一条路径。

退出码：0 = 全部 pass（含替身步）；1 = 任一步 fail；2 = 自身失败
（隔离检查未通过、参数错误、演练器自身异常）。

--json 输出结构::

    {"ok": bool,
     "steps": [{"name", "status", "detail", "elapsed_ms"}, ...],
     "summary": {"total", "pass", "fail", "skip", "surrogate",
                 "db", "state", "keep", "cleanup_errors"}}

默认输出人读逐步摘要。--keep 保留临时库与状态文件供人工复查；默认跑完
即删。可重复跑：每次用新的临时目录（或先清空旧目录），避免上一轮残留
数据干扰断言。
"""

import argparse
import gc
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent          # tools/
_REPO_ROOT = _HERE.parent
for _p in (str(_REPO_ROOT / "portal"), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import reconcile_subscription  # noqa: E402
import renewal_reminder as rr  # noqa: E402
import renewal_state as rs  # noqa: E402
import subscription_store as store  # noqa: E402
import logging

__all__ = ["IsolationError", "resolve_paths", "run_all", "main", "STEP_NAMES"]

# 九步演练的稳定步骤名（--json 消费方与测试按此对账）
STEP_NAMES = (
    "build_db", "grant", "summary_query", "entitlement",
    "expiry_reminder", "expire_sweep", "revoke_surrogate",
    "reconcile", "renewal_state_dedup",
)

# 真实默认路径锚（仅用于隔离检查的拒绝比对，绝不读写）
_REAL_WORK_DIR = Path(store.__file__).resolve().parent / "work"
_REAL_DB = _REAL_WORK_DIR / "subscription.sqlite3"
_REAL_STATE = _REAL_WORK_DIR / "renewal_state.json"

# 演练夹具（临时库内的一次性数据，输出一律经掩码）
PLAN_ID = "basic_monthly"      # SEED_PLANS 中真实存在的档位
KIND_COVERED = "news.brief"    # basic_monthly.kinds=["news.*"] 声明覆盖
OPEN_MAIN = "ou_smoke_main_0001"     # 主号：开通 30 天 → 查询/判定/替身撤销
OPEN_T3 = "ou_smoke_t3_0002"         # T-3 号：开通 3 天 → 到期提醒/去重状态
OPEN_EXPIRED = "ou_smoke_exp_0003"   # 过期号：直插已过期行 → 清扫
OPEN_NONE = "ou_smoke_none_0004"     # 无订阅号：判定拒绝

_MONEY_TOKENS = ("元", "¥", "￥", "价格", "金额", "收费", "费用")


class IsolationError(Exception):
    """隔离检查未通过：不落任何文件，直接拒绝运行（退出码 2）。"""


def _mask(open_id):
    """输出脱敏统一走 renewal_reminder.mask_open_id。"""
    return rr.mask_open_id(open_id)


def _fmt_now(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _open_rw(path):
    """以 sqlite URI ``mode=rw`` 打开临时库（文件不存在即抛错，绝不新建）。"""
    uri = Path(path).resolve().as_uri() + "?mode=rw"
    return sqlite3.connect(uri, uri=True, timeout=10)


def _open_ro(path):
    """以 sqlite URI ``mode=ro`` 只读打开临时库（双保险 query_only）。"""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.execute("PRAGMA query_only=ON")
    return conn


# ---------------------------------------------------------------------------
# 隔离检查
# ---------------------------------------------------------------------------

def resolve_paths(args):
    """解析并校验沙盒路径；不通过抛 IsolationError（调用方退出 2）。"""
    db_raw = (args.db or os.environ.get("SUBSCRIPTION_DB", "") or "").strip()
    state_raw = (args.state or os.environ.get("RENEWAL_STATE_PATH", "") or "").strip()
    problems = []
    if not db_raw:
        problems.append("SUBSCRIPTION_DB 未设置且未传 --db（拒绝落到真实库）")
    if not state_raw:
        problems.append("RENEWAL_STATE_PATH 未设置且未传 --state（拒绝落到真实状态文件）")
    if problems:
        raise IsolationError("；".join(problems))

    db = Path(os.path.abspath(os.path.expanduser(db_raw)))
    state = Path(os.path.abspath(os.path.expanduser(state_raw)))
    if db == state:
        raise IsolationError("演练库与去重状态文件不得指向同一路径")

    for label, p, real in (("SUBSCRIPTION_DB", db, _REAL_DB),
                           ("RENEWAL_STATE_PATH", state, _REAL_STATE)):
        if p == real:
            raise IsolationError("%s 指向真实默认路径 %s，拒绝运行" % (label, real))
        try:
            p.relative_to(_REAL_WORK_DIR)
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "tools/subscription_smoke.py", exc)
        else:
            raise IsolationError(
                "%s 落在真实运行目录 %s 内，拒绝运行" % (label, _REAL_WORK_DIR))
    return db, state


# ---------------------------------------------------------------------------
# 九步演练（每步抛异常即 fail；异常处理一律 closed on failure）
# ---------------------------------------------------------------------------

def _step_build_db(fx):
    r = store.ensure_schema()
    if not r.get("ok"):
        raise AssertionError("ensure_schema 未通过: %s" % (r.get("problems"),))
    want = {"subscriber", "plan", "subscription", "entitlement_log",
            "payment_order"}
    got = set(r.get("tables") or [])
    if got != want:
        raise AssertionError("5 表不齐备: got=%s want=%s" % (sorted(got), sorted(want)))
    if (r.get("seeded_plans") or 0) < 1:
        raise AssertionError("种子档位未落库")
    if PLAN_ID not in {p["plan_id"] for p in store.SEED_PLANS}:
        raise AssertionError("档位 %s 不在 SEED_PLANS，演练配置失效" % PLAN_ID)
    fx["db_path"] = r["db_path"]
    return "5 表齐备（%s），种子档位 %d 条，本次新建库=%s" % (
        "+".join(sorted(got)), r["seeded_plans"], r["created"])


def _step_grant(fx):
    store.upsert_subscriber(OPEN_MAIN, "演练主号")
    g = store.grant_subscription(OPEN_MAIN, PLAN_ID, 30)
    if g.get("status") != store.STATUS_ACTIVE:
        raise AssertionError("开通行状态异常: %s" % (g,))
    fx["main_grant"] = g
    return "开通 %s 档 30 天（%s → %s），订阅者 %s" % (
        PLAN_ID, g["start"], g["end"], _mask(OPEN_MAIN))


def _step_summary_query(fx):
    s = store.get_subscriber_summary(OPEN_MAIN)
    if "error" in s:
        raise AssertionError("summary 返回 error: %s" % s["error"])
    now_str = s.get("generated_at") or ""
    subs = s.get("subscriptions") or []
    hit = [x for x in subs if x.get("status") == store.STATUS_ACTIVE
           and x.get("start", "") <= now_str <= x.get("end", "")]
    if not hit:
        raise AssertionError("无覆盖当前时刻的 active 行: %s" % (subs,))
    if s.get("reason") != "subscription_active":
        raise AssertionError("reason 应为 subscription_active，got %s" % s.get("reason"))
    row = hit[0]
    if not row["end"] > row["start"]:
        raise AssertionError("起止日期不合理: %s" % row)
    return "status=active、reason=subscription_active，窗口 %s → %s 覆盖 now" % (
        row["start"], row["end"])


def _step_entitlement(fx):
    ok1, why1 = store.is_entitled(OPEN_MAIN, KIND_COVERED)
    if not ok1:
        raise AssertionError("已开通订阅者应放行，got (%s, %s)" % (ok1, why1))
    store.entitlement_log(OPEN_MAIN, KIND_COVERED, True, why1)
    ok2, why2 = store.is_entitled(OPEN_NONE, KIND_COVERED)
    if ok2:
        raise AssertionError("未开通订阅者应拒绝，got (%s, %s)" % (ok2, why2))
    return "已开通 %s → (True, %s)；未开通 %s → (False, %s)" % (
        KIND_COVERED, why1, _mask(OPEN_NONE), why2)


def _step_expiry_reminder(fx):
    store.upsert_subscriber(OPEN_T3, "演练T3号")
    g3 = store.grant_subscription(OPEN_T3, PLAN_ID, 3)
    fx["t3_grant"] = g3
    fetched = rr.collect_subscribers(store=store)
    if not fetched.get("ok"):
        raise AssertionError("collect_subscribers 失败: %s" % fetched.get("error"))
    if not fetched.get("complete"):
        raise AssertionError("取数未到底（截断）: pages=%s problems=%s" % (
            fetched.get("pages"), fetched.get("problems")))
    # now 用开通返回的 start，days_left 恒等于 3，断言不随时钟漂移
    report = rr.plan_reminders(fetched, now=g3["start"])
    hits = [i for i in report["items"] if i.get("open_id") == OPEN_T3]
    if len(hits) != 1 or hits[0].get("days_left") != 3 or hits[0].get("stage") != "t3":
        raise AssertionError("T-3 应恰命中 1 条 t3，got %s" % (hits,))
    text = rr.build_reminder_text(hits[0])
    if OPEN_T3 in text:
        raise AssertionError("提醒正文泄漏明文 open_id")
    if _mask(OPEN_T3) not in text:
        raise AssertionError("提醒正文缺少掩码账号")
    bad = [t for t in _MONEY_TOKENS if t in text]
    if bad:
        raise AssertionError("提醒正文含金额字样: %s" % bad)
    fx["t3_item"] = hits[0]
    return "T-3 命中 1 条（%s，days_left=3，stage=t3），正文无金额、无明文 open_id；取数 mode=%s complete=%s" % (
        _mask(OPEN_T3), fetched.get("mode"), fetched.get("complete"))


def _step_expire_sweep(fx):
    store.upsert_subscriber(OPEN_EXPIRED, "演练过期号")
    end_dt = datetime.now() - timedelta(days=1)
    start = _fmt_now(end_dt - timedelta(days=10))
    end = _fmt_now(end_dt)
    conn = _open_rw(fx["db_path"])
    try:
        conn.execute(
            "INSERT INTO subscription(subscriber_open_id, plan_id, start,"
            " end, status) VALUES (?,?,?,?,?)",
            (OPEN_EXPIRED, PLAN_ID, start, end, store.STATUS_ACTIVE))
        conn.commit()
    finally:
        conn.close()  # closed on failure：连接必须关，Windows 下未关会锁文件
    swept = store.expire_sweep()
    if swept < 1:
        raise AssertionError("清扫行数应 >=1，got %s" % swept)
    conn = _open_ro(fx["db_path"])
    try:
        row = conn.execute(
            "SELECT status FROM subscription WHERE subscriber_open_id=?"
            " AND plan_id=?", (OPEN_EXPIRED, PLAN_ID)).fetchone()
    finally:
        conn.close()  # closed on failure：连接必须关
    if not row or row[0] != store.STATUS_EXPIRED:
        raise AssertionError("清扫后未变 expired: %s" % (row,))
    return "构造已过期行 1 条（end=%s），expire_sweep 清扫 %d 行，行状态 → expired" % (end, swept)


def _step_revoke_surrogate(fx):
    # 撤销能力未上线（store 无 revoke，方案 a/b 待拍板）：替身操作 =
    # sqlite 直改 status=expired 并回写窗口（end=now-1s；start 必要时前移，
    # 保证 start<=end<now，既不触发 bad_range 也不触发 status_contradiction，
    # 同秒内快跑同样成立）。这是替身，不代表真 revoke 已实现。
    conn = _open_rw(fx["db_path"])
    try:
        row = conn.execute(
            "SELECT start FROM subscription"
            " WHERE subscriber_open_id=? AND status=?",
            (OPEN_MAIN, store.STATUS_ACTIVE)).fetchone()
        if not row:
            raise AssertionError("无 active 行可替身撤销")
        end_dt = datetime.now() - timedelta(seconds=1)
        start_dt = min(datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S"),
                       end_dt - timedelta(seconds=1))
        start, end = _fmt_now(start_dt), _fmt_now(end_dt)
        cur = conn.execute(
            "UPDATE subscription SET status=?, start=?, end=?"
            " WHERE subscriber_open_id=? AND status=?",
            (store.STATUS_EXPIRED, start, end, OPEN_MAIN, store.STATUS_ACTIVE))
        conn.commit()
        n = cur.rowcount
    finally:
        conn.close()  # closed on failure：连接必须关
    if n != 1:
        raise AssertionError("替身撤销应恰好更新 1 行，got %s" % n)
    ok, why = store.is_entitled(OPEN_MAIN, KIND_COVERED)
    if ok:
        raise AssertionError("替身撤销后权益应失效，got (%s, %s)" % (ok, why))
    return ("[替身操作] 撤销能力尚未上线（方案 a/b 待拍板）：sqlite 直改"
            " status=expired 并回写窗口（end=%s，start 必要时前移保证"
            " start<=end<now）；撤销后判定 (False, %s)。"
            "此为撤销能力上线前的替身，不代表真 revoke 已实现" % (end, why))


def _step_reconcile(fx):
    r = reconcile_subscription.reconcile(db_path=fx["db_path"])
    issues = r.get("issues") or []
    if not r.get("ok") or issues:
        raise AssertionError("勾稽发现 %d 条问题，样例: %s" % (len(issues), issues[:3]))
    return "账本与勾稽自检双向一致：checked=%d 行，0 issue" % r.get("checked", 0)


def _step_renewal_state_dedup(fx):
    item = fx.get("t3_item") or {"open_id": OPEN_T3, "plan_id": PLAN_ID,
                                 "stage": "t3"}
    day = rs.today_str()
    st = rs.load()
    if st.get("corrupt"):
        raise AssertionError("去重状态文件损坏，拒绝演练")
    new1, sent1 = rs.filter_new([item], st, day)
    if len(new1) != 1 or sent1:
        raise AssertionError("首次应判新项: new=%s sent=%s" % (len(new1), len(sent1)))
    m1 = rs.mark_sent(st, item, day)
    if not (m1.get("ok") and m1.get("written")):
        raise AssertionError("mark_sent 失败: %s" % m1)
    new2, sent2 = rs.filter_new([item], st, day)
    if new2 or len(sent2) != 1:
        raise AssertionError("标记后应判已发: new=%s sent=%s" % (len(new2), len(sent2)))
    m2 = rs.mark_sent(st, item, day)
    if not m2.get("ok"):
        raise AssertionError("重复标记失败: %s" % m2)
    if len(st.get("sent") or {}) != 1:
        raise AssertionError("重复标记应幂等（sent 键数应为 1）")
    return "mark_sent + filter_new 幂等：首次判新 1 条 → 标记后判已发 1 条 → 重复标记键数不变（day=%s，落沙盒状态文件）" % day


_RUNNERS = (
    ("build_db", _step_build_db),
    ("grant", _step_grant),
    ("summary_query", _step_summary_query),
    ("entitlement", _step_entitlement),
    ("expiry_reminder", _step_expiry_reminder),
    ("expire_sweep", _step_expire_sweep),
    ("revoke_surrogate", _step_revoke_surrogate),
    ("reconcile", _step_reconcile),
    ("renewal_state_dedup", _step_renewal_state_dedup),
)


# ---------------------------------------------------------------------------
# 演练主流程
# ---------------------------------------------------------------------------

def run_all(db_path, state_path, keep=False):
    """执行九步演练，返回 --json 同构的 result dict（不抛，异常落 fail/skip）。"""
    # 回写 env：subscription_store / renewal_state 都是调用时读 env（不缓存），
    # 此后所有 import 模块的账本与状态读写都锁定在沙盒路径内。
    os.environ["SUBSCRIPTION_DB"] = str(db_path)
    os.environ["RENEWAL_STATE_PATH"] = str(state_path)

    steps = []
    fx = {}
    failed = None
    for name, fn in _RUNNERS:
        if failed is not None:
            steps.append({"name": name, "status": "skip",
                          "detail": "前置步骤 %s 失败，跳过（closed on failure：不假装通过）" % failed,
                          "elapsed_ms": 0})
            continue
        t0 = time.perf_counter()
        try:
            detail = fn(fx)
            status = "pass"
        except Exception as exc:  # closed on failure：单步失败如实落 status，不中断收尾
            status, detail = "fail", "FAIL %s: %s" % (type(exc).__name__, exc)
            failed = name
        steps.append({"name": name, "status": status,
                      "detail": str(detail),
                      "elapsed_ms": int(round((time.perf_counter() - t0) * 1000))})
    for s in steps:
        if s["name"] == "revoke_surrogate":
            s["surrogate"] = True  # 撤销步恒为替身语义，与 pass/fail 无关

    n_pass = sum(1 for s in steps if s["status"] == "pass")
    n_fail = sum(1 for s in steps if s["status"] == "fail")
    n_skip = sum(1 for s in steps if s["status"] == "skip")

    cleanup_errors = []
    if not keep:
        gc.collect()  # 先回收残留连接（Windows 下未关连接会锁住库文件）
        for base in (Path(db_path), Path(state_path)):
            for suffix in ("", ".tmp", "-journal", "-wal", "-shm"):
                q = Path(str(base) + suffix)
                try:
                    if q.exists():
                        q.unlink()
                except OSError as exc:  # closed on failure：删不掉如实上报，不装干净
                    cleanup_errors.append("%s: %s" % (q, exc))

    surrogate = [s["name"] for s in steps
                 if s["name"] == "revoke_surrogate" and s["status"] == "pass"]
    summary = {"total": len(steps), "pass": n_pass, "fail": n_fail,
               "skip": n_skip, "surrogate": surrogate,
               "db": str(db_path), "state": str(state_path),
               "keep": bool(keep), "cleanup_errors": cleanup_errors}
    return {"ok": n_fail == 0, "steps": steps, "summary": summary}


def render_human(result):
    """人读逐步摘要（open_id 全部经掩码，不出现明文）。"""
    sm = result.get("summary") or {}
    lines = [
        "G3 订阅全流程演练（临时库沙盒，零真发零生产写入）",
        "演练库: %s" % sm.get("db"),
        "状态文件: %s" % sm.get("state"),
        "-" * 64,
    ]
    for i, s in enumerate(result.get("steps") or [], 1):
        lines.append("[%s] %02d %-20s (%dms) %s" % (
            s["status"], i, s["name"], s["elapsed_ms"], s["detail"]))
    lines.append("-" * 64)
    if sm.get("surrogate"):
        lines.append("替身步骤: %s（撤销能力未上线，sqlite 直改 status=expired）"
                     % ", ".join(sm["surrogate"]))
    if sm.get("cleanup_errors"):
        lines.append("清理残留失败: %s" % "; ".join(sm["cleanup_errors"]))
    lines.append("结果: %d/%d pass，%d fail，%d skip → 整体 %s" % (
        sm.get("pass", 0), sm.get("total", 0), sm.get("fail", 0),
        sm.get("skip", 0), "PASS" if result.get("ok") else "FAIL"))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="G3 订阅全流程演练（临时库沙盒，零真发零生产写入）")
    parser.add_argument("--db", default=None,
                        help="临时演练库路径（或 env SUBSCRIPTION_DB，必填其一）")
    parser.add_argument("--state", default=None,
                        help="临时去重状态文件路径（或 env RENEWAL_STATE_PATH，必填其一）")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON 结果结构")
    parser.add_argument("--keep", action="store_true",
                        help="保留临时库与状态文件供人工复查（默认跑完即删）")
    args = parser.parse_args(argv)

    try:
        db, state = resolve_paths(args)
    except IsolationError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": "isolation_refused",
                              "detail": str(exc), "steps": [],
                              "summary": {}}, ensure_ascii=False))
        else:
            print("[拒绝运行] 隔离检查未通过：%s" % exc, file=sys.stderr)
        return 2

    try:
        result = run_all(db, state, keep=args.keep)
    except Exception as exc:  # closed on failure：演练器自身失败退出码 2，不静默
        if args.json:
            print(json.dumps({"ok": False, "error": "smoke_runner_crashed",
                              "detail": "%s: %s" % (type(exc).__name__, exc),
                              "steps": [], "summary": {}}, ensure_ascii=False))
        else:
            print("[演练器自身失败] %s: %s" % (type(exc).__name__, exc),
                  file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_human(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
