import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from audit_p1_observability import audit


def test_audit_separates_wrapper_coverage_market_and_hire_evidence(tmp_path):
    (tmp_path / "news_collector").mkdir()
    (tmp_path / "quant_backtest").mkdir()
    (tmp_path / "portal").mkdir()
    (tmp_path / "k_stock" / "paper").mkdir(parents=True)
    (tmp_path / "news_collector" / "alert_fail_wrap.py").write_text(
        "task_rc log_path result_summary exception_class", encoding="utf-8")
    (tmp_path / "quant_backtest" / "start_old.ps1").write_text(
        "python alert_fail.py old 1 C:/Temp/old.log", encoding="utf-8")
    (tmp_path / "news_collector" / "start_new.ps1").write_text(
        "python alert_fail_wrap.py new 1 C:/Temp/new.log", encoding="utf-8")
    (tmp_path / "portal" / "portal_api.py").write_text(
        'path == "/api/market_performance"', encoding="utf-8")
    (tmp_path / "portal" / "market.html").write_text(
        "/api/market_performance", encoding="utf-8")
    (tmp_path / "portal" / "nginx.conf").write_text(
        "location = /market", encoding="utf-8")
    (tmp_path / "k_stock" / "paper" / "hires.json").write_text(
        json.dumps({"hires": []}), encoding="utf-8")
    (tmp_path / "k_stock" / "README.md").write_text(
        "07:05 58dd6940", encoding="utf-8")

    report = audit(tmp_path)

    assert report["alert_fail"]["direct_file_count"] == 1
    assert report["alert_fail"]["wrapper_file_count"] == 1
    assert report["alert_fail"]["wrapper_schema_complete"] is True
    assert report["market"]["route_wired"] is True
    assert report["market"]["ledger_present"] is False
    assert report["scheduler"]["configured"] is True
    assert report["scheduler"]["actual_execution_evidence"] is False
    assert report["employment"]["active_sources"] == []
    assert report["employment"]["three_source_skip_verified"] is False


def test_alert_coverage_ignores_comments_and_counts_real_python_calls(tmp_path):
    (tmp_path / "news_collector").mkdir()
    (tmp_path / "quant_backtest").mkdir()
    (tmp_path / "news_collector" / "alert_fail_wrap.py").write_text(
        "task_rc log_path result_summary exception_class", encoding="utf-8")
    (tmp_path / "quant_backtest" / "comment_only.py").write_text(
        '# alert_fail.py is mentioned in a comment\n'
        '"""alert_fail.py is mentioned in a docstring."""\n',
        encoding="utf-8")
    (tmp_path / "quant_backtest" / "real_call.py").write_text(
        'import subprocess\n'
        'subprocess.run(["python", "alert_fail.py", "task", "1"])\n',
        encoding="utf-8")
    (tmp_path / "quant_backtest" / "wrapper_call.py").write_text(
        'subprocess.run(["python", "alert_fail_wrap.py", "task", "1"])\n',
        encoding="utf-8")

    report = audit(tmp_path)

    assert report["alert_fail"]["direct_files"] == [
        "quant_backtest/real_call.py"
    ]
    assert report["alert_fail"]["direct_file_count"] == 1
    assert report["alert_fail"]["wrapper_files"] == [
        "quant_backtest/wrapper_call.py"
    ]
