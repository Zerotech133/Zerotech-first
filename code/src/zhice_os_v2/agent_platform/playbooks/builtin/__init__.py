"""内置打法包目录（BUILTIN_DIR 下 *.yaml 自动发现）。

- org_style_cn.yaml：机构风格·事件驱动（设计文档第二节原样落地；
  event_window 等预留 id 声明但 unavailable，T07 实现后自动参与投票）。
"""
from __future__ import annotations

from pathlib import Path

BUILTIN_DIR = Path(__file__).resolve().parent


def builtin_playbook_paths() -> list[Path]:
    """内置包文件清单（按文件名排序，确定性遍历）。"""
    return sorted(BUILTIN_DIR.glob("*.yaml"))


def load_builtin_playbooks() -> dict[str, "object"]:
    """加载全部内置包 → {playbook_id: PlaybookSpec}（坏文件跳过不阻塞）。"""
    from zhice_os_v2.agent_platform.playbooks.spec import load_playbook

    out: dict[str, object] = {}
    for path in builtin_playbook_paths():
        try:
            spec = load_playbook(path)
            out[spec.playbook_id] = spec
        except Exception as exc:  # 内置包损坏 → 跳过（CLI list 可见告警）
            import logging

            logging.getLogger(__name__).warning(
                "内置包 %s 加载失败(%s) — 跳过", path.name, exc)
    return out
