"""端到端测试（第 3 层）：合成视频 → 抽取管线真实运行。

样本：项目内 ffmpeg（tools/ffmpeg-9.0.2-essentials_build）lavfi 合成——
- qa_plain.mp4：testsrc 10s + 440Hz 正弦音（无字幕）；
- qa_sub.mp4：testsrc2 12s + drawtext 白字黑底字幕 + 600Hz 正弦音。

验证（任务 #4 要求）：
1. ffmpeg/ffprobe 真实调用：时长探测、16k 单声道 wav 抽取、抽帧+CLAHE+去重；
2. ASR/OCR 模型缺失（funasr/paddle 未装）→ 管线**优雅降级**：
   单挂降级留痕、双挂抛 ExtractionError（含中文原因），绝不裸 ImportError 崩溃；
3. 检查点断点重跑：data/work/{video_id}/ 中间产物落盘、二次运行命中最终
   检查点直接返回缓存（通道不重跑）、force=True 强制重跑；
4. ASR/OCR 输出 mock 注入 → 全编排（probe→双通道→align→StructuredDoc）真实走通。
"""

from __future__ import annotations

import subprocess
import sys
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import FFMPEG_BIN_DIR, PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common.paths import DATA_WORK  # noqa: E402
from extraction.asr_sensevoice import ASRModelUnavailableError, transcribe  # noqa: E402
from extraction.audio_extract import extract_wav16k, probe_duration_sec  # noqa: E402
from extraction.frame_extract import extract_frames  # noqa: E402
from extraction.ocr_paddle import OCREngineUnavailableError, _create_engine  # noqa: E402
from extraction.pipeline import ExtractionError, run  # noqa: E402

_FFMPEG = FFMPEG_BIN_DIR / "ffmpeg.exe"

pytestmark = pytest.mark.skipif(
    not _FFMPEG.exists(), reason=f"项目内 ffmpeg 不存在：{_FFMPEG}"
)

#: mock 通道输出（双通道一致文本 → 对齐后 source="asr+ocr" conf=0.83）
FAKE_ASR = [{"start": 0.0, "end": 6.0, "text": "质量验证占位语音段", "conf": 0.8}]
FAKE_OCR = [{"start": 0.5, "end": 6.5, "text": "质量验证占位语音段", "conf": 0.9}]


def _uid(prefix: str) -> str:
    """唯一 video_id（避免 QA 数据目录下检查点串扰）。"""
    return f"qa_{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def synth_videos(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """用项目内 ffmpeg 合成两个测试视频（模块级复用，约 3s）。"""
    out = tmp_path_factory.mktemp("synth")
    plain = out / "qa_plain.mp4"
    sub = out / "qa_sub.mp4"

    subprocess.run(
        [
            str(_FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=10:size=320x180:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(plain),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(_FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=duration=12:size=320x180:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=600:duration=12",
            "-vf",
            "drawtext=text='Hello OSINT QA':fontcolor=white:fontsize=32:"
            "x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.7",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(sub),
        ],
        check=True,
    )
    assert plain.stat().st_size > 0 and sub.stat().st_size > 0
    return {"plain": plain, "sub": sub}


# ---------------------------------------------------------------------------
# 1. ffmpeg 真实调用
# ---------------------------------------------------------------------------
class TestFfmpegCalls:
    def test_probe_duration(self, synth_videos):
        dur = probe_duration_sec(synth_videos["sub"])
        assert 11.5 <= dur <= 12.5

    def test_extract_wav16k_spec(self, synth_videos, tmp_path):
        """wav 必须是 16kHz 单声道（SenseVoice 输入规格）。"""
        wav = tmp_path / "qa.wav"
        ret = extract_wav16k(synth_videos["sub"], wav)
        assert ret == wav
        with wave.open(str(wav), "rb") as w:
            assert w.getnchannels() == 1
            assert w.getframerate() == 16000
            assert 11.5 <= w.getnframes() / w.getframerate() <= 12.5

    def test_extract_frames_real(self, synth_videos, tmp_path):
        """抽帧→CLAHE→去重真实执行：帧文件落盘、索引时间戳递增。"""
        frames = extract_frames(synth_videos["sub"], tmp_path / "frames")
        assert len(frames) >= 5  # 12s testsrc2 每秒画面都在变，至少保留大部分
        ts_list = [f["ts_sec"] for f in frames]
        assert ts_list == sorted(ts_list)
        for f in frames:
            assert Path(f["path"]).exists()
        assert (tmp_path / "frames" / "raw").exists() is False  # 原始帧已清理


# ---------------------------------------------------------------------------
# 2. 模型缺失的降级语义（延迟导入设计验证）
# ---------------------------------------------------------------------------
@pytest.fixture
def missing_model_dependencies(monkeypatch: pytest.MonkeyPatch):
    """Make optional ASR/OCR imports fail without changing the environment.

    The media test environment may have FunASR/PaddleOCR installed.  Injecting a
    temporary ``None`` module entry exercises each production ImportError handler
    deterministically, without uninstalling packages or downloading models.
    """
    monkeypatch.setitem(sys.modules, "funasr", None)
    monkeypatch.setitem(sys.modules, "paddleocr", None)


class TestModelUnavailableDegrade:
    def test_asr_missing_raises_clear_error(self, synth_videos, tmp_path, missing_model_dependencies):
        """Missing funasr is translated to ASRModelUnavailableError, not ImportError."""
        wav = extract_wav16k(synth_videos["plain"], tmp_path / "a.wav")
        with pytest.raises(ASRModelUnavailableError, match=r"funasr|FunASR"):
            transcribe(wav)

    def test_ocr_missing_raises_clear_error(self, missing_model_dependencies):
        """Missing paddleocr is translated to OCREngineUnavailableError, not ImportError."""
        with pytest.raises(OCREngineUnavailableError, match=r"[Pp]addleOCR"):
            _create_engine()

    def test_pipeline_double_missing_extraction_error(self, synth_videos, missing_model_dependencies):
        """双通道模型全缺 → ExtractionError（双通道均无文字产出），进程不崩。"""
        vid = _uid("degrade")
        with pytest.raises(ExtractionError, match=r"双通道均无文字产出"):
            run(synth_videos["sub"], vid, enable_asr=True, enable_ocr=True)

    def test_checkpoints_survive_failure(self, synth_videos, missing_model_dependencies):
        """失败运行的中间检查点（wav/帧索引）留存 → 修复模型后可断点续跑。"""
        vid = _uid("ckpt_fail")
        with pytest.raises(ExtractionError):
            run(synth_videos["sub"], vid, enable_asr=True, enable_ocr=True)
        work = DATA_WORK / vid
        assert (work / "audio.wav").exists()
        assert (work / "frame_index.json").exists()
        assert (work / "frames" / "enhanced").is_dir()

    def test_missing_video_file(self):
        with pytest.raises(ExtractionError, match=r"不存在"):
            run("Z:/definitely/not/exist.mp4", _uid("nofile"))


# ---------------------------------------------------------------------------
# 3. mock 通道输出 → 全编排真实走通 + StructuredDoc 结构
# ---------------------------------------------------------------------------
class TestFullOrchestration:
    def test_run_success_with_mocked_channels(self, synth_videos):
        vid = _uid("full")
        with patch("extraction.asr_sensevoice.transcribe", return_value=FAKE_ASR), \
             patch("extraction.ocr_paddle.recognize", return_value=FAKE_OCR):
            doc = run(synth_videos["sub"], vid, source_url="https://sph.example/x",
                      file_md5="d" * 32)

        assert doc["video_id"] == vid
        assert doc["schema_version"] == "1.0"
        assert 11.5 <= doc["duration"] <= 12.5
        assert doc["source"]["route"] == "MANUAL"
        assert doc["source"]["sph_url"] == "https://sph.example/x"
        assert doc["file_md5"] == "d" * 32
        # 对齐段：一致段 asr+ocr，conf=0.8*0.7+0.9*0.3=0.83
        assert len(doc["aligned_segments"]) == 1
        seg = doc["aligned_segments"][0]
        assert seg["source"] == "asr+ocr"
        assert seg["confidence"] == pytest.approx(0.83)
        assert seg["needs_review"] is False
        # 指标
        m = doc["metrics"]
        assert m["asr_segments_count"] == 1 and m["ocr_items_count"] == 1
        assert m["aligned_count"] == 1
        assert m["asr_coverage"] == pytest.approx(round(6.0 / 12.0, 4))
        # 占位结构（T05 回填）
        assert doc["entities"] == [] and doc["topics"] == []
        assert doc["compliance"]["internal_use_only"] is True
        # 落盘
        assert (DATA_WORK / vid / "structured_doc.json").exists()

    def test_checkpoint_reuse_skips_channels(self, synth_videos):
        """二次运行命中 structured_doc.json 最终检查点：通道不重跑、产物 mtime 不变。"""
        vid = _uid("reuse")
        with patch("extraction.asr_sensevoice.transcribe", return_value=FAKE_ASR), \
             patch("extraction.ocr_paddle.recognize", return_value=FAKE_OCR):
            doc1 = run(synth_videos["sub"], vid)
            wav_before = (DATA_WORK / vid / "audio.wav").stat().st_mtime_ns
            doc_before = (DATA_WORK / vid / "structured_doc.json").stat().st_mtime_ns

            # 通道函数换成炸弹：若检查点失效被调用 → 立即失败
            bomb_asr = patch(
                "extraction.asr_sensevoice.transcribe",
                side_effect=AssertionError("ASR 不应重跑"),
            )
            bomb_ocr = patch(
                "extraction.ocr_paddle.recognize",
                side_effect=AssertionError("OCR 不应重跑"),
            )
            with bomb_asr, bomb_ocr:
                doc2 = run(synth_videos["sub"], vid)

        assert doc2 == doc1  # 直接返回缓存文档
        assert (DATA_WORK / vid / "audio.wav").stat().st_mtime_ns == wav_before
        assert (DATA_WORK / vid / "structured_doc.json").stat().st_mtime_ns == doc_before

    def test_force_rerun_ignores_checkpoints(self, synth_videos):
        """force=True：忽略检查点重跑，最终文档重写。"""
        vid = _uid("force")
        with patch("extraction.asr_sensevoice.transcribe", return_value=FAKE_ASR), \
             patch("extraction.ocr_paddle.recognize", return_value=FAKE_OCR):
            run(synth_videos["sub"], vid)
            before = (DATA_WORK / vid / "structured_doc.json").stat().st_mtime_ns
            doc = run(synth_videos["sub"], vid, force=True)
        assert doc["video_id"] == vid
        assert (DATA_WORK / vid / "structured_doc.json").stat().st_mtime_ns != before

    def test_ocr_only_mode(self, synth_videos):
        """enable_asr=False：仅 OCR 通道，降级痕迹 asr_disabled 留存于 metrics。"""
        vid = _uid("ocronly")
        with patch("extraction.ocr_paddle.recognize", return_value=FAKE_OCR):
            doc = run(synth_videos["sub"], vid, enable_asr=False, enable_ocr=True)
        assert doc["metrics"]["degraded"] == ["asr_disabled"]
        assert doc["aligned_segments"][0]["source"] == "ocr"

    def test_both_channels_disabled_is_hard_error(self, synth_videos):
        """双通道显式关闭 → 无文字产出 → ExtractionError。"""
        with pytest.raises(ExtractionError, match=r"双通道"):
            run(synth_videos["sub"], _uid("bothoff"), enable_asr=False, enable_ocr=False)
