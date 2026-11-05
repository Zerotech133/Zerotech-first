"""导入级验证（第 1 层）：全部项目 Python 模块在受控环境真实 import。

验收口径（任务 #4）：
- 重依赖（funasr/paddle/cv2 等）必须延迟导入，这是架构 §2.4 的硬性设计；
- 核心依赖（FastAPI、SQLAlchemy、psycopg 等）由 requirements.txt 声明，
  受控环境应安装后执行本测试；缺失依赖必须作为真实失败报告，不能静默跳过；
- queue_link.api 可构建 FastAPI app（PG 未运行不触发连接）；
- scripts 两个入口模块可 import（不执行 main）。
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

#: 项目内全部 Python 模块（相对导入路径）。按包路径排序，新增模块必须同步纳入。
ALL_MODULES = [
    "common.atomic_io",  # 2026-11-05：原子文本写（台账/队列/标定戳共用，含 WinError 5 重试）
    "common.db",
    "common.feishu",
    "common.logging_setup",
    "common.paths",
    "config.settings",
    "extraction.align_merge",
    "extraction.asr_sensevoice",
    "extraction.audio_extract",
    "extraction.frame_extract",
    "extraction.ocr_paddle",
    "extraction.pipeline",
    "knowledge.api",
    "knowledge.authorized_browser",  # 2026-10-20 补登记：commit 0190195f 收编的授权浏览器知识件
    "knowledge.borderline_review",  # 2026-11-04 补登记：P1 临界带人工确认台账与待确认队列
    "knowledge.calibration_stamp",  # 2026-11-05：标定新鲜度戳（语料/探针指纹）
    "knowledge.run_ledger",  # 2026-11-05：轮次台账（跨轮可比性前提 + 结构性体检）
    "knowledge.browser_fetcher",
    "knowledge.creator_ops",
    "knowledge.creator_workers",
    "knowledge.embedder",  # 2026-11-04 补登记：P0 嵌入层（bge-m3 / Ollama HTTP）
    "knowledge.entity_alias",  # 2026-11-04 补登记：P1-B 实体别名归一（消歧禁入表）
    "knowledge.indexer",
    "knowledge.item_processor",
    "knowledge.llm_judge",  # 2026-11-04 补登记：P1-A 关系判定器（Ollama 四分类）
    "knowledge.repository",
    "knowledge.story_group",  # 2026-11-04 补登记：P0 事件层聚类算子
    "knowledge.story_query",  # 2026-11-04 补登记：P0 事件层只读消费出口
    "knowledge.story_recall",  # 2026-11-04 补登记：P1-C 跨窗口分裂探针纯函数
    "collectors.browser_mapper",  # 2026-10-20 补登记：commit 0190195f 收编的采集器模块，文件在盘
    "collectors.browser_visible_poc",  # 2026-10-20 补登记：同上，浏览器可见性 PoC 采集器
    "collectors.platform_adapter",  # 2026-10-20 补登记：同上，平台适配采集器
    "collectors.ports",
    "hermes_adapter.dto",
    "hermes_adapter.ports",
    "hermes_adapter.service",
    "integration.hermes_contract",
    "integration.hermes_api",
    "queue_link.api",
    "queue_link.import_api",  # 2026-10-20 补登记：commit 67039626 统一采集工作台的导入 API
    "queue_link.models",
    "queue_link.repository",
    "resdl_adapter.adapter",  # 2026-10-20 补登记：resdl 适配器包（文件在盘，期望集此前漏登记）
    "resdl_adapter.completion",
    "resdl_adapter.dto",
    "resdl_adapter.security",
    "scripts.backfill_entity_alias",  # 2026-11-04 补登记：P1-B 存量实体别名回填
    "scripts.borderline_review",  # 2026-11-04 补登记：P1 临界带人工确认 CLI
    "scripts.backfill_vectors",  # 2026-11-04 补登记：P0 存量向量回填入口
    "scripts.calibrate_borderline_margin",  # 2026-11-04 补登记：临界带宽标定（结论：0.05 最优）
    "scripts.calibrate_judge_prompt",  # 2026-11-04 补登记：判定提示词版本标定（v2 误合并归零）
    "scripts.make_calibration_db",  # 2026-11-05：克隆未分组库（带宽标定的前提）
    "scripts.cross_window_probe",  # 2026-11-05：跨窗口召回 A/B 探针（会清空一次性库）
    "scripts.story_round_log",  # 2026-11-05：轮次台账查询（list/show/check）
    "scripts.story_dashboard",  # 2026-11-05：事件层只读看板渲染（单文件 HTML 快照）
    "scripts.story_release_preflight",  # 2026-11-05：上线前自检（备份/标定新鲜度/口径/积压）
    "scripts.calibrate_story_threshold",  # 2026-11-04 补登记：事件判定阈值标定工具
    "scripts.calibrate_time_weight",  # 2026-11-04 补登记：时间近邻加权标定（结论：无效）
    "scripts.eval_story_quality",  # 2026-11-04 补登记：按真值语料给聚类打分
    "scripts.init_db",
    "scripts.launch_authorized_browser",
    "scripts.poc_manual_test",
    "scripts.story_group_run",  # 2026-11-04 补登记：P0 聚类算子执行入口
    "scripts.story_split_probe",  # 2026-11-04 补登记：P1-C 跨窗口分裂测量探针
    "scripts.seed_verify_corpus",  # 2026-11-04 补登记：P1 验收语料灌入（带真值）
    "store.models",
    "store.repository",
    "store.service",
    "workers.composition",  # 2026-10-20 补登记：commit 0190195f 收编的合成 worker
    "workers.run",
    "test_feishu_gate",
]


class TestImportLevel:
    """项目模块逐一真实 import。"""

    @pytest.mark.parametrize("module_name", ALL_MODULES)
    def test_module_imports(self, module_name: str):
        """单模块 import 成功即通过（ImportError 会直接抛出）。"""
        mod = importlib.import_module(module_name)
        assert mod is not None

    def test_module_count_matches_expectation(self):
        """ALL_MODULES 覆盖项目除 __init__/tests 外全部 .py。"""
        py_files = sorted(
            p.relative_to(PROJECT_ROOT).with_suffix("").as_posix().replace("/", ".")
            for p in PROJECT_ROOT.rglob("*.py")
            if p.name != "__init__.py"
            and "tests" not in p.parts
            and "__pycache__" not in p.parts
        )
        assert set(py_files) == set(ALL_MODULES), (
            f"项目文件与用例清单不一致：新增={set(py_files) - set(ALL_MODULES)} "
            f"缺失={set(ALL_MODULES) - set(py_files)}"
        )

    def test_heavy_deps_not_imported(self):
        """Import pipeline in a fresh interpreter without loading heavy dependencies.

        Checking the current pytest process is order-sensitive: another test may have
        legitimately imported OpenCV/FunASR before this assertion runs.  A clean child
        interpreter verifies the delayed-import contract independently of test order.
        """
        script = (
            "import json, sys; "
            "import extraction.pipeline; "
            "banned = ('funasr', 'torch', 'paddleocr', 'paddlepaddle', 'cv2', 'numpy'); "
            "print(json.dumps(sorted(name for name in banned if name in sys.modules)))"
        )
        env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(PROJECT_ROOT),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        loaded = json.loads(result.stdout)
        assert loaded == [], f"pipeline 提前导入了重依赖：{loaded}"
