#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""治理硬闸棘轮（P1/P2，ADR-025）。

职责：每日计算 worktree 并发上限 cap，写入 config/gov_state.json；只读仓库，
不做任何删除/回收动作。devtask.py 只在 GOV_MODE != off 时读该文件的 cap。

棘轮公式（ADR-025 §决策 1；修正 v2 的冻结缺陷）：

    cap_new = max(target, min(cap_prev if cap_prev else current + 1, current + 1))

- 初始（cap_prev 缺失）：cap = current + 1，保证至少 1 个开单位；
- worktree 实际下降时 cap 才跟随下降，不随增长上调；
- enforce 档额外守卫：cap 不得低于 current + 1（永不冻结）。

CLI（只写 config/gov_state.json；其余仅读 git）：

    python tools/gov_ratchet.py                 # 打印当前状态 + 建议 cap（不写）
    python tools/gov_ratchet.py --write         # 计算并落盘 cap
    python tools/gov_ratchet.py --set-mode shadow
    python tools/gov_ratchet.py --set-target 15
    python tools/gov_ratchet.py --set-ttl 72
    python tools/gov_ratchet.py --json          # 机器可读输出

阶段约束：P2 起每日 08:20 由计划任务执行 --write；P1 只建文件与命令，默认 off。
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(REPO, "config", "gov_state.json")
MODES = ("off", "shadow", "warn", "enforce")
DEFAULT_TARGET = 15
DEFAULT_TTL = None


def _as_positive_int(value):
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def default_state():
    return {"mode": "off", "cap": None, "target": DEFAULT_TARGET, "ttl": DEFAULT_TTL}


def load_state(path=None):
    """读状态文件；缺失/坏 JSON/坏字段一律回落默认值（fail-open，不拦开发）。"""
    path = path or STATE_PATH
    state = default_state()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return state
    if not isinstance(raw, dict):
        return state
    mode = str(raw.get("mode") or "").strip().lower()
    if mode in MODES:
        state["mode"] = mode
    cap = _as_positive_int(raw.get("cap"))
    if cap is not None:
        state["cap"] = cap
    target = _as_positive_int(raw.get("target"))
    if target is not None:
        state["target"] = target
    ttl = raw.get("ttl")
    if ttl is not None:
        try:
            ttl_f = float(ttl)
        except (TypeError, ValueError):
            ttl_f = 0
        if ttl_f > 0:
            state["ttl"] = ttl_f
    return state


def save_state(state, path=None):
    """原子写状态文件（tmp + replace），返回落盘路径。"""
    path = path or STATE_PATH
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".gov_state.", suffix=".tmp", dir=parent or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return path


def count_worktrees(repo=None):
    """当前非主 worktree 数（git worktree list --porcelain，只读）。"""
    repo = repo or REPO
    try:
        out = subprocess.run(
            ["git", "-C", repo, "worktree", "list", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if out.returncode != 0:
        return 0
    main = os.path.normcase(os.path.abspath(repo))
    count = 0
    for line in (out.stdout or "").splitlines():
        if not line.startswith("worktree "):
            continue
        path = line[len("worktree "):].strip()
        if os.path.normcase(os.path.abspath(path)) == main:
            continue
        count += 1
    return count


def compute_cap(current, cap_prev=None, target=DEFAULT_TARGET):
    """棘轮核心（纯函数）：返回新 cap。

    - cap_prev 缺失/非法 → current + 1（初始 shadow 至少放行 1 个）；
    - 否则 min(cap_prev, current + 1)，再以 target 兜底；
    - 结果恒 >= target。
    """
    current = max(0, int(current or 0))
    target = _as_positive_int(target) or DEFAULT_TARGET
    prev = _as_positive_int(cap_prev)
    if prev is None:
        prev = current + 1
    return max(target, min(prev, current + 1))


def next_cap(state, current, mode=None):
    """按当前模式计算下一 cap；enforce 档加“永不冻结”守卫。"""
    mode = (mode or state.get("mode") or "off").lower()
    cap = compute_cap(current, state.get("cap"), state.get("target", DEFAULT_TARGET))
    if mode == "enforce":
        cap = max(cap, current + 1)
    return cap


def apply_write(state, current, path=None, mode=None):
    """计算 cap 并落盘；返回 (new_state, path)。"""
    mode = (mode or state.get("mode") or "off").lower()
    if mode not in MODES:
        mode = "off"
    state = dict(state)
    state["mode"] = mode
    state["cap"] = next_cap(state, current, mode)
    if mode == "off" and state.get("cap") is None:
        # off 档 cap 只是展示值，不参与拦截；仍写 current+1 便于观测
        state["cap"] = current + 1
    return state, save_state(state, path)


def _print_state(state, current=None):
    payload = dict(state)
    if current is not None:
        payload["current"] = current
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description="治理硬闸棘轮（ADR-025）")
    parser.add_argument("--write", action="store_true", help="计算并写入 cap")
    parser.add_argument("--set-mode", choices=MODES, help="切换 GOV_MODE")
    parser.add_argument("--set-target", type=int, help="设置 target（>=1）")
    parser.add_argument("--set-ttl", type=float, help="设置 TTL 小时（>0）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--state-path", default=STATE_PATH, help="状态文件路径（测试用）")
    args = parser.parse_args(argv)

    path = args.state_path
    state = load_state(path)
    changed = False
    if args.set_mode:
        state["mode"] = args.set_mode
        changed = True
        if args.set_mode == "shadow" and not state.get("cap"):
            state["cap"] = count_worktrees() + 1
    if args.set_target is not None:
        if args.set_target < 1:
            parser.error("--set-target 必须 >= 1")
        state["target"] = args.set_target
        changed = True
    if args.set_ttl is not None:
        if args.set_ttl <= 0:
            parser.error("--set-ttl 必须 > 0")
        state["ttl"] = args.set_ttl
        changed = True

    current = count_worktrees()
    if args.write:
        state, path = apply_write(state, current, path)
        changed = True
    elif changed:
        save_state(state, path)

    if args.json:
        _print_state(state, current)
    else:
        print("GOV_MODE=%s cap=%s target=%s ttl=%s current_worktrees=%d state=%s"
              % (state.get("mode"), state.get("cap"), state.get("target"),
                 state.get("ttl"), current, path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
