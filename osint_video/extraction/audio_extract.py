"""ffmpeg 音频抽取与视频探测（架构 §2.4 步骤 1）。

- :func:`extract_wav16k`：``ffmpeg -vn -ar 16000 -ac 1`` → 16k 单声道 wav（ASR 输入）；
- :func:`probe_duration_sec`：ffprobe 探测视频时长（秒，抽取覆盖率的分母）；
- ffmpeg/ffprobe 经 subprocess 调用，宿主机缺失时抛
  :class:`FFmpegNotFoundError`，错误信息自带安装指引。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

#: ffmpeg 安装指引（Windows 宿主机）
FFMPEG_INSTALL_HINT = (
    "未找到可执行文件 {name}。请先安装 ffmpeg ≥ 6.0 并加入 PATH：\n"
    "  Windows: winget install -e --id Gyan.FFmpeg（安装后重开终端）\n"
    "  Debian/Ubuntu: apt install -y ffmpeg\n"
    "  macOS: brew install ffmpeg\n"
    "验证：ffmpeg -version"
)


class FFmpegNotFoundError(RuntimeError):
    """宿主机缺少 ffmpeg/ffprobe（属环境硬错误，不做降级）。"""


def _require_tool(name: str) -> str:
    """定位系统可执行文件；缺失时抛带安装指引的 FFmpegNotFoundError。"""
    path = shutil.which(name)
    if not path:
        raise FFmpegNotFoundError(FFMPEG_INSTALL_HINT.format(name=name))
    return path


def _run(cmd: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    """执行子进程（文本输出）；非 0 退出码时抛 RuntimeError 并附 stderr 摘要。"""
    logger.debug("执行命令：%s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        stderr_tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise RuntimeError(
            f"命令执行失败（exit={proc.returncode}）：{' '.join(cmd[:3])}…\n{stderr_tail}"
        )
    return proc


def probe_duration_sec(video_path: str | Path) -> float:
    """ffprobe 探测视频时长（秒）。

    Args:
        video_path: 视频文件路径。

    Returns:
        时长秒数（float）；无时长流的异常媒体返回 0.0。
    """
    ffprobe = _require_tool("ffprobe")
    path = Path(video_path)
    if not path.exists():
        raise FileNotFoundError(f"视频文件不存在：{path}")
    proc = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    try:
        info = json.loads(proc.stdout or "{}")
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("无法解析时长（%s）：返回 0.0", exc)
        return 0.0


def extract_wav16k(video_path: str | Path, wav_path: str | Path) -> Path:
    """抽取 16kHz 单声道 wav（SenseVoice 要求的输入规格）。

    ffmpeg 参数：``-vn``（丢弃视频流）``-ar 16000``（采样率）``-ac 1``（单声道）。

    Args:
        video_path: 输入视频（mp4 等 ffmpeg 可解容器）。
        wav_path: 输出 wav 路径（父目录自动创建，已存在则覆盖）。

    Returns:
        wav 文件 Path。
    """
    ffmpeg = _require_tool("ffmpeg")
    src = Path(video_path)
    dst = Path(wav_path)
    if not src.exists():
        raise FileNotFoundError(f"视频文件不存在：{src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [ffmpeg, "-y", "-i", str(src), "-vn", "-ar", "16000", "-ac", "1", str(dst)],
        timeout=1800,  # 长视频兜底 30 分钟
    )
    if not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg 未产出有效 wav：{dst}")
    logger.info("音频抽取完成：%s（%.1f KB）", dst.name, dst.stat().st_size / 1024)
    return dst
