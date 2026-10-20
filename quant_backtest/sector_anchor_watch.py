# -*- coding: utf-8 -*-
"""板块日线锚断线巡检（WO-QT-20261020-065，2026-10-21）：断 >3 天 → P0 告警。

背景：板块日线（tdx_sector_daily）自 20261012 停更（根因在远端采集，
WO-021 根因表），停更 6 日无人上报——strong_scan freshness_guard 只在
StrongEod 跑批时拦截，拦截出口又曾只落 stdout（guard_alert.py 立项
判例），缺一个独立巡检口把「板块锚断了」主动叫醒人。

判据口径：
  · 板块锚 = linkage.block_bars("881019", "1d", limit=3) 最新 dt——
    与 strong_scan.py freshness_guard / strong_guard_precheck.py 完全
    同一读取口径，不另发明（881019 板块指数，源 data/tdx/tdx_sector_daily）；
  · 断线天数 = 巡检基准日(baseline，缺省=当天) − 板块锚最新 dt。
    【简化声明】本模块按自然日计差；交易日口径下同跨度断线交易日数
    ≤自然日数，故自然日 >3 必有交易日 >3——宁早报不漏报，节假日密集
    期可能提前误报，由建议动作里人工复核兜底；
  · 差 > 3 天 → 调 guard_alert(task, reason, anchors, suggest, send,
    state_path, now) 发 P0（四要素卡 + 单线单键 12h 去重，键=任务名|原因
    的 sha1，锚日期变即新告警）；差 ≤ 3 → 静默；
  · 板块锚缺失（无K线/parquet 空）视同断线，同样 P0；
  · 去重状态默认独立落 quant_backtest/data/sector_anchor_watch_state.json
    （不与 guard_alert_state.json 混写，键空间隔离）。

发送口径：send 缺省 False（凭据未注入前不真实推送，只拼装判定文本）；
CLI --dry-run 强制 dry；--send 才走 runner.push_feishu 真推。

用法:
  python sector_anchor_watch.py                       # 巡检（dry）
  python sector_anchor_watch.py --baseline 2026-10-21 --send
  python sector_anchor_watch.py --dry-run             # 显式演练
"""
import argparse
import os
import sys
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SECTOR_BLOCK = "881019"   # 板块锚代码（与 strong_scan.py GUARD_BLOCK 同源）
MAX_STALE_DAYS = 3        # 断线阈值（天）：>3 告警，≤3 静默
DEFAULT_TASK = "SectorAnchorWatch"
DEFAULT_STATE_PATH = os.path.join(HERE, "data",
                                  "sector_anchor_watch_state.json")


def _read_sector_anchor():
    """板块锚最新交易日（ISO 日期串）；无K线返回 None。

    既有口径复用：linkage.block_bars 读 tdx_sector_daily parquet。
    懒导入——测试与本模块的纯判定路径不触 pandas/parquet。
    """
    import linkage
    blk = linkage.block_bars(SECTOR_BLOCK, "1d", limit=3)
    if not blk:
        return None
    return str(blk[-1]["dt"])[:10]


def sector_anchor_watch(baseline=None, send=False, state_path=None,
                        now=None, max_stale_days=MAX_STALE_DAYS):
    """巡检板块锚断线 → 断 >max_stale_days 天走 guard_alert 发 P0。

    返回 (sent, text, detail)：
      sent    guard_alert 是否真实发送（dry/静默均 False）；
      text    告警文本（静默时为空串）；
      detail  {anchor, baseline, stale_days, alerted, reason}。
    """
    baseline = baseline or date.today().isoformat()
    anchor = _read_sector_anchor()
    detail = {"anchor": anchor, "baseline": baseline,
              "stale_days": None, "alerted": False, "reason": None}

    if anchor is None:
        stale = None
        reason = ("板块日线锚断线：板块锚 %s 无K线"
                  "（tdx_sector_daily 空或缺失）" % SECTOR_BLOCK)
        anchors = {"板块锚 %s" % SECTOR_BLOCK: "无", "基准日": baseline}
    else:
        try:
            stale = (datetime.strptime(baseline, "%Y-%m-%d").date()
                     - datetime.strptime(anchor, "%Y-%m-%d").date()).days
        except ValueError:
            stale = None
            reason = ("板块日线锚日期不可解析：板块锚 %s=%s" %
                      (SECTOR_BLOCK, anchor))
            anchors = {"板块锚 %s" % SECTOR_BLOCK: anchor, "基准日": baseline}
        else:
            detail["stale_days"] = stale
            if stale <= max_stale_days:
                return False, "", detail          # 差≤3 静默
            reason = ("板块日线锚断线：板块锚 %s=%s，基准日 %s，"
                      "滞后 %d 天 > 阈 %d 天（自然日口径，见模块 docstring）"
                      % (SECTOR_BLOCK, anchor, baseline, stale,
                         max_stale_days))
            anchors = {"板块锚 %s" % SECTOR_BLOCK: anchor, "基准日": baseline}

    detail["reason"] = reason
    import guard_alert
    sent, text = guard_alert.guard_alert(
        DEFAULT_TASK, reason, anchors=anchors,
        suggest="核查 tdx_sector_daily 采集链（WO-021 根因表）；"
                "恢复后补数并复核断线期间交易日口径",
        send=send, state_path=state_path or DEFAULT_STATE_PATH, now=now)
    detail["alerted"] = True
    return sent, text, detail


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="板块日线锚断线巡检：锚落后基准日 >3 天（自然日口径）"
                    "→ guard_alert P0（send 缺省 False 只拼不推）。"
                    "退出码 0=巡检完成。")
    ap.add_argument("--baseline", help="巡检基准日 YYYY-MM-DD（缺省=当天）")
    ap.add_argument("--send", action="store_true",
                    help="真实推送（缺省 dry：只拼装判定文本）")
    ap.add_argument("--dry-run", action="store_true",
                    help="强制 dry（与缺省一致，显式演练用）")
    ap.add_argument("--state", default=None,
                    help="去重状态文件路径（缺省 data/sector_anchor_watch_"
                         "state.json）")
    ap.add_argument("--max-stale", type=int, default=MAX_STALE_DAYS,
                    help="断线告警阈值（天），缺省 %d" % MAX_STALE_DAYS)
    ap.add_argument("--now", help="覆盖当前时间 YYYY-MM-DD[ HH:MM:SS]（演练）")
    a = ap.parse_args(argv)
    now = None
    if a.now:
        s = a.now.strip()
        now = datetime.strptime(
            s, "%Y-%m-%d %H:%M:%S" if " " in s else "%Y-%m-%d")
    sent, text, detail = sector_anchor_watch(
        baseline=a.baseline,
        send=False if a.dry_run else a.send,
        state_path=a.state, now=now, max_stale_days=a.max_stale)
    print("anchor=%s baseline=%s stale_days=%s alerted=%s sent=%s" % (
        detail["anchor"], detail["baseline"], detail["stale_days"],
        detail["alerted"], sent))
    if text:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
