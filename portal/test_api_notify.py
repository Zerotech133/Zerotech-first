# -*- coding: utf-8 -*-
"""K-ORG-201 W36：api_notify 四态队列只读聚合单测。

monkeypatch 风格沿 test_notification_decision.py；通过 env AIOS_ROOT 把
api_notify 的 REPO 锚指向 tmp 根，FakeHandler 子类注入 _json 捕获 payload
（_json 由 portal_api 主 Handler 提供，单测用 fake 等价替换）。
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_notify

TODAY = datetime.now().strftime("%Y-%m-%d")
NOW_ISO = datetime.now().isoformat(timespec="seconds")
NOW_STR = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# decision DB 列序（notification_decision._db 建表口径，K-ORG-203 起
# 含 doc_url/doc_title 文档引用列）
COLS = (
    "id",
    "category",
    "target_name",
    "target_id",
    "send_at",
    "reason",
    "content",
    "created_at",
    "status",
    "executed_at",
    "outcome",
    "agent_source",
    "caller",
    "operator",
    "basis",
    "doc_url",
    "doc_title",
)


class FakeHandler(api_notify.Handler):
    def __init__(self):
        self.captured = None
        self.code = 200

    def _json(self, payload, code=200):
        self.captured = payload
        self.code = code


def get(root, qs=""):
    h = FakeHandler()
    h.notify_queue(qs)
    return h


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("AIOS_ROOT", str(tmp_path))
    (tmp_path / "portal" / "work").mkdir(parents=True)
    return tmp_path


def make_db(root, rows):
    con = sqlite3.connect(root / "portal" / "work" / "notification_decisions.sqlite3")
    con.execute("CREATE TABLE decisions (%s)" % ",".join("%s TEXT" % c for c in COLS))
    con.executemany(
        "INSERT INTO decisions VALUES (%s)" % ",".join("?" * len(COLS)),  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
        [tuple(r.get(c, "") for c in COLS) for r in rows],
    )
    con.commit()
    con.close()


def make_flow(root, rows):
    p = root / "portal" / "work" / "push_flow.jsonl"
    p.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def full_fixtures(root):
    """全套在役账本假数据：覆盖四态 + 底座埋点 + deadline + matrix log/preview。"""
    make_db(
        root,
        [
            {
                "id": "d1",
                "category": "announcement",
                "target_name": "全员群",
                "send_at": NOW_ISO,
                "reason": "管理公告草案",
                "created_at": NOW_ISO,
                "status": "recorded",
                "agent_source": "human",
                "caller": "admin_ui",
                "basis": "依据拍板会",
                "doc_url": "https://example.feishu.cn/docx/AbC123",
                "doc_title": "公告背景说明",
            },
            {
                "id": "d2",
                "category": "alert",
                "target_name": "值班群",
                "send_at": NOW_ISO,
                "reason": "预警",
                "created_at": NOW_ISO,
                "status": "sent",
                "agent_source": "hermes",
                "caller": "hermes_internal",
            },
            {
                "id": "d3",
                "category": "alert",
                "target_name": "值班群",
                "send_at": NOW_ISO,
                "reason": "闸门拒收",
                "created_at": NOW_ISO,
                "status": "gate_or_send_failed",
                "agent_source": "hermes",
                "caller": "hermes_internal",
            },
            {
                "id": "d4",
                "category": "os",
                "target_name": "值班群",
                "send_at": NOW_ISO,
                "reason": "结果不明",
                "created_at": NOW_ISO,
                "status": "attempted",
                "agent_source": "hermes",
                "caller": "hermes_internal",
            },
            # 未知 status：诚实归 blocked（map_decision_state 兜底，不猜成功）
            {
                "id": "d5",
                "category": "watch_strategy",
                "target_name": "策略群",
                "send_at": NOW_ISO,
                "reason": "未来状态",
                "created_at": NOW_ISO,
                "status": "some_future_status",
                "agent_source": "hermes",
                "caller": "hermes_internal",
            },
        ],
    )
    make_flow(
        root,
        [
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "decision:alert",
                "channel": "8097",
                "target": "oc_x",
                "count": 1,
                "ok": 0,
                "tenant": "default",
            },
            # 闸门主动拦截行（_flow_gate_deny 判例）：策略内抑制 → idle 不冒充异常
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "matrix:us|deny:silent window",
                "channel": "hub-gate",
                "target": "oc_us",
                "count": 1,
                "ok": 0,
                "tenant": "default",
            },
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "matrix:cn",
                "channel": "8097",
                "target": "oc_daily",
                "count": 1,
                "ok": 1,
                "tenant": "default",
            },
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "auto:news_digest",
                "channel": "openapi",
                "target": "oc_base",
                "count": 3,
                "ok": 1,
                "tenant": "default",
            },
            # 10 天前的 ok=0：超出近 7 天回看窗，不进队列
            {
                "ts": NOW_ISO,
                "date": (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"),
                "kind": "subscribe:daily",
                "channel": "8097",
                "target": "oc_old",
                "count": 1,
                "ok": 0,
                "tenant": "default",
            },
        ],
    )
    (root / "portal" / "work" / "deadline_pending.json").write_text(
        json.dumps(
            {
                "generated_at": NOW_STR,
                "attempts": {"cn|morning": 2},
                "pending": [
                    {
                        "market": "cn",
                        "window": "morning",
                        "anchor": "jev",
                        "status": "stale",
                        "detail": "mtime 停在 08:00",
                        "deadline": "08:00",
                        "attempts": 2,
                        "escalated": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "portal" / "work" / "matrix_send_log.json").write_text(
        json.dumps(
            [
                {
                    "date": TODAY,
                    "market": "cn",
                    "window": "morning",
                    "group": "oc_daily",
                    "ts": NOW_STR,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "portal" / "work" / "matrix_send_preview.json").write_text(
        json.dumps(
            {
                "generated_at": NOW_ISO,
                "would_send": [{"group": "g1"}, {"group": "g2"}],
                "skipped": [{"group": "g3", "reason": "静默窗"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_four_states_counts_and_evidence(root):
    full_fixtures(root)
    h = get(root)
    p = h.captured
    assert h.code == 200 and p["ok"] is True
    # 四态计数：review=1（recorded），auto=1(sent)+2(流水今日聚合)+1(矩阵log)=4，
    # blocked=3(决策库 gate_or_send_failed/attempted/未知status)+1(流水ok=0)+1(deadline)=5；idle=1(闸门抑制行)
    assert p["states"] == {"auto": 4, "review": 1, "blocked": 5, "idle": 1}
    # review 优先排序在首
    assert p["items"][0]["state"] == "review"
    review = next(i for i in p["items"] if i["state"] == "review")
    assert review["category"] == "announcement" and review["target"] == "全员群"
    assert review["evidence_lifecycle"] == "drilled"  # 非真实发送行
    # K-ORG-203：决策行文档引用随行携带（未核实原样透出）；无引用行 doc 为空
    assert review["doc"] == {
        "url": "https://example.feishu.cn/docx/AbC123",
        "title": "公告背景说明",
    }
    assert next(i for i in p["items"] if i["id"] == "decision:d2")["doc"] == ""
    # 标需审批：登记表 decision_announcement 频道 autonomy=approval_required
    ch = next(c for c in p["channels"] if c["id"] == "decision_announcement")
    assert ch["autonomy"] == "approval_required" and not p["channels_errors"]
    # sent → auto 且送达证据=api_accepted（API 受理），生命周期=live_sent
    sent = next(i for i in p["items"] if i["id"] == "decision:d2")
    assert sent["state"] == "auto"
    assert sent["evidence_delivery"] == "api_accepted"
    assert sent["evidence_lifecycle"] == "live_sent"
    # gate_or_send_failed / attempted / 未知状态 → blocked（不猜成功）
    assert (
        sum(
            1
            for i in p["items"]
            if i["source"] == "decision_db" and i["state"] == "blocked"
        )
        == 3
    )
    # 流水 ok=0 → blocked，kind 原样，送达证据=dead_letter
    fb = next(
        i for i in p["items"] if i["source"] == "push_flow" and i["state"] == "blocked"
    )
    assert fb["kind"] == "decision:alert" and fb["evidence_delivery"] == "dead_letter"
    # auto: 前缀底座追溯账 → title 标注「底座埋点」
    track = next(i for i in p["items"] if i["kind"] == "auto:news_digest")
    assert track["state"] == "auto" and "底座埋点" in track["title"]
    # deadline 条目 → blocked，reason 用自带字段拼接
    dl = next(i for i in p["items"] if i["source"] == "deadline_flag")
    assert dl["state"] == "blocked" and "锚 jev" in dl["reason"]
    assert "连续未就绪 2 次" in dl["reason"] and "已升级 L3" in dl["reason"]
    # 矩阵 log 条目 → auto + live_sent
    ml = next(i for i in p["items"] if i["source"] == "matrix_log")
    assert ml["state"] == "auto" and ml["evidence_lifecycle"] == "live_sent"
    # preview 只进 meta.drills，不进 items
    assert p["meta"]["drills"] == 3
    assert all(i["source"] != "matrix_preview" for i in p["items"])
    assert p["meta"]["sources"] == {
        "decision_db": 5,
        "push_flow": 4,
        "deadline_flag": 1,
        "matrix_log": 1,
    }
    # 闸门策略内抑制行（hub-gate）→ idle，不冒充异常阻断；真实失败仍 blocked
    gate = next(i for i in p["items"] if "deny:silent" in i["kind"])
    assert gate["state"] == "idle" and "闸门抑制" in gate["title"]
    assert gate["evidence_delivery"] == ""
    # legend 诚实标注 idle 无账本
    assert "R6 事件总线预留" in p["legend"]["idle_note"]
    assert p["legend"]["evidence_lifecycle"]["live_sent"]
    # 全源在位：errors 为空
    assert p["errors"] == []


def test_flow_cap_50_and_merged_summary(root):
    make_flow(
        root,
        [
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "auto:k%d" % n,
                "channel": "openapi",
                "target": "oc_%d" % n,
                "count": 1,
                "ok": 1,
                "tenant": "default",
            }
            for n in range(51)
        ],
    )
    p = get(root).captured
    flow_auto = [i for i in p["items"] if i["source"] == "push_flow"]
    assert len(flow_auto) == 51  # 50 条 + 1 条合并汇总
    merged = next(i for i in flow_auto if i["id"] == "flowauto:merged")
    assert "1 组合并" in merged["title"] and merged["state"] == "auto"
    assert p["meta"]["sources"]["push_flow"] == 51


def test_zero_param_constraint(root):
    full_fixtures(root)
    h = get(root, "foo=1")
    assert h.code == 400 and h.captured == {"ok": False, "error": "本端点零参数"}
    # dict 形态（stub 测试双形态兼容，api_perf 教训）
    assert get(root, {"a": 1}).code == 400
    assert get(root, {}).code == 200
    assert get(root, "").code == 200


def test_missing_sources_fail_visible(root):
    p = get(
        root
    ).captured  # 只建了 work 目录：决策 DB/流水缺失，deadline/matrix 合法空态
    assert p["ok"] is True and p["items"] == []
    assert p["states"] == {"auto": 0, "review": 0, "blocked": 0, "idle": 0}
    assert p["meta"]["drills"] == 0
    assert len(p["errors"]) == 2  # 决策 DB + 发送流水
    assert any("决策 DB" in e for e in p["errors"])
    assert not any("deadline 旗标" in e for e in p["errors"])  # 缺失=合法空态


def test_corrupt_sources_fail_visible(root):
    (root / "portal" / "work" / "push_flow.jsonl").write_text(
        "{broken json\nnot a line\n", encoding="utf-8"
    )
    (root / "portal" / "work" / "deadline_pending.json").write_text(
        "{bad", encoding="utf-8"
    )
    (root / "portal" / "work" / "matrix_send_preview.json").write_text(
        "[", encoding="utf-8"
    )
    p = get(root).captured
    assert p["ok"] is True  # 不炸
    assert p["items"] == []  # 流水坏行全跳过
    assert any("deadline 旗标" in e for e in p["errors"])
    assert any("矩阵预演账" in e for e in p["errors"])


def test_readonly_no_write_to_repo(root):
    full_fixtures(root)
    api_notify.notify_queue_payload()  # 预热（模块级缓存不存在，纯读）

    def snapshot():
        out = {}
        for dirpath, dirnames, filenames in os.walk(root):
            for n in dirnames + filenames:
                st = (Path(dirpath) / n).stat()
                out[str(Path(dirpath, n).relative_to(root))] = (
                    st.st_size,
                    st.st_mtime_ns,
                )
        return out

    before = snapshot()
    p = api_notify.notify_queue_payload()
    assert snapshot() == before  # 文件清单/mtime/size 全程一致
    assert p["ok"] is True
