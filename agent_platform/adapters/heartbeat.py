# -*- coding: utf-8 -*-
"""Paper 引擎心跳采集适配器（Paperclip 心跳对接第一刀，B 路）。

职责：周期性采集 Paper 交易引擎的存活/健康状态，落一条幂等心跳行
（data/heartbeat/heartbeat.jsonl），供门3 巡检/证据仓未来接线。

采集源本阶段为「探针抽象」——部署未发生，不造不可验的真实 Docker/网络
探针（红线：演示永不落真仓）：
  - HeartbeatProbe 协议：probe() -> {source, alive, detail, checked_at}
  - NullProbe：恒 alive=False + detail="not_configured"（未接线的诚实语义）
  - FileProbe：读显式传入的状态文件路径（JSON），坏文件 fail-visible

红线继承（M5 判例平移，安全阀与 engine/strategy_registry.record_verdicts
同形）：
  - 无显式 root 拒写（fail-visible 安全阀，M5 刀2 判例）；
  - 先全量校验后开写——坏样本零副作用（不建目录/不留半文件）；
  - 挂 SHADOW_DATA_ROOT 自动继承隔离根（run_shadow 同款，预演零污染）；
  - CLI 无 --persist-root 时只打印不落账（dry-run，退出码 0）；
  - 策略只产建议零凭据：本模块零凭据、零网络、零真实探针。
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

SCHEMA_VERSION = "hb.v1"
HEARTBEAT_DIR_NAME = "heartbeat"
HEARTBEAT_LEDGER_NAME = "heartbeat.jsonl"

# checked_at 分钟粒度（幂等键的时间维度）：YYYY-MM-DDTHH:MM
_MINUTE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")


class HeartbeatError(Exception):
    """心跳采集/落账契约非法（探针坏文件/字段缺失/安全阀触发）"""


def now_minute(ts=None):
    """当前本地时间，分钟粒度 ISO 形态。"""
    return time.strftime("%Y-%m-%dT%H:%M", time.localtime(ts))


def validate_checked_at(v):
    """checked_at 必须是合法的分钟粒度 ISO 时间戳。"""
    if not isinstance(v, str) or not _MINUTE_RE.match(v):
        raise HeartbeatError(
            "checked_at=%r 非分钟粒度 ISO 形态 (YYYY-MM-DDTHH:MM)" % (v,)
        )
    try:
        time.strptime(v, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise HeartbeatError("checked_at=%r 非法日期时间" % (v,)) from None
    return v


def validate_sample(sample):
    """心跳样本全量校验（先校验后开写的前半段）。返回规整后的 dict。"""
    if not isinstance(sample, dict):
        raise HeartbeatError("心跳样本必须是 dict，得到 %r" % type(sample).__name__)
    source = sample.get("source")
    if not isinstance(source, str) or not source.strip():
        raise HeartbeatError("source 缺失或非字符串")
    alive = sample.get("alive")
    if not isinstance(alive, bool):
        raise HeartbeatError("alive 必须是 bool，得到 %r" % (alive,))
    detail = sample.get("detail", "")
    if not isinstance(detail, str):
        raise HeartbeatError("detail 必须是字符串")
    checked_at = validate_checked_at(sample.get("checked_at"))
    return {
        "source": source.strip(),
        "alive": alive,
        "detail": detail[:200],
        "checked_at": checked_at,
    }


def heartbeat_key(source, checked_at):
    """幂等键：checked_at 分钟粒度 + source（同分钟同源=同键覆盖语义跳过）。"""
    raw = "%s|%s" % (checked_at, source)
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def resolve_root(root=None):
    """数据根解析：显式 root 优先；否则继承 SHADOW_DATA_ROOT 隔离根；
    两者皆无 → None（后续 _ledger_path 安全阀拒写）。"""
    if root:
        return Path(root)
    env = os.environ.get("SHADOW_DATA_ROOT")
    if env:
        return Path(env)
    return None


def _ledger_path(root):
    """账本路径。root 显式必填——M5 刀2 判例平移：无显式 root 拒绝落盘。"""
    if not root:
        raise HeartbeatError("root 缺失：心跳账本拒绝落盘（安全阀，fail-visible）")
    return Path(root) / HEARTBEAT_DIR_NAME / HEARTBEAT_LEDGER_NAME


def record_heartbeat(sample, root):
    """心跳入账（append-only + 幂等键防重）。root 显式必填。
    先全量校验后开写——坏样本零副作用（不建目录/不留半文件）。"""
    p = _ledger_path(root)
    src = validate_sample(sample)
    k = heartbeat_key(src["source"], src["checked_at"])
    seen = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    seen.add(json.loads(line).get("heartbeat_key"))
                except json.JSONDecodeError:
                    continue
    if k in seen:
        return {"recorded": 0, "replayed": 1, "path": str(p), "heartbeat_key": k}
    row = {
        "schema_version": SCHEMA_VERSION,
        "heartbeat_key": k,
        "source": src["source"],
        "alive": src["alive"],
        "detail": src["detail"],
        "checked_at": src["checked_at"],
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"recorded": 1, "replayed": 0, "path": str(p), "heartbeat_key": k}


# ---------------------------------------------------------------- 探针抽象


class HeartbeatProbe:
    """探针协议：子类实现 probe() 返回 {source, alive, detail, checked_at}。
    探针只读外部状态，不持任何凭据（红线：零凭据）。"""

    source = ""

    def probe(self):
        raise NotImplementedError("探针必须实现 probe()")


class NullProbe(HeartbeatProbe):
    """未接线探针：恒 alive=False + not_configured（诚实的降级语义）。"""

    source = "null"

    def probe(self):
        return {
            "source": self.source,
            "alive": False,
            "detail": "not_configured",
            "checked_at": now_minute(),
        }


class FileProbe(HeartbeatProbe):
    """文件探针：读显式传入的状态文件路径（JSON 对象），坏文件 fail-visible。
    文件形态：{"alive": bool, "detail": str?, "checked_at": "YYYY-MM-DDTHH:MM"?}
    缺 checked_at 时取采集时刻。不主动探测 Docker/网络（部署未发生）。"""

    source = "paperclip_org_shell"

    def __init__(self, state_file, source="paperclip_org_shell"):
        # 拍板③：source 二实体——paper_engine=交易引擎(军侧8790容器)，
        # paperclip_org_shell=组织壳(本仓探针默认)；两实体不得混用同一 source。
        if not state_file:
            raise HeartbeatError("FileProbe 需要显式 state_file 路径")
        self.state_file = Path(state_file)
        self.source = source

    def probe(self):
        if not self.state_file.exists():
            raise HeartbeatError("状态文件不存在：%s" % self.state_file)
        try:
            j = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise HeartbeatError("状态文件非合法 JSON：%s" % str(e)[:120]) from e
        if not isinstance(j, dict):
            raise HeartbeatError("状态文件顶层必须是 JSON 对象")
        if not isinstance(j.get("alive"), bool):
            raise HeartbeatError("状态文件缺 alive(bool)")
        detail = j.get("detail", "")
        if not isinstance(detail, str):
            raise HeartbeatError("状态文件 detail 必须是字符串")
        checked_at = j.get("checked_at") or now_minute()
        return {
            "source": self.source,
            "alive": j["alive"],
            "detail": detail,
            "checked_at": checked_at,
        }


def collect_and_record(probe, root=None):
    """采集一次并入账。root 显式优先，否则继承 SHADOW_DATA_ROOT；
    两者皆无 → 安全阀拒写（fail-visible）。"""
    sample = probe.probe()
    return record_heartbeat(sample, resolve_root(root))


# ---------------------------------------------------------------- CLI


def main(argv=None):
    """CLI：--probe null|file --state-file ... --persist-root ...
    无 --persist-root 时打印心跳 JSON 但不落账（退出码 0，stderr 提示 dry-run）；
    契约/安全阀失败退出码 2（fail-visible）。"""
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="Paper 引擎心跳采集（Paperclip 心跳对接；红线："
        "无显式 root 不落账，演示永不落真仓）"
    )
    ap.add_argument(
        "--probe",
        choices=("null", "file"),
        default="null",
        help="探针类型：null=未接线；file=读状态文件",
    )
    ap.add_argument(
        "--state-file", default=None, help="file 探针的状态文件路径（JSON）"
    )
    ap.add_argument(
        "--persist-root", default=None, help="数据根；缺省=dry-run 只打印不落账"
    )
    args = ap.parse_args(argv)

    if args.probe == "file":
        try:
            probe = FileProbe(args.state_file)
        except HeartbeatError as e:
            print("[FATAL] %s" % e, file=sys.stderr)
            return 2
    else:
        probe = NullProbe()
    try:
        sample = validate_sample(probe.probe())
    except HeartbeatError as e:
        print("[FATAL] %s" % e, file=sys.stderr)
        return 2

    if not args.persist_root:
        out = {
            "schema_version": SCHEMA_VERSION,
            "heartbeat_key": heartbeat_key(sample["source"], sample["checked_at"]),
        }
        out.update(sample)
        print(json.dumps(out, ensure_ascii=False))
        print("[dry-run] 无 --persist-root，心跳未落账", file=sys.stderr)
        return 0
    try:
        rep = collect_and_record(probe, root=args.persist_root)
    except HeartbeatError as e:
        print("[FATAL] %s" % e, file=sys.stderr)
        return 2
    print(json.dumps(rep, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
