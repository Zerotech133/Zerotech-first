# K-ORG-066: e2e 彩排轨迹汇总工具（只读，--json 输出轮次/关卡/结果一览）
# 边界: 不写文件、无网络; stdout 只输出一个 JSON 对象, 退出码 0
# SEC2 返工对照: P-1 主仓根 git common dir 锚定+TID 连字符正则 / P-2 last_step
# 取最后一步+终态判空缺省 / P-3 time 转字符串防 dumps TypeError / P-4 头注释
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _main_root():
    # 主仓根定位: worktree 内 __file__ 上溯不够(064 判例), 用 git common dir 锚定;
    # 非 git 环境降级 __file__ 上溯并以 data/e2e 目录存在性校验。
    here = Path(__file__).resolve().parent
    try:
        r = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                           cwd=str(here), capture_output=True, text=True, timeout=10)
        common = r.stdout.strip()
        if r.returncode == 0 and common:
            p = Path(common)
            if not p.is_absolute():
                # git 返回值相对 subprocess cwd(=here·tools/), 锚定基准须为 here
                # (判例: 主仓返回 '../.git', 用 here.parent 拼会解析到盘根 E:\)
                p = (here / common).resolve()
            return p.parent
    except Exception:
        pass
    root = here.parent.parent
    if (root / "data" / "e2e").is_dir():
        return root
    return here.parent.parent  # 最后兜底: 保持 tools/.. 结构


def parse_trace_file(path):
    # 终态: 首行标题括号内 DONE/ABORT/SEC2_FAIL/QA1_FAIL; 判空缺省 UNKNOWN
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    final = "UNKNOWN"
    if lines:
        m = re.search(r"[（(](DONE|ABORT|SEC2_FAIL|QA1_FAIL)[)）]", lines[0])
        if m:
            final = m.group(1)
    steps = [l for l in lines if l.startswith("- [")]
    last_step = steps[-1].strip() if steps else "(无步骤)"
    return final, len(steps), last_step


def scan_e2e_traces():
    # 文件名: <TID>_trace_<HHMMSS>.md · TID 含连字符(K-ORG-064 式)用 [\w-]+
    root = _main_root()
    items = []
    for p in sorted(glob.glob(str(root / "data" / "e2e" / "K-*_trace_*.md"))):
        base = os.path.basename(p)
        m = re.match(r"(K-[\w-]+)_trace_(\d{6})\.md$", base)
        if not m:
            continue
        tid, tm = m.group(1), m.group(2)
        final, steps, last_step = parse_trace_file(p)
        items.append({
            "tid": tid,
            "time": tm,  # P-3: 字符串, 防 json.dumps TypeError
            "final": final,
            "steps": steps,
            "last_step": last_step[:120],
        })
    items.sort(key=lambda x: x["time"], reverse=True)
    return {"runs": len(items), "items": items}


def main():
    ap = __import__("argparse").ArgumentParser(description="K-ORG-066 e2e report (read-only)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if not args.json:
        ap.error("only --json is supported")
    print(json.dumps(scan_e2e_traces(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
