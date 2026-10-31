"""赵云 · 治理席前端页测试（WO-LEGION-ZHAOYUNPAGE-20261026 / ENH-20261026）。

覆盖：/api/zhaoyun 聚合函数（能力卡/判例库全量/建议/时钟/签字链/保活）、
治理健康（AI 建议 + 经验库红/黄/绿三档、gov_ai import 失败 fail-soft）、
待签 payload 扫描（两种签名命名、有/无签名、归档目录跳过、目录缺失 fail-soft）、
数据源缺失时 fail-soft、静态页存在性与关键契约（fetch /api/zhaoyun / textContent / 禁 innerHTML）。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
for _p in (str(_PLATFORM), str(_PLATFORM / "legion_command")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from legion_command import zhaoyun_view as V  # noqa: E402

_TZ8 = dt.timezone(dt.timedelta(hours=8))


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def _now_after_advice() -> float:
    """固定判级时刻（夹具建议生成于 13:00/13:10），保证健康档位不受跑测时间影响。"""
    return dt.datetime(2026, 10, 26, 13, 20, tzinfo=_TZ8).timestamp()


def _write_experience(root: Path, rows: list[dict]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "_index.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    return root


def _fixture(tmp_path: Path) -> dict:
    platform = tmp_path / "repo" / "agent_platform"
    profile = tmp_path / "profile"
    exp_root = tmp_path / "experience"
    sign_root = tmp_path / "sign_requests"
    sign_root.mkdir(parents=True, exist_ok=True)
    _write(
        profile / "skills" / "CAPABILITIES.json",
        {
            "total": 3,
            "ready": 2,
            "pending_key": 1,
            "capabilities": [
                {"name": "scan", "what": "扫描", "boundary": "只读", "status": "ready"},
                {
                    "name": "case-sign-chain",
                    "what": "签字链",
                    "boundary": "人签",
                    "status": "ready",
                },
                {
                    "name": "harness-runner",
                    "what": "执行框架",
                    "boundary": "DSH",
                    "status": "pending_key",
                },
            ],
        },
    )
    rows = [
        {
            "entry_id": "EXP-SUPERVISION-001",
            "domain": "supervision",
            "cue": "假成功",
            "lesson": "正文不应出现在只读视图",
            "confidence": 0.9,
            "signed_by": "老田",
            "written_at": "2026-10-26T12:46:53+0800",
            "verified": True,
        },
        {
            "entry_id": "EXP-SUPERVISION-002",
            "domain": "supervision",
            "cue": "快照假绿",
            "lesson": "正文不应出现在只读视图",
            "confidence": 0.9,
            "signed_by": "老田",
            "written_at": "2026-10-26T12:47:00+0800",
            "verified": True,
        },
    ]
    _write_experience(exp_root, rows)
    advice = platform / "data" / "governance_ai" / "advice" / "2026-10-26"
    _write(
        advice / "anomaly_digest.json",
        {
            "status": "draft",
            "generated_at": "2026-10-26T13:00:00+08:00",
            "input_digest": "abc123def456",
            "model": "deepseek-flash",
            "items": [
                {
                    "id": "det-1",
                    "title": "取消码误计",
                    "confidence": 0.8,
                    "evidence": ["exit:3221225786"],
                }
            ],
            "validator": {"schema_ok": True, "grounding_ok": True, "z5_ok": True},
        },
    )
    _write(
        advice / "priority_advice.json",
        {
            "status": "draft",
            "generated_at": "2026-10-26T13:10:00+08:00",
            "items": [
                {
                    "id": "det-2",
                    "title": "压降失败率",
                    "confidence": 0.8,
                    "evidence": ["health:ai.error_rate"],
                }
            ],
        },
    )
    clock_dir = platform / "data"
    _write(
        clock_dir / "governance_clock_state.json",
        {
            "last_tick_iso": "2026-10-26T13:20:00+08:00",
            "fired": {"2026-10-26|08:46": {"action": "scan", "ok": True}},
        },
    )
    _write(
        clock_dir / "governance_clock.lock",
        {"pid": 12345, "started_at": "2026-10-26T11:28:05+08:00"},
    )
    return {
        "platform": platform,
        "profile": profile,
        "experience_root": exp_root,
        "sign_requests_root": sign_root,
    }


@pytest.fixture(autouse=True)
def _silence_keepalive(monkeypatch):
    """所有用例都不依赖 C:\\Temp 真实保活日志。"""
    monkeypatch.setattr(
        V, "_keepalive", lambda: {"available": False, "last": "", "lines": 0}
    )


def test_build_view_full(monkeypatch, tmp_path):
    fx = _fixture(tmp_path)
    # WO-ZHAOYUN-PIDPROBE：进程探测注入固定值，不依赖真实系统 PID/进程表
    # （旧版直接 OpenProcess(12345)，整包并发撞 PID 即假红）。
    probed = []
    monkeypatch.setattr(V, "PID_PROBE", lambda pid: probed.append(pid) or False)
    monkeypatch.setattr(
        V,
        "_keepalive",
        lambda: {
            "available": True,
            "last": "2026-10-26 13:25:00 OK alive pid=30616",
            "lines": 9,
        },
    )
    view = V.build_view(
        fx["platform"],
        profile=fx["profile"],
        experience_root=fx["experience_root"],
        now=_now_after_advice(),
        sign_requests_root=fx["sign_requests_root"],
    )
    assert view["ok"] is True
    assert view["identity"]["name"] == "赵云"
    assert view["capabilities"]["total"] == 3
    assert view["capabilities"]["ready"] == 2
    assert [c["name"] for c in view["capabilities"]["items"]][:1] == ["scan"]
    assert view["experience"]["entries_total"] == 2
    assert view["experience"]["verified_total"] == 2
    assert len(view["experience"]["all"]) == 2
    assert len(view["experience"]["recent"]) == 2
    assert view["advice"]["anomaly_digest"]["status"] == "draft"
    assert view["advice"]["anomaly_digest"]["items"][0]["title"] == "取消码误计"
    assert view["advice"]["case_draft"]["exists"] is False
    assert view["clock"]["pid"] == 12345  # pid 字段保留
    assert view["clock"]["alive"] is False  # 注入探测返回 False
    assert probed == [12345]  # 探测函数被调用且入参=夹具 pid（不触真实系统调用）
    assert view["clock"]["last_tick"] == "2026-10-26T13:20:00+08:00"
    assert view["keepalive"]["last"].startswith("2026-10-26 13:25")
    assert view["sign_chain"]["entries_total"] == 2
    assert view["sign_chain"]["verified_total"] == 2
    assert "老田" in view["sign_chain"]["private_key_holder"]
    # 治理健康：夹具建议 13:10 生成 + 经验库全 verified → 双绿
    assert view["governance_health"]["ai_advice"]["level"] == "green"
    assert view["governance_health"]["experience_library"]["level"] == "green"
    # 待签：空 tmp 目录 → 0 条且 ok
    assert view["pending_signatures"]["ok"] is True
    assert view["pending_signatures"]["count"] == 0


def test_build_view_missing_sources_fail_soft(tmp_path):
    view = V.build_view(
        tmp_path / "empty",
        profile=tmp_path / "no-profile",
        experience_root=tmp_path / "no-exp",
        sign_requests_root=tmp_path / "no-sign",
    )
    assert view["ok"] is True
    assert view["capabilities"]["total"] == 0
    assert view["experience"]["entries_total"] == 0
    assert view["experience"]["all"] == []
    assert view["advice"]["anomaly_digest"]["exists"] is False
    assert view["clock"]["pid"] is None
    assert view["clock"]["alive"] is False
    # 空经验库 = 红；无建议产物 = 黄；签名目录缺失 = fail-soft 不炸
    assert view["governance_health"]["experience_library"]["level"] == "red"
    assert view["governance_health"]["ai_advice"]["level"] == "yellow"
    assert view["pending_signatures"]["ok"] is False
    assert view["pending_signatures"]["count"] == 0


def test_experience_all_returns_full_list_without_lesson(tmp_path):
    exp_root = tmp_path / "experience"
    rows = [
        {
            "entry_id": "EXP-SUPERVISION-%03d" % i,
            "domain": "supervision",
            "cue": "cue-%02d" % i,
            "lesson": "判例正文-不得外泄-%02d" % i,
            "confidence": round(0.5 + i / 100.0, 2),
            "signed_by": "老田",
            "written_at": "2026-10-26T12:%02d:00+0800" % i,
            "verified": True,
        }
        for i in range(1, 13)
    ]
    _write_experience(exp_root, rows)
    view = V.build_view(
        tmp_path / "platform",
        profile=tmp_path / "no-profile",
        experience_root=exp_root,
        sign_requests_root=tmp_path / "no-sign",
    )
    exp = view["experience"]
    assert exp["ok"] is True
    assert exp["entries_total"] == 12
    assert exp["verified_total"] == 12
    assert len(exp["all"]) == 12  # 全量：12 条不截断
    assert len(exp["recent"]) == V._RECENT_KEEP  # 兼容字段仍只留最近 5 条
    assert [r["entry_id"] for r in exp["all"]][:2] == [
        "EXP-SUPERVISION-012",
        "EXP-SUPERVISION-011",
    ]  # written_at 倒序
    assert exp["all"][0]["cue"] == "cue-12"
    assert exp["all"][0]["confidence"] == 0.62
    assert exp["all"][0]["signed_by"] == "老田"
    assert exp["all"][0]["written_at"] == "2026-10-26T12:12:00+0800"
    assert exp["all"][0]["verified"] is True
    assert exp["domains"]["supervision"]["entries"] == 12
    # 只读摘要原则：不返回 lesson 正文
    assert all("lesson" not in r for r in exp["all"])
    assert all("判例正文" not in json.dumps(r, ensure_ascii=False) for r in exp["all"])


def _build_with_experience(tmp_path: Path, rows: list[dict]) -> dict:
    exp_root = (
        _write_experience(tmp_path / "exp", rows) if rows else (tmp_path / "exp0")
    )
    return V.build_view(
        tmp_path / "platform",
        profile=tmp_path / "no-profile",
        experience_root=exp_root,
        now=_now_after_advice(),
        sign_requests_root=tmp_path / "no-sign",
    )


def test_governance_health_experience_library_three_levels(tmp_path):
    # 0 条 → 红
    red = _build_with_experience(tmp_path / "a", [])
    assert red["governance_health"]["experience_library"]["level"] == "red"
    assert "经验库为空" in red["governance_health"]["experience_library"]["detail"]

    # 有未验签 → 黄
    yellow = _build_with_experience(
        tmp_path / "b",
        [
            {
                "entry_id": "EXP-SUPERVISION-001",
                "domain": "supervision",
                "cue": "甲",
                "confidence": 0.9,
                "signed_by": "老田",
                "written_at": "2026-10-26T12:00:00+0800",
                "verified": True,
            },
            {
                "entry_id": "EXP-SUPERVISION-002",
                "domain": "supervision",
                "cue": "乙",
                "confidence": 0.8,
                "signed_by": "老田",
                "written_at": "2026-10-26T12:01:00+0800",
                "verified": False,
            },
        ],
    )
    h = yellow["governance_health"]["experience_library"]
    assert h["level"] == "yellow"
    assert "存在未验签" in h["detail"]

    # 全 verified → 绿
    green = _build_with_experience(
        tmp_path / "c",
        [
            {
                "entry_id": "EXP-SUPERVISION-001",
                "domain": "supervision",
                "cue": "甲",
                "confidence": 0.9,
                "signed_by": "老田",
                "written_at": "2026-10-26T12:00:00+0800",
                "verified": True,
            },
        ],
    )
    h = green["governance_health"]["experience_library"]
    assert h["level"] == "green"
    assert "全部 verified" in h["detail"]
    assert "supervision=1" in h["detail"]


def test_governance_health_ai_advice_three_levels(tmp_path):
    now = _now_after_advice()
    # 无产物 → 黄
    yellow = V.build_view(
        tmp_path / "empty",
        profile=tmp_path / "no-profile",
        experience_root=tmp_path / "no-exp",
        now=now,
        sign_requests_root=tmp_path / "no-sign",
    )
    assert yellow["governance_health"]["ai_advice"]["level"] == "yellow"
    assert "不可用" not in yellow["governance_health"]["ai_advice"]["detail"]

    # 连续失败 2 次 → 红
    platform = tmp_path / "platform_red"
    advice = platform / "data" / "governance_ai" / "advice" / "2026-10-26"
    for i, name in enumerate(("anomaly_digest", "priority_advice")):
        _write(
            advice / (name + ".json"),
            {
                "status": "failed",
                "generated_at": "2026-10-26T13:%02d:00+08:00" % (i + 1),
                "items": [],
            },
        )
    red = V.build_view(
        platform,
        profile=tmp_path / "no-profile",
        experience_root=tmp_path / "no-exp",
        now=now,
        sign_requests_root=tmp_path / "no-sign",
    )
    assert red["governance_health"]["ai_advice"]["level"] == "red"
    assert "连续失败" in red["governance_health"]["ai_advice"]["detail"]


def test_governance_health_ai_advice_fail_soft_on_import_error(monkeypatch, tmp_path):
    def _boom():
        raise ImportError("gov_ai broken for test")

    monkeypatch.setattr(V, "_load_gov_ai", _boom)
    view = V.build_view(
        tmp_path / "platform",
        profile=tmp_path / "no-profile",
        experience_root=tmp_path / "no-exp",
        sign_requests_root=tmp_path / "no-sign",
    )
    h = view["governance_health"]["ai_advice"]
    assert h["level"] == "yellow"
    assert h["detail"].startswith("不可用:")
    assert view["ok"] is True  # 健康源坏掉不拖垮整页


def test_pending_signatures_two_naming_styles(tmp_path):
    root = tmp_path / "sign_requests"
    batch = root / "batch1"
    nested = root / "batch2" / "nested"
    # A：<item_id>.signature.json（已签）
    _write(batch / "EXP-A.payload.json", {"item_id": "EXP-A"})
    (batch / "EXP-A.signature.json").write_text("{}", encoding="utf-8")
    # B：<item_id>.payload.json.signature.json（已签，嵌套目录）
    _write(nested / "EXP-B.payload.json", {"item_id": "EXP-B"})
    (nested / "EXP-B.payload.json.signature.json").write_text("{}", encoding="utf-8")
    # C、D：无签名 → 待签
    _write(batch / "EXP-C.payload.json", {"item_id": "EXP-C"})
    _write(root / "EXP-D.payload.json", {"item_id": "EXP-D"})
    # 非 EXP 前缀不参与统计
    _write(batch / "other.payload.json", {"item_id": "other"})

    view = V.build_view(
        tmp_path / "platform",
        profile=tmp_path / "no-profile",
        experience_root=tmp_path / "no-exp",
        sign_requests_root=root,
    )
    ps = view["pending_signatures"]
    assert ps["ok"] is True
    assert ps["count"] == 2
    assert ps["scanned"] == 4
    assert ps["signed"] == 2
    assert {it["item_id"] for it in ps["items"]} == {"EXP-C", "EXP-D"}
    assert all(it["payload_path"].endswith(".payload.json") for it in ps["items"])
    assert all(Path(it["payload_path"]).is_file() for it in ps["items"])


def test_pending_signatures_archived_dir_is_not_pending(tmp_path):
    """归档目录（_ 前缀，如 _superseded）里的未签 payload 不催签，只统计供审计。"""
    root = tmp_path / "sign_requests"
    _write(root / "20261026" / "EXP-NEW.payload.json", {})
    (root / "20261026" / "EXP-NEW.signature.json").write_text("{}", encoding="utf-8")
    _write(root / "_superseded_case_20261026_123545" / "EXP-OLD.payload.json", {})

    ps = V.build_view(
        tmp_path / "platform",
        profile=tmp_path / "no-profile",
        experience_root=tmp_path / "no-exp",
        sign_requests_root=root,
    )["pending_signatures"]
    assert ps["ok"] is True
    assert ps["count"] == 0
    assert ps["archived_pending"] == 1
    assert ps["scanned"] == 1 and ps["signed"] == 1


def test_default_sign_requests_root_is_real_vault():
    """默认扫描根仍是真实 _vault\\sign_requests（测试通过注入 tmp 目录隔离）。"""
    assert V.DEFAULT_SIGN_REQUESTS_ROOT == Path(r"C:\项目\_vault\sign_requests")


def test_static_page_contract():
    page = (
        Path(__file__).resolve().parents[1]
        / "legion_command"
        / "static"
        / "zhaoyun.html"
    )
    text = page.read_text(encoding="utf-8")
    assert "赵云" in text and "治理席" in text
    assert "fetch('/api/zhaoyun')" in text.replace('"', "'")
    for marker in (
        "能力卡",
        "判例库",
        "治理 AI",
        "签字链",
        "身份对照",
        "治理 AI 健康",
        "经验库健康",
        "无待签",
    ):
        assert marker in text
    for field in (
        "governance_health",
        "experience_library",
        "pending_signatures",
        "exp.all",
    ):
        assert field in text
    assert "setInterval(load, 60000)" in text  # 保留 60s 自刷新
    assert "textContent" in text  # 数据渲染走 textContent（防注入）
    assert "innerHTML" not in text  # 禁止 innerHTML 拼接数据
    assert "PRIVATE KEY" not in text  # 页面不得出现任何私钥内容


def test_index_has_zhaoyun_tab():
    idx = (
        Path(__file__).resolve().parents[1] / "legion_command" / "static" / "index.html"
    ).read_text(encoding="utf-8")
    assert 'data-v="zhaoyun"' in idx
    assert "/static/zhaoyun.html" in idx


def test_pid_probe_injectable_exists_and_absent(monkeypatch):
    """PID_PROBE 可注入：默认实现不变，用例不依赖真实系统进程。"""
    assert V.PID_PROBE is V._default_pid_probe  # 默认行为不变
    monkeypatch.setattr(V, "PID_PROBE", lambda pid: pid == 12345)
    assert V._pid_alive(12345) is True  # 注入"存在"
    assert V._pid_alive(99999999) is False  # 注入"不存在"
    monkeypatch.setattr(V, "PID_PROBE", lambda pid: (_ for _ in ()).throw(OSError("boom")))
    assert V._pid_alive(12345) is False  # 探测异常 → fail-closed


def test_pid_alive_bad_pid_short_circuit(monkeypatch):
    """边界：非法/非正 pid 直接 False，且不进入探测函数（不触系统调用）。"""
    called = []
    monkeypatch.setattr(V, "PID_PROBE", lambda pid: called.append(pid) or True)
    for bad in ("not-a-pid", None, -1, 0):
        assert V._pid_alive(bad) is False
    assert called == []


def test_no_os_kill_probe_in_view():
    """回归锚点：不得再用 os.kill 探活（Windows 语义会 TerminateProcess）。"""
    src = (
        Path(__file__).resolve().parents[1] / "legion_command" / "zhaoyun_view.py"
    ).read_text(encoding="utf-8")
    assert "os.kill(pid, 0)" in src  # 仅 POSIX 分支允许出现
    assert "OpenProcess" in src  # Windows 分支必须用 OpenProcess
    # 出现位置必须在 POSIX 分支：Windows 分支（os.name == "nt"）早于最后的 os.kill
    assert src.index('os.name == "nt"') < src.rindex("os.kill(pid, 0)")


def test_serve_has_clean_zhaoyun_route():
    src = (
        Path(__file__).resolve().parents[1] / "legion_command" / "serve.py"
    ).read_text(encoding="utf-8")
    assert '"/zhaoyun"' in src and '"zhaoyun.html"' in src
