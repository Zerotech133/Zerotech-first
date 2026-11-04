# -*- coding: utf-8 -*-
"""D1 training episodes 只读 API（IFS v1 F5；WO-IFS-TRAINAPI-20261104）。

路由（route baseline 同批登记）
------------------------------
- ``GET /api/d1/training/episodes[?limit=N]``：读取
  ``agent_platform/data/training/episodes.jsonl``，返回 episode 列表摘要；
- ``GET /api/d1/training/episodes/<episode_id>``：单条 episode 详情。

只读与诚实语义（硬约束）
------------------------
- **只读**：不写盘、不训练、不执行、不改 registry/paper；``readonly=true`` /
  ``paper_only=true`` / ``auto_training=false`` / ``trading_permitted=false``；
- **不造数**：数据文件不存在 → ``ok=true, data_unavailable=true, count=0``
  （不是 500、不生成样例）；文件存在但为空 → ``data_unavailable=false, count=0``；
- **坏行 fail-visible**：坏 JSON / 非 object / schema 不符 / episode_id 非法逐行计数，
  记入 ``invalid_lines`` 与 ``errors[前10条]``，不中断、不静默吞掉；
- 数据文件可用 ``D1_TRAINING_EPISODES_PATH`` 覆盖（测试/多环境用，指向临时文件）。

契约：``training_episode/v1``（docs/standards/2026-11-03_OUTCOME_TRAIN_契约_v1.md）；
本 API 只读消费，生产写入路径由 F5 模块 ``agent_platform/training/outcome_chain.py``
显式路径控制（本模块不调用任何写函数）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs

import ifs_envelope

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

SCHEMA = "training_episode/v1"
API_SCHEMA = "d1_training_api/v1"
EPISODES_ENV = "D1_TRAINING_EPISODES_PATH"
DEFAULT_EPISODES = _REPO / "agent_platform" / "data" / "training" / "episodes.jsonl"
MAX_LIMIT = 200
DEFAULT_LIMIT = 50
EPISODE_ID_RE = re.compile(r"^ep-\d{8}-[0-9A-F]{8}$")
_LIST_ROUTE = "/api/d1/training/episodes"
_DETAIL_PREFIX = _LIST_ROUTE + "/"


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def episodes_path(path=None):
    """数据源路径：显式参数 > 环境变量 D1_TRAINING_EPISODES_PATH > 默认。"""
    return Path(path or os.environ.get(EPISODES_ENV) or DEFAULT_EPISODES)


def _rel(path):
    """报告用路径（仓库内相对 POSIX；不在仓库内则绝对路径）。"""
    target = Path(path)
    try:
        return str(target.resolve().relative_to(_REPO.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(target)


def read_episodes(path=None):
    """读取 episodes.jsonl → ``{episodes, count, invalid_lines, errors, data_unavailable, note, source}``。"""
    target = episodes_path(path)
    result = {
        "schema_version": SCHEMA,
        "data_unavailable": False,
        "episodes": [],
        "count": 0,
        "invalid_lines": 0,
        "errors": [],
        "source": _rel(target),
    }
    if not target.is_file():
        result["data_unavailable"] = True
        result["note"] = "episodes.jsonl 不存在：训练 episode 数据尚未生成（不做样例、不造数）"
        return result
    try:
        text = target.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        result["data_unavailable"] = True
        result["note"] = "episodes.jsonl 读取失败：%s" % str(exc)[:200]
        return result
    episodes = []
    errors = []
    invalid = 0
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            invalid += 1
            errors.append({"line": line_no, "code": "bad_json"})
            continue
        if not isinstance(row, dict):
            invalid += 1
            errors.append({"line": line_no, "code": "not_object"})
            continue
        if row.get("schema") != SCHEMA:
            invalid += 1
            errors.append(
                {"line": line_no, "code": "bad_schema", "schema": row.get("schema")}
            )
            continue
        episode_id = str(row.get("training_episode_id") or "")
        if not EPISODE_ID_RE.match(episode_id):
            invalid += 1
            errors.append(
                {"line": line_no, "code": "bad_episode_id", "training_episode_id": episode_id[:64]}
            )
            continue
        episodes.append(row)
    result.update(
        episodes=episodes,
        count=len(episodes),
        invalid_lines=invalid,
        errors=errors[:10],
    )
    if invalid:
        result["note"] = "存在 %d 行坏数据（已逐行跳过并计数，见 invalid_lines/errors）" % invalid
    return result


def _flags():
    """统一只读/paper 标记（所有响应必带，防止消费方误判）。"""
    return {
        "api_schema": API_SCHEMA,
        "schema_version": SCHEMA,
        "readonly": True,
        "paper_only": True,
        "not_a_trade": True,
        "auto_training": False,
        "trading_permitted": False,
        "generated_at": _now_iso(),
    }


def list_payload(limit=DEFAULT_LIMIT, path=None):
    """列表响应：``count`` 为本次返回条数、``total`` 为有效 episode 总数。"""
    data = read_episodes(path)
    episodes = data["episodes"][: max(0, int(limit))]
    payload = {
        "ok": True,
        **_flags(),
        "data_unavailable": data["data_unavailable"],
        "count": len(episodes),
        "total": data["count"],
        "invalid_lines": data["invalid_lines"],
        "episodes": episodes,
        "errors": data["errors"],
        "source": data["source"],
    }
    if data.get("note"):
        payload["note"] = data["note"]
    return payload


def detail_payload(episode_id, path=None):
    """详情响应：``(payload, http_code)``；缺数据诚实 data_unavailable，不造数。"""
    episode_id = str(episode_id or "").strip().strip("/")
    if not EPISODE_ID_RE.match(episode_id):
        return (
            {"ok": False, "error": "bad_episode_id",
             "message": "episode_id 必须形如 ep-YYYYMMDD-XXXXXXXX"},
            400,
        )
    data = read_episodes(path)
    base = {
        **_flags(),
        "data_unavailable": data["data_unavailable"],
        "invalid_lines": data["invalid_lines"],
        "source": data["source"],
    }
    if data["data_unavailable"]:
        base.update({"ok": True, "episode": None, "count": 0})
        if data.get("note"):
            base["note"] = data["note"]
        return base, 200
    for row in data["episodes"]:
        if row.get("training_episode_id") == episode_id:
            base.update({"ok": True, "episode": row, "count": 1})
            return base, 200
    base.update({"ok": False, "error": "episode_not_found", "episode": None, "count": 0})
    return base, 404


def _json(handler, obj, code=200):
    ifs_envelope.send(handler, obj, code, fallback_interface="training.episode.get")


def _query_limit(qs):
    raw = (parse_qs(qs or "").get("limit") or [""])[0].strip()
    if not raw:
        return DEFAULT_LIMIT, ""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, "bad_limit"
    if value <= 0:
        return None, "bad_limit"
    return min(value, MAX_LIMIT), ""


def handle_get(handler, qs=""):
    """portal_api GET 分派入口：只读，不写盘、不训练。"""
    path = handler.path.split("?", 1)[0]
    if path == _LIST_ROUTE:
        limit, err = _query_limit(qs)
        if err:
            _json(handler, {"ok": False, "error": err,
                            "message": "limit 必须为正整数（上限 %d）" % MAX_LIMIT}, 400)
            return
        _json(handler, list_payload(limit), 200)
        return
    if path.startswith(_DETAIL_PREFIX):
        episode_id = path[len(_DETAIL_PREFIX):].strip("/")
        payload, code = detail_payload(episode_id)
        _json(handler, payload, code)
        return
    _json(handler, {"ok": False, "error": "not_found"}, 404)


def main(argv=None):
    """只读自检 CLI（便于接线验收；不写盘、不训练）。"""
    parser = argparse.ArgumentParser(description="D1 training episodes 只读自检（IFS F5）")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--episode-id", default="", help="查看单条 episode")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--path", default="", help="覆盖 episodes.jsonl 路径（只读）")
    args = parser.parse_args(argv)
    path = args.path or None
    if args.episode_id:
        payload, _code = detail_payload(args.episode_id, path=path)
    else:
        payload = list_payload(limit=args.limit, path=path)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            "[d1_training] ok=%s data_unavailable=%s count=%s total=%s invalid_lines=%s source=%s"
            % (
                payload.get("ok"),
                payload.get("data_unavailable"),
                payload.get("count"),
                payload.get("total", payload.get("count")),
                payload.get("invalid_lines"),
                payload.get("source"),
            )
        )
    return 0 if payload.get("ok") else 1


__all__ = [
    "API_SCHEMA",
    "DEFAULT_EPISODES",
    "DEFAULT_LIMIT",
    "EPISODES_ENV",
    "EPISODE_ID_RE",
    "MAX_LIMIT",
    "SCHEMA",
    "detail_payload",
    "episodes_path",
    "handle_get",
    "list_payload",
    "main",
    "read_episodes",
]


if __name__ == "__main__":
    sys.exit(main())
