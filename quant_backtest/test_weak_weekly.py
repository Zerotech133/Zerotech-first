# -*- coding: utf-8 -*-
"""weak_track_weekly（§15）聚合与推送验证（K35-z + K35-ab 增强 · 可重复跑）。

T1 全绿聚合：green/verdict/extra 空/trend 透传
T2 双异常聚合：extra 含两节明细块
T3 文案：全绿版/异常版（外置模板，含动态行/trend/空行压缩）
T4 坏模板回退内置
T5 卡片条件渲染：全绿含动态块（4）/双异常（8）
T6 真实 CLI 预览（在案 1 案+0 结案 → 全绿版含动态行 rc=0）
T7 weekly_activity：fake reports（新立案/结案/打点/触发/明细）
T8 trend：首期无环比/state 对比/坏 state 容错
T9 recent_lines：色标一行式（§14/§15 共用）

全部 PASS 退出码 0。
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import weak_track_weekly as wk        # noqa: E402
import weak_track_card as wcard      # noqa: E402

PY = r"C:\Program Files\Python311\python.exe"
fails = []


def check(name, cond, detail=""):
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       "｜" + detail if detail and not cond else ""))
    if not cond:
        fails.append(name)


def w(n, n_bad, detail=""):
    return {"n": n, "n_bad": n_bad, "ts": "x", "detail": detail}


def act(**kw):
    d = {"new_open": 0, "closed_week": 0, "rows_week": 0,
         "trig_week": 0, "act_detail": ""}
    d.update(kw)
    return d


def main():
    # T1 全绿（新签名：act+trend）
    g = wk.weekly_fields(w(2, 0), w(3, 0),
                         act(new_open=1, closed_week=1, rows_week=9,
                             trig_week=2, act_detail="- 301216 万凯：打点 9 行"),
                         "结案 +1｜巡检异常 0｜审计异常 0")
    check("T1 green", g["green"] is True and g["extra"] == "")
    check("T1 verdict", "本周全绿" in g["verdict"])
    check("T1 trend 透传", g["trend"] == "结案 +1｜巡检异常 0｜审计异常 0")
    check("T1 动态字段", g["new_open"] == 1 and g["trig_week"] == 2)
    # T2 双异常
    b = wk.weekly_fields(w(2, 1, "- 600000：超龄\n  → 结案"),
                         w(3, 2, "- 000001.SZ（A）：字段缺失:ts"), act(),
                         "首期无环比")
    check("T2 not green", b["green"] is False)
    check("T2 extra 两块", "巡检明细（§12）" in b["extra"]
          and "审计明细（§13）" in b["extra"])
    # T3 文案（外置模板真渲染 + recent 注入 + 空行压缩）
    g = dict(g, recent="", wdetail="", adetail="")
    mg, mb = wk.build_weekly(g), wk.build_weekly(dict(b, recent=""))
    check("T3 全绿文案", "WEAK WEEKLY" in mg and "在案 2 案" in mg
          and "本周全绿" in mg and "较上周：结案 +1" in mg
          and "本周动态：新立案 1｜结案 1｜跟踪打点 9 行｜触发 2 次" in mg)
    check("T3 空行压缩", "\n\n\n" not in mg)
    check("T3 异常文案", "在案 2 案（巡检异常 1）" in mb
          and "字段缺失" in mb)
    # T4 坏模板回退
    orig, wk.TPL = wk.TPL, os.path.join(HERE, "_bad_tpl.txt")
    open(wk.TPL, "w", encoding="utf-8").write("坏 {bogus}\n")
    check("T4 回退内置", "WEAK WEEKLY" in wk.build_weekly(dict(b, recent="")))
    os.remove(wk.TPL)
    wk.TPL = orig
    # T5 卡片条件渲染：全绿=fields+hr+动态块+act明细+note(5)；
    # 异常=+recent(hr+块)+巡检(hr+块)+审计(hr+块)→10；空块不显
    cg, cb = wcard.weekly_card(g), wcard.weekly_card(
        dict(b, recent="- 🟢 301216 万凯：+5.0%", act_detail=""))
    js_g = json.dumps(cg, ensure_ascii=False)
    js_b = json.dumps(cb, ensure_ascii=False)
    check("T5 全绿卡", len(cg["elements"]) == 5
          and cg["header"]["template"] == "indigo"
          and "巡检明细" not in js_g and "最近结案" not in js_g)
    check("T5 异常卡", len(cb["elements"]) == 10
          and "巡检明细" in js_b and "审计明细" in js_b
          and "最近结案" in js_b)
    check("T5 空动态明细不显", "打点 9 行" not in js_b
          and "本周动态" in js_b)

    # T7 weekly_activity（fake reports + fake close cases）
    tmp = tempfile.mkdtemp()
    try:
        wk_mon = datetime.date.today() - datetime.timedelta(
            days=datetime.date.today().weekday())
        last_wk = (wk_mon - datetime.timedelta(days=7)
                   ).strftime("%Y-%m-%d")
        wk_s = wk_mon.strftime("%Y-%m-%d")
        # 案A：上周立案、本周 3 行（1 行触发）；案B：本周立案 2 行；
        # 案C：上周立案本周无行（不活跃）
        def jl(rows):
            return "".join(json.dumps(r) + "\n" for r in rows)
        open(os.path.join(tmp, "weak_track_600000.jsonl"), "w",
             encoding="utf-8").write(jl([
                 {"ts": last_wk + "T15:30", "code": "600000.SH",
                  "trig": []},
                 {"ts": wk_s + "T15:30", "code": "600000.SH", "trig": []},
                 {"ts": wk_s + "T16:00", "code": "600000.SH",
                  "trig": ["价格触发"]},
                 {"ts": wk_s + "T17:00", "code": "600000.SH", "trig": []}]))
        open(os.path.join(tmp, "weak_track_600001.jsonl"), "w",
             encoding="utf-8").write(jl([
                 {"ts": wk_s + "T15:30", "code": "600001.SH", "trig": []},
                 {"ts": wk_s + "T16:00", "code": "600001.SH", "trig": []}]))
        open(os.path.join(tmp, "weak_track_600002.jsonl"), "w",
             encoding="utf-8").write(jl([
                 {"ts": last_wk + "T15:30", "code": "600002.SH",
                  "trig": []}]))
        # fake close cases：1 例本周结案、1 例上周
        cases = [{"code": "000333.SZ", "ts": wk_s, "type": "A"},
                 {"code": "000006.SZ", "ts": last_wk, "type": "C"}]
        orig_cn = wk.wc._case_name
        wk.wc._case_name = lambda c6: {"600000": "A名", "600001": "B名",
                                       "600002": "C名"}.get(c6, "")
        a = wk.weekly_activity(cases, rep=tmp)
        wk.wc._case_name = orig_cn
        check("T7 新立案", a["new_open"] == 1)
        check("T7 打点/触发", a["rows_week"] == 5 and a["trig_week"] == 1)
        check("T7 本周结案", a["closed_week"] == 1)
        check("T7 明细", "600000 A名：打点 3 行·触发 1" in a["act_detail"]
              and "600001 B名：打点 2 行" in a["act_detail"]
              and "600002" not in a["act_detail"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # T8 trend（state 三态）
    orig_state, wk.STATE = wk.STATE, os.path.join(
        tempfile.mkdtemp(), "s.json")
    check("T8 首期", wk._trend_text(5, 1, 0) == "首期无环比")
    json.dump({"closed": 4, "open_bad": 2, "closed_bad": 0},
              open(wk.STATE, "w", encoding="utf-8"))
    check("T8 对比", wk._trend_text(5, 1, 0)
          == "结案 +1｜巡检异常 -1｜审计异常 0")
    open(wk.STATE, "w", encoding="utf-8").write("{bad json")
    check("T8 坏 state 容错", wk._trend_text(5, 1, 0) == "首期无环比")
    wk.STATE = orig_state

    # T9 recent_lines（§14/§15 共用）
    rl = wcard.recent_lines([
        {"code": "000333.SZ", "name": "美的", "type": "A", "ts": "2026-10-01",
         "chg": 5.0},
        {"code": "000006.SZ", "name": "", "type": "C", "ts": "2026-09-20",
         "chg": None}], n=2)
    check("T9 色标行", rl.splitlines()[0] == "- 🟢 000333.SZ 美的：+5.0%"
          and rl.splitlines()[1] == "- 🔴 000006.SZ：—%")

    # T6 真实 CLI 预览
    r = subprocess.run(
        [PY, "-X", "utf8", os.path.join(HERE, "weak_track_weekly.py")],
        capture_output=True, text=True, encoding="utf-8", timeout=180)
    check("T6 CLI rc=0", r.returncode == 0,
          r.stdout[-150:] + r.stderr[-150:])
    check("T6 周报形态", "WEAK WEEKLY" in r.stdout or "产线空转" in r.stdout)
    check("T6 动态行", "本周动态" in r.stdout or "产线空转" in r.stdout)

    print("\n%s（%d 项失败）" % ("ALL PASS" if not fails else "FAILED: %s" % fails,
                               len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
