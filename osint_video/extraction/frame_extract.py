"""视频抽帧 + CLAHE 增强 + 相邻帧差异去重（架构 §2.4 步骤 3）。

流程（ffmpeg 负责抽帧，OpenCV 负责增强与去重判断）：
1. ``ffmpeg -vf fps=1,format=gray`` → 1fps 灰度帧序列（灰度足够 OCR 与差异判断）；
2. OpenCV 逐帧 CLAHE（限制对比度自适应直方图均衡，提升暗场字幕可读性）；
3. 与上一保留帧做平均绝对差（MAD），低于阈值判为重复帧丢弃（静态画面），
   避免同屏字幕在 OCR 阶段产生冗余条目。

OpenCV（opencv-python-headless + numpy）延迟导入：仅本模块使用。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

#: 帧文件命名（ffmpeg %05d 序号）
_FRAME_NAME_RE = re.compile(r"^frame_(\d{5})\.jpg$")

#: opencv 安装指引
OPENCV_INSTALL_HINT = (
    "opencv 未安装。帧增强/去重需要：pip install opencv-python-headless numpy\n"
    "（headless 版无 GUI 依赖，适配服务器环境）"
)


class FrameExtractError(RuntimeError):
    """抽帧失败（ffmpeg 错误 / OpenCV 缺失 / 视频损坏）。"""


class FrameInfo(TypedDict):
    """保留帧的索引条目（OCR 通道输入单元）。"""

    index: int      # 保留帧序号（从 1 起）
    ts_sec: float   # 帧在原视频中的时间戳（秒）
    path: str       # 增强后帧文件绝对路径


def _run_ffmpeg_extract(video_path: Path, raw_dir: Path, fps: float) -> None:
    """ffmpeg 抽原始灰度帧到 raw_dir/frame_00001.jpg …（内部中间目录）。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FrameExtractError(
            "未找到可执行文件 ffmpeg。请安装（winget install -e --id Gyan.FFmpeg）后重试。"
        )
    raw_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-i", str(video_path),
        "-vf", f"fps={fps},format=gray",
        "-q:v", "2",  # JPEG 质量（2=高质量）
        str(raw_dir / "frame_%05d.jpg"),
    ]
    logger.debug("抽帧命令：%s", " ".join(cmd))
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600
    )
    if proc.returncode != 0:
        raise FrameExtractError(
            f"ffmpeg 抽帧失败（exit={proc.returncode}）：{(proc.stderr or '')[-800:]}"
        )


def extract_frames(
    video_path: str | Path,
    frames_dir: str | Path,
    fps: float = 1.0,
    diff_threshold: float = 0.02,
    clip_limit: float = 2.0,
    tile_grid_size: tuple[int, int] = (8, 8),
) -> list[FrameInfo]:
    """抽帧主入口：1fps → CLAHE 增强 → 相邻差异去重。

    Args:
        video_path: 输入视频。
        frames_dir: 增强帧输出目录（``<frames_dir>/enhanced/``），
                    原始灰度帧在 ``<frames_dir>/raw/``（去重后自动清理）。
        fps: 抽帧率（默认 1fps，字幕场景足够）。
        diff_threshold: 帧去重 MAD 阈值（0~1，默认 0.02≈5 灰度级；越大去重越狠）。
        clip_limit: CLAHE 对比度限制（默认 2.0）。
        tile_grid_size: CLAHE 分块网格（默认 8×8）。

    Returns:
        保留帧索引列表（按时间升序）；首帧必然保留。视频无帧产出时抛错。

    Raises:
        FrameExtractError: ffmpeg 失败 / OpenCV 缺失 / 视频损坏（硬错误，不降级）。
    """
    src = Path(video_path)
    if not src.exists():
        raise FileNotFoundError(f"视频文件不存在：{src}")
    out_dir = Path(frames_dir)
    raw_dir = out_dir / "raw"
    enhanced_dir = out_dir / "enhanced"
    enhanced_dir.mkdir(parents=True, exist_ok=True)

    try:
        import cv2  # 延迟导入：仅本模块需要
        import numpy as np  # noqa: F401 —— cv2 依赖 numpy，提前给出清晰报错
    except ImportError as exc:
        raise FrameExtractError(OPENCV_INSTALL_HINT) from exc

    _run_ffmpeg_extract(src, raw_dir, fps)

    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
    kept: list[FrameInfo] = []
    prev_raw: "np.ndarray | None" = None  # type: ignore[name-defined]

    frame_files = sorted(p for p in raw_dir.iterdir() if _FRAME_NAME_RE.match(p.name))
    if not frame_files:
        raise FrameExtractError(f"ffmpeg 未产出任何帧（视频损坏或无视频流）：{src.name}")

    for seq, raw_file in enumerate(frame_files, start=1):
        ts_sec = round((seq - 1) / fps, 3)
        raw = cv2.imread(str(raw_file), cv2.IMREAD_GRAYSCALE)
        if raw is None:
            logger.warning("帧读取失败，跳过：%s", raw_file.name)
            continue
        # 相邻帧差异去重：与上一保留帧的 MAD 低于阈值 → 静态画面，丢弃
        if prev_raw is not None and prev_raw.shape == raw.shape:
            diff = cv2.absdiff(prev_raw, raw)
            mad = float(cv2.mean(diff)[0]) / 255.0
            if mad < diff_threshold:
                continue
        enhanced = clahe.apply(raw)
        keep_index = len(kept) + 1
        out_path = enhanced_dir / f"frame_{keep_index:05d}.jpg"
        if not cv2.imwrite(str(out_path), enhanced):
            raise FrameExtractError(f"帧写出失败：{out_path}")
        kept.append(FrameInfo(index=keep_index, ts_sec=ts_sec, path=str(out_path)))
        prev_raw = raw  # 只更新"保留帧"作为对照，静态长场景可被持续折叠

    # 清理原始灰度帧（增强帧为准，节省磁盘）
    shutil.rmtree(raw_dir, ignore_errors=True)
    logger.info(
        "抽帧完成：%s → 保留 %d / %d 帧（MAD<%.3f 去重）",
        src.name, len(kept), len(frame_files), diff_threshold,
    )
    return kept
