# -*- coding: utf-8 -*-
"""weak_track_audit（§13）审计判定与推送验证（K35-x · 可重复跑）。

T1 干净 A 类：五项全过（归档/档案实物齐+任务已停）
T2 字段缺失：缺结案时间行 → 字段缺失:ts
T3 归档缺失：声明的 closed jsonl 不在 reports/
T4 档案缺失：open 有但 weak_review_<c6>_<open>.md 缺
T5 B 类全对：新案行+任务 Ready（滚动重启）→ 不报任务未停
T6 任务未停：A 类任务 Ready → 报
T7 B 类新案缺：B 类无新案行+任务 Ready → 双 issue
T8 空目录 []；T9 真实 CLI 空态 rc=0
T14/T15 新案行三态：已建/未立（合法）/生成失败（仍报）

隔离：T1-T8 monkeypatch cases/reports 目录与任务表；全部 PASS 退出码 0。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_audit as wa  # noqa: E402
import cn_trading_days as ctd  # noqa: E402

PY = r"C:\Program Files\Python311\python.exe"
TMP = os.path.join(tempfile.gettempdir(), "weak_audit_test")
CASES = os.path.join(TMP, "cases")
REP = os.path.join(TMP, "rep")
fails = []


def check(name, cond, detail=""):
    print(
        "%s %s%s"
        % (
            "PASS" if cond else "FAIL",
            name,
            "｜" + detail if detail and not cond else "",
        )
    )
    if not cond:
        fails.append(name)


# 坏日期串（月 68 / 日 91 越界），码点构造避免手写字面量歧义
BAD = "".join(chr(c) for c in (50, 48, 50, 54, 45, 54, 56, 45, 57, 49))
ARROW = chr(0x1F504)  # 🔄 用码点构造，避免源码编码歧义
NC_SKIP = ARROW + " 新案未立"  # 声明不立新案（合法终态）
NC_FAIL = ARROW + " 新案生成失败"  # 真异常，不得被豁免吞掉


CHG_LINE = (
    "- 区间涨跌：10.0 → 10.5（+5.0%）｜"
    "超额250：-10pp → -5pp（+5pp·收敛）\n"
)  # 无变量不进 % 模板


def put_close(c6, close_ymd, open_ymd, title, extra="", tail=""):
    """fake 结案 md（extra 追加头部尾、tail 追加文件尾）。"""
    dash = "%s-%s-%s" % (close_ymd[:4], close_ymd[4:6], close_ymd[6:])
    code = c6 + (".SH" if c6.startswith("6") else ".SZ")
    txt = (
        "# 结案报告 · %s（%s）\n\n"
        "- 结案时间：%s 21:00\n"
        "- 状态流：weak_track_%s.closed_%s.jsonl（已归档）\n"
        "- 案例档案：weak_review_%s_%s.md\n"
        "%s"
        "- 触发：共 1 次（时间线：无）\n%s%s"
    ) % (code, title, dash, c6, close_ymd, c6, open_ymd, CHG_LINE, extra, tail)
    open(
        os.path.join(CASES, "weak_close_%s_%s.md" % (c6, close_ymd)),
        "w",
        encoding="utf-8",
    ).write(txt)


def put_arch(c6, ymd):
    open(
        os.path.join(REP, "weak_track_%s.closed_%s.jsonl" % (c6, ymd)),
        "w",
        encoding="utf-8",
    ).write('{"code":"x","ts":"2026-09-01"}\n')


def put_arch_bad(c6, ymd, rows):
    """归档流（自定义行）：用于坏日期用例。"""
    with open(
        os.path.join(REP, "weak_track_%s.closed_%s.jsonl" % (c6, ymd)),
        "w",
        encoding="utf-8",
    ) as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def put_review(c6, ymd):
    open(
        os.path.join(CASES, "weak_review_%s_%s.md" % (c6, ymd)), "w", encoding="utf-8"
    ).write("# 档案\n| 标的 | x |\n")


def main():
    for d in (TMP, CASES, REP):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)

    # T1 干净 A 类：实物齐 + 任务表空（已停）
    put_close("301216", "20260901", "20260801", "情景A兑现·修复")
    put_arch("301216", "20260901")
    put_review("301216", "20260801")
    # T2 字段缺失：缺结案时间行（ts 空）
    code = "000333.SZ"
    open(
        os.path.join(CASES, "weak_close_000333_20260905.md"), "w", encoding="utf-8"
    ).write(
        (
            "# 结案报告 · %s（情景A兑现·修复）\n\n"
            "- 状态流：weak_track_000333.closed_20260905.jsonl（已归档）\n"
            "- 案例档案：weak_review_000333_20260820.md\n"
            "%s"
            "- 触发：共 1 次（时间线：无）\n"
        )
        % (code, CHG_LINE)
    )
    put_arch("000333", "20260905")
    put_review("000333", "20260820")
    # T3 归档缺失：声明归档但不放实物
    put_close(
        "600519",
        "20260910",
        "20260820",
        "情景B立案·另起新案",
        tail="🔄 新案已建：weak_track_600519.jsonl（新案模板已生成：x）\n",
    )
    put_review("600519", "20260820")
    # T4 档案缺失：open 有但无档案实物（归档实物在）
    put_close("000006", "20260915", "20260901", "利空实证·转专项")
    put_arch("000006", "20260915")
    # T7 B 类新案缺：无新案行 + 任务 Ready
    put_close("000002", "20260920", "20260901", "情景B立案·另起新案")
    put_arch("000002", "20260920")
    put_review("000002", "20260901")
    # T10 日期坏点：归档流含 2 行坏 px_d（其余字段齐、任务已停）
    # 日期一律取真实交易日（2026-10-13/14/15 = 周二/三/四）——用周末日期会
    # 被新增的「非交易日」项抓住，测的就不是坏点逻辑了
    put_close("000009", "20260925", "20260825", "情景A兑现·修复")
    put_review("000009", "20260825")
    put_arch_bad(
        "000009",
        "20260925",
        [
            {"code": "000009.SZ", "ts": "2026-10-13T15:00:00", "px_d": "2026-10-13"},
            {"code": "000009.SZ", "ts": "2026-10-14T15:00:00", "px_d": BAD},
            {"code": "000009.SZ", "ts": "2026-10-15T15:00:00", "px_d": BAD},
        ],
    )

    # T12 日期倒退：px_d 10-14 → 10-13（回退），其余字段齐
    put_close("000010", "20260928", "20260828", "情景A兑现·修复")
    put_review("000010", "20260828")
    put_arch_bad(
        "000010",
        "20260928",
        [
            {"code": "000010.SZ", "ts": "2026-10-14T15:00:00", "px_d": "2026-10-14"},
            {"code": "000010.SZ", "ts": "2026-10-15T15:00:00", "px_d": "2026-10-13"},
        ],
    )

    # T14 B 类「声明不立新案」→ 合法终态，不报 B 类新案缺
    put_close(
        "000015",
        "20260930",
        "20260830",
        "情景B立案·另起新案",
        tail=NC_SKIP + "：结案结论不立新案（破位归档定性）\n",
    )
    put_arch("000015", "20260930")
    put_review("000015", "20260830")

    # T15 「生成失败」不等于「未立」→ 仍报 B 类新案缺（豁免不得泛化）
    put_close(
        "000016",
        "20260930",
        "20260830",
        "情景B立案·另起新案",
        tail=NC_FAIL + "（可手动跑命令块⑨）：无输出\n",
    )
    put_arch("000016", "20260930")
    put_review("000016", "20260830")

    TM = {
        "WeakTrack600519": "Ready",  # T5 B 类滚动重启 → 合法
        "WeakTrack000002": "Ready",
        "WeakTrack000016": "Ready",  # T15 生成失败 + 任务未停 → 双 issue
    }  # T7 B 类无新案 → 双 issue
    res = wa.audit_cases(CASES, REP, task_map=TM)
    check("T0 共 9 例", len(res) == 9, str(len(res)))
    g = lambda c6: next(c for c in res if c["c6"] == c6)
    check("T1 干净 A 类", g("301216")["issues"] == [], str(g("301216")["issues"]))
    check(
        "T2 字段缺失 ts",
        g("000333")["issues"] == ["字段缺失:ts"],
        str(g("000333")["issues"]),
    )
    check("T3 归档缺失", "归档缺失" in g("600519")["issues"])
    check(
        "T5 B 类滚动不报任务未停",
        "任务未停" not in g("600519")["issues"]
        and "B 类新案缺" not in g("600519")["issues"],
    )
    check("T4 档案缺失", g("000006")["issues"] == ["档案缺失"])
    check(
        "T7 B 类双 issue",
        g("000002")["issues"] == ["B 类新案缺", "任务未停"],
        str(g("000002")["issues"]),
    )
    check(
        "T14 声明未立不报新案缺",
        g("000015")["issues"] == [],
        str(g("000015")["issues"]),
    )
    check(
        "T15 生成失败仍报新案缺",
        g("000016")["issues"] == ["B 类新案缺", "任务未停"],
        str(g("000016")["issues"]),
    )
    i9 = g("000009")["issues"]
    check(
        "T10 日期坏点计 2 行",
        len(i9) == 1 and i9[0].startswith("日期坏点:2行"),
        str(i9),
    )
    check("T10 坏点样本带原值", BAD in (i9[0] if i9 else ""))
    check(
        "T11 无坏点的流不报",
        not any("日期坏点" in x for c in res for x in c["issues"] if c["c6"] != "000009"),
    )
    i10 = g("000010")["issues"]
    check(
        "T12 日期倒退计 1 处",
        len(i10) == 1 and i10[0].startswith("日期倒退:1处"),
        str(i10),
    )
    # T13 非交易日：px_d 是合法日期但当天休市（2026 国庆 10-01~10-07），
    # 且休市日不得参与倒退判定（09-30 是真·节前最后交易日，不是"倒退"）
    hol_dir = os.path.join(TMP, "hol")
    os.makedirs(hol_dir, exist_ok=True)
    with open(
        os.path.join(hol_dir, "weak_track_000011.closed_20261020.jsonl"),
        "w",
        encoding="utf-8",
    ) as fh:
        for ts, pd in (
            ("2026-09-29T15:00:00", "2026-09-29"),
            ("2026-09-30T15:00:00", "2026-09-30"),
            ("2026-10-02T15:00:00", "2026-10-02"),  # 休市日，行情源写进来了
            ("2026-10-08T15:00:00", "2026-10-08"),
        ):
            fh.write(json.dumps({"code": "000011.SZ", "ts": ts, "px_d": pd}) + "\n")
    ds = wa._bad_date_stats(hol_dir, "000011")
    check("T13 非交易日计 1 行", ds["hol"] == 1, str(ds))
    check("T13 休市样本带原因", "2026-10-02(休市)" in ds["hol_sample"], ds["hol_sample"])
    check("T13 休市日不判倒退", ds["back"] == 0, str(ds["back_sample"]))
    # T16 日历未覆盖：某年未登记休市安排时，工作日 px_d 只能按周末判断，
    # 节假日会漏判成交易日而不报警 → 必须单独报出来。
    # 造未来年份的数据行不通（wts.valid_day 拒未来年份，2027 会被判成坏点），
    # 且快照已覆盖 2026/2027，故快照与内置表「一起关掉」模拟真正的降级态
    # （对应实盘场景：快照目录缺失 + 该年还没手写进 HOLIDAYS）。
    with ctd.snapshot_off():
        saved_hol = dict(ctd.HOLIDAYS)
        ctd.HOLIDAYS.clear()
        try:
            gap_dir = os.path.join(TMP, "gap")
            os.makedirs(gap_dir, exist_ok=True)
            with open(
                os.path.join(gap_dir, "weak_track_000012.closed_20261020.jsonl"),
                "w",
                encoding="utf-8",
            ) as fh:
                for dt in (date(2026, 9, 29), date(2026, 9, 30)):
                    v = dt.isoformat()
                    fh.write(
                        json.dumps({"code": "000012.SZ", "ts": v + "T15:00:00", "px_d": v})
                        + "\n"
                    )
            dsg = wa._bad_date_stats(gap_dir, "000012")
            check("T16 未覆盖年份计 2 行", dsg["gap"] == 2, str(dsg))
            check("T16 样本带年份", "2026年未登记" in dsg["gap_sample"], dsg["gap_sample"])
            check(
                "T16 未覆盖时不误报坏点/非交易日",
                dsg["bad"] == 0 and dsg["hol"] == 0,
                str(dsg),
            )
        finally:
            ctd.HOLIDAYS.update(saved_hol)
    check(
        "T16 恢复后 2026 重判为已覆盖",
        wa._bad_date_stats(gap_dir, "000012")["gap"] == 0,
    )
    # T6 任务未停（A 类 + Ready）
    res2 = wa.audit_cases(CASES, REP, task_map={"WeakTrack301216": "Ready"})
    check(
        "T6 A 类任务未停",
        "任务未停" in next(c for c in res2 if c["c6"] == "301216")["issues"],
    )
    f = wa.audit_fields(res)
    check("T0 计数 9/7", (f["n"], f["n_bad"]) == (9, 7))
    check(
        "T0 明细含修复", "Disable 每日任务" in f["detail"] and "命令块⑨" in f["detail"]
    )
    msg = wa.build_audit(f)
    check("T0 文案头", "WEAK AUDIT" in msg and "异常 7 例" in msg)
    check(
        "T8 空目录",
        wa.audit_cases(
            os.path.join(TMP, "empty"), os.path.join(TMP, "empty"), task_map={}
        )
        == [],
    )

    # T9：真实 CLI 三种合法输出——空态 / 异常明细(AUDIT) / 全绿
    # （原断言只认前两种，真实数据全绿时漏判，2026-11-04 修正）
    r = subprocess.run(
        [PY, "-X", "utf8", os.path.join(HERE, "weak_track_audit.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    check("T9 CLI rc=0", r.returncode == 0, r.stdout[-200:] + r.stderr[-200:])
    check(
        "T9 空态提示",
        any(k in r.stdout for k in ("暂无已结案案例", "AUDIT", "审计全绿")),
        r.stdout[-200:],
    )

    shutil.rmtree(TMP, ignore_errors=True)
    print(
        "\n%s（%d 项失败）"
        % ("ALL PASS" if not fails else "FAILED: %s" % fails, len(fails))
    )
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
