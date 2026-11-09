# -*- coding: utf-8 -*-
"""verify_daily_report 纯逻辑测试（mock/临时文件，不依赖真实 latest.json）。

覆盖：
  - 15 键缺失逐项检出；
  - 内容语义（extractor_version / pipeline / word_tempo / content_type / 类型抽查）；
  - --smoke 只验链路（坏 payload 也放行，不判语义）；
  - 退出码三态 0/1/2（run() 对临时文件落 status/alert 日志）；
  - mtime 新鲜度判定（今天 19:00 前后）。
"""
import importlib.util
import json
import os
import datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
_SPEC = importlib.util.spec_from_file_location(
    "verify_daily_report", REPO / "tools" / "verify_daily_report.py")
vdr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vdr)

EXPECT = ("v1.3", "qwen3.5:9b", "v1.3")
NOW = datetime.datetime(2026, 10, 18, 19, 5, 0)


def full_payload():
    """15 键齐全 + 语义全过的 payload。"""
    return {
        "date": "2026-10-18",
        "generated_at": "2026-10-18 19:00:30",
        "extractor_version": "v1.3",
        "pipeline": {"llm_model": "qwen3.5:9b", "prompt_version": "v1.3"},
        "ingested_today": 14721,
        "posts_today": 320,
        "classified_today": 310,
        "event_type": {"个股研判": 120},
        "sentiment": {"bullish": 80, "bearish": 50, "neutral": 90},
        "content_type": {"tech": 60, "trade": 40},
        "top_codes": {"600519": 25},
        "top_themes": {"白酒": 30},
        "anomaly_signals": [],
        "word_tempo": {"bearish": ["崩"], "bullish": ["新高"]},
        "high_confidence": [{"code": "600519"}],
    }


def _write_latest(tmp_path, payload, mtime=None):
    p = tmp_path / "latest.json"
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime.timestamp(), mtime.timestamp()))
    return str(p)


# ---------- 键缺失逐项检出 ----------

def test_each_missing_key_detected():
    base = full_payload()
    for key in vdr.REQUIRED_KEYS:
        payload = {k: v for k, v in base.items() if k != key}
        fails = vdr.check_payload(payload, EXPECT)
        names = [n for n, _ in fails]
        assert "required_keys" in names, "missing %r not detected" % key
        reasons = [w for n, w in fails if n == "required_keys"]
        assert key in reasons[0]


def test_all_pass_payload_clean():
    assert vdr.check_payload(full_payload(), EXPECT) == []


# ---------- 内容语义 ----------

def test_extractor_version_mismatch():
    p = full_payload()
    p["extractor_version"] = "v1.2"
    fails = dict(vdr.check_payload(p, EXPECT))
    assert "extractor_version" in fails
    assert "v1.2" in fails["extractor_version"]


def test_pipeline_bad_model_and_empty_prompt():
    p = full_payload()
    p["pipeline"] = {"llm_model": "other:7b", "prompt_version": ""}
    fails = dict(vdr.check_payload(p, EXPECT))
    assert "pipeline_llm_model" in fails
    assert "pipeline_prompt_version" in fails


def test_word_tempo_missing_column():
    p = full_payload()
    del p["word_tempo"]["bearish"]
    fails = dict(vdr.check_payload(p, EXPECT))
    assert "word_tempo_bearish" in fails
    # bullish 列还在，不应误报
    assert "word_tempo_bullish" not in fails


def test_content_type_empty_with_posts_is_anomaly():
    p = full_payload()
    p["content_type"] = {}
    fails = dict(vdr.check_payload(p, EXPECT))
    assert "content_type_empty" in fails
    # posts_today==0 时空 dict 合法（18:00 爬虫前）
    p2 = full_payload()
    p2["posts_today"] = 0
    p2["content_type"] = {}
    assert vdr.check_payload(p2, EXPECT) == []


def test_container_type_check():
    p = full_payload()
    p["top_codes"] = ["600519"]  # 应为 dict
    p["high_confidence"] = {}    # 应为 list
    fails = dict(vdr.check_payload(p, EXPECT))
    assert "type_top_codes" in fails
    assert "type_high_confidence" in fails


def test_non_dict_json_object():
    fails = vdr.check_payload([1, 2], EXPECT)
    assert fails and fails[0][0] == "json_shape"


# ---------- smoke 语义 ----------

def test_smoke_skips_semantics():
    # 键全缺的坏 payload，smoke 模式只验链路（可读即过），不判语义
    assert vdr.check_payload.__doc__  # sanity
    # smoke 放行逻辑在 run() 层面覆盖，见下文 run_* 测试


# ---------- 退出码三态 + 日志落盘 ----------

def _write_manifest(tmp_path: Path, hot_status: str = "ok") -> str:
    """本地采集 manifest（只关心 xq_hot_post.status）。

    2026-11-08：此前 run() 只吃默认 MANIFEST_JSON = 线上真实文件，线上
    hot 源一旦 failed，下面的 rc0 健康路径用例就会假红。改为显式注入。
    """
    p = tmp_path / "_manifest.json"
    p.write_text(json.dumps({"xq_hot_post": {"status": hot_status,
                                             "note": "test fixture"}},
                            ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_run_full_ok_rc0(tmp_path):
    latest = _write_latest(tmp_path, full_payload(),
                           mtime=datetime.datetime(2026, 10, 18, 19, 0, 30))
    slog, alog = tmp_path / "s.log", tmp_path / "a.log"
    rc = vdr.run(latest, "full", now=NOW, expect=EXPECT,
                 status_log=str(slog), alert_log=str(alog),
                 manifest_path=_write_manifest(tmp_path, "ok"))
    assert rc == 0
    assert slog.read_text(encoding="ascii").strip()
    assert not alog.exists()  # 全过不写告警


def test_run_full_source_degraded_rc1(tmp_path):
    """采集侧降级（hot 源 failed）必须判红——此前该分支零覆盖。"""
    latest = _write_latest(tmp_path, full_payload(),
                           mtime=datetime.datetime(2026, 10, 18, 19, 0, 30))
    slog, alog = tmp_path / "s.log", tmp_path / "a.log"
    rc = vdr.run(latest, "full", now=NOW, expect=EXPECT,
                 status_log=str(slog), alert_log=str(alog),
                 manifest_path=_write_manifest(tmp_path, "failed"))
    assert rc == 1
    assert "source_degraded" in alog.read_text(encoding="ascii")


def test_run_full_fail_rc1(tmp_path):
    bad = full_payload()
    del bad["word_tempo"]
    latest = _write_latest(tmp_path, bad,
                           mtime=datetime.datetime(2026, 10, 18, 19, 0, 30))
    slog, alog = tmp_path / "s.log", tmp_path / "a.log"
    rc = vdr.run(latest, "full", now=NOW, expect=EXPECT,
                 status_log=str(slog), alert_log=str(alog))
    assert rc == 1
    alert = alog.read_text(encoding="ascii")
    assert "word_tempo" in alert
    assert "2026-10-18" in alert  # 含时间戳
    # 覆盖式：再跑一次失败告警不追加
    vdr.run(latest, "full", now=NOW, expect=EXPECT,
            status_log=str(slog), alert_log=str(alog))
    assert alog.read_text(encoding="ascii") == alert


def test_run_missing_file_rc2(tmp_path):
    slog, alog = tmp_path / "s.log", tmp_path / "a.log"
    rc = vdr.run(str(tmp_path / "nope.json"), "full", now=NOW, expect=EXPECT,
                 status_log=str(slog), alert_log=str(alog))
    assert rc == 2
    assert not alog.exists()  # 极早期态不告警
    assert "rc=2" in slog.read_text(encoding="ascii")


def test_run_smoke_ok_rc0(tmp_path):
    latest = _write_latest(tmp_path, full_payload(),
                           mtime=datetime.datetime(2026, 10, 18, 13, 0, 0))
    rc = vdr.run(latest, "smoke", now=NOW, expect=EXPECT,
                 status_log=str(tmp_path / "s.log"), alert_log=str(tmp_path / "a.log"))
    assert rc == 0


def test_run_smoke_bad_payload_still_rc0(tmp_path):
    # smoke 不判语义：空 dict payload 也可读 → rc0
    latest = _write_latest(tmp_path, {})
    rc = vdr.run(latest, "smoke", now=NOW, expect=EXPECT,
                 status_log=str(tmp_path / "s.log"), alert_log=str(tmp_path / "a.log"))
    assert rc == 0


def test_run_smoke_missing_file_rc2(tmp_path):
    rc = vdr.run(str(tmp_path / "nope.json"), "smoke", now=NOW, expect=EXPECT,
                 status_log=str(tmp_path / "s.log"), alert_log=str(tmp_path / "a.log"))
    assert rc == 2


def test_run_smoke_unreadable_json_rc1(tmp_path):
    p = tmp_path / "latest.json"
    p.write_text("{broken json", encoding="utf-8")
    rc = vdr.run(str(p), "smoke", now=NOW, expect=EXPECT,
                 status_log=str(tmp_path / "s.log"), alert_log=str(tmp_path / "a.log"))
    assert rc == 1  # 链路坏（JSON 不可读）smoke 也要 fail-visible


# ---------- mtime 新鲜度 ----------

def test_freshness_after_19h():
    mt = datetime.datetime(2026, 10, 18, 19, 0, 30)
    assert vdr.check_freshness(mt.timestamp(), NOW) == []


def test_freshness_before_19h_fails():
    mt = datetime.datetime(2026, 10, 18, 13, 25, 0)
    fails = vdr.check_freshness(mt.timestamp(), NOW)
    assert fails and fails[0][0] == "freshness"
    assert "19:00" in fails[0][1]


def test_freshness_full_mode_wired(tmp_path):
    # 产物存在且语义全过，但 mtime 是今天 13:00（日报没跑）→ rc1
    latest = _write_latest(tmp_path, full_payload(),
                           mtime=datetime.datetime(2026, 10, 18, 13, 0, 0))
    rc = vdr.run(latest, "full", now=NOW, expect=EXPECT,
                 status_log=str(tmp_path / "s.log"), alert_log=str(tmp_path / "a.log"))
    assert rc == 1
    assert "freshness" in (tmp_path / "a.log").read_text(encoding="ascii")


# ---------- 动态版本抓取 ----------

def test_load_expected_pipeline_fallback(monkeypatch):
    # 回归修（2026-10-19）：原名 fallback 但未屏蔽动态抓取源——xq_classify
    # 可导入时必得 v1.6 而非兜底 v1.3，假红判「产品坏」实为「测试测错路径」。
    # 现显式模拟导入失败环境，专测兜底三元组。
    import builtins
    real_import = builtins.__import__

    def _blocked(name, *a, **k):
        if name == "xq_classify":
            raise ImportError("blocked for fallback-path test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    ver, model, _prompt = vdr.load_expected_pipeline()
    assert ver == "v1.3"
    assert model == "qwen3.5:9b"


def test_load_expected_pipeline_dynamic_matches_source():
    # 动态路径契约：三元组必须与 xq_classify 源码事实逐位一致——
    # 版本演进（v1.3→v1.6）不再破测试，破的只有「源码与动态抓取失联」。
    import xq_classify
    ver, model, _prompt = vdr.load_expected_pipeline()
    assert ver == str(xq_classify._TICKER_VERSION)
    assert model == str(xq_classify.MODEL)
