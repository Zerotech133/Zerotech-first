"""顶层入口垫片：支持 `python -m agent_platform.cli`（editable 安装把 code/src 已入 sys.path）。

真身在 zhice_os_v2.agent_platform.cli；本垫片只做转发，避免 `python -m zhice_os_v2.agent_platform.cli` 的长路径。
"""
from zhice_os_v2.agent_platform.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
