# -*- coding: utf-8 -*-
"""只读工具「退出码契约」测试（subprocess 级，端到端锁定 CLI 语义）。

## 为什么要有它

下面五个只读工具是运维判读的判据来源——**退出码本身就是契约**：
值班人只看 `$?` 就下结论（可放行 / 中性待观察 / 先查清再判门）。
一旦 argparse 段或 main() 返回路径被悄改，人不会收到任何告警，
只会照着错的退出码做出错的判断。本文件把文档里承诺的语义逐条钉死。

## 契约表（以 tools/*.py 源码为准，2026-10-18 实测核对）

| 工具                       | 参数纪律                                  | 退出码语义 |
|----------------------------|-------------------------------------------|------------|
| check_observation_point.py | --data-root 必填                          | 2=有 FAIL / 0=无 FAIL 且有 PASS / 1=全 PENDING（中性） |
| verify_strategy_ledger.py  | --data-root 必填                          | 0=全 PASS 或空账本中性 / 1=有 FAIL / 2=参数缺失 |
| check_trigger_chain.py     | --repo-root 必填（--print-config 亦然）   | 2=有 FAIL / 0=无 FAIL 且有 PASS / 1=全 WARN（无正向证据） |
| list_rejects.py            | --show 与 --stats 互斥                    | 2=参数冲突 / 0=浏览恒 0 |
| scan_secrets.py            | --paths 或默认 --git-status               | 1=--strict 有命中 / 0=无 --strict（恒 0） / 2=非 git 目录跑 --git-status |

注意 check_trigger_chain 的 1 是「全 WARN」而非「全 UNKNOWN」：源码已按
2026-10-18 收口判例取消 UNKNOWN 状态（docstring 第 53-63 行），
本测试以源码为准。

## 隔离纪律

全部用 pytest tmp_path 造隔离根，绝不触碰真仓 data/，且断言这些工具
在空根上连目录都不创建（零写面红线）。子进程 cwd 固定为仓库根，
env 注入 PYTHONIOENCODING=utf-8（Windows 控制台默认 GBK 会炸中文输出）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
SUBPROCESS_TIMEOUT_S = 120


def run_tool(tool: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """以子进程调用 tools/<tool>，返回 CompletedProcess（text 模式，UTF-8 解码）。"""
    script = TOOLS_DIR / tool
    assert script.is_file(), "工具脚本缺失：%s" % script
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONPATH", None)  # 外部 sys.path 不得污染被调工具
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd if cwd is not None else REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SUBPROCESS_TIMEOUT_S,
    )


def _both(proc: subprocess.CompletedProcess) -> str:
    """stdout+stderr 合并（用于只关心「哪条消息出现过」的断言）。"""
    return (proc.stdout or "") + "\n" + (proc.stderr or "")


# ---------------------------------------------------------------------------
# check_observation_point：2=有 FAIL / 0=有 PASS 无 FAIL / 1=全 PENDING（中性）
# ---------------------------------------------------------------------------

def test_observation_point_empty_root_is_neutral_rc1(tmp_path):
    """空根（首跑前形态）→ 五判据全 PENDING → rc 1，输出显式中性字样。"""
    data_root = tmp_path / "data"  # 故意不创建：模拟真仓首跑前

    proc = run_tool("check_observation_point.py", "--data-root", str(data_root))

    assert proc.returncode == 1, _both(proc)
    assert "PENDING" in proc.stdout
    assert "中性" in proc.stdout
    assert "汇总: PASS 0 / PENDING 5 / FAIL 0" in proc.stdout
    # 中性 ≠ 失败：不得出现任何 FAIL 判据行，也不得出现退出码 2 的结论
    assert "[FAIL]" not in proc.stdout
    assert "退出码 2" not in proc.stdout


def test_observation_point_zero_write_surface(tmp_path):
    """零写面红线：巡检空根后不得创建目录（连 mkdir 都不做）。"""
    data_root = tmp_path / "never_created"

    proc = run_tool("check_observation_point.py", "--data-root", str(data_root))

    assert proc.returncode == 1, _both(proc)
    assert not data_root.exists(), "巡检工具越界创建了数据根：%s" % data_root


def test_observation_point_missing_data_root_rc2(tmp_path):
    """--data-root 必填：缺参由 argparse 拒绝 → rc 2。"""
    proc = run_tool("check_observation_point.py")

    assert proc.returncode == 2, _both(proc)
    assert "--data-root" in proc.stderr


# ---------------------------------------------------------------------------
# verify_strategy_ledger：0=全 PASS（含空账本中性）/ 1=有 FAIL / 2=缺参
# ---------------------------------------------------------------------------

def test_ledger_empty_root_is_neutral_rc0(tmp_path):
    """空根 → 空账本中性 → rc 0（中性非通过，但退出码仍为 0 契约）。"""
    data_root = tmp_path / "data"

    proc = run_tool("verify_strategy_ledger.py", "--data-root", str(data_root))

    assert proc.returncode == 0, _both(proc)
    assert "[中性]" in proc.stdout
    assert "空账本中性（退出码 0）" in proc.stdout
    assert not data_root.exists(), "对账工具越界创建了数据根：%s" % data_root


def test_ledger_missing_data_root_rc2():
    """--data-root 必填：无 root 拒绝运行 → rc 2。"""
    proc = run_tool("verify_strategy_ledger.py")

    assert proc.returncode == 2, _both(proc)
    assert "--data-root" in proc.stderr


# ---------------------------------------------------------------------------
# check_trigger_chain：2=有 FAIL / 0=有 PASS 无 FAIL / 1=全 WARN；--print-config
# ---------------------------------------------------------------------------

def test_trigger_chain_print_config_rc0():
    """--print-config 只打印配置常量后 rc 0。

    该分支在 run_checks 之前 return（源码 main() 第 457-459 行），不读计划任务
    XML、不探 Ollama 端口、不启子进程，因此无网络/任务计划依赖，无需 skip。
    """
    proc = run_tool("check_trigger_chain.py",
                    "--repo-root", str(REPO_ROOT), "--print-config")

    assert proc.returncode == 0, _both(proc)
    assert "check_trigger_chain 配置面" in proc.stdout
    assert "AgentShadowDaily" in proc.stdout
    assert "AgentMondayCheckpoint" in proc.stdout
    assert "AgentChiefPush" in proc.stdout


def test_trigger_chain_print_config_still_requires_repo_root(tmp_path):
    """参数纪律：--repo-root 是 required，--print-config 也不能省 → rc 2。"""
    proc = run_tool("check_trigger_chain.py", "--print-config", cwd=tmp_path)

    assert proc.returncode == 2, _both(proc)
    assert "--repo-root" in proc.stderr


# ---------------------------------------------------------------------------
# list_rejects：--show 与 --stats 互斥 → 2；浏览路径恒 0
# ---------------------------------------------------------------------------

def test_list_rejects_show_stats_mutex_rc2(tmp_path):
    """同给 --show 与 --stats → rc 2，stdout 无表格输出。"""
    rejects_root = tmp_path / "rejects"

    proc = run_tool("list_rejects.py", "--rejects-root", str(rejects_root),
                    "--show", "abc123", "--stats")

    assert proc.returncode == 2, _both(proc)
    assert "互斥" in proc.stderr
    assert "stage 汇总" not in proc.stdout


def test_list_rejects_empty_root_rc0(tmp_path):
    """浏览工具恒 0：无拒档 = 中性输出，不是错误。"""
    rejects_root = tmp_path / "rejects"

    proc = run_tool("list_rejects.py", "--rejects-root", str(rejects_root))

    assert proc.returncode == 0, _both(proc)
    assert "无拒档" in proc.stdout
    assert not rejects_root.exists(), "浏览工具越界创建了归档根：%s" % rejects_root


# ---------------------------------------------------------------------------
# scan_secrets：1=--strict 有命中 / 0=无 --strict 恒 0 / 2=非 git 目录 --git-status
# ---------------------------------------------------------------------------

def test_scan_secrets_paths_clean_rc0(tmp_path):
    """隔离临时目录 + --paths 指定干净文件 → rc 0（--paths 模式不看 git 环境）。"""
    target = tmp_path / "clean.txt"
    target.write_text("plain text\nnothing sensitive here\n", encoding="utf-8")

    proc = run_tool("scan_secrets.py", "--paths", str(target))

    assert proc.returncode == 0, _both(proc)
    assert "命中 0 处" in proc.stdout


def test_scan_secrets_paths_hit_without_strict_rc0(tmp_path):
    """默认（报告口径）有命中仍 rc 0——非门禁模式不得拦路。"""
    target = tmp_path / "dirty.txt"
    target.write_text("api_key = 'abcdefgh12345678'\n", encoding="utf-8")

    proc = run_tool("scan_secrets.py", "--paths", str(target))

    assert proc.returncode == 0, _both(proc)
    assert "命中 1 处" in proc.stdout


def test_scan_secrets_strict_hit_rc1(tmp_path):
    """--strict + 命中 → rc 1（供 pre-commit 门禁）。"""
    target = tmp_path / "dirty.txt"
    target.write_text("api_key = 'abcdefgh12345678'\n", encoding="utf-8")

    proc = run_tool("scan_secrets.py", "--paths", str(target), "--strict")

    assert proc.returncode == 1, _both(proc)
    assert "拒绝通过" in proc.stderr
    assert "KEYWORD_ASSIGN" in proc.stdout


def test_scan_secrets_non_git_cwd_default_mode_rc2(tmp_path):
    """非 git 目录跑默认 --git-status 模式 → rc 2（拒绝带病扫描）。

    唯一一处 cwd 不取仓库根：该语义恰恰要求进程 cwd 不在任何 git 仓内，
    否则测不到环境错误分支。tmp_path 本身即非 git 目录。
    """
    assert not (tmp_path / ".git").exists()

    proc = run_tool("scan_secrets.py", cwd=tmp_path)

    assert proc.returncode == 2, _both(proc)
    assert "不在任何 git 仓库内" in proc.stderr
