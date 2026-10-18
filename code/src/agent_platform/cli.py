"""`python -m agent_platform.cli` 转发垫片（与 __init__.py 同属顶层 shim 包）。"""
from zhice_os_v2.agent_platform.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
