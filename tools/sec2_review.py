# -*- coding: utf-8 -*-
"""sec2_review.py — SEC2 方案级走形检查固化工具（K-ORG-054 · G3 闭环）。

用法（仓库根目录运行）:
    python tools/sec2_review.py --plan docs/plans/xxx.md --sha <实现提交sha> [--out <报告路径>]
    python tools/sec2_review.py --plan docs/plans/xxx.md --files a.py b.html

流程:
    1. 读方案文档（SEC2 按条目号逐条对照）；
    2. 取实现证据：--sha 走 git show <sha> --stat + diff 截断，或 --files 读文件；
    3. 构造走形检查 prompt（固定产出格式：方案条目→实现证据→判定(完善/走形/缺失)→建议命令），
       调 agent_coder.chat_auto("SEC2", prompt)——智谱包年 glm-4.6（K-ORG-050 通道，审查类单约 4~5 积分）；
    4. 解析判定计数，报告落 agent_platform/data/sec2/，emit deviation_report 事件；
    5. 有走形/缺失 → exit 2（提示老田/田丰下发修改命令）；全完善 → exit 0。
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

TOOLS_DIR = str(Path(__file__).resolve().parent)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import agent_coder  # noqa: E402
import logging

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "agent_platform" / "data" / "sec2"
PLAN_MAX = 9000      # 方案全文上限（K-ORG-051 设计稿约 6KB 不会触发）
EVIDENCE_MAX = 9000  # 实现证据上限
FILE_MAX = 18000     # K-ORG-056 判例：--files 单文件上限（原 4500 截断致 SEC2
                     # 把完整实现误判"缺失"两例——截断≠缺失，必须让审查者看见全文
                     # 或看见显式截断标注）

FORMAT_RULES = """你是 SEC2 网络安全工程师（30年网安+全栈）。对照研发方案逐条检查实现，只做判定不修改代码。
产出格式（固定，不许加散文开头）：
## 走形检查报告
对照方案：<方案名>
### P-1 <条目名>
- 实现证据：<文件:行为或提交中的证据>
- 判定：完善 | 走形 | 缺失（三选一）
- 说明：<一两句>
- 建议命令：<走形/缺失时给田丰的下发命令建议；完善写"无">
（逐条目重复；最后加一行统计：`统计：完善 N · 走形 N · 缺失 N`）"""


def load_plan(path):
    txt = Path(path).read_text(encoding="utf-8", errors="replace")
    return txt[:PLAN_MAX]


def load_evidence(args):
    parts = []
    if args.sha:
        r = subprocess.run(["git", "show", "--stat", args.sha],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        parts.append("提交 %s 概览:\n%s" % (args.sha, (r.stdout or "")[:1500]))
        r = subprocess.run(["git", "show", args.sha], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        parts.append("提交 diff（截断）:\n%s" % (r.stdout or "")[:EVIDENCE_MAX])
    for f in (args.files or []):
        p = Path(f)
        if p.exists():
            body = p.read_text(encoding="utf-8", errors="replace")
            if len(body) > FILE_MAX:
                body = (body[:FILE_MAX]
                        + "\n\n【证据截断标注】以上为前 %d 字符，全文件共 %d 字符——"
                          "尾部内容未展示属证据截断，请勿判「缺失」，可建议补交全文。"
                          % (FILE_MAX, len(body)))
            parts.append("文件 %s:\n%s" % (f, body))
        else:
            parts.append("文件 %s: 不存在（这可能本身就是缺失证据）" % f)
    joined = "\n\n".join(parts)
    if len(joined) > EVIDENCE_MAX * 3:  # K-ORG-056·QA1 observation：外层截断不得静默
        print("[sec2_review][warn] 证据总长 %d 超上限 %d，已截断——审查者须知"
              % (len(joined), EVIDENCE_MAX * 3), file=sys.stderr)
    return joined[:EVIDENCE_MAX * 3]


def parse_verdict(report):
    m = re.search(r"统计：完善\s*(\d+)\s*·\s*走形\s*(\d+)\s*·\s*缺失\s*(\d+)", report)
    if m:
        return tuple(int(x) for x in m.groups())
    ok = len(re.findall(r"判定：\s*完善", report))
    dev = len(re.findall(r"判定：\s*走形", report))
    miss = len(re.findall(r"判定：\s*缺失", report))
    return ok, dev, miss


def emit_event(task, detail):
    try:
        subprocess.run([sys.executable, str(TOOLS_DIR / "events.py"),
                        "--kind", "deviation_report", "--task", task,
                        "--agent", "SEC2", "--detail", detail],
                       capture_output=True, timeout=30)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/sec2_review.py", exc)


def main(argv=None):
    ap = argparse.ArgumentParser(description="SEC2 方案级走形检查")
    ap.add_argument("--plan", required=True, help="研发方案文档路径")
    ap.add_argument("--sha", help="实现提交 sha（git show 取证据）")
    ap.add_argument("--files", nargs="*", help="实现文件列表（读文件取证据）")
    ap.add_argument("--out", help="报告输出路径（缺省 agent_platform/data/sec2/）")
    a = ap.parse_args(argv)

    if not a.sha and not a.files:
        ap.error("--sha 与 --files 至少给一个")

    plan_name = Path(a.plan).name
    plan = load_plan(a.plan)
    evidence = load_evidence(a)
    prompt = ("%s\n\n=== 研发方案（%s）===\n%s\n\n=== 实现证据 ===\n%s"
              % (FORMAT_RULES, plan_name, plan, evidence))

    print("SEC2 走形检查启动：方案=%s 证据=%s"
          % (plan_name, a.sha or ("%d 个文件" % len(a.files))))
    t0 = time.time()
    report = agent_coder.chat_auto("SEC2", {"label": "sec2_review:" + plan_name,
                                            "prompt": prompt}).strip()
    dt = time.time() - t0
    if not report:
        print("FAIL: 空报告（通道异常）")
        return 3
    ok, dev, miss = parse_verdict(report)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(a.out) if a.out else OUT_DIR / (
        time.strftime("%Y%m%d_%H%M") + "_" + plan_name.replace(".md", "") + ".md")
    header = ("# SEC2 走形检查报告（K-ORG-054）\n\n- 方案：%s\n- 证据：%s\n"
              "- 模型：%s（%.1fs）\n- 判定：完善 %d · 走形 %d · 缺失 %d\n\n"
              % (plan_name, a.sha or ", ".join(a.files or []),
                 agent_coder.last_channel(), dt, ok, dev, miss))
    out.write_text(header + report + "\n", encoding="utf-8")
    print("报告落盘:", out)
    print("判定: 完善 %d · 走形 %d · 缺失 %d（%.1fs, via %s）"
          % (ok, dev, miss, dt, agent_coder.last_channel()))
    emit_event("SEC2走查:" + plan_name,
               "报告=%s 完善=%d 走形=%d 缺失=%d%s"
               % (out.name, ok, dev, miss,
                  " → 建议田丰下发修改命令" if (dev or miss) else " → 建议归档结案"))
    return 2 if (dev or miss) else 0


if __name__ == "__main__":
    sys.exit(main())
