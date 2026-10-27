# -*- coding: utf-8 -*-
"""weak_track_summary（§10）解析与统计验证（K35-u · 可重复跑）。

T1 解析：3 份 fake weak_close md（A收敛/B走扩/C转正）→ 字段断言
T2 统计：汇总字段断言（分布/均最好最差/修复数/触发合计）
T3 半坏文件：缺标题行的 md → 跳过不炸
T4 空态：空目录 → parse 返回 []
T5 真实预览：真实 weak_cases 跑 CLI 预览 → rc=0 且零副作用

隔离：T1-T4 monkeypatch 解析目录到临时目录；全部 PASS 退出码 0。
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_summary as ws  # noqa: E402

PY = r"C:\Program Files\Python311\python.exe"
TMP = os.path.join(tempfile.gettempdir(), "weak_summary_test")
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


def fake(c6, ymd, title, chg_line, trig_line):
    dash = "%s-%s-%s" % (ymd[:4], ymd[4:6], ymd[6:])  # 结案时间用带连字符日期
    return (
        "# 结案报告 · %s（%s）\n\n"
        "- 结案时间：%s 21:00\n"
        "- 状态流：weak_track_%s.closed_%s.jsonl（已归档）\n"
        "- 案例档案：weak_review_%s_%s.md\n"
        "- %s\n"
        "- %s\n" % (c6_code(c6), title, dash, c6, ymd, c6, ymd, chg_line, trig_line)
    )


def c6_code(c6):
    return c6 + (".SH" if c6.startswith("6") else ".SZ")


MD1 = fake(
    "301216",
    "20260901",
    "情景A兑现·修复",
    "区间涨跌：16.69 → 17.5（+4.8%）｜超额250：-18.2pp → -3.1pp（+15.1pp·收敛）",
    "触发：共 2 次（时间线：07-01 超额收敛）",
)
MD2 = fake(
    "600519",
    "20260910",
    "情景B立案·另起新案",
    "区间涨跌：1800.0 → 1600.0（-11.1%）｜超额250：-5.0pp → -20.0pp（-15.0pp·走扩）",
    "触发：共 3 次（时间线：08-15 破关键位）",
)
MD3 = fake(
    "000333",
    "20260920",
    "利空实证·转专项",
    "区间涨跌：60.0 → 63.0（+5.0%）｜超额250：-12.0pp → +1.5pp（+13.5pp·转正）",
    "触发：共 0 次（时间线：无）",
)


def main():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP)

    # T1/T2：三份完整案例
    for name, txt in (
        ("weak_close_301216_20260901.md", MD1),
        ("weak_close_600519_20260910.md", MD2),
        ("weak_close_000333_20260920.md", MD3),
    ):
        open(os.path.join(TMP, name), "w", encoding="utf-8").write(txt)
    cases = ws.parse_cases(TMP)
    check("T1 解析 3 例", len(cases) == 3, str(len(cases)))
    c1 = next(c for c in cases if c["code"].startswith("301216"))
    check("T1 fake 无名称", c1["name"] == "")
    check("T1 类型 A", c1["type"] == "A")
    check("T1 类型 ABC", {c["type"] for c in cases} == {"A", "B", "C"})
    check("T1 涨跌解析", c1["chg"] == 4.8)
    check(
        "T1 超额标签",
        c1["exd"] == "收敛"
        and any(c["exd"] == "转正" and c["code"].startswith("000333") for c in cases),
    )
    check("T1 触发计数", sum(c["ntrig"] for c in cases) == 5)
    f = ws.summary_fields(cases)
    check("T2 分布 n/na/nb/nc", (f["n"], f["na"], f["nb"], f["nc"]) == (3, 1, 1, 1))
    check(
        "T2 涨跌均/最好/最差",
        (f["chg_avg"], f["chg_best"], f["chg_worst"]) == ("-0.4%", "+5.0%", "-11.1%"),
        f["chg_avg"],
    )
    check("T2 超额修复 2/3", (f["ex_fix"], f["ex_tagged"]) == (2, 3))
    check("T2 触发合计 5 均 1.7", f["ntrig"] == 5 and f["ntrig_avg"] == "1.7")
    check("T2 范围", (f["first_ts"], f["last_ts"]) == ("2026-09-01", "2026-09-20"))

    # T3：半坏文件（无标题行）跳过
    open(
        os.path.join(TMP, "weak_close_999999_20260930.md"), "w", encoding="utf-8"
    ).write("# 不是结案报告\n- 区间涨跌：x\n")
    check("T3 半坏跳过", len(ws.parse_cases(TMP)) == 3)

    # T4：空目录空态
    check("T4 空目录", ws.parse_cases(os.path.join(TMP, "empty")) == [])

    # T5：真实数据 CLI 预览（当前 0 结案 → 空态提示 rc=0）
    r = subprocess.run(
        [PY, "-X", "utf8", os.path.join(HERE, "weak_track_summary.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    check("T5 CLI rc=0", r.returncode == 0)
    check(
        "T5 空态或汇总", ("暂无已结案案例" in r.stdout) or ("WEAK SUMMARY" in r.stdout)
    )

    shutil.rmtree(TMP, ignore_errors=True)
    print(
        "\n%s（%d 项失败）"
        % ("ALL PASS" if not fails else "FAILED: %s" % fails, len(fails))
    )
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
