# -*- coding: utf-8 -*-
"""weak_track_close.write_archive 落盘功能验证（K35 §9 · 可重复跑）。

T1 基本落盘：3 行 fixture（含触发行/ex250=None 行）→ 结构断言
T2 同日重结：连续第二次 → 自动加序号 _2
T3 档案定位：weak_cases 有 weak_review_999999_*.md → 头部指向它
T4 回退路径：删掉 review → 头部回退首例模板名
T5 预览无副作用：真实数据跑 close --type A 预览 → weak_cases 无新文件

隔离：T1-T4 monkeypatch REP 到 C:\\Temp 临时目录，不碰真实 reports；
     测试标的 code6=999999（不存在冲突）。全部 PASS 退出码 0。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_close as wc  # noqa: E402

PY = r"C:\Program Files\Python311\python.exe"
REAL_CASES = os.path.join(wc.REP, "weak_cases")
TMP = os.path.join(tempfile.gettempdir(), "weak_close_test")
CODE = "999999.SZ"
C6 = "999999"

ROWS = [
    {
        "ts": "2026-10-02T15:30:01",
        "px_d": "2026-10-02",
        "px": 16.69,
        "ex250": -18.2,
        "trend": ["平（均线缠绕）", "空", "平（均线缠绕）"],
        "trig": [],
    },
    {
        "ts": "2026-10-03T15:30:02",
        "px_d": "2026-10-03",
        "px": 16.05,
        "ex250": -19.0,
        "trend": ["空", "空", "平"],
        "trig": ["跌破关键位16.12"],
    },
    {
        "ts": "2026-10-04T15:30:03",
        "px_d": "2026-10-04",
        "px": 15.8,
        "code": CODE,
        "ex250": None,
        "trend": ["空", "空", "空"],
        "trig": [],
    },
]
MSG = None  # build_close 在 T1 前生成
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


def setup():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(os.path.join(TMP, "weak_cases"))
    wc.REP = TMP  # monkeypatch：write_archive 落盘/定位全部进临时目录


def t1_basic(arch):
    f = wc.close_fields(ROWS, wc.CTYPE["A"], "测试结案", arch)
    fp = wc.write_archive(f, MSG, ROWS, arch)
    txt = open(fp, encoding="utf-8").read()
    check("T1 文件生成", os.path.isfile(fp), fp)
    check("T1 标题", "# 结案报告 · %s（情景A兑现·修复）" % CODE in txt)
    check("T1 元信息·状态流", arch in txt)
    check("T1 元信息·结案时间", "结案时间：20" in txt)
    check("T1 增强·区间涨跌", "- 区间涨跌：16.69 → 15.8（-5.3%）" in txt)
    check("T1 增强·超额变化容错", "超额250：-18.2pp → —（—）" in txt)
    check("T1 增强·触发计数", "- 触发：共 1 次（时间线：" in txt)
    check("T1 推送文案块", "## 推送文案（终版）" in txt and "[WEAK TRACK CLOSE]" in txt)
    check("T1 表头", "| 时间 | 数据日·现价 | 超额250 | 趋势 个/板/沪 | 触发 |" in txt)
    check("T1 行数=3", "## 全量状态流（3 行）" in txt)
    check("T1 触发行⚠复评", "跌破关键位16.12 ⚠复评" in txt)
    check("T1 ex250=None 渲染—", "| 2026-10-04T15:30:03 | 10-04·15.8 | — |" in txt)
    check("T1 趋势压缩·缠", "平·缠" in txt)
    return fp


def t2_seq():
    f = wc.close_fields(ROWS, wc.CTYPE["A"], "测试结案", "a.jsonl")
    fp = wc.write_archive(f, MSG, ROWS, "a.jsonl")
    base = os.path.basename(fp)
    check("T2 同日重结加序号", base.endswith("_2.md"), base)


def t3_locate():
    rev = os.path.join(TMP, "weak_cases", "weak_review_%s_20260101.md" % C6)
    open(rev, "w", encoding="utf-8").write("# fake review\n")
    f = wc.close_fields(ROWS, wc.CTYPE["B"], "测试结案", "a.jsonl")
    fp = wc.write_archive(f, MSG, ROWS, "a.jsonl")
    txt = open(fp, encoding="utf-8").read()
    check("T3 档案定位到 review", "weak_review_%s_20260101.md" % C6 in txt)
    os.remove(rev)
    f = wc.close_fields(ROWS, wc.CTYPE["C"], "测试结案", "a.jsonl")
    fp = wc.write_archive(f, MSG, ROWS, "a.jsonl")
    txt = open(fp, encoding="utf-8").read()
    check("T4 回退首例模板名", "linkage_weak_review_template.md" in txt)


def t5_preview_noside():
    """预览无副作用：合成临时状态流跑 CLI 预览（--stream 指定）。

    历史版本硬编码 301216：该标的 2026-10-20 已结案、状态流被归档成
    .closed jsonl，预览必然「状态流不存在」返回 1——属测试夹具腐化，
    不是产品缺陷。改为自建临时状态流，且不往真实 reports 目录落任何文件。
    """
    before = set(os.listdir(REAL_CASES)) if os.path.isdir(REAL_CASES) else set()
    stream = os.path.join(TMP, "weak_track_999998.jsonl")
    with open(stream, "w", encoding="utf-8") as fh:
        for r in ROWS:
            fh.write(json.dumps(dict(r, code="999998.SZ"), ensure_ascii=False) + "\n")
    r = subprocess.run(
        [
            PY,
            "-X",
            "utf8",
            os.path.join(HERE, "weak_track_close.py"),
            "999998",
            "--type",
            "A",
            "--note",
            "测试预览",
            "--stream",
            stream,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    after = set(os.listdir(REAL_CASES)) if os.path.isdir(REAL_CASES) else set()
    check("T5 预览退出码0", r.returncode == 0, (r.stderr or "").strip()[-160:])
    check("T5 预览提示--send", "--send" in (r.stdout or ""))
    check("T5 预览零副作用", before == after, "新增：%s" % (after - before))


def main():
    global MSG
    setup()
    MSG = wc.build_close(
        ROWS, wc.CTYPE["A"], "测试结案", "weak_track_%s.closed_x.jsonl" % C6
    )
    arch = "weak_track_%s.closed_20261004.jsonl" % C6
    t1_basic(arch)
    t2_seq()
    t3_locate()
    t5_preview_noside()
    shutil.rmtree(TMP, ignore_errors=True)
    print(
        "\n%s（%d 项失败）"
        % ("ALL PASS" if not fails else "FAILED: %s" % fails, len(fails))
    )
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
