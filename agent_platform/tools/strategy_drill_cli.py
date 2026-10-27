# -*- coding: utf-8 -*-
"""开发者可执行的独立 SHADOW 策略验证 CLI。"""

from dataclasses import asdict, is_dataclass
import argparse
import json
from collections.abc import Mapping
from pathlib import Path
import sys


ENGINE = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

try:  # Windows 管道输出统一 UTF-8（CLI 契约测试解码防 cp936 失败）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from shadow_strategy_runner import SHADOW_MODE, run_shadow_validation  # noqa: E402


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _parser():
    parser = argparse.ArgumentParser(
        description="运行一次只读 SHADOW 策略验证，不执行交易。"
    )
    parser.add_argument("--manifest", required=True, help="策略清单 JSON 路径")
    parser.add_argument("--config", required=True, help="策略配置 JSON 路径")
    parser.add_argument("--root", required=True, help="显式隔离根路径")
    parser.add_argument("--context-json", required=True, help="内联 JSON 上下文")
    parser.add_argument("--evidence-path", help="可选的 JSONL 证据路径")
    return parser


def _context(value):
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("--context-json 必须是有效 JSON: %s" % exc) from exc


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        result = run_shadow_validation(
            args.manifest,
            args.config,
            args.root,
            _context(args.context_json),
            SHADOW_MODE,
            evidence_path=args.evidence_path,
        )
        sys.stdout.write(json.dumps(_jsonable(result), ensure_ascii=False) + "\n")
        return 0
    except Exception as exc:  # CLI 边界：把验证失败转换为可读错误
        print("ERROR: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
