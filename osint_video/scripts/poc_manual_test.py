"""PoC 手工验证脚本：mp4 目录 → 抽取管线 → 质量报表 Markdown（T03 验收工具）。

用法::

    python scripts/poc_manual_test.py <mp4目录> [--out 报告.md] [--force] [--no-asr] [--no-ocr]

行为：
- 递归扫描目录下 ``*.mp4``（PoC 样本即官方 res-downloader.exe 手工下载件）；
- 逐个跑 ``extraction.pipeline.run()``（检查点断点续跑，``--force`` 全量重跑）；
- 逐视频记录 ASR/OCR 段数、平均置信度、覆盖率、总时长、降级与失败明细；
- 汇总均值 + 失败清单，输出 Markdown 报表（默认 ``data/work/poc_report_<时间戳>.md``）。

退出码：0=全部成功；1=存在失败视频；2=目录无 mp4。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# 让脚本在任意 cwd 下都能以包内绝对导入运行（python scripts/poc_manual_test.py）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.logging_setup import setup_logging  # noqa: E402
from common.paths import DATA_WORK, ensure_dirs  # noqa: E402
from extraction.pipeline import ExtractionError, run  # noqa: E402


def _fmt_pct(value: float | None) -> str:
    """0.7234 → 72.3%（None/负值 → "-"）。"""
    if value is None or value < 0:
        return "-"
    return f"{value * 100:.1f}%"


def _scan_videos(root: Path) -> list[Path]:
    """递归收集 mp4 样本（按文件名排序保证报表稳定）。"""
    return sorted(root.rglob("*.mp4"))


def process_directory(
    video_dir: Path,
    *,
    force: bool = False,
    enable_asr: bool = True,
    enable_ocr: bool = True,
) -> list[dict[str, Any]]:
    """逐视频跑管线，收集结果记录（失败不中断，计入失败清单）。"""
    records: list[dict[str, Any]] = []
    videos = _scan_videos(video_dir)
    for idx, video in enumerate(videos, start=1):
        video_id = video.stem  # PoC：文件名去扩展名作 video_id
        print(f"[{idx}/{len(videos)}] 处理 {video.name}（video_id={video_id}）…")
        record: dict[str, Any] = {
            "file": video.name,
            "video_id": video_id,
            "ok": False,
            "error": None,
            "duration": 0.0,
            "asr_segments": 0,
            "ocr_items": 0,
            "aligned": 0,
            "asr_avg_conf": None,
            "ocr_avg_conf": None,
            "asr_coverage": None,
            "ocr_coverage": None,
            "degraded": [],
            "needs_review": 0,
        }
        try:
            doc = run(
                video, video_id,
                force=force, enable_asr=enable_asr, enable_ocr=enable_ocr,
            )
            metrics: dict[str, Any] = doc.get("metrics", {})
            record.update(
                ok=True,
                duration=float(doc.get("duration", 0.0) or 0.0),
                asr_segments=int(metrics.get("asr_segments_count", 0) or 0),
                ocr_items=int(metrics.get("ocr_items_count", 0) or 0),
                aligned=int(metrics.get("aligned_count", 0) or 0),
                asr_avg_conf=metrics.get("asr_avg_conf"),
                ocr_avg_conf=metrics.get("ocr_avg_conf"),
                asr_coverage=metrics.get("asr_coverage"),
                ocr_coverage=metrics.get("ocr_coverage"),
                degraded=metrics.get("degraded", []),
                needs_review=sum(
                    1 for s in doc.get("aligned_segments", []) if s.get("needs_review")
                ),
            )
        except ExtractionError as exc:
            record["error"] = str(exc)
            print(f"    ✗ 失败：{exc}")
        except Exception as exc:  # noqa: BLE001 —— 未预期异常也计入失败清单，不中断批次
            record["error"] = f"未预期异常：{type(exc).__name__}: {exc}"
            print(f"    ✗ 未预期异常：{exc}")
        else:
            print(
                f"    ✓ ASR {record['asr_segments']} 段 / OCR {record['ocr_items']} 条 / "
                f"对齐 {record['aligned']} 段 / 时长 {record['duration']:.1f}s"
            )
        records.append(record)
    return records


def build_report(records: list[dict[str, Any]]) -> str:
    """汇总结果 → Markdown 质量报表。"""
    ok_records = [r for r in records if r["ok"]]
    fail_records = [r for r in records if not r["ok"]]
    n = max(len(ok_records), 1)

    def _avg(key: str) -> float:
        vals = [r[key] for r in ok_records if isinstance(r.get(key), (int, float))]
        return sum(vals) / len(vals) if vals else 0.0

    lines: list[str] = []
    lines.append("# PoC 抽取质量报表（T03）")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 样本总数：{len(records)}（成功 {len(ok_records)} / 失败 {len(fail_records)}）")
    lines.append("")
    lines.append("## 汇总（成功样本均值）")
    lines.append("")
    lines.append("| 指标 | 均值 |")
    lines.append("|---|---|")
    lines.append(f"| 总时长（秒） | {_avg('duration'):.1f} |")
    lines.append(f"| ASR 段数 | {_avg('asr_segments'):.1f} |")
    lines.append(f"| OCR 条数 | {_avg('ocr_items'):.1f} |")
    lines.append(f"| 对齐段数 | {_avg('aligned'):.1f} |")
    lines.append(f"| ASR 平均置信度 | {_avg('asr_avg_conf'):.3f} |")
    lines.append(f"| OCR 平均置信度 | {_avg('ocr_avg_conf'):.3f} |")
    lines.append(f"| ASR 覆盖率 | {_fmt_pct(_avg('asr_coverage'))} |")
    lines.append(f"| OCR 覆盖率 | {_fmt_pct(_avg('ocr_coverage'))} |")
    lines.append(f"| 冲突待复核段（累计） | {sum(r['needs_review'] for r in ok_records)} |")
    lines.append("")
    lines.append("## 逐视频明细")
    lines.append("")
    lines.append(
        "| 文件 | 时长(s) | ASR段 | OCR条 | 对齐段 | ASR置信 | OCR置信 | "
        "ASR覆盖 | OCR覆盖 | 待复核 | 降级 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in records:
        if r["ok"]:
            degraded = ",".join(r["degraded"]) if r["degraded"] else "-"
            lines.append(
                f"| {r['file']} | {r['duration']:.1f} | {r['asr_segments']} | {r['ocr_items']} "
                f"| {r['aligned']} | {r['asr_avg_conf'] if r['asr_avg_conf'] is not None else '-'} "
                f"| {r['ocr_avg_conf'] if r['ocr_avg_conf'] is not None else '-'} "
                f"| {_fmt_pct(r['asr_coverage'])} | {_fmt_pct(r['ocr_coverage'])} "
                f"| {r['needs_review']} | {degraded} |"
            )
        else:
            lines.append(f"| {r['file']} | ✗ 失败 | - | - | - | - | - | - | - | - | - |")
    lines.append("")
    if fail_records:
        lines.append("## 失败清单")
        lines.append("")
        for r in fail_records:
            lines.append(f"- **{r['file']}**：{r['error']}")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "> 说明：ASR 置信度为占位值（SenseVoice-Small 不输出 token 置信度），"
        "质量结论请结合覆盖率与人工抽听 CER（验收线 CER≤8%）。"
        "中间产物与 StructuredDoc 见 data/work/{video_id}/。"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PoC 手工验证：mp4 目录 → 抽取管线 → 质量报表 Markdown"
    )
    parser.add_argument("video_dir", help="mp4 样本目录（递归扫描 *.mp4）")
    parser.add_argument("--out", default=None, help="报表输出路径（默认 data/work/poc_report_<时间戳>.md）")
    parser.add_argument("--force", action="store_true", help="忽略检查点强制全量重跑")
    parser.add_argument("--no-asr", action="store_true", help="禁用 ASR 通道（仅 OCR 验证）")
    parser.add_argument("--no-ocr", action="store_true", help="禁用 OCR 通道（仅 ASR 验证）")
    args = parser.parse_args()

    setup_logging()
    ensure_dirs()
    video_dir = Path(args.video_dir)
    if not video_dir.is_dir():
        print(f"[error] 目录不存在：{video_dir}")
        return 2
    videos = _scan_videos(video_dir)
    if not videos:
        print(f"[error] 目录下未找到 mp4：{video_dir}")
        return 2
    print(f"发现 {len(videos)} 个 mp4 样本，开始抽取…\n")

    records = process_directory(
        video_dir, force=args.force,
        enable_asr=not args.no_asr, enable_ocr=not args.no_ocr,
    )
    report = build_report(records)
    out_path = (
        Path(args.out)
        if args.out
        else DATA_WORK / f"poc_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    ok_count = sum(1 for r in records if r["ok"])
    fail_count = len(records) - ok_count
    print(f"\n完成：成功 {ok_count} / 失败 {fail_count}")
    print(f"报表：{out_path}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
