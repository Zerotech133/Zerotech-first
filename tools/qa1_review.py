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

import subprocess
import sys
from pathlib import Path

# sys.path 处理：与 tools/repo_map.py 被外部调用的方式一致，
# 把本文件所在目录加入 sys.path，保证 `import agent_coder` 可用。
TOOLS_DIR = str(Path(__file__).resolve().parent)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import agent_coder  # noqa: E402  （必须在 sys.path 处理之后再导入）

DIFF_MAX_CHARS = 3000
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
        raise RuntimeError(f"git show {sha} 失败: {err}") from exc
    diff = proc.stdout or ""
    return diff[:DIFF_MAX_CHARS]


def build_prompt(sha, diff, note=None):
    """构造 QA1 复核 prompt：逐笔三项作答，结论只能二选一。
    note: 提交方附证（负面断言如「全库清扫」须附现值核验计数，K-ORG-042 判例）。"""
    note_blk = ("\n【提交方附证（可复现现值核验）】\n<<<<\n%s\n>>>>\n" % note) if note else ""
    return (
        f"你是 QA1 质检复核员，请复核以下这笔提交。\n"
        f"提交 sha: {sha}\n\n"
        f"diff（截前 {DIFF_MAX_CHARS} 字，完整 diff 可用 git show {sha} 复现）:\n"
        f"<<<<\n{diff}\n>>>>\n{note_blk}\n"
        "请逐项回答：\n"
        "1) 改动与提交消息是否相符；\n"
        "2) 有无逻辑/边界隐患；\n"
        "3) 结论：必须明确给出「通过」或「退回+理由」。\n"
        "【diff 判读纪律（K-ORG-042 判例）】diff 中以 - 开头的行是被「移除的旧内容」"
        "而非提交后的现状——判定「某引用仍存在」不能以 - 行为据；"
        "负面断言（如「已清零/已全删」）以【提交方附证】的现值计数为准，"
        "无附证且 diff 截断不足以判断时，结论给「退回+理由：需附证重提」。\n"
        "输出保持简洁，结论放在最后一行。"
    )


def review_one(sha, note=None):
    """复核单笔提交，返回 QA1 模型结论文本。"""
    diff = get_diff(sha)
    task = build_prompt(sha, diff, note)
    return str(
        agent_coder.chat_auto(
            "QA1",
            {"label": "QA1复核 " + sha, "prompt": task, "json_out": False},
        )
    )


def main(argv):
    task_id = "QA1-REVIEW"
    note = None
    while argv and argv[0] in ("--task", "--note"):
        opt = argv[0]
        if len(argv) < 2:
            print("用法: python tools/qa1_review.py [--task <工单号>] [--note <附证文本>] <短sha> [<sha>...]", file=sys.stderr)
            return 2
        if opt == "--task":
            task_id = argv[1]
        else:
            note = argv[1]
        argv = argv[2:]

    if not argv:
        print("用法: python tools/qa1_review.py <短sha> [<sha>...]", file=sys.stderr)
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