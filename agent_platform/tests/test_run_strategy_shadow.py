# -*- coding: utf-8 -*-
"""strategy_drill_cli CLI 契约测试（原名 run_strategy_shadow.py，拍板 02 更名去混淆）。"""
import json
from pathlib import Path
import subprocess
import sys

PLATFORM = Path(__file__).resolve().parent.parent
CLI = PLATFORM / "tools" / "strategy_drill_cli.py"
ENGINE = PLATFORM / "engine"
sys.path.insert(0, str(CLI.parent))


BASE_CONFIG = {
    "version": "1.0.0",
    "allowed_data": ["bars"],
    "resource_budget": {
        "timeout_s": 1.0,
        "max_concurrency": 1,
        "max_context_bytes": 4096,
    },
    "allowed_modes": ["SHADOW"],
    "permissions": ["propose", "read_context"],
}


def _write_inputs(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({
            "strategies": [{
                "module": "shadow_strategies",
                "class": "TrendFollowAgent",
                "strategy_id": "shadow_trend",
            }]
        }),
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    item = dict(BASE_CONFIG)
    item["strategy_id"] = "shadow_trend"
    config.write_text(json.dumps({"strategies": [item]}), encoding="utf-8")
    return manifest, config


def _run(tmp_path, *extra):
    manifest, config = _write_inputs(tmp_path)
    return subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--manifest", str(manifest),
            "--config", str(config),
            "--root", str(tmp_path / "root"),
            "--context-json", '{"symbol":"600519","bars":[100,101]}',
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_cli_success_outputs_json_and_no_execution(tmp_path):
    completed = _run(tmp_path)

    assert completed.returncode == 0
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert result["mode"] == "SHADOW"
    assert result["no_execution"] is True
    assert result["execution_allowed"] is False
    assert result["strategy_results"][0]["status"] == "success"


def test_cli_missing_required_argument_fails_readably(tmp_path):
    manifest, config = _write_inputs(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--manifest", str(manifest),
            "--config", str(config),
            "--root", str(tmp_path / "root"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode != 0
    assert "--context-json" in completed.stderr


def test_cli_rejects_live_without_running_validation(tmp_path):
    manifest, config = _write_inputs(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--manifest", str(manifest),
            "--config", str(config),
            "--root", str(tmp_path / "root"),
            "--context-json", '{"symbol":"600519","bars":[100,101]}',
            "--trade-mode", "LIVE",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode != 0
    assert "unrecognized arguments: --trade-mode" in completed.stderr


def test_cli_result_contains_no_execution_field(tmp_path):
    result = json.loads(_run(tmp_path).stdout)

    assert result["no_execution"] is True
    assert result["execution_allowed"] is False
    assert "execute" not in result
    assert "order" not in result
