# -*- coding: utf-8 -*-
"""JEV 积压批量补判（历史新闻逐条过本地模型，每轮限量防过载）。

news.jev 为空的条目按时间倒序取一批（--batch，默认 100），
按 news_ai.jev_judge_batch 的 8 条/组上限分批调用（条间 sleep 由
JEV_ITEM_SLEEP_S 控制；2026-10-26 EXEC-R28 起默认 0.2s，设 0.8 可回退），
判完落库并打印剩余积压。单轮 100 条约 2-3 分钟，剩余积压
靠计划任务多次运行逐步消化（或手动重复执行）。

并发/锁序（2026-10-26 OS-FIX-JEVLOCK，JEV 审计 §四-②）：
原实现先 SELECT 待判批、再抢 ollama_lock（等锁最长 900s）——等锁期间批已过期，
双实例会各自缓存同一批，后抢到锁的实例重复判读并覆盖已判值。现改为
「先抢锁 → 再 SELECT → 落库」：占锁失败直接退出（不连库、不拿陈旧批）；
落库 UPDATE 增加 `jev IS NULL OR jev=''` 守卫（事务内二次校验，不新增 DB 列），
若该行已被外部先写（锁被陈旧回收/另一实例先落库）则跳过，并计入日志 dup_skip=N。
断点续跑语义不变：未判行仍在下一轮 SELECT 中。

护栏（2026-10-08 K-NEWS-001，A1）：
--deadline 秒数（默认 1800）。每组 8 条处理前检查已耗时，超限即收工。
背景：08:57 实例 batch 500 × 15s timeout × 重试悬挂 2h+，PowerShell 宿主被
任务 ExecutionTimeLimit 杀掉后 python 成孤儿继续跑，IgnoreNew 策略拒绝后续
全部小时轮（0x800710E0 连锁）。deadline 保证 python 自身限时退出。

吞吐开关（2026-10-26 EXEC-R28，均可 env 一键回退）：
  · JEV_ITEM_SLEEP_S=0.2：每条之间的 sleep（原 news_ai 内硬编码 0.8s，
    是云侧防限流遗留；本地 Ollama 无限流需求。设 0.8 即完全回退）。
  · M3 暂停旗（C:\\Temp\\k266_m3.pause）存在时跳过 ollama_lock 租约让位：
    夜间 jev_night 独占、没有 M3 要让，20s/300s 让位纯浪费；锁仍持有并续心跳。
    设 JEV_LOCK_YIELD_PAUSED=0 恢复原让位逻辑（排障/回滚）。
  · 不改变判定逻辑：reason/重采样/提示词/模型参数原样。

告警（K-NEWS-001，A3）：
结束后 left > --alert-threshold（默认 100）时经飞书推送告警；
logs/alert_state.json 记录上次告警时间，6 小时内不重发（防小时任务刷屏）。

用法：cd news_collector && python jev_backfill.py [--batch 100] [--deadline 1800]
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import news_ai  # noqa: E402
import logging

ALERT_STATE = os.path.join(HERE, "logs", "alert_state.json")
ALERT_COOLDOWN_S = 6 * 3600  # 同类告警 6 小时内不重发

# ---- 2026-10-26 EXEC-R28：吞吐计划 A 的两项零质量风险开关 -------------------
# ① 条间 sleep：原由 news_ai.jev_judge_batch() 硬编码 0.8s（云侧防限流遗留），
#    现由本文件的本地等价循环读取 JEV_ITEM_SLEEP_S（默认 0.2，设 0.8 回退）。
#    不改 news_ai：jev_judge_batch 还被 jev_feed/news_collector/stock_analyze 调用，
#    那些交互链保持原 0.8s 不变，本开关只作用于积压回填。
# ② 租约让位：M3 暂停旗存在（夜间 jev_night 独占）时跳过 ollama_lock.tick()
#    的 20s/300s 让位——没有 M3 要让，纯浪费；锁仍持有并续心跳。
#    JEV_LOCK_YIELD_PAUSED=0 可恢复原让位逻辑（排障/回滚用）。
JEV_ITEM_SLEEP_DEFAULT_S = 0.2
JEV_ITEM_SLEEP_MAX_S = 5.0
M3_PAUSE_FLAG = Path(os.environ.get("JEV_M3_PAUSE_FLAG", r"C:\Temp\k266_m3.pause"))
LOCK_YIELD_PAUSED_ENV = "JEV_LOCK_YIELD_PAUSED"
_JEV_BATCH_MAX = 8


def _item_sleep_seconds() -> float:
    """条间 sleep 秒数：JEV_ITEM_SLEEP_S（默认 0.2；非法值回落默认；夹在 [0,5]）。"""
    raw = os.environ.get("JEV_ITEM_SLEEP_S")
    if raw is None or not str(raw).strip():
        return JEV_ITEM_SLEEP_DEFAULT_S
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return JEV_ITEM_SLEEP_DEFAULT_S
    if value != value:  # NaN
        return JEV_ITEM_SLEEP_DEFAULT_S
    return max(0.0, min(JEV_ITEM_SLEEP_MAX_S, value))


def m3_paused(flag_path: Path = M3_PAUSE_FLAG) -> bool:
    """M3 暂停旗是否存在（k266_services 见旗不拉起 M3；jev_night 用旗独占夜间）。"""
    try:
        return flag_path.exists()
    except OSError:
        return False


PREFILTER_ENV = "JEV_PREFILTER"


def prefilter_mode() -> str:
    """预筛模式：off（默认）/ safe（推荐，仅 routine 治理模板）/ aggressive（旧全量规则）。

    2026-10-30 PREFILTEREVAL：旧全量 skip 误杀率 58%，safe 模式按模式拆分后
    routine 模板精度 100%——开启时默认走 safe。
    """
    raw = str(os.environ.get(PREFILTER_ENV, "0")).strip().lower()
    if raw in ("0", "off", "false", "no", ""):
        return "off"
    if raw in ("1", "safe", "on", "true", "yes"):
        return "safe"
    if raw in ("aggressive", "legacy"):
        return "aggressive"
    return "off"


def prefilter_enabled() -> bool:
    """两段式推理灰度开关（K-ORG-20261026-JEV2STAGE）。默认 off。"""
    return prefilter_mode() != "off"


def _lease_yield_disabled() -> bool:
    """JEV_LOCK_YIELD_PAUSED=0/false/no/off → 恢复原租约让位逻辑。"""
    return str(os.environ.get(LOCK_YIELD_PAUSED_ENV, "1")).strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def _should_skip_lease_yield(paused: bool, disabled: bool) -> bool:
    """仅当 M3 暂停且未显式禁用时才跳过让位（两条分支可单测）。"""
    return bool(paused) and not bool(disabled)


def _judge_batch_sleep(items, sleep_s: float, deadline_ts=None) -> dict:
    """news_ai.jev_judge_batch() 的等价循环，仅把 0.8s 换成可配置 sleep_s。

    与 news_ai 版逐行对齐：8 条上限、条间 sleep、失败重试一次、成功才收录。
    EXEC-R28 的 writeScope 只允许改本文件，故就地实现；news_ai 其他调用方不变。
    """
    out = {}
    for i, it in enumerate(items[:_JEV_BATCH_MAX]):
        # 2026-10-30 BATCH4：组内逐条检查 deadline —— 旧实现只在每组 8 条前检查，
        # 单条最坏（云 25s+本地 2×60s+resample 2×60s+reason 45s）可让一轮超 1800s。
        if deadline_ts is not None and time.time() >= deadline_ts:
            break
        if i and sleep_s > 0:
            time.sleep(sleep_s)
        # 2026-10-30 JEVSAFE：单条异常隔离——旧实现任一异常（如 event_type
        # 归一化 KeyError）会中断整组并让整轮 backfill 崩溃，坏行永远重来。
        try:
            j = news_ai.jev_judge_one(it.get("title") or "", it.get("stocks") or [])
            if j is None:
                j = news_ai.jev_judge_one(it.get("title") or "", it.get("stocks") or [])
        except Exception as exc:  # noqa: BLE001
            print("BACKFILL item FAIL id=%s %r（单条隔离，跳过）"
                  % (it.get("id"), exc), file=sys.stderr, flush=True)
            continue
        if j:
            out[it["id"]] = j
    return out


def _locked_tick(
    lock_mod, purpose: str, paused: bool, disabled: bool, deadline_ts=None
) -> bool:
    """持锁续期：M3 暂停时只 heartbeat 不让位；否则走 lock_mod.tick() 原逻辑。

    deadline_ts（OS-FIX-JEVLOCK）：本轮自报截止时刻（t0+--deadline），传给 tick()
    让其单次等待/冷却不越过 deadline —— 否则 tick 最坏阻塞可击穿外层 hard timeout。
    """
    if _should_skip_lease_yield(paused, disabled):
        # 2026-10-30 BATCH2：heartbeat 显式返回 False = 锁已丢失，停止本轮判读；
        # None 兼容旧 mock/旧模块（视为仍持锁）。
        return lock_mod.heartbeat() is not False
    if deadline_ts is None:
        return lock_mod.tick(purpose)
    return lock_mod.tick(purpose, deadline_ts=deadline_ts)


def _alert_ok(throttle):
    """节流检查：距上次告警超过 cooldown 才允许再发。"""
    if not throttle:
        return True
    try:
        with open(ALERT_STATE, encoding="utf-8") as f:
            last = json.load(f).get("last_alert_ts", 0)
        return (time.time() - last) >= ALERT_COOLDOWN_S
    except Exception:
        return True  # 状态文件缺失/损坏按可告警处理


def _alert_mark():
    try:
        os.makedirs(os.path.dirname(ALERT_STATE), exist_ok=True)
        with open(ALERT_STATE, "w", encoding="utf-8") as f:
            json.dump({"last_alert_ts": time.time()}, f)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/jev_backfill.py", exc)


def alert_backlog(left, judged, deadline_hit):
    """积压超阈值告警：飞书推送（失败静默，不影响退出码）。"""
    try:
        msg = (
            "【JEV 判断积压告警】\n"
            "剩余未判断新闻 %d 条（超过阈值）\n"
            "本轮 judged=%d%s\n"
            "可能原因：云端 systemone 故障或回填实例异常。\n"
            "处置：观察下一小时轮；持续恶化检查 "
            "news_collector\\logs\\ai.log 与 C:\\jev_backfill_task.log"
            % (left, judged, "，deadline 提前收工" if deadline_hit else "")
        )
        news_ai.push_message(msg)
        _alert_mark()
        return True
    except Exception as e:
        news_ai.log("backfill alert FAIL %s" % str(e)[:100])
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument(
        "--deadline",
        type=int,
        default=1800,
        help="单轮硬超时秒数，超限提前收工（0=不限）",
    )
    ap.add_argument(
        "--alert-threshold", type=int, default=100, help="剩余积压告警阈值（0=不告警）"
    )
    args = ap.parse_args()
    item_sleep = _item_sleep_seconds()
    m3_is_paused = m3_paused()
    yield_disabled = _lease_yield_disabled()
    skip_yield = _should_skip_lease_yield(m3_is_paused, yield_disabled)
    # 生效配置显式落日志，便于次日核对 EXEC-R28 是否真的生效（不写额外文件）。
    print(
        "BACKFILL cfg item_sleep=%.3fs m3_paused=%s lease_yield=%s"
        % (item_sleep, m3_is_paused, "skip" if skip_yield else "normal"),
        flush=True,
    )
    # Ollama 错峰锁：与 M3 结构化批处理互斥（wait_s≈M3 单条看门狗上限+余量；
    # 正常退出/异常走 atexit 释放，强杀靠锁模块 20 分钟陈旧回收）
    # 2026-10-26 OS-FIX-JEVLOCK（JEV 审计 §四-②）：**先抢锁、再取批**。旧顺序
    # （SELECT → acquire 900s）会让双实例各自缓存同一批：等锁期间批已过期，后抢到
    # 锁者重复判读并覆盖已判值。占锁失败直接退出 —— 不连库、不 SELECT、不拿陈旧批。
    _root = os.path.dirname(HERE)
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import atexit
    import ollama_lock

    atexit.register(ollama_lock.release)
    if not ollama_lock.acquire(
        "jev_backfill", wait_s=900
    ):  # K-ORG-20261025-LOCK 覆盖 M3 一个租约周期
        print("BACKFILL skip: ollama 锁被 M3 持有超 900s，本轮让位（小时轮自愈）")
        return
    # 2026-10-16 审计：timeout=30 防与速报链写库竞争立刻抛 locked
    # （默认 5s 偏短；配合下方组级回滚兜底）
    db = sqlite3.connect(os.path.join(HERE, "news.db"), timeout=30)
    rows = db.execute(
        "SELECT id, title, stocks, source FROM news "
        "WHERE (jev IS NULL OR jev='') ORDER BY ts DESC LIMIT ?",
        (args.batch,),
    ).fetchall()
    if not rows:
        print("BACKFILL done: no pending")
        return
    items = []
    for nid, title, stocks, source in rows:
        try:
            st = json.loads(stocks) if stocks else []
        except Exception:
            st = []
        items.append(
            {"id": nid, "title": title, "stocks": st, "source": str(source or "")}
        )
    # ── 第一段：规则粗筛（灰度，默认关，K-ORG-20261026-JEV2STAGE）────────
    pref_skipped = 0
    if prefilter_enabled():
        try:
            import jev_prefilter

            kept = []
            skipped_jsons = []
            for it in items:
                j = jev_prefilter.prefilter_jev(
                    it.get("title") or "", it.get("source") or "",
                    mode=prefilter_mode()
                )
                if j is None:
                    kept.append(it)
                else:
                    skipped_jsons.append((it["id"], j))
            if skipped_jsons:
                try:
                    for nid, j in skipped_jsons:
                        cur = db.execute(
                            "UPDATE news SET jev=? WHERE id=? AND (jev IS NULL OR jev='')",
                            (json.dumps(j, ensure_ascii=False), nid),
                        )
                        pref_skipped += int(cur.rowcount or 0)
                    db.commit()
                except sqlite3.OperationalError as e:
                    db.rollback()
                    print(
                        "BACKFILL prefilter commit FAIL %r（改为全部走 9B）" % e,
                        file=sys.stderr,
                    )
                    kept = items
                    pref_skipped = 0
            items = kept
            print(
                "BACKFILL prefilter ON: skip=%d keep=%d（skip 直接写 neutral，带 pref 证据可回退）"
                % (pref_skipped, len(items)),
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print("BACKFILL prefilter DISABLED by error: %r" % (exc,), file=sys.stderr)
            pref_skipped = 0

    t0 = time.time()
    judged = 0
    dup_skip = 0  # OS-FIX-JEVLOCK：落库前二次校验发现已被外部先写的行数
    deadline_hit = 0
    cloud_fail = 0  # K-STOCK-072：连续空判短路（2026-10-10）
    _yield_skip_logged = False  # EXEC-R28：免让位只在首轮打印一次，防日志刷屏
    empty_streak = 0
    deadline_ts = (t0 + args.deadline) if args.deadline else None
    for i in range(0, len(items), 8):  # jev_judge_batch 单次上限 8
        if args.deadline and (time.time() - t0) >= args.deadline:
            deadline_hit = 1
            break
        judges = _judge_batch_sleep(items[i : i + 8], item_sleep, deadline_ts)
        written = 0
        try:
            for nid, j in judges.items():
                # OS-FIX-JEVLOCK：事务内二次校验——只写仍是"待判"的行，绝不覆盖
                # 已被另一实例/外部写入的 jev（锁被陈旧回收时的最后一道防重复写）。
                cur = db.execute(
                    "UPDATE news SET jev=? WHERE id=? AND (jev IS NULL OR jev='')",
                    (json.dumps(j, ensure_ascii=False), nid),
                )
                written += int(cur.rowcount or 0)
            db.commit()
        except sqlite3.OperationalError as e:
            # 2026-10-16 审计：写库瞬态失败（locked/busy）原炸整个轮次，
            # 本组判定全丢；回滚留待下轮重判（jev 判定幂等）。
            db.rollback()
            print(
                "BACKFILL group commit FAIL %r（跳过本组，下轮重判）" % e,
                file=sys.stderr,
            )
            continue
        judged += written
        dup_skip += len(judges) - written
        if deadline_ts is not None and time.time() >= deadline_ts:
            deadline_hit = 1
            break
        # 云侧故障短路：连续 3 组（24 条）全军覆没即收工。背景：typesafe
        # systemone 451 自 10-09 09:18 起持续，旧逻辑每轮空转 25-30 分钟
        # （451 立即返回+0.8s 间隔+二次重试）还占 ollama 锁。空判恢复即
        # 自动回归正常路径，云侧自愈无需改码。
        if len(judges) == 0:
            empty_streak += 1
            if empty_streak >= 3:
                cloud_fail = 1
                break
        else:
            empty_streak = 0
        if skip_yield:
            # EXEC-R28：M3 暂停旗存在时不让位（无 M3 竞争，让位纯浪费）；锁仍持有。
            ollama_lock.heartbeat()
            if not _yield_skip_logged:
                print(
                    "BACKFILL lock lease-yield skipped：M3 暂停旗存在（EXEC-R28）",
                    flush=True,
                )
                _yield_skip_logged = True
        elif not _locked_tick(
            ollama_lock,
            "jev_backfill",
            m3_is_paused,
            yield_disabled,
            deadline_ts=deadline_ts,
        ):
            # 租约制（K-ORG-20261025-LOCK）：也让一手，M3 才不会被长期挤占。
            # OS-FIX-JEVLOCK：tick 已受 deadline_ts 约束，超时会打 OLLAMA_LOCK tick timeout。
            print(
                "BACKFILL lock lost: tick 未抢回（见 OLLAMA_LOCK tick timeout 日志），本轮收工",
                flush=True,
            )
            break
    left = db.execute(
        "SELECT COUNT(*) FROM news WHERE jev IS NULL OR jev=''"
    ).fetchone()[0]
    alerted = 0
    if (
        args.alert_threshold
        and left > args.alert_threshold
        and _alert_ok(throttle=True)
    ):
        alerted = 1 if alert_backlog(left, judged, deadline_hit) else 0
    print(
        "BACKFILL judged=%d/%d left=%d %.1fs deadline_hit=%d alerted=%d "
        "cloud_fail=%d item_sleep=%.2f lease_yield=%s dup_skip=%d pref_skip=%d pref_keep=%d"
        % (
            judged,
            len(items),
            left,
            time.time() - t0,
            deadline_hit,
            alerted,
            cloud_fail,
            item_sleep,
            "skip" if skip_yield else "normal",
            dup_skip,
            pref_skipped,
            len(items),
        )
    )


if __name__ == "__main__":
    main()
