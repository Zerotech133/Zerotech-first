# -*- coding: utf-8 -*-
"""weak_track_learn（§11）解析与统计验证（K35-v · 可重复跑）。

T1 解析：fake weak_close md（立案日≠结案日）→ open 字段+跟踪天数
T2 统计（n=3<5 门槛）：命中率/时长桶/触发密度 + 积累中简版 lessons
T3 门槛达标（n=6≥5）：规律版 lessons（含高频触发条目，条条带样本量）
T4 容错：非标档案行 open 缺席→时长不计；半坏文件跳过；空目录
T5 真实 CLI 预览：rc=0 且零副作用

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
import weak_track_learn as wl  # noqa: E402

PY = r"C:\Program Files\Python311\python.exe"
TMP = os.path.join(tempfile.gettempdir(), "weak_learn_test")
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


def fake(c6, open_ymd, close_ymd, title, chg_line, trig_line):
    dash = "%s-%s-%s" % (close_ymd[:4], close_ymd[4:6], close_ymd[6:])
    code = c6 + (".SH" if c6.startswith("6") else ".SZ")
    return (
        "# 结案报告 · %s（%s）\n\n"
        "- 结案时间：%s 21:00\n"
        "- 状态流：weak_track_%s.closed_%s.jsonl（已归档）\n"
        "- 案例档案：weak_review_%s_%s.md\n"
        "- %s\n"
        "- %s\n" % (code, title, dash, c6, close_ymd, c6, open_ymd, chg_line, trig_line)
    )


CHG_A = "区间涨跌：16.69 → 17.5（+4.8%）｜超额250：-18.2pp → -3.1pp（+15.1pp·收敛）"
CHG_B = "区间涨跌：1800.0 → 1600.0（-11.1%）｜超额250：-5.0pp → -20.0pp（-15.0pp·走扩）"
CHG_C = "区间涨跌：60.0 → 63.0（+5.0%）｜超额250：-12.0pp → +1.5pp（+13.5pp·转正）"

# 基础三案（时长 31/21/19 天；hi≥2 = 案1 案2）
BASE = [
    (
        "weak_close_301216_20260901.md",
        fake(
            "301216",
            "20260801",
            "20260901",
            "情景A兑现·修复",
            CHG_A,
            "触发：共 2 次（时间线：07-01 超额收敛）",
        ),
    ),
    (
        "weak_close_600519_20260910.md",
        fake(
            "600519",
            "20260820",
            "20260910",
            "情景B立案·另起新案",
            CHG_B,
            "触发：共 3 次（时间线：08-15 破关键位）",
        ),
    ),
    (
        "weak_close_000333_20260920.md",
        fake(
            "000333",
            "20260901",
            "20260920",
            "利空实证·转专项",
            CHG_C,
            "触发：共 0 次（时间线：无）",
        ),
    ),
]
# 补三案凑门槛（案4 A·2 次；案5 B·4 次；案6 B·3 次 → hi=5 例、hi_bc=3）
MORE = [
    (
        "weak_close_000001_20260925.md",
        fake(
            "000001",
            "20260910",
            "20260925",
            "情景A兑现·修复",
            CHG_A,
            "触发：共 2 次（时间线：09-20 新池信号）",
        ),
    ),
    (
        "weak_close_000002_20260928.md",
        fake(
            "000002",
            "20260901",
            "20260928",
            "情景B立案·另起新案",
            CHG_B,
            "触发：共 4 次（时间线：09-28 破关键位）",
        ),
    ),
    (
        "weak_close_000006_20260930.md",
        fake(
            "000006",
            "20260815",
            "20260930",
            "情景B立案·另起新案",
            CHG_B,
            "触发：共 3 次（时间线：09-30 超额收敛）",
        ),
    ),
]


def put(files):
    for name, txt in files:
        open(os.path.join(TMP, name), "w", encoding="utf-8").write(txt)


def main():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP)

    # T1/T2：基础三案（n=3 < 5 门槛）
    put(BASE)
    cases = ws.parse_cases(TMP)
    check("T1 解析 3 例", len(cases) == 3, str(len(cases)))
    c1 = next(c for c in cases if c["code"].startswith("301216"))
    check("T1 立案日提取", c1["open"] == "20260801", c1["open"])
    check("T1 跟踪天数 31", wl._days(c1) == 31, str(wl._days(c1)))
    f = wl.learn_fields(cases)
    check("T2 命中率 33%", f["a_rate"] == "33%", f["a_rate"])
    check(
        "T2 时长均/端点",
        (f["dur_avg"], f["dur_min"], f["dur_max"]) == ("24", "19", "31"),
        f["dur_avg"],
    )
    check("T2 时长桶 0/2/1", (f["d1"], f["d2"], f["d3"]) == ("0", "2", "1"))
    check(
        "T2 触发密度 A2.0/BC1.5",
        (f["trig_a"], f["trig_bc"]) == ("2.0", "1.5"),
        f["trig_a"],
    )
    check(
        "T2 超额修复 2/3·A 1/1",
        (f["ex_fix"], f["ex_tagged"], f["fix_a"], f["tag_a"]) == (2, 3, 1, 1),
    )
    check("T2 门槛积累中", f["gate"] == "积累中")
    check(
        "T2 简版 lessons 单条",
        (
            f["lessons"].count("\n") == 0
            and "样本积累中" in f["lessons"]
            and "暂不下规律结论" in f["lessons"]
        ),
        f["lessons"][:40],
    )

    # T3：补三案凑 n=6 ≥ 5 → 规律版
    put(MORE)
    cases6 = ws.parse_cases(TMP)
    f6 = wl.learn_fields(cases6)
    check("T3 门槛已达标", (f6["n"], f6["gate"]) == (6, "已达标"))
    lines6 = f6["lessons"].split("\n")
    check("T3 lessons 5 条", len(lines6) == 5, str(len(lines6)))
    check("T3 条条带样本量", all("（n=" in x or "可用样本" in x for x in lines6))
    check(
        "T3 高频触发条目", any("高频触发" in x and "5 例中 3 例" in x for x in lines6)
    )
    check("T3 明细最多 5 行", len(f6["detail"].splitlines()) == 5)
    msg = wl.build_learn(f6)
    check("T3 文案含经验块", "经验条目" in msg and "剧本兑现" in msg)

    # T4：非标档案行（首例回退）→ open 缺席不计时长；半坏跳过；空目录
    open(
        os.path.join(TMP, "weak_close_999999_20260929.md"), "w", encoding="utf-8"
    ).write(
        "# 结案报告 · 999999.SZ（情景A兑现·修复）\n\n"
        "- 结案时间：2026-09-29 21:00\n"
        "- 案例档案：quant_backtest/linkage_weak_review_template.md（首例档案）\n"
        "- 触发：共 1 次（时间线：无）\n"
    )
    open(
        os.path.join(TMP, "weak_close_888888_20260930.md"), "w", encoding="utf-8"
    ).write("# 不是结案报告\n- 触发：共 1 次\n")
    cases7 = ws.parse_cases(TMP)
    check("T4 半坏跳过共 7 例", len(cases7) == 7, str(len(cases7)))
    c7 = next(c for c in cases7 if c["code"].startswith("999999"))
    check("T4 非标档案 open 空", c7["open"] == "" and wl._days(c7) is None)
    f7 = wl.learn_fields(cases7)
    check("T4 时长可用 6", f7["ndays"] == 6, str(f7["ndays"]))
    check("T4 空目录", ws.parse_cases(os.path.join(TMP, "empty")) == [])

    # T5：真实数据 CLI 预览（当前 0 结案 → 空态提示 rc=0）
    r = subprocess.run(
        [PY, "-X", "utf8", os.path.join(HERE, "weak_track_learn.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    check("T5 CLI rc=0", r.returncode == 0)
    check(
        "T5 空态或学习版", ("暂无已结案案例" in r.stdout) or ("WEAK LEARN" in r.stdout)
    )

    shutil.rmtree(TMP, ignore_errors=True)
    print(
        "\n%s（%d 项失败）"
        % ("ALL PASS" if not fails else "FAILED: %s" % fails, len(fails))
    )
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
