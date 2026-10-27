# -*- coding: utf-8 -*-
"""chan_monitor 适配器测试：mock 信号池 JSONL 文件测转换逻辑。"""

import json

import pytest

from strategy_layer.adapters.chan_monitor import ChanMonitorAdapter


def _write_pool(tmp_path, name, rows):
    """写一个假信号池 JSONL（可混入坏行）。"""
    p = tmp_path / name
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(p)


def _row(code="000338.SZ", sig_type="2买", dt="2026-10-15", **over):
    row = {
        "market": "CN",
        "code": code,
        "name": "潍柴动力",
        "period": "1d",
        "type": sig_type,
        "dt": dt,
        "price": 15.8,
        "detect_date": "2026-10-16",
        "lag": 1,
        "entry_px": 15.9,
        "mbei": None,
        "bei": None,
        "bei2": None,
        "vv": None,
        "reg": "up",
        "tbe": None,
        "trend": "up",
    }
    row.update(over)
    return row


def _adapter(pool_paths):
    return ChanMonitorAdapter(pool_globs=pool_paths)


class TestTypeMapping:
    @pytest.mark.parametrize(
        "sig_type,score,action",
        [
            ("1买", 95.0, "buy"),
            ("2买", 85.0, "buy"),
            ("3买", 75.0, "buy"),
            ("3卖", 25.0, "sell"),
            ("2卖", 15.0, "sell"),
            ("1卖", 5.0, "sell"),
        ],
    )
    def test_base_score_and_action(self, tmp_path, sig_type, score, action):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row(sig_type=sig_type)])
        sigs = _adapter([pool]).score("20261016")
        assert len(sigs) == 1
        assert sigs[0].score == score
        assert sigs[0].action == action
        assert sigs[0].strategy == "chan_monitor"
        assert sigs[0].trade_date == "20261016"
        assert sigs[0].symbol == "000338.SZ"

    def test_score_capped_at_100(self, tmp_path):
        # 95 + 4×2 = 103 → 封顶 100
        pool = _write_pool(
            tmp_path,
            "pool.jsonl",
            [_row(sig_type="1买", mbei=True, bei=True, bei2=True, vv=True)],
        )
        assert _adapter([pool]).score("20261016")[0].score == 100.0


class TestResonance:
    def test_resonance_adds_score_and_confidence(self, tmp_path):
        pool = _write_pool(
            tmp_path,
            "pool.jsonl",
            [_row(sig_type="3买", bei=True, bei2=True, vv=True, mbei=None)],
        )
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 75.0 + 6.0
        assert s.confidence == 0.9  # 0.6 + 3×0.1

    def test_no_resonance_base_confidence(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row()])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 85.0
        assert s.confidence == 0.6


class TestDatePicking:
    def test_picks_latest_detect_batch(self, tmp_path):
        """Δ1：选日按 detect_date（最新扫描批次），与 picks 链口径一致。"""
        pool = _write_pool(
            tmp_path,
            "pool.jsonl",
            [
                _row(
                    code="A.SH",
                    dt="2026-10-10",
                    detect_date="2026-10-14",
                    sig_type="3买",
                ),
                _row(
                    code="B.SH",
                    dt="2026-10-15",
                    detect_date="2026-10-16",
                    sig_type="1买",
                ),
                _row(
                    code="C.SH",
                    dt="2026-10-15",
                    detect_date="2026-10-16",
                    sig_type="2卖",
                ),
            ],
        )
        sigs = {s.symbol: s for s in _adapter([pool]).score("20261016")}
        assert set(sigs) == {"B.SH", "C.SH"}  # 只取最新扫描批次当日

    def test_picks_by_detect_date_not_dt(self, tmp_path):
        """Δ1 核心回归：旧信号日 + 新扫描批次要入选（dt 口径会漏）。"""
        pool = _write_pool(
            tmp_path,
            "pool.jsonl",
            [
                _row(
                    code="A.SH",
                    dt="2026-10-08",
                    detect_date="2026-10-16",
                    sig_type="3买",
                ),
                _row(
                    code="B.SH",
                    dt="2026-10-09",
                    detect_date="2026-10-15",
                    sig_type="1买",
                ),
            ],
        )
        sigs = {s.symbol: s for s in _adapter([pool]).score("20261016")}
        assert set(sigs) == {"A.SH"}  # 取 10-16 扫描批次，非 10-09 信号日

    def test_exact_detect_date_match(self, tmp_path):
        pool = _write_pool(
            tmp_path,
            "pool.jsonl",
            [
                _row(code="A.SH", dt="2026-10-15", detect_date="2026-10-16"),
                _row(code="B.SH", dt="2026-10-15", detect_date="2026-10-15"),
            ],
        )
        sigs = {s.symbol: s for s in _adapter([pool]).score("20261016")}
        assert set(sigs) == {"A.SH"}  # detect_date 恰等于交易日上限

    def test_no_signal_before_trade_date(self, tmp_path, capsys):
        pool = _write_pool(
            tmp_path, "pool.jsonl", [_row(dt="2026-10-15", detect_date="2026-10-20")]
        )
        assert _adapter([pool]).score("20261016") == []
        assert "降级" in capsys.readouterr().out

    def test_multi_market_pools_merged(self, tmp_path):
        cn = _write_pool(
            tmp_path,
            "pool.jsonl",
            [_row(code="000338.SZ", dt="2026-10-15", sig_type="2买")],
        )
        us = _write_pool(
            tmp_path,
            "pool_us.jsonl",
            [
                {
                    "market": "US",
                    "code": "NVDA",
                    "name": "英伟达",
                    "type": "1买",
                    "dt": "2026-10-15",
                    "price": 180.5,
                    "detect_date": "2026-10-16",
                    "mbei": True,
                    "bei": None,
                    "bei2": None,
                    "vv": True,
                    "trend": "up",
                }
            ],
        )
        sigs = {s.symbol: s for s in _adapter([cn, us]).score("20261016")}
        assert set(sigs) == {"000338.SZ", "NVDA"}
        assert sigs["NVDA"].score == 95.0 + 4.0
        assert sigs["NVDA"].confidence == 0.8  # 0.6 + 2×0.1

    def test_per_market_latest_detect_us_newer_than_cn(self, tmp_path):
        """QA-20261017 回归（detect_date 口径）：美股池 detect_date 较新时
        不得遮蔽 A 股信号（分市场选日）。"""
        cn = _write_pool(
            tmp_path,
            "pool.jsonl",
            [
                _row(
                    code="000338.SZ",
                    dt="2026-09-25",
                    detect_date="2026-09-28",
                    sig_type="1买",
                ),
                _row(
                    code="000338.SZ",
                    dt="2026-09-30",
                    detect_date="2026-09-30",
                    sig_type="2买",
                ),
            ],
        )
        us = _write_pool(
            tmp_path,
            "pool_us.jsonl",
            [
                {
                    "market": "US",
                    "code": "NXPI",
                    "name": "NXP",
                    "type": "3卖",
                    "dt": "2026-10-02",
                    "price": 248.86,
                    "detect_date": "2026-10-02",
                    "mbei": None,
                    "bei": None,
                    "bei2": None,
                    "vv": None,
                    "trend": "up",
                }
            ],
        )
        sigs = {s.symbol: s for s in _adapter([cn, us]).score("20261017")}
        # 两市场各取组内最新 detect_date：CN 组取 09-30 批次（而非被 10-02 遮蔽）
        assert set(sigs) == {"000338.SZ", "NXPI"}
        assert sigs["000338.SZ"].score == 85.0  # 2买 基准分
        assert sigs["NXPI"].score == 25.0  # 3卖 基准分

    def test_market_without_recent_rows_returns_empty(self, tmp_path):
        """某市场无 detect_date≤trade_date 行时该组为空，不影响其他组。"""
        cn = _write_pool(
            tmp_path,
            "pool.jsonl",
            [_row(code="000338.SZ", dt="2026-10-15", sig_type="2买")],
        )
        us = _write_pool(
            tmp_path,
            "pool_us.jsonl",
            [
                {
                    "market": "US",
                    "code": "NXPI",
                    "name": "NXP",
                    "type": "3卖",
                    "dt": "2026-11-01",
                    "price": 248.86,
                    "detect_date": "2026-11-01",
                    "mbei": None,
                    "bei": None,
                    "bei2": None,
                    "vv": None,
                    "trend": "up",
                }
            ],
        )
        sigs = {s.symbol: s for s in _adapter([cn, us]).score("20261016")}
        assert set(sigs) == {"000338.SZ"}


class TestA4ReadOnlyEvidence:
    def test_same_direction_duplicates_keep_each_event_and_skip(self, tmp_path):
        from strategy_layer.a4_readonly_probe import inspect

        rows = [
            _row(sig_type="2买"),
            _row(sig_type="2买"),
            _row(sig_type="3买", dt="2026-10-14", bei=False),
            _row(sig_type="4买", dt="2026-10-13"),
        ]
        pool = _write_pool(tmp_path, "pool.jsonl", rows)
        result = inspect("20261016", [pool])
        symbol = "000338.SZ"
        detail = result["duplicate_evidence"][symbol]
        assert result["duplicate_symbols"] == {symbol: 3}
        assert result["contradictory_symbols"] == {}
        assert detail["selected_row_count"] == 4
        assert detail["signal_count"] == 3
        assert detail["event_counts"] == [
            {
                "type": "2买",
                "dt": "2026-10-15",
                "detect_date": "2026-10-16",
                "count": 2,
            },
            {
                "type": "3买",
                "dt": "2026-10-14",
                "detect_date": "2026-10-16",
                "count": 1,
            },
            {
                "type": "4买",
                "dt": "2026-10-13",
                "detect_date": "2026-10-16",
                "count": 1,
            },
        ]
        assert [r["selected_row_index"] for r in detail["rows"]] == [0, 1, 2, 3]
        assert [r["action"] for r in detail["rows"]] == ["buy", "buy", "buy", None]
        assert [r["score"] for r in detail["rows"]] == [85.0, 85.0, 60.0, None]
        assert "毒层降档" in detail["rows"][2]["rationale"]
        assert detail["rows"][3]["conversion"] == "SKIPPED"
        assert detail["rows"][3]["skip_reason"] == "UNKNOWN_TYPE_OR_INVALID_CODE"
        assert detail["contribution_keys"] == ["chan_monitor"]
        assert detail["contribution_overwritten"] is True
        assert detail["file_hash_conversion_snapshot"] == "SAME_BYTES_IN_PROBE_ONLY"
        assert result["input_snapshot_scope"] == "PROBE_ONLY_NOT_PRODUCTION"
        assert [r["source"]["line_number"] for r in detail["rows"]] == [1, 2, 3, 4]
        assert all(r["source"]["path"] == pool for r in detail["rows"])
        assert result["verdict"] == "BLOCK_CUTOVER"

    def test_opposite_actions_retain_source_event_and_conversion(self, tmp_path):
        from strategy_layer.a4_readonly_probe import inspect

        pool = _write_pool(
            tmp_path,
            "pool.jsonl",
            [
                _row(sig_type="1买", dt="2026-10-15"),
                _row(sig_type="3卖", dt="2026-10-14"),
            ],
        )
        result = inspect("20261016", [pool])
        detail = result["duplicate_evidence"]["000338.SZ"]
        assert [
            (r["type"], r["dt"], r["action"], r["score"]) for r in detail["rows"]
        ] == [
            ("1买", "2026-10-15", "buy", 95.0),
            ("3卖", "2026-10-14", "sell", 25.0),
        ]
        assert detail["selected_row_count"] == detail["signal_count"] == 2
        assert detail["contribution_overwritten"] is True
        assert result["contradictory_symbols"]["000338.SZ"]["divergence"] is True
        assert {"BUY_SELL_CONTRADICTION", "DUPLICATE_STRATEGY_SYMBOL"} <= set(
            result["issues"]
        )

    def test_multi_file_snapshot_deduplicates_globs_and_traces_lines(self, tmp_path):
        import hashlib
        from strategy_layer.a4_readonly_probe import inspect

        first = tmp_path / "a.jsonl"
        second = tmp_path / "b.jsonl"
        first.write_text(
            "{broken\n" + json.dumps(_row(sig_type="1买"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        second.write_text(
            json.dumps(_row(sig_type="3卖"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        result = inspect(
            "20261016", [str(second), str(tmp_path / "*.jsonl"), str(first)]
        )
        detail = result["duplicate_evidence"]["000338.SZ"]
        assert len(result["source_files"]) == 2
        assert [item["path"] for item in result["source_files"]] == [
            str(first),
            str(second),
        ]
        assert [
            (r["source"]["path"], r["source"]["line_number"]) for r in detail["rows"]
        ] == [(str(first), 2), (str(second), 1)]
        assert (
            detail["rows"][0]["source"]["sha256"]
            == hashlib.sha256(first.read_bytes()).hexdigest()
        )
        assert detail["signal_count"] == 2
        assert "SOURCE_MALFORMED_ROWS" in result["issues"]

    def test_malformed_source_lines_have_individual_file_origins(self, tmp_path):
        import hashlib
        from strategy_layer.a4_readonly_probe import inspect

        first = tmp_path / "a.jsonl"
        second = tmp_path / "b.jsonl"
        first.write_text(
            "{broken\n" + json.dumps(_row(), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        second.write_text(
            "[]\n" + json.dumps(_row(code="000001.SZ"), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        result = inspect("20261016", [str(second), str(first)])
        anomalies = result["source_anomalies"]
        assert len(anomalies) == 2
        assert [item["source"] for item in anomalies] == [
            {
                "path": str(first),
                "line_number": 1,
                "sha256": hashlib.sha256(first.read_bytes()).hexdigest(),
            },
            {
                "path": str(second),
                "line_number": 1,
                "sha256": hashlib.sha256(second.read_bytes()).hexdigest(),
            },
        ]
        assert "JSON" in anomalies[0]["reason"].upper()
        assert "OBJECT" in anomalies[1]["reason"].upper()
        assert [
            (item["bad_json"], item["non_object"]) for item in result["source_files"]
        ] == [(1, 0), (0, 1)]
        assert "SOURCE_MALFORMED_ROWS" in result["issues"]
        assert result["verdict"] == "BLOCK_CUTOVER"

    def test_invalid_detect_dates_include_selected_and_selector_skipped_lines(
        self, tmp_path
    ):
        import hashlib
        from strategy_layer.a4_readonly_probe import inspect

        pool = tmp_path / "pool.jsonl"
        rows = [
            _row(code="000338.SZ"),
            _row(code="000002.SZ", detect_date="2026-10-XX"),
            _row(code="00700.HK", detect_date="2026-10-00"),
            _row(code="000003.SZ", detect_date=None),
        ]
        pool.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        result = inspect("20261016", [str(pool)])
        anomalies = result["source_anomalies"]
        assert len(anomalies) == 3
        expected_hash = hashlib.sha256(pool.read_bytes()).hexdigest()
        assert [
            (item["source"], item["code"], item["type"], item["detect_date"])
            for item in anomalies
        ] == [
            (
                {"path": str(pool), "line_number": line, "sha256": expected_hash},
                rows[line - 1]["code"],
                rows[line - 1]["type"],
                rows[line - 1]["detect_date"],
            )
            for line in (2, 3, 4)
        ]
        assert all("DATE" in item["reason"].upper() for item in anomalies)
        assert result["source_files"][0]["invalid_dates"] == 3
        assert result["invalid_source_dates"] == {"CN": 2, "HK": 1}
        assert result["market_health"]["CN"]["status"] == "FRESH"
        assert result["market_health"]["HK"]["status"] == "INVALID_BATCH_DATE"
        assert "INVALID_SOURCE_DATE" in result["issues"]
        assert result["verdict"] == "BLOCK_CUTOVER"

    def test_unknown_type_skip_has_origin_without_unselected_conversion(self, tmp_path):
        import hashlib
        from strategy_layer.a4_readonly_probe import inspect

        pool = _write_pool(
            tmp_path,
            "pool.jsonl",
            [
                _row(code="000001.SZ", sig_type="4买"),
                _row(code="000002.SZ", sig_type="4买", detect_date="2026-10-15"),
                _row(code="000338.SZ"),
            ],
        )
        result = inspect("20261016", [pool])
        skips = result["selected_conversion_skips"]
        assert len(skips) == 1
        assert skips[0]["source"] == {
            "path": pool,
            "line_number": 1,
            "sha256": hashlib.sha256(
                (tmp_path / "pool.jsonl").read_bytes()
            ).hexdigest(),
        }
        assert (
            skips[0]["reason"],
            skips[0]["code"],
            skips[0]["type"],
            skips[0]["detect_date"],
        ) == ("UNKNOWN_TYPE_OR_INVALID_CODE", "000001.SZ", "4买", "2026-10-16")
        assert result["signals_a"] == 1
        assert result["verdict"] == "BLOCK_CUTOVER"

    def test_mutation_after_capture_does_not_change_probe_conversion(
        self, tmp_path, monkeypatch
    ):
        from strategy_layer import a4_readonly_probe as probe

        pool = _write_pool(
            tmp_path, "pool.jsonl", [_row(sig_type="1买"), _row(sig_type="3卖")]
        )
        original = probe._source_files

        def capture_then_mutate(adapter):
            captured = original(adapter)
            (tmp_path / "pool.jsonl").write_text(
                json.dumps(_row(sig_type="2卖"), ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            return captured

        monkeypatch.setattr(probe, "_source_files", capture_then_mutate)
        result = probe.inspect("20261016", [pool])
        detail = result["duplicate_evidence"]["000338.SZ"]
        assert [r["action"] for r in detail["rows"]] == ["buy", "sell"]
        assert (
            detail["rows"][0]["source"]["sha256"] == result["source_files"][0]["sha256"]
        )
        assert result["signals_a"] == 2
        assert detail["file_hash_conversion_snapshot"] == "SAME_BYTES_IN_PROBE_ONLY"
        assert result["verdict"] == "BLOCK_CUTOVER"

    def test_missing_and_malformed_pool_are_separate(self, tmp_path):
        from strategy_layer.a4_readonly_probe import inspect

        pool = tmp_path / "pool.jsonl"
        pool.write_text(
            "{broken\n[]\n" + json.dumps(_row(), ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        result = inspect("20261016", [str(pool), str(tmp_path / "missing.jsonl")])
        by_status = {item["status"]: item for item in result["source_files"]}
        assert by_status["MALFORMED_ROWS"]["bad_json"] == 1
        assert by_status["MALFORMED_ROWS"]["non_object"] == 1
        assert by_status["MALFORMED_ROWS"]["parsed_rows"] == 1
        assert len(by_status["MALFORMED_ROWS"]["sha256"]) == 64
        assert by_status["MISSING"]["sha256"] is None
        assert {"SOURCE_FILE_MISSING", "SOURCE_MALFORMED_ROWS"} <= set(result["issues"])
        assert result["verdict"] == "BLOCK_CUTOVER"

    def test_empty_and_unreadable_pool_evidence(self, tmp_path):
        from strategy_layer.a4_readonly_probe import inspect

        good = _write_pool(tmp_path, "good.jsonl", [_row()])
        empty = tmp_path / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        invalid_utf8 = tmp_path / "invalid.jsonl"
        invalid_utf8.write_bytes(
            json.dumps(_row(), ensure_ascii=False).encode("utf-8") + b"\n\xff"
        )
        result = inspect("20261016", [good, str(empty), str(invalid_utf8)])
        assert {item["status"] for item in result["source_files"]} == {
            "OK",
            "EMPTY",
            "UNREADABLE",
        }
        assert "SOURCE_FILE_UNUSABLE" in result["issues"]
        assert result["signals_a"] == 1  # bad UTF-8 quarantines even earlier good lines
        unreadable = next(
            item for item in result["source_files"] if item["status"] == "UNREADABLE"
        )
        assert unreadable["parsed_rows"] == 0

    def test_same_codes_can_hide_source_event_and_batch_mismatch(self, tmp_path):
        from strategy_layer.a4_readonly_probe import inspect

        pool = _write_pool(tmp_path, "pool.jsonl", [_row(sig_type="2买")])
        hist = tmp_path / "history.json"
        doc = {
            "scan_run_date": "2026-10-16",
            "trade_basis": "2026-10-16 收盘",
            "strategies": {"chanlun": {"batch_date": "2026-10-15"}},
            "stocks": [
                {
                    "code": "000338.SZ",
                    "sources": [{"s": "chanlun", "tag": "3卖", "dt": "2026-10-14"}],
                }
            ],
        }
        hist.write_text(json.dumps(doc), encoding="utf-8")
        result = inspect("20261016", [pool], str(hist))
        assert result["picks_only"] == result["adapter_only"] == []
        evidence = result["chanlun_reconciliation"]
        assert evidence["source_difference_count"] == 1
        assert evidence["source_differences"]["000338.SZ"]["pool_only"] == [
            {"tag": "2买", "dt": "2026-10-15", "count": 1}
        ]
        assert evidence["batch_status"] == "MISMATCH"
        assert evidence["tier_action_score"] == "NOT_COMPARABLE_WITH_FUSED_SIGNAL"
        assert {"CHAN_SOURCE_EVENT_MISMATCH", "ARCHIVE_CHAN_BATCH_MISMATCH"} <= set(
            result["issues"]
        )
        doc["strategies"]["chanlun"]["batch_date"] = "2026-10-16"
        doc["stocks"][0]["sources"][0].update(tag="2买", dt="2026-10-15")
        hist.write_text(json.dumps(doc), encoding="utf-8")
        matched = inspect("20261016", [pool], str(hist))
        assert matched["chanlun_reconciliation"]["batch_status"] == "MATCHED"
        assert matched["chanlun_reconciliation"]["source_difference_count"] == 0
        assert matched["verdict"] == "BLOCK_CUTOVER"

    def test_duplicate_signal_counts_and_invalid_archive_fail_closed(self, tmp_path):
        from strategy_layer.a4_readonly_probe import inspect

        pool = _write_pool(tmp_path, "pool.jsonl", [_row(), _row()])
        hist = tmp_path / "history.json"
        doc = {
            "scan_run_date": "2026-10-16",
            "trade_basis": "2026-10-16 收盘",
            "stocks": [
                {
                    "code": "000338.SZ",
                    "sources": [{"s": "chanlun", "tag": "2买", "dt": "2026-10-15"}],
                }
            ],
        }
        hist.write_text(json.dumps(doc), encoding="utf-8")
        result = inspect("20261016", [pool], str(hist))
        assert result["chanlun_reconciliation"]["source_differences"]["000338.SZ"][
            "pool_only"
        ] == [{"tag": "2买", "dt": "2026-10-15", "count": 1}]
        assert "ARCHIVE_CHAN_BATCH_UNPROVEN" in result["issues"]
        doc["stocks"][0]["sources"][0]["dt"] = "2026-10-XX"
        hist.write_text(json.dumps(doc), encoding="utf-8")
        invalid = inspect("20261016", [pool], str(hist))
        assert invalid["history"]["status"] == "INVALID_ARCHIVE"
        assert invalid["chanlun_reconciliation"] is None
        assert invalid["picks_only"] is None
        assert "HISTORY_UNAVAILABLE" in invalid["issues"]

    def test_history_must_match_business_day(self, tmp_path):
        from strategy_layer.a4_readonly_probe import inspect

        pool = _write_pool(tmp_path, "pool.jsonl", [_row()])
        hist = tmp_path / "archive.json"
        doc = {
            "scan_run_date": "2026-10-15",
            "trade_basis": "2026-10-15 收盘",
            "strategies": {"chanlun": {"batch_date": "2026-10-16"}},
            "stocks": [
                {
                    "code": "000338.SZ",
                    "sources": [{"s": "chanlun", "tag": "2买", "dt": "2026-10-15"}],
                }
            ],
        }
        hist.write_text(json.dumps(doc), encoding="utf-8")
        wrong = inspect("20261016", [pool], str(hist))
        assert wrong["history"]["status"] == "DATE_MISMATCH"
        assert wrong["historic_picks_symbols"] is None
        assert wrong["picks_only"] is None and wrong["adapter_only"] is None
        assert "HISTORY_DATE_MISMATCH" in wrong["issues"]
        doc["scan_run_date"] = "2026-10-16"
        hist.write_text(json.dumps(doc), encoding="utf-8")
        assert (
            inspect("20261016", [pool], str(hist))["history"]["status"]
            == "DATE_MISMATCH"
        )
        doc["trade_basis"] = "2026-10-16 收盘"
        hist.write_text(json.dumps(doc), encoding="utf-8")
        matched = inspect("20261016", [pool], str(hist))
        assert matched["history"]["status"] == "MATCHED"
        assert matched["picks_only"] == matched["adapter_only"] == []
        hist.unlink()
        missing = inspect("20261016", [pool], str(hist))
        assert missing["history"]["status"] == "UNREADABLE"
        assert "HISTORY_UNAVAILABLE" in missing["issues"]

    def test_source_file_is_read_once_for_hash_rows_and_origins(
        self, tmp_path, monkeypatch
    ):
        import hashlib
        from pathlib import Path
        from strategy_layer import a4_readonly_probe as probe

        pool = tmp_path / "pool.jsonl"
        original_bytes = (
            "{broken\n\n"
            + json.dumps(_row(sig_type="1买"), ensure_ascii=False)
            + "\n"
            + json.dumps(_row(sig_type="3卖"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        pool.write_bytes(original_bytes)
        read_bytes = Path.read_bytes
        reads = []

        def read_then_change(path):
            data = read_bytes(path)
            if path == pool:
                reads.append(data)
                pool.write_bytes(
                    (
                        json.dumps(_row(sig_type="2卖"), ensure_ascii=False) + "\n"
                    ).encode("utf-8")
                )
            return data

        monkeypatch.setattr(Path, "read_bytes", read_then_change)
        result = probe.inspect("20261016", [str(pool)])
        source = result["source_files"][0]
        detail = result["duplicate_evidence"]["000338.SZ"]
        expected_hash = hashlib.sha256(original_bytes).hexdigest()
        assert reads == [original_bytes]
        assert pool.read_bytes() != original_bytes
        assert (
            source["path"],
            source["bytes"],
            source["sha256"],
            source["bad_json"],
            source["parsed_rows"],
        ) == (str(pool), len(original_bytes), expected_hash, 1, 2)
        assert [
            (
                row["source"]["path"],
                row["source"]["line_number"],
                row["source"]["sha256"],
                row["action"],
            )
            for row in detail["rows"]
        ] == [
            (str(pool), 3, expected_hash, "buy"),
            (str(pool), 4, expected_hash, "sell"),
        ]
        assert result["input_snapshot_scope"] == "PROBE_ONLY_NOT_PRODUCTION"
        assert result["verdict"] == "BLOCK_CUTOVER"

    def test_duplicate_events_keep_distinct_file_line_origins(self, tmp_path):
        import hashlib
        from strategy_layer.a4_readonly_probe import inspect

        first = tmp_path / "a.jsonl"
        second = tmp_path / "b.jsonl"
        event = json.dumps(_row(), ensure_ascii=False)
        first.write_text("\n" + event + "\n{broken\n" + event + "\n", encoding="utf-8")
        second.write_text(event + "\n", encoding="utf-8")
        result = inspect("20261016", [str(second), str(first)])
        rows = result["duplicate_evidence"]["000338.SZ"]["rows"]
        first_hash = hashlib.sha256(first.read_bytes()).hexdigest()
        second_hash = hashlib.sha256(second.read_bytes()).hexdigest()
        assert result["duplicate_symbols"] == {"000338.SZ": 3}
        assert [(row["selected_row_index"], row["source"]) for row in rows] == [
            (0, {"path": str(first), "line_number": 2, "sha256": first_hash}),
            (1, {"path": str(first), "line_number": 4, "sha256": first_hash}),
            (2, {"path": str(second), "line_number": 1, "sha256": second_hash}),
        ]
        assert result["duplicate_evidence"]["000338.SZ"]["event_counts"] == [
            {"type": "2买", "dt": "2026-10-15", "detect_date": "2026-10-16", "count": 3}
        ]
        assert "SOURCE_MALFORMED_ROWS" in result["issues"]
        assert result["verdict"] == "BLOCK_CUTOVER"

    @pytest.mark.parametrize(
        "archive_content,expected_status,expected_issue",
        [
            (None, "UNREADABLE", "HISTORY_UNAVAILABLE"),
            ("{broken", "INVALID_ARCHIVE", "HISTORY_UNAVAILABLE"),
            ({"scan_run_date": "2026-10-15"}, "DATE_MISMATCH", "HISTORY_DATE_MISMATCH"),
            (
                {"trade_basis": "2026-10-15 收盘"},
                "DATE_MISMATCH",
                "HISTORY_DATE_MISMATCH",
            ),
        ],
    )
    def test_missing_bad_or_out_of_window_archive_cannot_reconcile(
        self, tmp_path, archive_content, expected_status, expected_issue
    ):
        from strategy_layer.a4_readonly_probe import inspect

        pool = _write_pool(tmp_path, "pool.jsonl", [_row()])
        archive = tmp_path / "history.json"
        doc = {
            "scan_run_date": "2026-10-16",
            "trade_basis": "2026-10-16 收盘",
            "strategies": {"chanlun": {"batch_date": "2026-10-16"}},
            "stocks": [
                {
                    "code": "000338.SZ",
                    "sources": [{"s": "chanlun", "tag": "2买", "dt": "2026-10-15"}],
                }
            ],
        }
        if isinstance(archive_content, dict):
            doc.update(archive_content)
            archive.write_text(json.dumps(doc), encoding="utf-8")
        elif archive_content is not None:
            archive.write_text(archive_content, encoding="utf-8")
        result = inspect("20261016", [pool], str(archive))
        assert result["history"]["path"] == str(archive)
        assert result["history"]["status"] == expected_status
        assert result["historic_picks_symbols"] is None
        assert result["picks_only"] is None and result["adapter_only"] is None
        assert result["chanlun_reconciliation"] is None
        assert expected_issue in result["issues"]
        assert result["verdict"] == "BLOCK_CUTOVER"

    def test_same_day_archive_edit_uses_current_bytes_not_cached_evidence(
        self, tmp_path
    ):
        import hashlib
        from strategy_layer.a4_readonly_probe import inspect

        pool = _write_pool(tmp_path, "pool.jsonl", [_row()])
        archive = tmp_path / "history.json"
        doc = {
            "scan_run_date": "2026-10-16",
            "trade_basis": "2026-10-16 收盘",
            "strategies": {"chanlun": {"batch_date": "2026-10-16"}},
            "stocks": [
                {
                    "code": "000338.SZ",
                    "sources": [{"s": "chanlun", "tag": "2买", "dt": "2026-10-15"}],
                }
            ],
        }
        archive.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        before_bytes = archive.read_bytes()
        before = inspect("20261016", [pool], str(archive))
        assert before["history"]["status"] == "MATCHED"
        assert before["history"]["sha256"] == hashlib.sha256(before_bytes).hexdigest()
        assert before["chanlun_reconciliation"]["source_difference_count"] == 0

        doc["stocks"][0]["sources"][0]["tag"] = "3卖"
        archive.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        after_bytes = archive.read_bytes()
        after = inspect("20261016", [pool], str(archive))
        assert after["history"]["status"] == "MATCHED"
        assert after["history"]["sha256"] == hashlib.sha256(after_bytes).hexdigest()
        assert after["history"]["sha256"] != before["history"]["sha256"]
        assert after["picks_only"] == after["adapter_only"] == []
        assert after["chanlun_reconciliation"]["source_differences"]["000338.SZ"] == {
            "pool_only": [{"tag": "2买", "dt": "2026-10-15", "count": 1}],
            "archive_only": [{"tag": "3卖", "dt": "2026-10-15", "count": 1}],
        }
        assert "CHAN_SOURCE_EVENT_MISMATCH" in after["issues"]
        assert before["verdict"] == after["verdict"] == "BLOCK_CUTOVER"


class TestToxicDowngrade:
    """Δ4 毒层降档（确认门① A 方案，口径同 picks 链 _chanlun_toxic）：
    3买∧bei=False / 2买∧trend=range → score 封顶 60、action 不变、
    rationale 标记；TOXIC_DOWNGRADE=False 一键回退。"""

    def test_toxic_3buy_bei_false_capped(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row(sig_type="3买", bei=False)])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 60.0
        assert s.action == "buy"  # 仍在池中，低分沉底（语义同 picks 链 T2）

    def test_toxic_3buy_bei_false_with_resonance_capped(self, tmp_path):
        pool = _write_pool(
            tmp_path,
            "pool.jsonl",
            [_row(sig_type="3买", bei=False, mbei=True, vv=True)],
        )
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 60.0  # 75+4=79 → 仍封顶 60

    def test_toxic_2buy_range_capped(self, tmp_path):
        pool = _write_pool(
            tmp_path, "pool.jsonl", [_row(sig_type="2买", trend="range")]
        )
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 60.0
        assert s.action == "buy"

    def test_toxic_rationale_marked(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row(sig_type="3买", bei=False)])
        s = _adapter([pool]).score("20261016")[0]
        assert "毒层降档" in s.rationale
        assert "3买∧bei=False" in s.rationale

    def test_nontoxic_3buy_bei_true_unaffected(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row(sig_type="3买", bei=True)])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 77.0  # 75+2，不封顶
        assert "毒层降档" not in s.rationale

    def test_nontoxic_3buy_bei_none_unaffected(self, tmp_path):
        """bei=None 不可判 → 保守不判毒（对齐 picks 链 P4 口径）。"""
        pool = _write_pool(tmp_path, "pool.jsonl", [_row(sig_type="3买", bei=None)])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 75.0

    def test_nontoxic_2buy_trend_up_unaffected(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row(sig_type="2买", trend="up")])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 85.0

    def test_toxic_sell_unaffected(self, tmp_path):
        """卖点不判毒：3卖 bei=False 照常 25 分 sell。"""
        pool = _write_pool(tmp_path, "pool.jsonl", [_row(sig_type="3卖", bei=False)])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 25.0
        assert s.action == "sell"

    def test_toxic_switch_off_restores(self, tmp_path):
        """TOXIC_DOWNGRADE=False 一键回退：毒层买点恢复同权分。"""
        from strategy_layer.adapters import chan_monitor as cm

        pool = _write_pool(tmp_path, "pool.jsonl", [_row(sig_type="3买", bei=False)])
        old = cm.TOXIC_DOWNGRADE
        try:
            cm.TOXIC_DOWNGRADE = False
            s = _adapter([pool]).score("20261016")[0]
            assert s.score == 75.0
            assert "毒层降档" not in s.rationale
        finally:
            cm.TOXIC_DOWNGRADE = old


class TestRationale:
    def test_rationale_human_readable(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row()])
        s = _adapter([pool]).score("20261016")[0]
        assert "缠论2买" in s.rationale
        assert "2026-10-15" in s.rationale
        assert "15.8" in s.rationale
        assert s.rationale.strip() != ""

    def test_rationale_includes_trend(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row(trend="down", reg="down")])
        s = _adapter([pool]).score("20261016")[0]
        assert "down" in s.rationale


class TestOptionalFieldAnnotation:
    """R0-S 契约（WO-QT-20261020-043 重建）：可选共振字段缺键/显式 False
    在 rationale 留可追溯标注，None（不可判）不标注；评分置信度不变。"""

    def test_missing_keys_are_annotated_individually(self, tmp_path):
        row = _row()
        del row["mbei"]
        del row["bei2"]
        pool = _write_pool(tmp_path, "pool.jsonl", [row])
        s = _adapter([pool]).score("20261016")[0]
        assert "mbei=缺字段" in s.rationale
        assert "bei2=缺字段" in s.rationale
        assert "（mbei=缺字段，bei2=缺字段）" in s.rationale, "缺键逐字段标注"
        assert "bei=None" not in s.rationale and "，bei=缺字段" not in s.rationale, (
            "present-None（不可判）不标注"
        )

    def test_explicit_false_is_annotated(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row(vv=False)])
        s = _adapter([pool]).score("20261016")[0]
        assert "vv=False" in s.rationale

    def test_annotation_does_not_change_score_or_confidence(self, tmp_path):
        row = _row()
        del row["mbei"]
        del row["bei2"]
        pool = _write_pool(tmp_path, "pool.jsonl", [row])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 85.0
        assert s.confidence == 0.6

    def test_full_row_has_no_annotation(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row()])
        s = _adapter([pool]).score("20261016")[0]
        assert "缺字段" not in s.rationale
        assert "=False" not in s.rationale
        assert "不可判" not in s.rationale, "None（不可判）不得以任何标签标注"

    def test_sell_signal_is_annotated_too(self, tmp_path):
        row = _row(sig_type="1卖")
        del row["bei"]
        pool = _write_pool(tmp_path, "pool.jsonl", [row])
        s = _adapter([pool]).score("20261016")[0]
        assert s.action == "sell"
        assert "bei=缺字段" in s.rationale, "卖点同样标注（P2-B 缝隙钉住）"


class TestDegradation:
    def test_missing_pool_returns_empty(self, tmp_path, capsys):
        adapter = _adapter([str(tmp_path / "not_exist.jsonl")])
        assert adapter.score("20261016") == []
        assert "降级" in capsys.readouterr().out

    def test_bad_lines_skipped(self, tmp_path):
        p = tmp_path / "pool.jsonl"
        good = json.dumps(_row(), ensure_ascii=False)
        p.write_text("{broken\n\n" + good + "\n", encoding="utf-8")
        sigs = _adapter([str(p)]).score("20261016")
        assert len(sigs) == 1

    def test_unknown_type_and_missing_code_skipped(self, tmp_path):
        pool = _write_pool(
            tmp_path,
            "pool.jsonl",
            [
                _row(code="A.SH", sig_type="4买"),
                _row(code="", sig_type="1买"),
                _row(code="B.SH", sig_type="1卖"),
            ],
        )
        sigs = _adapter([pool]).score("20261016")
        assert [s.symbol for s in sigs] == ["B.SH"]

    def test_never_raises_to_caller(self, tmp_path, capsys):
        # 目录当池文件读 → OSError → 降级
        d = tmp_path / "adir"
        d.mkdir()
        assert _adapter([str(d)]).score("20261016") == []
        capsys.readouterr()

    def test_sorted_by_score_desc(self, tmp_path):
        pool = _write_pool(
            tmp_path,
            "pool.jsonl",
            [
                _row(code="A.SH", sig_type="2卖", dt="2026-10-15"),
                _row(code="B.SH", sig_type="1买", dt="2026-10-15"),
            ],
        )
        sigs = _adapter([pool]).score("20261016")
        assert [s.score for s in sigs] == sorted([s.score for s in sigs], reverse=True)
