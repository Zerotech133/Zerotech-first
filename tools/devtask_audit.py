# tools/devtask_audit.py -- K-ORG-064 review 工单审计只读工具
# 边界: 不写文件、无网络、git 仅用 log; stdout 只输出一个 JSON 对象, 退出码 0
# 用法: python tools/devtask_audit.py --json
# 修复: 路径锚定到脚本所在仓库根, 不依赖 CWD (上轮冒烟 Errno 2 的根因)
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# K-ORG-064 返工(QA1退回项①): worktree 内运行时 __file__ 上溯到的是 worktree 根,
# 而 .worktrees/tasks.json 只存在于主仓——用 git common dir 锚定主仓根。
def _main_root():
    r = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    common = r.stdout.strip()
    if r.returncode == 0 and common:
        p = Path(common)
        if not p.is_absolute():
            p = (ROOT / common).resolve()
        return p.parent  # .../<主仓>/.git -> 主仓根
    return ROOT  # 非 git 环境降级(保持旧行为)

ROOT = _main_root()
TASKS_FILE = ROOT / ".worktrees" / "tasks.json"


def load_review_tids(path):
    # 返回 status=='review' 的 TID 列表; 文件缺失或损坏时降级为空列表并警告到 stderr
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"warn: cannot load {path}: {exc}", file=sys.stderr)
        return []
    if not isinstance(data, dict):
        print(f"warn: {path} is not a dict", file=sys.stderr)
        return []
    return sorted(
        tid
        for tid, info in data.items()
        if isinstance(info, dict) and info.get("status") == "review"
    )


def is_merged(tid):
    # git log --all --grep=<TID>, 提交消息含 merge task/<TID> 即视为已合并
    proc = subprocess.run(
        ["git", "log", "--all", "-F", "--grep={}".format(tid)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    # K-ORG-064 返工(QA1退回项②): 裸子串有前缀撞号误判——"merge task/K-STOCK-136"
    # 会命中 K-STOCK-1361 的提交; 实际 merge 消息格式为 "merge task/<TID>: ..."
    # (git log 实证), 以冒号锚定消除前缀歧义。
    return proc.returncode == 0 and "merge task/{}:".format(tid) in proc.stdout


def audit():
    # 单次遍历分流: 已合并 / 仍在 review, total 取输入 TID 总数
    tids = load_review_tids(TASKS_FILE)
    merged, reviewing = [], []
    for tid in tids:
        (merged if is_merged(tid) else reviewing).append(tid)
    return {
        "total": len(tids),
        "already_merged": merged,
        "still_reviewing": reviewing,
    }


def main():
    # 唯一 CLI 契约: --json 输出单 JSON 对象 rc=0; 无 --json 则 argparse 报错退出
    ap = argparse.ArgumentParser(description="K-ORG-064 review audit (read-only)")
    ap.add_argument("--json", action="store_true", help="emit one JSON object")
    args = ap.parse_args()
    if not args.json:
        ap.error("only --json is supported")
    print(json.dumps(audit(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
