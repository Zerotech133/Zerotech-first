# -*- coding: utf-8 -*-
"""K225 · miss 检测哨兵：应跑未跑巡检（与「跑了但失败」告警互补）。

背景：K221 巡检发现 MorningReport 07:00 静默 miss 无人知晓——alert_fail
只覆盖「跑了但失败」（rc≠0），「应跑未跑」（错过/被禁/挂死）是盲区。

口径：
· 白名单底册 REGISTRY：任务名 → {expect "HH:MM", since "YYYY-MM-DD"}——
  expect=每日排程时刻（显式可审计，优于从 NextRun 反推）；since=该任务
  生效日（之前不检，防新任务首日前误报）。
· 判定：now >= expect + GRACE 且 Last Run < 今日 expect → MISS。
  Last Run 从 schtasks /v 实测（不信任日志）。
· 2026-10-24 加「刚醒宽限」：机器从挂起恢复后的 RESUME_SETTLE_MIN 分钟内，判定
  窗口顺延。理由：挂起结束时机器的系统时间会被硬件钟一次性纠正（实测 +600437ms
  / +1084752ms），一跳就跨过 deadline，而 Task Scheduler 的 StartWhenAvailable
  还在补跑，此刻 _task_last_run 仍是旧值 → 假 MISS（WO-20261024-02）。
  锚点仍取任务自己的 Last Run（输出字段 last_success_at），now 只回答"够不够久、
  能不能下结论"；过了结算窗口还没跑，照报 MISS，真漏不放走。
· 首期只收「交易日无关」子集 8 任务（休市也必须跑的同步/巡检/抽取/
  告警类）；交易日依赖任务（缠论扫描/回填/复盘类）休市日会 miss，属
  K221 判例的正常跳过，二期接交易日判定后纳入。
· 影子纪律：结果落 data/miss_check_last.json（原子写）；有 MISS 时经
  alert_fail 推送（失败可见），全绿静默 exit 0。

用法：python miss_check.py run [--json]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OUT = os.path.join(HERE, "data", "miss_check_last.json")
GRACE_MIN = 90  # 排程后宽限（错过补跑 AIColAudit 实测隔 4h，先 90min 起步）

# 2026-10-24 加：机器刚醒时的结算窗口（分钟）。
# 为什么需要：本哨兵用 `now` 判断"是否已过宽限期"，而 `now` 会被挂起/睡醒打乱。
# 实测（2026-10-24，WO-20261024-02）：13:50:35→14:08:40 挂起 18.1 分钟、
# 15:32:45→15:42:45 挂起 10.0 分钟，醒来后系统时间被硬件钟一次纠正 +600437ms。
# 副作用：一跳就跨过 deadline，而 Task Scheduler 的 StartWhenAvailable 还在补跑，
# `_task_last_run` 此刻仍是旧值 → 判 MISS → 假告警。
# 口径：若最近一次挂起/唤醒在 SETTLE 分钟以内，把判定窗口顺延到补跑结算完，
# 不在"刚醒"这一刻下结论；过了窗口仍没跑，才报 MISS（真漏不放走）。
RESUME_SETTLE_MIN = 45
# 单次时钟纠正 ≥ 60s 才算挂起（与 tools/gen_health_card.py 的 machine_suspend 同口径）
SUSPEND_DELTA_MIN_SECONDS = 60.0
_RESUME_QUERY_TIMEOUT = 25.0
_RESUME_PS = (
    "Get-WinEvent -FilterHashtable @{LogName='System'; Id=107,1;"
    "StartTime=(Get-Date).AddHours(-6)} -ErrorAction SilentlyContinue |"
    "Select-Object Id,@{n='at';e={$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')}}"
    ",Message | ConvertTo-Json -Compress -Depth 3"
)

# 交易日无关子集：白名单底册（显式 expect，since=生效日）
#
# 2026-11-05 底册校准（老田指令"先找问题再修"）：一晚固定误报 10 条，
# 核因是底册写的还是换机/改造前的任务名。逐条对过实际计划任务后改名如下；
# 改不了的（真断供）留在册内继续报——宁可每晚吵，也不许静默断供。
#   KG-TushareSync(00:10) → 旧名退役，实际为 TushareSync(23:30)，本册下方已有正确条目
#   AIColAudit(06:10)     → 现名 \ColAudit，时刻不变
#   CryptoSync            → 时刻写错 08:10，实际 09:00（任务当前 Disabled，见下方注）
#   KG-TdxSync(17:40)     → 旧名退役，11-02 起拆 \ZeroBound\TdxSync_A/B/C/D 四片
#   KG-Cl1Sync            → 现名 KgCl1Sync，时刻不变
#   AI工作站每日备份(03:00) → 现名 BackupDaily，实际 07:45 起跑
#   JevReviewBackfill     → 现名 \JevBackfill（整点轮），锚点仍取 20:30
REGISTRY = {
    "ColAudit": {"expect": "06:10", "since": "2026-09-01"},
    "TbeStats": {"expect": "06:45", "since": "2026-10-05"},
    "KgExtractShadow": {"expect": "06:50", "since": "2026-10-05"},
    "MorningReport": {"expect": "07:00", "since": "2026-09-28"},
    "KgResonanceNotify": {"expect": "08:05", "since": "2026-10-06"},
    # 2026-11-05 13:12 已恢复：\CryptoSync 仍叫原名，但 wrapper 改接
    # export_us_hk --market CRYPTO（统一供给层），State=Ready、触发 rc=0、
    # 下次 11-06 09:00。保留在册继续守，别再按"Disabled 真断供"读这条。
    "CryptoSync": {"expect": "09:00", "since": "2026-09-20"},
    "KgCl1Sync": {"expect": "18:10", "since": "2026-10-05"},
    # 10-08 补：JEV 20:30 曾被睡眠窗口静默吃掉，哨兵底册原缺此项——
    # 手动守候才撞见
    # 2026-11-05 复核：九表拉取脚本 cainiao_pull.py 已重写并全量跑通
    # （9/9 rc=0、源/镜像行数 ALL_MATCH），但 19:00 任务仍未排班（需 L2 批准）
    # → 调度层仍是真断供，保留在册继续报（不许静默退役）。
    "CainiaoPull": {"expect": "19:00", "since": "2026-10-09"},
    "JevBackfill": {"expect": "20:30", "since": "2026-10-07"},
    # 10-09 补：晨间三连（06:20/06:30/06:40）原全不在册——恰处夜间睡眠
    # 窗口尾段，被静默吃掉将无人报（JEV 前车）；三任务已同批加
    # StartWhenAvailable，本册兜底「唤醒后也没补跑」的最后情形
    "KgDailySync": {"expect": "06:20", "since": "2026-10-09"},
    "StrongEod": {"expect": "06:30", "since": "2026-10-09"},
    # 2026-11-05 12:29 已按批准重建，13:14 触发 rc=0（图谱 2 条漂移修复后
    # 五断言全绿）。保留在册继续守，别再按"真断供"读这条。
    "K260RegressionDaily": {"expect": "06:40", "since": "2026-10-09"},
    # 10-09 凌晨整理补（项目全面整理役）：两大盲区入册——
    # TushareSync 23:30 老田侧全量（1.5~3.5h，10-09 误杀事故主角；
    # alert_fail 兜跑挂，本册兜没跑，21:30 档次日检出）；AI工作站
    # 每日备份 03:00（B4 链核心，深睡高危时段，21:30 档当晚检出，
    # 08:30 晨档 3.8h 检出——晨档与 21:30 双跑位同批部署）
    "TushareSync": {"expect": "23:30", "since": "2026-10-09"},
    # 2026-11-05：原 "AI工作站每日备份" 03:00 已不存在，现为 \BackupDaily
    # （触发 07:45，实测 07:56 完成，产物 1 加密 DB dump + 4 tar + 镜像）。
    "BackupDaily": {"expect": "07:45", "since": "2026-10-31"},
    # 10-10 补（八审 TodaysPicks 时序核查）：T1 聚合链四任务原全不在册。
    # 单日漏跑时 alert_fail 不推（没跑≠失败）、聚合闸门 4 天容差拦不住
    # （lag=1<4），T1 错日共振整链静默。原 ChanlunRefill 06:30 = 聚合消费的早批
    # （当时 ChanlunScan 15:35 晚批在 15:10 聚合后落盘，不参与当日聚合）。
    # 2026-11-05 复核：ChanlunRefill 任务已不存在，但同一份
    # start_chanlun_scan.ps1 由 \ChanlunScan 自 10-26 起改到 07:20 每日跑
    # （11-05 07:20 触发、07:48 完成、rc=0），已在 15:10 聚合前落盘，
    # 早批职责实际由它承担。故不重建 06:30 任务（会变成一天两次全市场扫描），
    # 底册改为监控真实任务名与真实触发时刻。
    # RpsDaily 仅交易日跑 → trade_only（非交易日跳过，见 _trade_today）。
    "ChanlunScan": {"expect": "07:20", "since": "2026-10-26"},
    "NineDimScan": {"expect": "06:40", "since": "2026-10-10"},
    "TodaysPicks": {"expect": "15:10", "since": "2026-10-10"},
    "RpsDaily": {"expect": "16:05", "since": "2026-10-10", "trade_only": True},
    # 10-10 补（十三审字段契约审计）：WeakScan 周扫（周一 15:35）原不在册
    # ——漏跑时 weak 源静默断供，lag>10 后 stale 拦截属设计内放行（不触发
    # T1 停挂），weak 否决长期失效无人报。周任务用 weekday 语义防误报；
    # 周一 21:30 档检出当日漏跑；若当日档也挂，下周一复查仍报（last 恒旧）。
    "WeakScan": {
        "expect": "15:35",
        "since": "2026-10-10",
        "weekday": 0,
    },  # 0=周一（Python weekday 约定）
    # 2026-11-05 补：\TdxSync 自 11-02 起拆成 \ZeroBound\ 下四分片，旧任务
    # Disabled 属设计内（盲目启用会与四分片重复写库），但四分片本身一直不在册
    # ——任一片静默死掉无人知。按实际触发时刻补哨兵。
    # 任务在子路径且名称含中文，schtasks /tn 需完整路径，故用 "path" 指定；
    # 查不到时 run() 回退短名。沙箱禁 schtasks，端到端由 21:30 首轮观察。
    "TdxSync_A_高频": {
        "expect": "17:35", "since": "2026-11-02",
        "path": r"\ZeroBound\TdxSync_A_高频",
    },
    "TdxSync_B_财务": {
        "expect": "17:45", "since": "2026-11-02",
        "path": r"\ZeroBound\TdxSync_B_财务",
    },
    "TdxSync_C_低频": {
        "expect": "17:55", "since": "2026-11-02",
        "path": r"\ZeroBound\TdxSync_C_低频",
    },
    "TdxSync_D_缺口": {
        "expect": "18:05", "since": "2026-11-02",
        "path": r"\ZeroBound\TdxSync_D_缺口",
    },
}


def _task_last_run(name):
    """schtasks /v 实测 Last Run Time → datetime 或 None（从未运行）。

    10-08 修（两晚 9/9 never 根因）：任务环境下 schtasks 输出中文本地化
    标签（「上次运行时间:」）且字节为 UTF-8，mbcs 解码即乱码、英文
    regex 永不命中 → bytes 捕获 + utf-8/mbcs 双解 + 中英双 regex。
    """
    try:
        p = subprocess.run(
            ["schtasks", "/query", "/tn", name, "/v", "/fo", "list"],
            capture_output=True,
            timeout=20,
        )
        for enc in ("utf-8", "mbcs"):
            out = p.stdout.decode(enc, errors="replace")
            m = re.search(r"Last Run Time:\s*(\S+ \S+)", out) or re.search(
                r"上次运行时间:\s*(\S+ \S+)", out
            )
            if m and not m.group(1).startswith("1999/"):
                try:
                    return datetime.strptime(m.group(1), "%Y/%m/%d %H:%M:%S")
                except ValueError:
                    continue
        return None
    except Exception:
        return None


def _recent_resume_minutes(now=None):
    """最近一次"从挂起/睡眠恢复"距今多少分钟；查不到返回 None（不判、不加宽限）。

    判据（只认结构化 ID，不解析本地化文案）：
      · Kernel-Power 107：系统已从睡眠状态恢复
      · Kernel-General 1 且消息含 ≥60s 的毫秒增量：时间被硬件钟纠正，
        增量本身就是刚才那次挂起的时长（醒来瞬间写入）。
    两者取时间最大者。查不到 = 这台机器近 6 小时没睡过 → 返回 None。
    """
    now = now or datetime.now()
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _RESUME_PS],
            capture_output=True,
            timeout=_RESUME_QUERY_TIMEOUT,
        )
    except Exception:
        return None
    raw = p.stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    events = parsed if isinstance(parsed, list) else [parsed]
    latest = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        try:
            event_id = int(ev.get("Id"))
        except (TypeError, ValueError):
            continue
        candidate = False
        if event_id == 107:
            candidate = True
        elif event_id == 1:
            deltas = re.findall(r"(-?\d[\d,]*)\s*ms", str(ev.get("Message") or ""))
            for d in deltas:
                try:
                    if (
                        abs(float(d.replace(",", ""))) / 1000.0
                        >= SUSPEND_DELTA_MIN_SECONDS
                    ):
                        candidate = True
                        break
                except ValueError:
                    continue
        if not candidate:
            continue
        try:
            when = datetime.strptime(str(ev.get("at")), "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            continue
        if latest is None or when > latest:
            latest = when
    if latest is None:
        return None
    return max(0.0, (now - latest).total_seconds() / 60.0)


def wake_grace_minutes(minutes_since_resume):
    """机器刚醒 → 把本轮判定窗口顺延，避免时钟跳变造成误报。

    纯函数，便于单测：None（没睡过）或已超结算窗口 → 0；否则返回剩余分钟数。
    """
    if minutes_since_resume is None:
        return 0.0
    if minutes_since_resume >= RESUME_SETTLE_MIN:
        return 0.0
    return RESUME_SETTLE_MIN - max(0.0, float(minutes_since_resume))


def _trade_today(now=None):
    """今天是否为 A 股交易日：行情湖 ts_daily 最新日 == 今天。

    八审加，供 trade_only 项（仅交易日运行的任务，如 RpsDaily 16:05）
    非交易日免检：21:30 档检查时 TdxSync 17:30 已入库当日数据 →
    最新日==今天 即交易日；周末/假日最新日停在上一交易日 → 跳过。
    查询失败按非交易日跳过（宁漏报不误报）；「TdxSync 挂导致当日
    未入库」由 KG-TdxSync 在册项兜住，不在此扩大检查面。
    """
    try:
        from tdx_read import latest_date

        return latest_date("ts_daily") == (now or datetime.now()).strftime("%Y%m%d")
    except Exception as e:
        print("trade_today 查询失败（trade_only 项按非交易日跳过）: %s" % str(e)[:60])
        return False


def run(now=None):
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    # 2026-10-24：先把"机器是不是刚醒"算出来。刚醒时 Task Scheduler 的
    # StartWhenAvailable 正在补跑，此刻下 MISS 结论 = 必然误报（时钟刚跳完）。
    minutes_since_resume = _recent_resume_minutes(now)
    wake_grace = wake_grace_minutes(minutes_since_resume)
    if wake_grace > 0:
        print(
            "机器 %.1f 分钟前从挂起恢复：本轮判定窗口顺延 %.0f 分钟"
            "（补跑结算中，先不下 MISS 结论）" % (minutes_since_resume, wake_grace)
        )
    miss, checked = [], 0
    for name, cfg in REGISTRY.items():
        if cfg.get("since") and today < cfg["since"]:
            continue  # 生效日前不检（新任务防误报）
        if cfg.get("trade_only") and not _trade_today(now):
            continue  # 仅交易日任务：非交易日不检（八审）
        if cfg.get("weekday") is not None and now.weekday() != cfg["weekday"]:
            continue  # 周任务：仅指定星期检查（十三审）
        exp_h, exp_m = map(int, cfg["expect"].split(":"))
        # 2026-11-05 修：原来只 replace(second=0)，microsecond 仍带 now 的余数，
        # 于是 expect = HH:MM:00.xxxxxx；任务恰好在整分整秒跑完（last=HH:MM:00.000000）
        # 就判成 last < expect → 假 MISS。RpsDaily 因此连续误报（实际 16:05:00 已跑）。
        expect = now.replace(hour=exp_h, minute=exp_m, second=0, microsecond=0)
        deadline = expect + timedelta(minutes=GRACE_MIN + wake_grace)
        if now < deadline:
            continue  # 未到宽限期不检
        checked += 1
        # 锚点是任务自己的「最后成功时间」（schtasks Last Run），不是 now；
        # now 只回答一个问题：过去够久了吗、现在能不能下结论。
        # 子路径任务（如 \ZeroBound\TdxSync_*）用 schtasks /tn 查询时必须给
        # 完整路径，否则查不到会假 MISS；查不到时再回退短名兜一层。
        lookup = cfg.get("path") or name
        last_success = _task_last_run(lookup)
        if last_success is None and cfg.get("path"):
            last_success = _task_last_run(name)
        if last_success is None or last_success < expect:
            stamp = last_success.strftime("%Y-%m-%d %H:%M") if last_success else "never"
            miss.append(
                {
                    "task": name,
                    "expect": cfg["expect"],
                    "last_success_at": stamp,
                    "last_run": stamp,
                }
            )  # last_run 保留：既有消费方兼容
    for m_ in miss:
        print(
            "MISS  %s  expect %s  last %s" % (m_["task"], m_["expect"], m_["last_run"])
        )
    print(
        "checked %d / miss %d（交易日无关子集 · 宽限 %d+%.0f 分钟）"
        % (checked, len(miss), GRACE_MIN, wake_grace)
    )
    res = {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "grace_min": GRACE_MIN,
        "minutes_since_resume": (
            round(minutes_since_resume, 1) if minutes_since_resume is not None else None
        ),
        "wake_grace_min": round(wake_grace, 1),
        "checked": checked,
        "n_miss": len(miss),
        "miss": miss,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    if miss:
        # 失败可见：alert_fail 承担推送（复用既有通道）
        try:
            subprocess.run(
                [
                    sys.executable,
                    os.path.join(HERE, "..", "news_collector", "alert_fail_wrap.py"),
                    "miss_check",
                    "MISS×%d" % len(miss),
                    json.dumps(miss, ensure_ascii=False),
                ],
                timeout=30,
            )
        except Exception as e:
            print("alert_fail 调用失败: %s" % str(e)[:60])
    else:
        print("全绿（静默）")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run")
    ap.parse_args()
    run()


if __name__ == "__main__":
    main()
