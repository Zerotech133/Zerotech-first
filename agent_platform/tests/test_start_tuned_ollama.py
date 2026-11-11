# -*- coding: utf-8 -*-
"""start_tuned_ollama.py 的判定逻辑测试（tuned 闸门不许恒真）。

背景：这个脚本 2026-10-18 首版的 `--status` 恒返回 0，且只读自己写的
`automation_runs/ollama_guard/serve_tuned.log`（该文件从未存在），于是永远
打印「无 serve 日志」并报健康。等于校验器永远说「没问题」——这类 bug 比没有
校验更危险，因为它让人放心地把默认配置实例当成 tuned 实例。

本文件的测试就是锁死三件事：
  1. 关键参数缺失/取默认值时，`tuned_deviations()` **必须**报偏差；
  2. `server_config()` 必须按**时间戳**取最新 config 行（不能被 App 写的
     旧行骗到），并且能在「config 行在文件开头」的大日志里找到它；
  3. `--status` 在端口不通时**必须**返回非 0（不许恒 0）。
"""

import sys
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import start_tuned_ollama as sto  # noqa: E402


TUNED_FULL = {
    # 与 start_tuned_ollama.TUNED_ENV 对齐（2026-10-24 全仓收口：FLASH 布尔归一 1、ctx 32768、并行 2→1）
    "OLLAMA_FLASH_ATTENTION": "1",
    "OLLAMA_KV_CACHE_TYPE": "q8_0",
    "OLLAMA_IGPU_ENABLE": "1",
    "OLLAMA_VULKAN": "1",  # 2026-10-24 第 7 项：缺它则 iGPU 不生效
    "OLLAMA_CONTEXT_LENGTH": "32768",
    "OLLAMA_KEEP_ALIVE": "30m0s",
    "OLLAMA_NUM_PARALLEL": "1",  # qwen35 不支持并行请求，10-24 起定版 1
}

DEFAULT_CFG = {
    "OLLAMA_FLASH_ATTENTION": "false",
    "OLLAMA_KV_CACHE_TYPE": "",
    "OLLAMA_IGPU_ENABLE": "",
    "OLLAMA_VULKAN": "false",
    "OLLAMA_CONTEXT_LENGTH": "0",
    "OLLAMA_KEEP_ALIVE": "5m0s",
    "OLLAMA_NUM_PARALLEL": "2",  # 模拟非 tuned 实例（旧并行度 2 现在算偏差值）
}


def _cfg_line(ts: str, cfg: dict) -> str:
    """按 ollama 真实日志格式拼一条 server config 行。"""
    env = " ".join("%s:%s" % (k, v) for k, v in cfg.items())
    return (
        'time=%s level=INFO source=routes.go:2439 msg="server config" '
        'env="map[%s]"' % (ts, env)
    )


# ---------------------------------------------------------------- 参数达标判定


def test_tuned_full_has_no_deviation():
    assert sto.tuned_deviations(TUNED_FULL) == []


def test_default_cfg_reports_all_critical_deviations():
    bad = sto.tuned_deviations(DEFAULT_CFG)
    # 五个关键参数全部应被点出（keep_alive 不在关键项内）
    assert len(bad) == len(sto.TUNED_CRITICAL)
    joined = "; ".join(bad)
    for key in sto.TUNED_CRITICAL:
        assert key in joined


def test_ctx_floor_boundary():
    """ctx 只要达线就算达标；4096 必须判不达标。"""
    ok = dict(TUNED_FULL, OLLAMA_CONTEXT_LENGTH=str(sto.CTX_OK_FLOOR))
    assert sto.tuned_deviations(ok) == []
    low = dict(TUNED_FULL, OLLAMA_CONTEXT_LENGTH="4096")
    assert any("OLLAMA_CONTEXT_LENGTH" in x for x in sto.tuned_deviations(low))


def test_missing_key_is_deviation():
    cfg = dict(TUNED_FULL)
    cfg.pop("OLLAMA_KV_CACHE_TYPE")
    bad = sto.tuned_deviations(cfg)
    assert any("OLLAMA_KV_CACHE_TYPE" in x and "缺失" in x for x in bad)


def test_keep_alive_not_critical():
    """watchdog 定版是 30m、bat 是 2h，两者都不该让判定失败。"""
    a = dict(TUNED_FULL, OLLAMA_KEEP_ALIVE="2h0m0s")
    b = dict(TUNED_FULL, OLLAMA_KEEP_ALIVE="30m0s")
    assert sto.tuned_deviations(a) == []
    assert sto.tuned_deviations(b) == []


# ---------------------------------------------------------------- 日志选取


def test_parse_ts():
    line = "time=2026-10-18T17:17:11.012+08:00 level=INFO msg=x"
    assert sto._parse_ts(line) == "2026-10-18T17:17:11.012+08:00"
    assert sto._parse_ts("no timestamp here") == ""


def test_server_config_picks_newest_by_timestamp(tmp_path, monkeypatch):
    """旧 App 行（17:12 默认值）与新 tuned 行（17:17）并存时，必须取新的。"""
    stale = tmp_path / "server.log"
    stale.write_text(
        _cfg_line("2026-10-18T17:12:15.061+08:00", DEFAULT_CFG) + "\n", encoding="utf-8"
    )
    fresh = tmp_path / "tuned.log"
    fresh.write_text(
        _cfg_line("2026-10-18T17:17:11.012+08:00", TUNED_FULL) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(sto, "CONFIG_LOG_CANDIDATES", [stale, fresh])

    cfg, src, ts = sto.server_config()
    assert src == fresh, "必须选时间戳最新的文件，而不是候选顺序第一个"
    assert cfg["OLLAMA_CONTEXT_LENGTH"] == "32768"
    assert sto.tuned_deviations(cfg) == []


def test_server_config_finds_head_of_big_log(tmp_path, monkeypatch):
    """真实故障回归：config 行在文件开头，服务跑久后被挤出尾部窗口。

    曾因只扫尾部 6000 行，导致读不到 tuned.log 的 config 行，
    退回读 App 的旧 server.log 并误判 NOT_TUNED。
    """
    big = tmp_path / "tuned.log"
    body = [_cfg_line("2026-10-18T17:17:11.012+08:00", TUNED_FULL)]
    body += [
        "time=2026-10-18T17:20:00.000+08:00 level=INFO msg=request id=%d" % i
        for i in range(12000)
    ]  # 远超 8000 行的截取阈值
    big.write_text("\n".join(body) + "\n", encoding="utf-8")
    monkeypatch.setattr(sto, "CONFIG_LOG_CANDIDATES", [big])

    cfg, _, _ = sto.server_config()
    assert cfg.get("OLLAMA_CONTEXT_LENGTH") == "32768", "config 行在开头也必须找到"


def test_server_config_empty_when_no_log(tmp_path, monkeypatch):
    monkeypatch.setattr(sto, "CONFIG_LOG_CANDIDATES", [tmp_path / "nope.log"])
    assert sto.server_config() == ({}, None, None)


# ---------------------------------------------------------------- 闸门不许恒 0


def test_status_returns_nonzero_when_port_closed(monkeypatch, capsys):
    """端口不通必须返回 1——这是「校验器不许恒真」的核心断言。"""
    monkeypatch.setattr(sto, "port_open", lambda *a, **k: False)
    monkeypatch.setattr(sto, "ollama_procs", lambda: [])
    monkeypatch.setattr(sto, "api_ps", lambda *a, **k: None)
    monkeypatch.setattr(sto, "CONFIG_LOG_CANDIDATES", [])

    rc = sto.main(["--status"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "NOT_RUNNING" in out


def test_status_not_tuned_when_live_ctx_small(monkeypatch, capsys):
    """实测 ctx 小（默认 4096）必须判 NOT_TUNED 并退 1。"""
    monkeypatch.setattr(sto, "port_open", lambda *a, **k: True)
    monkeypatch.setattr(sto, "ollama_procs", lambda: ["ollama.exe"])
    monkeypatch.setattr(sto, "api_ps", lambda *a, **k: [{"context_length": 4096}])
    monkeypatch.setattr(sto, "CONFIG_LOG_CANDIDATES", [])

    rc = sto.main(["--status"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "NOT_TUNED" in out


def test_status_tuned_when_live_ctx_ok(monkeypatch, capsys):
    monkeypatch.setattr(sto, "port_open", lambda *a, **k: True)
    monkeypatch.setattr(sto, "ollama_procs", lambda: ["ollama.exe"])
    monkeypatch.setattr(sto, "api_ps", lambda *a, **k: [{"context_length": 65536}])
    monkeypatch.setattr(sto, "CONFIG_LOG_CANDIDATES", [])

    assert sto.main(["--status"]) == 0
    assert "VERDICT TUNED" in capsys.readouterr().out


def test_live_ctx_wins_over_stale_log_line(monkeypatch, tmp_path, capsys):
    """日志行与实测冲突时以实测为准：不许因旧行把在跑的 tuned 服务判成坏的。

    这是真踩过的坑——2026-10-18 17:39 的首版修正版就因为读到 server.log 的
    旧行，对着一台 ctx=65536 的正常服务报了 NOT_TUNED。
    """
    stale = tmp_path / "server.log"
    stale.write_text(
        _cfg_line("2026-10-18T17:12:15.061+08:00", DEFAULT_CFG) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(sto, "CONFIG_LOG_CANDIDATES", [stale])
    monkeypatch.setattr(sto, "port_open", lambda *a, **k: True)
    monkeypatch.setattr(sto, "ollama_procs", lambda: ["ollama.exe"])
    monkeypatch.setattr(sto, "api_ps", lambda *a, **k: [{"context_length": 65536}])

    rc = sto.main(["--status"])
    out = capsys.readouterr().out
    assert rc == 0, "实测 ctx 达标时不能因为旧日志行报错"
    assert "VERDICT TUNED" in out
    assert "冲突" in out, "冲突要显式告警，不能静默忽略"
