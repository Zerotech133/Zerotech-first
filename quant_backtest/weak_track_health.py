# -*- coding: utf-8 -*-
"""§16 任务群健康守护（weak 产线第 16 节 · K35-ac 新增）。

「谁看守守夜人」——weak 产线已自动化六任务（WeakScan/月度三/周报/
案例级 WeakTrack*），但任务自身失败无人知（计划任务静默失败）。
本节巡检 Weak* 全任务族三检测：

  ① 状态异常：产线级任务非 Ready（Disabled/Faulted/卡死）；
     案例级 WeakTrack* 仅 Faulted/Unknown（Disabled 归 §12/§13 语义）
  ② 结果异常：LastTaskResult 非良性——良性集 {0 成功, 267009 未跑过,
     267011 运行中}；WeakMonthlyArchive 的 2=等授权（README 已预告，
     属已知待办不告警）；267014 超时被杀/其他全报
  ③ 错失运行：产线级任务无下次计划或计划时间已过

静默纪律（§12 同款）：全绿 rc=0 不推送；异常推红卡 🚨（每任务两行：
判定+修复动作）。

默认预览；--send 推送（--text 回退文本）。文案外置
weak_track_health_tpl.txt（坏模板回退内置）。

用法:
  python weak_track_health.py                # 预览
  python weak_track_health.py --send         # 推送红卡
  python weak_track_health.py --send --text  # 推送文本
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TPL = os.path.join(HERE, "weak_track_health_tpl.txt")
NEWS_DIR = os.path.abspath(os.path.join(HERE, "..", "news_collector"))

# 产线级任务（必须 Ready、必须有下次计划；rc=2 特例仅归档任务）
# K35-ae：WeakMonthlyGuide 入列（§17 已挂月度 08:20）；WeakDailyHealth
# 不入列——运行中查自身状态恒为 Running，进 CORE 会天天误报（自看守陷阱）
CORE = {"WeakScan", "WeakMonthlySummary", "WeakMonthlyLearn",
        "WeakMonthlyArchive", "WeakWeeklyReport", "WeakMonthlyGuide"}
BENIGN = {0, 267009, 267011}          # 成功/未跑过/运行中
_KNOWN_RC = {"WeakMonthlyArchive": {2}}   # 2=等授权（已知待办）


def _task_states():
    """Weak* 全任务族 → [{name,state,lrc,last,next}]（一次 PS 查询）。"""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-ScheduledTask -TaskPath '\\' | "
         "Where-Object {$_.TaskName -like 'Weak*'} | ForEach-Object { "
         "$i = $_ | Get-ScheduledTaskInfo; "
         "\"$($_.TaskName)|$($_.State)|$($i.LastTaskResult)|\" + "
         "$(if ($i.LastRunTime) { $i.LastRunTime.ToString('yyyy-MM-dd HH:mm') } else { '' }) + "
         "'|' + $(if ($i.NextRunTime) { $i.NextRunTime.ToString('yyyy-MM-dd HH:mm') } else { '' })"
         " }"],
        capture_output=True, text=True, timeout=90)
    out = []
    for ln in (r.stdout or "").splitlines():
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) == 5 and parts[0]:
            try:
                lrc = int(parts[2])
            except ValueError:
                lrc = -1
            out.append({"name": parts[0], "state": parts[1], "lrc": lrc,
                        "last": parts[3], "next": parts[4]})
    return out


def health_cases(states, now=None):
    """任务状态 → 健康问题列表（每任务 issues 非空即异常）。"""
    now = now or time.strftime("%Y-%m-%d %H:%M")
    out = []
    for t in states:
        name, st, lrc = t["name"], t["state"], t["lrc"]
        core = name in CORE
        issues = []
        if core:
            if st != "Ready":
                issues.append("状态异常(%s)" % st)
            if not t["next"]:
                issues.append("无下次运行计划")
            elif t["next"] < now:
                issues.append("已错过计划(%s)" % t["next"])
        else:                       # 案例级 WeakTrack*
            if st in ("Faulted", "Unknown"):
                issues.append("状态异常(%s)" % st)
        if lrc not in BENIGN and lrc not in _KNOWN_RC.get(name, set()):
            issues.append("LastResult=%s" % lrc)
        if issues:
            out.append(dict(t, issues=issues))
    return out


_ACT = {
    "状态": "Enable-ScheduledTask 重启；反复 Faulted 查任务历史/事件日志",
    "无下次运行计划": "查触发器是否被清（重新注册 deploy_*.ps1）",
    "已错过计划": "StartWhenAvailable 应自动补跑；手动 Start-ScheduledTask",
    "LastResult": "看任务历史输出；脚本级失败先手动跑一遍定位",
}


def _action(i):
    for k, v in _ACT.items():
        if i.startswith(k):
            return v
    return "手动跑一遍定位"


def health_fields(cases):
    """健康问题 → 推送字段 dict（文案与卡片共用单一数据源）。"""
    return {
        "n": len(cases), "ts": time.strftime("%Y-%m-%d %H:%M"),
        "detail": "\n".join(
            "- %s：%s\n  → %s" % (c["name"], "、".join(c["issues"]),
                                 "；".join(_action(i)
                                           for i in c["issues"]))
            for c in cases),
    }


def build_health(f):
    """健康字段 → 文本文案（外置模板，坏模板回退内置）。"""
    if os.path.isfile(TPL):
        try:
            text = "".join(l for l in
                           open(TPL, encoding="utf-8").read().splitlines(True)
                           if not l.lstrip().startswith("#"))
            return text.format(**f).rstrip("\n")
        except (KeyError, IndexError, ValueError):
            pass
    return ("🚨 [WEAK HEALTH] 任务群健康守护（§16）\n"
            "异常任务 {n} 个｜{ts}\n{detail}").format(**f)


def main():
    ap = argparse.ArgumentParser(description="§16 任务群健康守护推送")
    ap.add_argument("--send", action="store_true",
                    help="执行推送（默认仅预览）")
    ap.add_argument("--text", action="store_true",
                    help="文本版推送（默认飞书卡片）")
    a = ap.parse_args()
    bad = health_cases(_task_states())
    if not bad:
        print("Weak* 任务族全部健康（状态 Ready、结果良性、计划正常）"
              "——静默不推送")
        return 0
    f = health_fields(bad)
    msg = build_health(f)
    print(msg)
    if not a.send:
        print("\n（预览模式：加 --send 执行推送）")
        if not a.text:
            import weak_track_card as wcard
            print("\n（卡片版预览，--text 可回退文本）")
            import json
            print(json.dumps(wcard.health_card(f),
                             ensure_ascii=False, indent=1))
        return 0
    if not a.text:
        import weak_track_card as wcard
        print("\n推送(卡片)：" + wcard.send_card(wcard.health_card(f)))
    else:
        tmp = os.path.join(NEWS_DIR, "outbox",
                           "pending_alert_weak_health.txt")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fo:
            fo.write(msg)
        r = subprocess.run(
            [sys.executable, os.path.join(NEWS_DIR, "send_feishu.py"), tmp],
            capture_output=True, text=True, encoding="utf-8", timeout=60)
        print("\n推送：" + ((r.stdout or "").strip()[:60] or "sent"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
