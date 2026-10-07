# -*- coding: utf-8 -*-
"""post_holiday_check.py —— 假期后首个交易日自动核验（模板执行器）

依据 post_holiday_check_template.md。每日 08:30 由 PostHolidayCheck 任务
触发，脚本自判：
  - nine_dim 快照 trade_date 仍为 20260930 → 未开市，静默退出；
  - 已刷新且报告未生成 → 执行三链路核验，写
    post_holiday_check_<trade_date>.md，自删计划任务；
  - 报告已存在（重入）→ 退出。

退出码：0 正常/跳过；1 存在 ❌ 项（已推送 alert_fail）。
"""
import csv
import datetime
import glob
import json
import os
import subprocess
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(HERE, "data", "nine_dim_scores.json")
POOL = os.path.join(HERE, "data", "chanlun_pool.jsonl")
REP = os.path.abspath(os.path.join(HERE, "..", "reports"))
BASE_TD = "20260930"          # 假期前最后快照
PORTAL = "http://127.0.0.1:8091"


def _task(name):
    """schtasks 查询 → (last_run, last_result) 字符串。"""
    try:
        out = subprocess.run(
            ["schtasks", "/query", "/tn", name, "/v", "/fo", "CSV"],
            capture_output=True, text=True, timeout=20).stdout
        lines = [l for l in out.splitlines() if l.strip()]
        if len(lines) < 2:
            return "未注册", "—"
        hdr = next(csv.reader([lines[0]]))
        row = next(csv.reader([lines[-1]]))
        d = dict(zip(hdr, row))
        return (d.get("Last Run Time") or "—",
                d.get("Last Result") or "—")
    except Exception as e:
        return "查询失败:%s" % str(e)[:40], "—"


def _page(path, kw):
    """门户页面关键词核验（绕系统代理）。"""
    try:
        op = urllib.request.build_opener(
            urllib.request.ProxyHandler({}))
        html = op.open(PORTAL + path, timeout=20).read().decode("utf-8",
                                                                "ignore")
        return kw in html, len(html)
    except Exception as e:
        return False, str(e)[:60]


def main():
    if not os.path.exists(SNAP):
        print("skip: no snap")
        return 0
    s = json.load(open(SNAP, encoding="utf-8"))
    td = s.get("trade_date")
    if td == BASE_TD:
        print("未开市（快照仍为 %s），跳过" % BASE_TD)
        return 0
    rpt = os.path.join(HERE, "post_holiday_check_%s.md" % td)
    if os.path.exists(rpt):
        print("已核验（%s 存在），退出" % os.path.basename(rpt))
        return 0

    L = []
    fails = []
    ap = L.append
    ap("# 假期后首个交易日核验报告 · %s" % td)
    ap("")
    ap("- 核验时点：%s（PostHolidayCheck 自动执行）"
       % datetime.datetime.now().strftime("%F %T"))
    ap("- 前一快照基线：nine_dim trade_date=%s · 依据 "
       "post_holiday_check_template.md" % BASE_TD)
    ap("")

    # ── 链路一：ChanlunScan（mls 首批）──
    lr, rc = _task("ChanlunScan")
    pool_rows, new_sig, mls_ok = [], 0, 0
    last_d = ""
    if os.path.exists(POOL):
        with open(POOL, encoding="utf-8") as f:
            pool_rows = [json.loads(l) for l in f if l.strip()]
        last_d = max((r.get("detect_date") or "" for r in pool_rows),
                     default="")
        new_sig = sum(1 for r in pool_rows if r.get("detect_date") == last_d)
        mls_ok = sum(1 for r in pool_rows
                     if r.get("detect_date") == last_d
                     and r.get("mls") is not None)
    mls_cov = ("%.0f%%" % (mls_ok * 100.0 / new_sig)) if new_sig else "—"
    ok_l1 = rc == "0" and (new_sig == 0 or mls_ok == new_sig)
    if not ok_l1:
        fails.append("ChanlunScan(rc=%s, mls %s/%d)" % (rc, mls_ok, new_sig))
    ap("## 1. ChanlunScan（05:50，mls 首批自然数据）%s"
       % ("✅" if ok_l1 else "❌"))
    ap("")
    ap("| 核验项 | 实际 |")
    ap("|---|---|")
    ap("| 任务 Last Run / Result | %s / %s |" % (lr, rc))
    ap("| pool 最新 detect_date | %s |" % (last_d or "—"))
    ap("| 新信号数 / mls 覆盖 | %d / %s |" % (new_sig, mls_cov))
    pg_ok, pg_len = _page("/bt-chanlun-pool", "<th>mls</th>")
    ap("| 门户 mls 列 | %s（页面 %s 字节）|"
       % ("命中" if pg_ok else "未命中", pg_len))
    if not pg_ok:
        fails.append("门户 mls 列未命中")
    ap("")

    # ── 链路二：NineDimScan（K33 首跑）──
    lr2, rc2 = _task("NineDimScan")
    rows = s.get("rows") or []
    band_ok = sum(1 for r in rows if r.get("band"))
    n_alert = s.get("n_alert")
    alert_rows = []
    if n_alert:
        hist = os.path.join(HERE, "data", "nine_dim_history.jsonl")
        if os.path.exists(hist):
            with open(hist, encoding="utf-8") as f:
                alert_rows = [json.loads(l) for l in f if l.strip()
                              and json.loads(l).get("pool_type") == "alert"]
    ok_l2 = (rc2 == "0" and td != BASE_TD
             and band_ok == len(rows) and n_alert is not None)
    if not ok_l2:
        fails.append("NineDimScan(rc=%s, band %d/%d, n_alert=%s)"
                     % (rc2, band_ok, len(rows), n_alert))
    ap("## 2. NineDimScan（06:40，K33 新代码首跑）%s"
       % ("✅" if ok_l2 else "❌"))
    ap("")
    ap("| 核验项 | 实际 |")
    ap("|---|---|")
    ap("| 任务 Last Run / Result | %s / %s |" % (lr2, rc2))
    ap("| 快照 trade_date | %s（已刷新）|" % td)
    ap("| band 覆盖 | %d/%d 行 |" % (band_ok, len(rows)))
    ap("| n_alert / alert_th | %s / %s |" % (n_alert, s.get("alert_th")))
    if alert_rows:
        ap("| 警报池首批 | %d 票：%s |"
           % (len(alert_rows), "、".join(sorted({r["code"]
                                                 for r in alert_rows})[:10])))
    pg2_ok, pg2_len = _page("/bt-9dim", "≥75 拥挤警报")
    ap("| 门户警报头部 | %s（页面 %s 字节）|"
       % ("命中" if pg2_ok else "未命中", pg2_len))
    if not pg2_ok:
        fails.append("门户 9dim 警报头部未命中")
    ap("")

    # ── 链路三：K33ShadowCheck（周五）──
    if datetime.date.today().weekday() == 4:
        lr3, rc3 = _task("K33ShadowCheck")
        ok_l3 = rc3 == "0"
        if not ok_l3:
            fails.append("K33ShadowCheck rc=%s（应已有推送）" % rc3)
        ap("## 3. K33ShadowCheck（08:00 周例行）%s"
           % ("✅" if ok_l3 else "❌（推送应已触达，按 checklist §A 处置）"))
        ap("")
        ap("| 任务 Last Run / Result | %s / %s |" % (lr3, rc3))
        ap("")
    else:
        ap("## 3. K33ShadowCheck：非周五，跳过")
        ap("")

    # ── 链路四：weak 产线（WeakTrackCheck 15:30 / WeakScan 周一 15:35）──
    # 08:30 核验时当日任务尚未到点，只验注册健康+存量数据+门户页；
    # 当日产出由任务自身推送触达（触发红卡/扫描蓝卡），异常走 alert_fail。
    lr4, rc4 = _task("WeakTrackCheck")
    lr5, rc5 = _task("WeakScan")
    tracks = [p for p in sorted(glob.glob(
        os.path.join(REP, "weak_track_*.jsonl")))
        if ".closed_" not in os.path.basename(p)]
    t_ok, t_info = len(tracks) > 0, []
    for p in tracks:
        try:
            last = ""
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last = line
            r = json.loads(last.lstrip("\ufeff"))
            t_info.append("%s（数据日 %s）" % (r.get("code"), r.get("px_d")))
        except Exception as e:
            t_ok = False
            t_info.append("%s 尾行读取失败：%s"
                          % (os.path.basename(p), str(e)[:40]))
    scans = sorted(glob.glob(os.path.join(REP, "weak_scan_*.csv")))
    s_info = "无扫描档"
    s_ok = bool(scans)
    if scans:
        with open(scans[-1], encoding="utf-8-sig") as f:
            hdr = next(csv.reader(f))
        s_info = "%s（focus 列%s）" % (
            os.path.basename(scans[-1]),
            "有" if "focus" in hdr else "无——下周一 WeakScan 刷新后应有")
    pg4_ok, pg4_len = _page("/bt-weak", "弱于板块")
    ok_l4 = rc4 == "0" and rc5 == "0" and t_ok and s_ok and pg4_ok
    if not ok_l4:
        fails.append("weak(rc=%s/%s, 状态流%s, 扫描%s, 门户%s)"
                     % (rc4, rc5, "OK" if t_ok else "坏",
                        "OK" if s_ok else "无", "OK" if pg4_ok else "坏"))
    ap("## 4. weak 产线（WeakTrackCheck 15:30 / WeakScan 周一 15:35）%s"
       % ("✅" if ok_l4 else "❌"))
    ap("")
    ap("| 核验项 | 实际 |")
    ap("|---|---|")
    ap("| WeakTrackCheck Run / Result | %s / %s |" % (lr4, rc4))
    ap("| WeakScan Run / Result | %s / %s |" % (lr5, rc5))
    ap("| 在案状态流 | %s |" % ("；".join(t_info) if t_info else "无在案"))
    ap("| 最新扫描档 | %s |" % s_info)
    ap("| 门户案例库页 | %s（页面 %s 字节）|"
       % ("命中" if pg4_ok else "未命中", pg4_len))
    ap("| 当日产出 | 15:30 后自动运行，不在 08:30 核验范围 |")
    ap("")

    # ── 结论 ──
    ap("## 5. 结论")
    ap("")
    ap("- 判定汇总：%s（%s）"
       % ("全部通过" if not fails else "存在异常",
          "无 ❌" if not fails else "；".join(fails)))
    ap("- 警报池首批入池：%s" % ("是，%d 票" % len(alert_rows)
                                 if alert_rows else "否（n_alert=%s 属正常）"
                                 % n_alert))
    ap("- mls 首批数据：%s" % ("是，%d 条（覆盖 %s）" % (new_sig, mls_cov)
                               if new_sig else "否（当日无新信号）"))
    ap("- weak 产线：%s" % ("健康（在案 %d 案%s）"
                             % (len(tracks),
                                "，重点 %s" % s_info if scans else "")
                             if ok_l4 else "见上表 ❌ 项"))
    ap("- 异常处置：见模板 §4" if fails else "- 异常处置：无")
    ap("")
    with open(rpt, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("report written: %s" % os.path.basename(rpt))
    print("判定: %s" % ("PASS" if not fails else "FAIL " + "; ".join(fails)))

    # 首次核验完成（无论成败）→ 自删计划任务，异常靠推送触达
    subprocess.run(["schtasks", "/delete", "/tn", "PostHolidayCheck",
                    "/f"], capture_output=True)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
