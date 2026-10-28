# -*- coding: utf-8 -*-
"""qa1_review.py — QA1 质检复核固化工具（K-ORG-038a）。

用法（仓库根目录运行）:
    python tools/qa1_review.py <短sha> [<sha>...]

流程:
    1. 对每个 sha 调 `git show <sha>` 取 diff，截前 3000 字；
    2. 构造复核 prompt（逐笔三问：改动是否相符/有无隐患/通过或退回+理由），
       调 agent_coder.chat_auto("QA1", task)；
    3. 逐笔打印复核结论；
    4. 任一笔结论含「退回」→ exit 1，否则 exit 0。

依赖: 仅标准库 + tools/agent_coder.py。
"""

import re
import subprocess
import sys
from pathlib import Path

# sys.path 处理：与 tools/repo_map.py 被外部调用的方式一致，
# 把本文件所在目录加入 sys.path，保证 `import agent_coder` 可用。
TOOLS_DIR = str(Path(__file__).resolve().parent)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import agent_coder  # noqa: E402

DIFF_MAX_CHARS = 16000  # K-ORG-056 判例：原 3000 截断致 QA1 看不见核心交付物而退回（与 sec2_review FILE_MAX 同宗根因）
NOTE_MAX_CHARS = 8000  # K-ORG-056: 附证上限放宽（证据包四项约 4~6KB）  # K-ORG-043: 附证文本上限，进 prompt 前截断
REJECT_KEYWORD = "退回"


def get_diff(sha):
    """调 git show 取该提交 diff（截前 3000 字）；失败抛 RuntimeError。"""
    try:
        proc = subprocess.run(
            ["git", "show", sha],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("未找到 git 命令，请在装有 git 的环境运行") from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or "").strip()[:300]
        raise RuntimeError(f"git show {sha} 失败：{err}") from exc
    diff = proc.stdout or ""
    return diff[:DIFF_MAX_CHARS]


def build_prompt(sha, diff, note=None):
    """构造 QA1 复核 prompt：逐笔三项作答，结论只能二选一。
    note: 提交方附证（负面断言如「全库清扫」须附现值核验计数，K-ORG-042 判例）。
    安全加固（K-ORG-043，QA1 R7 观察项 a）: 附证先中性化（任意连续 >=4 个 '>' 压为 '>>>'）、
    再截断至 NOTE_MAX_CHARS（超长加省略号"…"），保证 '>>>>' 只以模板定界符出现。
    diff 安全加固（K-ORG-045）: 在截断前对 diff 参数执行与 note 完全一致的中性化处理，防止恶意注入。"""
    if note:
        note = re.sub(r">{4,}", ">>>", note)
        if len(note) > NOTE_MAX_CHARS:
            note = note[:NOTE_MAX_CHARS] + "…"
    note_blk = ("\n【提交方附证（可复现现值核验）】\n<<<<\n%s\n>>>>\n" % note) if note else ""
    
    # 安全加固：在截断前对 diff 做中性化，防止 '>>>>' 越栏注入
    safe_diff = re.sub(r">{4,}", ">>>", diff)
    return (
        f"你是 QA1 质检复核员，请复核以下这笔提交。\n"
        f"提交 sha: {sha}\n\n"
        f"diff（截前 {DIFF_MAX_CHARS} 字，完整 diff 可用 git show {sha} 复现）:\n"
        f"<<<<\n{safe_diff}\n>>>>\n{note_blk}\n"
        "请逐项回答：\n"
        "1) 改动与提交消息是否相符；\n"
        "2) 有无逻辑/边界隐患；\n"
        "3) 结论：必须明确给出「通过」或「退回 + 理由」。\n"
        "【diff 判读纪律（K-ORG-042 判例）】diff 中以 - 开头的行是被「移除的旧内容」"
        "而非提交后的现状——判定「某引用仍存在」不能以 - 行为据；"
        "负面断言（如「已清零/已全删」）以【提交方附证】的现值计数为准，"
        "无附证且 diff 截断不足以判断时，结论给「退回 + 理由：需附证重提」。\n"
        "输出保持简洁，结论放在最后一行。"
    )


# K-ORG-062a(F6b): 只读审查纪律附言——QA1 经 claude -p 拿到 Read/Grep/Glob 三件套，
# 可直读仓库核现状（根治 K-ORG-042 判例「diff - 行是被移除旧内容、不能据以判现状」）。
READ_ONLY_ADDENDUM = (
    "\n【只读审查纪律（K-ORG-062a）】你配有只读工具 Read/Grep/Glob（无 Edit/Bash/Write，"
    "不得也不可修改任何文件）。判定「现状是什么」以你读到的文件为准，"
    "diff 中 - 开头的行只是被移除的旧内容；【提交方附证】中的现值计数可用 Grep 抽查复核。"
)

# K-ORG-062a(F6b) SEC2 返工项③：只读面 fail-closed 自检（import 时执行）——
# 工具本体与只读承诺绑死：附言或工具面一旦被改出写能力，import 即炸，质检不静默降权。
# QA1 复核观察修正：①显式 raise（assert 在 python -O 下被剥离会静默失效=fail-open）；
# ②白名单式校验（⊆ {Read,Grep,Glob}，新增写工具名不在黑名单也能拦住）。
_READONLY_TOOLS = "Read,Grep,Glob"


def _readonly_guard():
    allowed = {"Read", "Grep", "Glob"}
    tools = set(_READONLY_TOOLS.split(","))
    if tools - allowed:
        raise RuntimeError(
            "K-ORG-062a 只读面被破坏：QA1 复核工具面出现白名单外工具 %s"
            % sorted(tools - allowed))
    if "无 Edit/Bash/Write" not in READ_ONLY_ADDENDUM:
        raise RuntimeError("K-ORG-062a 只读纪律附言被移除，QA1 只读承诺失据")


_readonly_guard()


def review_one(sha, note=None):
    """复核单笔提交，返回 QA1 模型结论文本。
    K-ORG-062a(F6b): 主路径=claude -p 只读审查（allowed_tools=Read,Grep,Glob，
    无写面）；CLI 缺失/超时/rc 非零等异常降级 chat_auto 并留痕。"""
    diff = get_diff(sha)
    task = build_prompt(sha, diff, note)
    try:
        return str(
            agent_coder.chat_claude(
                "QA1",
                {"label": "QA1 复核 " + sha,
                 "prompt": task + READ_ONLY_ADDENDUM,
                 "allowed_tools": _READONLY_TOOLS,
                 "max_turns": 15},
                timeout=600,
            )
        )
    except Exception as exc:  # 降级链：只读审查通道不可用时不挡质检
        print("[qa1_review] claude 只读通道不可用，降级 chat_auto: %s"
              % str(exc)[:160], file=sys.stderr)
        return str(
            agent_coder.chat_auto(
                "QA1",
                {"label": "QA1 复核 " + sha, "prompt": task, "json_out": False},
            )
        )


def main(argv):
    task_id = "QA1-REVIEW"
    note = None
    while argv and argv[0] in ("--task", "--note"):
        opt = argv[0]
        if len(argv) < 2:
            print("用法：python tools/qa1_review.py [--task <工单号>] [--note <附证文本>] <短sha> [<sha>...]", file=sys.stderr)
            return 2
        if opt == "--task":
            task_id = argv[1]
        else:
            note = argv[1]
        argv = argv[2:]

    if not argv:
        print("用法：python tools/qa1_review.py <短sha> [<sha>...]", file=sys.stderr)
        return 2

    rejected = False
    for sha in argv:
        print(f"=== QA1 复核 {sha} ===")
        try:
            verdict = review_one(sha, note)
        except Exception as exc:
            # git 取 diff 失败无法复核，按退回处理，防止坏账混过质检门
            print(f"[退回] {sha}: {exc}", file=sys.stderr)
            rejected = True
            continue
        print(verdict)
        print("")

        last_line = next(
            (line for line in reversed(verdict.splitlines()) if line.strip()),
            "",
        )
        if REJECT_KEYWORD in last_line:
            rejected = True

        try:
            subprocess.run(
                [
                    "python",
                    "tools/events.py",
                    "emit",
                    "--kind",
                    "review_verdict",
                    "--agent",
                    "QA1",
                    "--task",
                    task_id,
                    "--detail",
                    f"{sha}: {last_line.strip()[:200]}",
                ],
                check=True,
            )
        except Exception as exc:
            print(f"[警告] review_verdict 留痕失败（{sha}）: {exc}", file=sys.stderr)

    return 1 if rejected else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))