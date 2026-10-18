# -*- coding: utf-8 -*-
"""deadline_watchdog.py — 市场推送矩阵数据到点升级器（K-ORG-197 轨道B，W33 提前实装）。

职责：
    通用化 portal/publish_watchdog.py（picks 发布巡检判例）的看门狗机制，
    对推送矩阵（portal/push_matrix.json，六市场 x 窗口 x 锚）做数据到点巡检：
      1. run_check 纯逻辑：遍历六市场今日全部窗口，筛选「今天、
         deadline <= now <= end+30min」的在判窗口，对窗口锚调
         push_matrix.anchor_status；missing/stale 记 pending，
         disabled/unknown 锚跳过（不算告警面，仅计 skipped）。
      2. 升级账本：旗标 portal/work/deadline_pending.json 原子写
         （tmpfile + os.replace，机制出处 publish_watchdog.atomic_write_json）。
         每次运行从旧旗标继承 attempts，仍 pending 的键 +1；
         attempts >= 2 的条目标 escalated=true（L3 升级红卡）。
      3. 告警通道：直接复用 publish_watchdog.send_feishu（底层
         feishu_card_v2.send_card，schema 2.0 卡片，template 色 red/green，
         默认发给老田）。存在 escalated 条目发红卡（逐行列出
         市场/窗口/锚/status/detail/截止/attempts）；本轮 pending 清空但
         旧旗标有账则发绿卡「已恢复」并清旗标；仅 pending 未升级只更新
         旗标不发卡（巡检留痕）。sender 做成可注入参数，测试传 fake。

用法：
    python -X utf8 news_collector/deadline_watchdog.py            # 正常巡检
    python -X utf8 news_collector/deadline_watchdog.py --dry-run  # 只判不发不写
    python -X utf8 news_collector/deadline_watchdog.py --now "2026-10-19 08:05"
                                                                  # --now 仅测试用
退出码：0 = 全部就绪或已恢复；2 = 有 pending（与 picks 巡检同语义）。
零第三方依赖；AIOS_ROOT 环境变量语义与 push_matrix 一致（直接用 pm.REPO）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portal"))

import push_matrix as pm  # noqa: E402
import publish_watchdog as pw  # noqa: E402

WORK = ROOT / "portal" / "work"
LOG_PATH = str(WORK / "deadline_watch.log")
FLAG_PATH = str(WORK / "deadline_pending.json")

GRACE_MIN = 30    # 判定带宽：deadline <= now <= end + GRACE_MIN 分钟
ESCALATE_AT = 2   # 连续 pending 次数达到该值即升级 L3 红卡


def log(msg):
    """巡检留痕（镜像 publish_watchdog.log 的落盘方式，独立日志文件）。"""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass  # 日志失败不影响巡检主流程（结果仍由退出码与旗标承载）


def _at(d, hhmm):
    """HH:MM -> 当日 datetime（与 push_matrix._at 同口径，避免依赖私有函数）。"""
    hh, mm = int(hhmm[:2]), int(hhmm[3:5])
    return datetime(d.year, d.month, d.day, hh, mm)


def run_check(matrix, repo, now):
    """纯逻辑巡检：扫六市场今日窗口，返回在判窗口的就绪状况。

    筛选「今天、deadline <= now <= end+30min」的窗口（到点未就绪的
    机器可判带）；锚 disabled/unknown 跳过且不计 checked（计 skipped）。
    返回 {"now", "checked", "skipped", "pending": [{market, window,
    anchor, status, detail, deadline}, ...]}。
    """
    d = now.date()
    pending, checked, skipped = [], 0, 0
    for mkt, cfg in matrix["markets"].items():
        for w in pm.resolve_windows(cfg, d):
            dl = _at(d, w["deadline"])
            end_grace = _at(d, w["end"]) + timedelta(minutes=GRACE_MIN)
            if not (dl <= now <= end_grace):
                continue  # 未到截止或已出宽限带，不在本次告警面
            st = pm.anchor_status(w["anchor"], matrix, now=now, repo=repo)
            if st["status"] in ("disabled", "unknown"):
                skipped += 1  # 预研期未实核锚绝不产生假告警
                continue
            checked += 1
            if st["status"] != "ok":
                pending.append({
                    "market": mkt,
                    "window": w["name"],
                    "anchor": w["anchor"],
                    "status": st["status"],
                    "detail": st["detail"],
                    "deadline": w["deadline"],
                })
    return {"now": now.strftime("%Y-%m-%d %H:%M:%S"), "checked": checked,
            "skipped": skipped, "pending": pending}


def _read_flag(path):
    """读升级账本；缺失/坏 JSON 按空账处理并在返回里留痕（不阻塞巡检）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), None
    except FileNotFoundError:
        return {}, None
    except (OSError, ValueError) as e:
        return {}, "旗标读取失败按空账重建: %s" % e


def _write_flag(path, obj):
    """旗标原子写（复用 publish_watchdog.atomic_write_json：tmpfile+os.replace）。"""
    pw.atomic_write_json(path, obj)


def _clear_flag(path):
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _key(item):
    return "%s|%s" % (item["market"], item["window"])


def _red_md(pending):
    """红卡正文：逐行列出 市场/窗口/锚/status/detail/截止/attempts。"""
    lines = []
    for it in pending:
        lines.append(
            "- 市场 **%s** / 窗口 %s / 锚 `%s`：**%s**（%s）"
            " 截止 %s，连续未就绪 %s 次"
            % (it["market"], it["window"], it["anchor"], it["status"],
               it["detail"] or "无详情", it["deadline"], it["attempts"]))
    return ("推送矩阵数据锚到点未就绪（L3 升级，连续 %d 轮）：\n\n%s\n\n"
            "**处置**：优先补跑对应数据采集；恢复后下一轮巡检自动发绿卡销账。"
            % (ESCALATE_AT, "\n".join(lines)))


def _green_md(old_pending):
    lines = ["- %s / %s（锚 %s，%s）" % (it["market"], it["window"],
                                        it["anchor"], it["status"])
             for it in old_pending]
    return "以下数据锚已恢复就绪，升级账本已清：\n\n%s" % "\n".join(lines)


def run_cycle(matrix=None, repo=None, now=None, sender=None,
              flag_path=None, dry_run=False):
    """一次完整巡检 + 升级计数 + 发送；返回 (exit_code, report)。

    matrix/repo/now/sender/flag_path 均可注入（默认走真实矩阵与真实通道）；
    dry_run 只判不写不发。退出码：0=就绪或恢复；2=有 pending。
    """
    matrix = matrix if matrix is not None else pm.load_matrix()
    repo = repo if repo is not None else pm.REPO
    now = now if now is not None else datetime.now()
    sender = sender if sender is not None else pw.send_feishu
    flag_path = flag_path or FLAG_PATH

    report = run_check(matrix, repo, now)
    old, flag_err = _read_flag(flag_path)
    if flag_err:
        log("[warn] %s" % flag_err)
        report["flag_error"] = flag_err
    old_pending = old.get("pending") or []
    attempts = dict(old.get("attempts") or {})

    # 继承账本：仍 pending 的键 +1（新键从 0 起算记 1）
    for it in report["pending"]:
        attempts[_key(it)] = int(attempts.get(_key(it), 0)) + 1
        it["attempts"] = attempts[_key(it)]
        it["escalated"] = attempts[_key(it)] >= ESCALATE_AT

    if report["pending"]:
        if not dry_run:
            keep = {_key(it) for it in report["pending"]}
            _write_flag(flag_path, {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "pending": report["pending"],
                "attempts": {k: v for k, v in attempts.items() if k in keep},
            })
            log("[pending] %d 项未就绪（checked=%d skipped=%d）"
                % (len(report["pending"]), report["checked"],
                   report["skipped"]))
        if any(it.get("escalated") for it in report["pending"]):
            log("[升级] attempts>=%d，发 L3 红卡" % ESCALATE_AT)
            if not dry_run:
                sender("数据锚到点升级告警（L3）", "red",
                       _red_md(report["pending"]))
        exit_code = 2
        return exit_code, report

    if old_pending:
        # 本轮全部恢复且旧旗标有账：绿卡销账 + 清旗标
        if not dry_run:
            sender("数据锚到点已恢复", "green", _green_md(old_pending))
            if _clear_flag(flag_path):
                log("[恢复] 清除 deadline_pending 旗标（%d 项）"
                    % len(old_pending))
            else:
                log("[warn] 旗标清除失败（可能被占用），保留待下次重试")
        return 0, report

    if not dry_run and os.path.exists(flag_path):
        _clear_flag(flag_path)  # 空账兜底清理
    return 0, report


def main(argv=None):
    ap = argparse.ArgumentParser(description="市场推送矩阵数据到点升级器")
    ap.add_argument("--now", default=None,
                    help="仅测试用：指定判定时刻 YYYY-MM-DD HH:MM")
    ap.add_argument("--dry-run", action="store_true",
                    help="只判不发不写（不落旗标不发送）")
    args = ap.parse_args(argv)
    now = None
    if args.now:
        now = datetime.strptime(args.now, "%Y-%m-%d %H:%M")
    rc, report = run_cycle(now=now, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
