# -*- coding: utf-8 -*-
"""§9 结案报告与归档（K35-b §9 命令块⑧ · 案例生命周期收尾）。

一键结案三件事：
  1. 生成终版结案报告（全量状态流统计：跟踪天数/首末现价与超额/
     触发次数/结案结论）并推送（--send 时，飞书直发同 alert 口径）；
  2. 停用计划任务 WeakTrackCheck（Disable 不 Unregister，可重启）；
  3. 状态流归档：weak_track_<code6>.jsonl → .closed_YYYYMMDD.jsonl。

默认只打印预览（不动任务不改文件）；--send 才执行推送+停任务+归档。
文案格式外置 weak_track_close_tpl.txt（判例：推送格式一律外置模板，
坏模板回退内置）。结案类型：A=情景A兑现 / B=情景B立案 / C=利空实证。

用法:
  python weak_track_close.py 301216 --type A --note "超额转正站稳16.80"        # 预览
  python weak_track_close.py 301216 --type A --note "..." --send               # 结案
  python weak_track_close.py 301216 --type B --note "破16.12另立新案" --send
"""

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(os.path.dirname(HERE), "reports")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))
TPL = os.path.join(HERE, "weak_track_close_tpl.txt")

CTYPE = {"A": "情景A兑现·修复", "B": "情景B立案·另起新案", "C": "利空实证·转专项"}


def _load(fp):
    rows = []
    with open(fp, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line.lstrip("\ufeff")))
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_close.py", exc)
    return rows


def _f(v):
    return "%+.1fpp" % v if isinstance(v, (int, float)) else "—"


def _review_file(c6):
    """案例档案定位：最新 weak_review_<c6>_*.md（无则 None）。"""
    import glob

    revs = sorted(
        glob.glob(os.path.join(REP, "weak_cases", "weak_review_%s_*.md" % c6))
    )
    return revs[-1] if revs else None


def _case_name(c6):
    """从案例档案 §0 表格行提取标的名称（拿不到回退空串）。"""
    fp = _review_file(c6)
    if not fp:
        return ""
    try:
        for line in open(fp, encoding="utf-8"):
            if line.startswith("| 标的 |"):
                cell = line.split("|")[2].strip()  # "| 标的 | 万凯新材 301216.SZ |"
                return cell.split()[0] if cell else ""
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_close.py", exc)
    return ""


def close_fields(rows, ctype, note, arch, ncase=""):
    """全量状态流 → 结案统计字段 dict（文案渲染与飞书卡片共用）。

    K35-t 增强：name/chg/exd/rev/trig_hist/ncase
    （名称+档案定位/区间涨跌幅/超额变化方向/触发时间线/B 类滚动新案行）。
    """
    r0, r1 = rows[0], rows[-1]
    trigs = [r for r in rows if r.get("trig")]
    # 跟踪日数 = 不同行情日个数。缺失值（空/"?"）不是交易日，不计入
    # （旧写法把 "?" 当成一个独立日期，会虚增 1 天）
    _dset = {str(r.get("px_d") or "")[:10] for r in rows}
    nd = len(_dset - {"", "?"})
    c6 = str(r1.get("code", "?")).split(".")[0]
    chg = ""  # 区间涨跌幅
    try:
        chg = "%+.1f%%" % ((float(r1["px"]) / float(r0["px"]) - 1) * 100)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_close.py", exc)
    exd = ""  # 超额250变化+方向
    try:
        e0, e1 = float(r0["ex250"]), float(r1["ex250"])
        tag = (
            "转正"
            if e1 >= 0 > e0
            else ("持平" if e1 == e0 else ("收敛" if abs(e1) < abs(e0) else "走扩"))
        )
        exd = "%+.1fpp·%s" % (e1 - e0, tag)
    except (KeyError, TypeError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_close.py", exc)
    revf = _review_file(c6)
    hist = [
        "%s %s" % (str(t.get("ts", "?"))[5:10], "；".join(t["trig"])) for t in trigs
    ]
    return {
        "code": r1.get("code", "?"),
        "ctype": ctype,
        "note": note,
        "first": str(r0.get("ts", "?"))[:16],
        "last": str(r1.get("ts", "?"))[:16],
        "n": "%d 日/%d 次" % (nd, len(rows)),
        "n_days": nd,
        "n_runs": len(rows),
        "px0": r0.get("px"),
        "px1": r1.get("px"),
        "ex0": _f(r0.get("ex250")),
        "ex1": _f(r1.get("ex250")),
        "ntrig": len(trigs),
        "trig_last": "；".join(trigs[-1]["trig"]) if trigs else "无",
        "trig_hist": "｜".join(hist[-3:]) if hist else "无",
        "name": _case_name(c6),
        "chg": chg or "—",
        "exd": exd or "—",
        "rev": os.path.basename(revf) if revf else "—",
        "ncase": ncase,
        "arch": arch,
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    }


def build_close(rows, ctype, note, arch, ncase=""):
    """全量状态流 → 结案报告文案（rows 非空时调用）。"""
    f = close_fields(rows, ctype, note, arch, ncase=ncase)
    for p in (TPL,):
        if os.path.isfile(p):
            try:
                text = "".join(
                    l
                    for l in open(p, encoding="utf-8").read().splitlines(True)
                    if not l.lstrip().startswith("#")
                )
                text = text.format(**f).rstrip("\n")
                if ncase:  # B 类滚动新案行（空则不占行）
                    text += "\n" + ncase
                return text
            except (KeyError, IndexError, ValueError) as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_close.py", exc)
    return (
        "🏁 [WEAK TRACK CLOSE] {code} {name} 结案（{ctype}）\n"
        "立案 {first} → 结案 {last}（跟踪 {n}）\n"
        "现价 {px0} → {px1}（{chg}）｜超额250 {ex0} → {ex1}（{exd}）\n"
        "复评触发 {ntrig} 次｜时间线：{trig_hist}\n"
        "结论：{note}\n"
        "档案 {rev}｜任务已停用（可 Enable 重启）｜状态流已归档 {arch}"
    ).format(**f) + (("\n" + ncase) if ncase else "")


from codes import normalize_code as _norm  # noqa: E402
import logging


def _close_ts(last):
    """结案时刻取「状态流末条时间」，不取报告落盘时间（K35-ac 口径修正）。

    历史版本头部写的是 time.strftime() 即报告生成时刻，比真实结案晚
    1 个自然日（本机凌晨跑批），导致 §14 案例库台账的结案日偏后、
    跟踪时长多算 1 天。末条 ts 缺失或非时间格式时回退当前时刻。
    """
    s = str(last or "").strip().replace("T", " ")
    try:
        time.strptime(s, "%Y-%m-%d %H:%M")
        return s
    except ValueError:
        return time.strftime("%Y-%m-%d %H:%M")


def write_archive(f, msg, rows, arch):
    """结案报告落盘 md（推送终版+全量状态流表）→ reports/weak_cases/。

    K35-t：签名改为接收 close_fields 的 f dict，头部元信息补
    标的名称/区间涨跌/超额变化；档案定位复用 _review_file。
    同日重复结案自动加序号 _2/_3。
    """
    out_dir = os.path.join(REP, "weak_cases")
    os.makedirs(out_dir, exist_ok=True)
    code = str(f.get("code", "?"))
    c6 = code.split(".")[0]
    ymd = time.strftime("%Y%m%d")
    fp = os.path.join(out_dir, "weak_close_%s_%s.md" % (c6, ymd))
    n = 1
    while os.path.exists(fp):
        n += 1
        fp = os.path.join(out_dir, "weak_close_%s_%s_%d.md" % (c6, ymd, n))
    revf = _review_file(c6)
    rev = (
        os.path.basename(revf)
        if revf
        else "quant_backtest/linkage_weak_review_template.md（首例档案）"
    )
    _t = lambda s: str(s).replace("（均线缠绕）", "·缠")[:8]  # noqa: E731
    L = [
        "# 结案报告 · %s（%s）"
        % (" ".join(x for x in [code, f.get("name") or ""] if x), f["ctype"]),
        "",
        "- 结案时间：%s" % _close_ts(f.get("last")),
        "- 报告生成：%s" % time.strftime("%Y-%m-%d %H:%M"),
        "- 状态流：%s（已归档）" % arch,
        "- 案例档案：%s" % rev,
        "- 区间涨跌：%s → %s（%s）｜超额250：%s → %s（%s）"
        % (
            f.get("px0"),
            f.get("px1"),
            f.get("chg"),
            f.get("ex0"),
            f.get("ex1"),
            f.get("exd"),
        ),
        "- 触发：共 %s 次（时间线：%s）" % (f.get("ntrig"), f.get("trig_hist")),
        "",
        "## 推送文案（终版）",
        "",
        "```",
        msg,
        "```",
        "",
        "## 全量状态流（%d 行）" % len(rows),
        "",
        "| 时间 | 数据日·现价 | 超额250 | 趋势 个/板/沪 | 触发 |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        ex = r.get("ex250")
        exs = "%+.1fpp" % ex if ex is not None else "—"
        trig = r.get("trig") or []
        L.append(
            "| %s | %s | %s | %s | %s |"
            % (
                str(r.get("ts", "?"))[:19],
                "%s·%s" % (str(r.get("px_d", "?"))[5:], r.get("px")),
                exs,
                "/".join(_t(x) for x in (r.get("trend") or [])),
                ("；".join(trig) + " ⚠复评") if trig else "—",
            )
        )
    with open(fp, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return fp


def main():
    ap = argparse.ArgumentParser(description="§9 结案报告与归档")
    ap.add_argument("code", nargs="?", default="301216")
    ap.add_argument(
        "--type",
        required=True,
        choices=["A", "B", "C"],
        help="结案类型：A=情景A兑现 B=情景B立案 C=利空实证",
    )
    ap.add_argument("--note", required=True, help="结案结论（一句话）")
    ap.add_argument(
        "--send", action="store_true", help="执行结案：推送+停任务+归档（默认仅预览）"
    )
    ap.add_argument("--task", default="WeakTrackCheck", help="要停用的任务名")
    ap.add_argument(
        "--newcase-name",
        help="B 类结案时自动生成新案模板：同标的情景 B 迁移填本名；"
        "换标的配 --newcase-code（调 weak_track_newcase.py，"
        "关键位 TBD 待首查手填）",
    )
    ap.add_argument("--newcase-code", help="B 类新案的标的代码（缺省=本案同标的）")
    ap.add_argument("--text", action="store_true", help="文本版推送（默认飞书卡片）")
    ap.add_argument(
        "--stream",
        help="状态流文件路径（默认 reports/weak_track_<code>.jsonl）；"
        "供测试与历史数据回溯指定非默认位置",
    )
    a = ap.parse_args()
    c6 = a.code.strip().upper().split(".")[0]
    fp = a.stream or os.path.join(REP, "weak_track_%s.jsonl" % c6)
    if not os.path.exists(fp):
        print("状态流不存在：%s（未跟踪过的案例无案可结）" % fp)
        return 1
    rows = _load(fp)
    if not rows:
        print("状态流为空")
        return 1
    ymd = time.strftime("%Y%m%d")
    arch = os.path.basename(fp).replace(".jsonl", ".closed_%s.jsonl" % ymd)
    # 0. B 类滚动新案先生成（仅写模板文件，无副作用），结果回填推送；
    #    失败不阻塞结案（K35-t：闭环信息进推送而非只打控制台）
    ncase_line = ""
    if a.send and a.type == "B" and a.newcase_name:
        try:
            r = subprocess.run(
                [
                    sys.executable,
                    os.path.join(HERE, "weak_track_newcase.py"),
                    _norm(a.newcase_code) if a.newcase_code else c6,
                    "--name",
                    a.newcase_name,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            if r.returncode == 0:
                for ln in (r.stdout or "").splitlines():
                    if "新案模板已生成" in ln:
                        ncase_line = "🔄 新案已建：%s" % ln.split("：", 1)[-1].strip()
                        break
            if not ncase_line:
                head = (r.stdout or "").strip().splitlines()
                ncase_line = "🔄 新案生成失败（可手动跑命令块⑨）：%s" % (
                    head[0][:60] if head else "无输出"
                )
        except Exception as e:
            ncase_line = "🔄 新案生成异常（可手动跑命令块⑨）：%s" % str(e)[:60]
    msg = build_close(rows, CTYPE[a.type], a.note, arch, ncase=ncase_line)
    print(msg)
    if not a.send:
        print("\n（预览模式：加 --send 执行推送+停任务+归档）")
        if not a.text:
            import weak_track_card as wcard

            print("\n（卡片版预览，--text 可回退文本）")
            print(
                json.dumps(
                    wcard.close_card(close_fields(rows, CTYPE[a.type], a.note, arch)),
                    ensure_ascii=False,
                    indent=1,
                )
            )
        if a.type == "B":
            hint = (
                '--newcase-name "新标的名称"'
                if not a.newcase_name
                else "--newcase-name %s" % a.newcase_name
            )
            print("（B 类结案：%s 可同时自动生成新案模板，见 §9）" % hint)
        return 0

    # 1. 推送（默认飞书卡片；--text 回退文本；落盘 md 始终用文本版）
    if not a.text:
        import weak_track_card as wcard

        print(
            "\n推送(卡片)："
            + wcard.send_card(
                wcard.close_card(
                    close_fields(rows, CTYPE[a.type], a.note, arch, ncase=ncase_line)
                )
            )
        )
    else:
        tmp = os.path.join(NEWS_DIR, "outbox", "pending_alert_weak_track.txt")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(msg)
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        print("\n推送：" + ((r.stdout or "").strip()[:60] or "sent"))
    # 2. 停任务（Disable 不 Unregister，可 Enable 重启）
    r = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Disable-ScheduledTask -TaskName '%s' | Out-Null; $?" % a.task,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    ok = r.returncode == 0
    print("停任务 %s：%s" % (a.task, "已停用" if ok else "失败（可手动 Disable）"))
    # 3. 归档状态流（改名，防重开混淆）
    try:
        os.rename(fp, os.path.join(REP, arch))
        print("状态流已归档：%s" % arch)
    except Exception as e:
        print("归档失败（可手动改名）：%s" % str(e)[:80])
    # 4. 结案报告落盘存档（md，与 newcase 模板同目录；失败不影响结案）
    try:
        md = write_archive(
            close_fields(rows, CTYPE[a.type], a.note, arch, ncase=ncase_line),
            msg,
            rows,
            arch,
        )
        print("结案报告已落盘：reports\\weak_cases\\%s" % os.path.basename(md))
    except Exception as e:
        print("结案报告落盘失败（不影响结案）：%s" % str(e)[:80])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
