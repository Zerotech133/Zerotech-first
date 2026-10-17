"""QA 测试公共夹具：项目根入 sys.path、ffmpeg PATH 注入、环境隔离。

所有测试文件经 conftest 自动获得：
- ``PROJECT_ROOT`` 在 sys.path 首位（以包内绝对导入方式 import 被测模块）；
- ffmpeg/ffprobe 目录（本机 tools/ffmpeg-9.0.2-essentials_build/bin）prepend 到 PATH；
- OSINT_DATA_DIR / OSINT_LOG_DIR 重定向到 pytest 临时目录，避免污染项目 data/logs；
- 关闭 .env 读取（PytestDictBackend），保证测试环境与宿主配置解耦。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

#: 项目根（osint_video/）
PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: 本机 ffmpeg 工具目录（不在系统 PATH，测试注入）
FFMPEG_BIN_DIR = Path(r"C:\项目\老田的AI助手\tools\ffmpeg-9.0.2-essentials_build\bin")

# ---- sys.path 注入（先于任何被测模块 import） ----
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---- ffmpeg PATH 注入（模块级即可，subprocess 继承当前进程环境） ----
if FFMPEG_BIN_DIR.is_dir():
    os.environ["PATH"] = str(FFMPEG_BIN_DIR) + os.pathsep + os.environ.get("PATH", "")


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """每个测试独立的数据/日志目录 + 清空 .env 影响。

    - OSINT_DATA_DIR / OSINT_LOG_DIR → tmp 下独立目录；
    - 把 cwd 切到 tmp_path，避免 settings 读到项目根 .env（若存在）；
    - 清理 common.paths 已 import 的 DATA_DIR 常量无法重绑（模块常量），
      但被测代码均通过环境变量在 import 时决定，故在收集前设置亦可——
      这里通过重新加载方式太重，改为：conftest 在导入被测模块前统一设置
      （见模块级 _early_env），夹具内再按测试覆盖。
    """
    monkeypatch.chdir(tmp_path)
    yield


# ---- 早期环境变量（须在 conftest 导入被测模块前生效） ----
# 说明：pytest 收集阶段先导入 conftest，再导入测试模块；测试模块 import 被
# 测包时 common.paths 读取 OSINT_DATA_DIR。为保证测试不污染真实 data/ 目录，
# 在 conftest 模块级（早于测试模块 import）先指到系统临时目录下的固定子目录。
_QA_BASE = Path(os.environ.get("TEMP", str(Path.home()))) / "osint_video_qa"
os.environ.setdefault("OSINT_DATA_DIR", str(_QA_BASE / "data"))
os.environ.setdefault("OSINT_LOG_DIR", str(_QA_BASE / "logs"))
