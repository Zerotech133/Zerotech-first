# -*- coding: utf-8 -*-
"""弱于板块案例 · 新案模板生成器（K35 §9 B 类结案延伸 · 命令块⑨）。

从 weak_review_blank.md（九节空白骨架）复制并替换占位符，生成新案报告：
  reports/weak_cases/weak_review_<code6>_<YYYYMMDD>.md

占位符（逐个 replace，避开 §8 推送模板说明里的花括号）：
  {CODE} {NAME} {C6} {DATE} {P1} {P2} {P3} {TASK}

用法:
  python weak_track_newcase.py 600519.SH --name 贵州茅台
  python weak_track_newcase.py 600519.SH --name 贵州茅台 --pivots 1450,1520,1600
生成后打印「下一步三件事」：联动页首查 / 关键位 TBD 手填 / 注册每日任务
（Register-ScheduledTask 现成命令，任务名 WeakTrack<code6>）。

weak_track_close.py --type B --newcase-name "名称" --send 时自动调用本脚本。
"""
import argparse
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BLANK = os.path.join(HERE, "weak_review_blank.md")
OUT_DIR = os.path.join(os.path.dirname(HERE), "reports", "weak_cases")


from codes import normalize_code as _norm  # noqa: E402
import logging


def render(code, name, pivots):
    c6 = code.split(".")[0]
    p = (pivots or "TBD,TBD,TBD").split(",")
    p += ["TBD"] * (3 - len(p))
    rep = {
        "{CODE}": code, "{NAME}": name, "{C6}": c6,
        "{DATE}": time.strftime("%Y-%m-%d"),
        "{P1}": p[0].strip(), "{P2}": p[1].strip(), "{P3}": p[2].strip(),
        "{TASK}": "WeakTrack" + c6,
    }
    text = open(BLANK, encoding="utf-8").read()
    for k, v in rep.items():
        text = text.replace(k, v)
    return text, rep


def block_info(code):
    """联动板块自动解析（§0 板块行回填）。失败返回 None 不阻塞生成。"""
    try:
        import linkage
        blk = linkage.resolve_block(code)
        if blk:
            return "%s（%s，%s 匹配）" % (blk["name"], blk["code"],
                                         blk.get("seg", "industry"))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/weak_track_newcase.py", exc)
    return None


def next_steps(rep, has_tbd):
    _c6, task = rep["{C6}"], rep["{TASK}"]
    piv = ",".join(x for x in (rep["{P1}"], rep["{P2}"], rep["{P3}"]))
    s = ["\n下一步三件事：",
         "1) 联动页首查回填 §1：http://127.0.0.1:8091/bt-linkage?code=%s"
         % rep["{CODE}"]]
    if has_tbd:
        s.append("2) 关键位为 TBD：先跑 §7⑤ turn_lookup 拐点+看联动图，手填 §0 三关键位")
    else:
        s.append("2) §0 关键位已代入（%s），核对日线图确认" % piv)
    s.append("3) 注册每日跟踪任务（15:30，含电池启动判例）：")
    if has_tbd:
        s.append("   （先定关键位，再把下行 -Pivots TBD,TBD,TBD 换成实际值后执行）")
    s.append("   Register-ScheduledTask -TaskName '%s' -Action (New-ScheduledTaskAction"
             " -Execute 'powershell' -Argument \\\"-NoProfile -ExecutionPolicy Bypass"
             " -File C:\\aios\\quant_backtest\\start_weak_track.ps1 -Code %s"
             " -Pivots '%s' -Since %s\\\") -Trigger (New-ScheduledTaskTrigger -Daily"
             " -At 15:30) -Settings (New-ScheduledTaskSettingsSet"
             " -AllowStartIfOnBatteries)" % (task, rep["{CODE}"], piv, rep["{DATE}"]))
    return "\n".join(s)


def main():
    ap = argparse.ArgumentParser(description="弱于板块新案模板生成器（§9 命令块⑨）")
    ap.add_argument("code", help="股票代码（600519.SH / 301216 等）")
    ap.add_argument("--name", required=True, help="股票名称")
    ap.add_argument("--pivots", help="三关键位 左侧低点,站稳位,中枢高点（缺省 TBD）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的当日文件")
    a = ap.parse_args()
    code = _norm(a.code)
    if not os.path.isfile(BLANK):
        print("空白模板缺失：%s" % BLANK)
        return 1
    text, rep = render(code, a.name, a.pivots)
    bi = block_info(code)
    if bi:
        text = text.replace("待填（联动页信息行：板块名/码/解析方式）", bi)
        print("§0 板块已自动解析：%s" % bi)
    else:
        print("§0 板块解析失败，保留待填（联动页信息行核对）")
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "weak_review_%s_%s.md"
                       % (code.split(".")[0], time.strftime("%Y%m%d")))
    if os.path.exists(out) and not a.force:
        print("已存在（--force 覆盖）：%s" % out)
        return 1
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print("新案模板已生成：%s" % out)
    print(next_steps(rep, "TBD" in (rep["{P1}"], rep["{P2}"], rep["{P3}"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
