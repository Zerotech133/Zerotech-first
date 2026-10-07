#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书卡片回调监听器：监听 card.action.trigger 事件，处理告警卡片「已处理/忽略」按钮。

工作流：
  1. 启动 lark-cli event consume card.action.trigger --as bot（子进程，stdin 管道保持打开）
     stderr 合并进 stdout 单流消费，防双管道缓冲填满死锁（P0-1）
  2. 后台线程读行入队，主循环带超时消费：ready 等待 15s 上限，不会永久阻塞（P0-1）
  3. 对 button 回调：解析 action_value → 去重 → ack DB → 更新卡片（P0-2）

一致性设计（P0-2）：
  - 去重键 token:action:alert_id，仅在 DB ack 成功后记录（ack 失败允许飞书重投再试）
  - 先 ack DB 再动卡片：DB 失败保留按钮，用户再点/飞书重投会重试
  - 卡片更新带 3 次指数退避重试；仍失败写 card_update_pending.jsonl，重启时冲刷
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import jev_common as jc
from jev_common import NOOP, atomic_write_text, install_stop, log, new_trace, set_module

if jc.METRICS:
    from prometheus_client import Counter
    C_ACTION = Counter("jev_card_actions_total", "卡片按钮回调处理次数", ["action", "result"])
else:
    C_ACTION = NOOP

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).parent
IGNORE_FILE = HERE / "gate_ignored.json"
PENDING_FILE = HERE / "card_update_pending.jsonl"
READY_TIMEOUT_SEC = 15  # ready 标记等待上限，超时判定启动失败
CARD_RETRY = 3          # 卡片更新重试次数
DEDUP_MAX = 1000        # 事件去重窗口（飞书重投间隔在分钟级，内存集合足够）
METRICS_PORT = 9104     # P1-6 Prometheus /metrics 端点

# 优雅退出（P1-1：收敛到 jev_common）
_STOP = install_stop()


def _load_allowed_operators() -> set:
    """操作者白名单（P1-5）：LARK_ALLOWED_OPERATORS 显式配置优先（逗号分隔 open_id）；
    未配置时默认仅允许告警收件人本人（卡片只发给他，白名单也只认他）。
    空集 = 未获知任何允许者（仍会拒绝而非放行所有）。"""
    explicit = {s.strip() for s in os.environ.get("LARK_ALLOWED_OPERATORS", "").split(",") if s.strip()}
    if explicit:
        return explicit
    recipient = os.environ.get("LARK_ALERT_OPEN_ID", "").strip()
    return {recipient} if recipient else set()


ALLOWED_OPERATORS = _load_allowed_operators()


# ── 忽略集合持久化 ──────────────────────────────────────────
def load_ignored() -> set:
    if IGNORE_FILE.exists():
        try:
            return set(json.loads(IGNORE_FILE.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            log(f"ignored.jsonl 解析失败，忽略集清零（已忽略告警会重复弹卡）: {str(e)[:80]}",
                level="warn")  # C2批2
            return set()
    return set()


def save_ignored(ignored: set) -> None:
    atomic_write_text(IGNORE_FILE, json.dumps(list(ignored)[-2000:], ensure_ascii=False))


# ── 事件去重（有界内存集合，DB ack 成功后才记录） ───────────
class _Dedup:
    def __init__(self, cap: int = DEDUP_MAX) -> None:
        self._keys: deque = deque()
        self._set: set = set()
        self._cap = cap

    def has(self, key: str) -> bool:
        return key in self._set

    def add(self, key: str) -> None:
        if key in self._set:
            return
        self._keys.append(key)
        self._set.add(key)
        if len(self._keys) > self._cap:  # 淘汰最旧，防膨胀
            self._set.discard(self._keys.popleft())


_DEDUP = _Dedup()


# ── 数据库回写（P1-7：psycopg2 直连参数化，降级自动 docker exec） ──
def ack_alert_in_db(alert_id: str) -> bool:
    """把告警置 ACKED 并写 acked_at（仅当仍 OPEN）。

    系统状态枚举：OPEN / ACKED（人工确认）/ RESOLVED（系统自动消解）。
    「已处理」「忽略」都是人工已知悉、无需继续停留在 OPEN 队列，故都置 ACKED；
    二者差异由卡片颜色 + gate_ignored.json 区分。
    UPDATE 0 行 = 不存在/非 OPEN（幂等，视为成功）。
    """
    try:
        n = jc.db_execute(
            "UPDATE v2_alert SET status='ACKED', acked_at=now() "
            "WHERE alert_id=%s AND status='OPEN'",
            (alert_id,))
        log("数据库回写", alert_id=alert_id, rows=n)
        return True
    except Exception as e:  # noqa: BLE001
        log("数据库回写异常", alert_id=alert_id, err=str(e)[:150], level="error")
        return False


# ── 卡片更新（带重试） ──────────────────────────────────────
def _mutate_card(card_content: str, status_text: str, template: str,
                 operator_id: str) -> str | None:
    """把原始卡片 JSON 改为终态（header 变色 + 按钮区替换为状态文本）。"""
    try:
        card = json.loads(card_content)
    except Exception:  # noqa: BLE001
        return None  # 豁免（C2批2）：调用方 update_card 已落日志（card_content 解析失败）
    if "header" in card:
        card["header"]["template"] = template
    elements = card.get("body", {}).get("elements", [])
    if elements:
        elements[-1] = {
            "tag": "markdown",
            "content": f"**{status_text}** · by {operator_id}",
            "margin": "0px",
        }
    return json.dumps(card, ensure_ascii=False)


def _update_card_api(token: str, card_json: str) -> str:
    """单次调用卡片更新 API（lark-cli 统一信封判定收敛到 jev_common）。
    成功返回 ""，失败返回错误信息。"""
    data = json.dumps({"token": token, "card": json.loads(card_json)}, ensure_ascii=False)
    try:
        jc.lark_cli_json(
            ["api", "POST", "/open-apis/interactive/v1/card/update", "--as", "bot",
             "--data", data],
            timeout=15, kind="card_update")
        return ""
    except Exception as e:  # noqa: BLE001
        return str(e)[:200]  # 豁免（C2批2）：错误信息随返回值上抛，update_card 重试链已有日志


def update_card(token: str, card_content: str, status_text: str,
                template: str, operator_id: str) -> bool:
    """更新卡片：3 次指数退避重试。无法构造卡片时视为完成（不重试）。"""
    if not token or not card_content:
        log("token/card_content 为空，跳过卡片更新（仅记录操作）")
        return True
    card_json = _mutate_card(card_content, status_text, template, operator_id)
    if card_json is None:
        log("card_content 解析失败，跳过卡片更新")
        return True

    last_err = ""
    for i in range(CARD_RETRY):
        last_err = _update_card_api(token, card_json)
        if not last_err:
            log(f"卡片已更新为「{status_text}」")
            return True
        if i < CARD_RETRY - 1:
            wait = 2 * (i + 1)
            log(f"卡片更新失败（{i + 1}/{CARD_RETRY}），{wait}s 后重试: {last_err}",
                level="warn")
            time.sleep(wait)
    log(f"卡片更新失败（已重试 {CARD_RETRY} 次）: {last_err}", level="error")
    return False


# ── 卡片更新 outbox：失败补偿，重启时冲刷 ───────────────────
def append_pending(token: str, card_content: str, status_text: str,
                   template: str, operator_id: str) -> None:
    rec = {"ts": datetime.now().isoformat(timespec="seconds"), "token": token,
           "card_content": card_content, "status_text": status_text,
           "template": template, "operator_id": operator_id}
    try:
        with PENDING_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log(f"卡片更新转入 outbox 待冲刷（共待处理），alert 见上一条日志")
    except Exception as e:  # noqa: BLE001
        log(f"outbox 写入失败: {e}")


def flush_pending() -> None:
    """重启时冲刷 outbox：逐条重试卡片更新，成功的移出。"""
    if not PENDING_FILE.exists():
        return
    lines = PENDING_FILE.read_text(encoding="utf-8").splitlines()
    remaining = []
    n_ok = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue  # 豁免（C2批2）：outbox 损坏行直接丢弃，正常容错
        if update_card(rec["token"], rec["card_content"], rec["status_text"],
                       rec["template"], rec["operator_id"]):
            n_ok += 1
        else:
            remaining.append(line)
    atomic_write_text(PENDING_FILE, "\n".join(remaining) + ("\n" if remaining else ""))
    if n_ok or remaining:
        log(f"outbox 冲刷完成：成功 {n_ok}，剩余 {len(remaining)}")


# ── 事件处理 ────────────────────────────────────────────────
def handle_event(event: dict) -> None:
    """处理一个 card.action.trigger 事件（去重 + ack 优先 + 卡片补偿）。"""
    action_tag = event.get("action_tag", "")
    if action_tag != "button":
        return

    action_value_str = event.get("action_value", "")
    try:
        action_value = json.loads(action_value_str)
    except Exception:  # noqa: BLE001
        log(f"action_value 非 JSON，事件丢弃（操作者点击无响应风险）: "
            f"{str(action_value_str)[:80]}", level="warn")  # C2批2
        return

    action = action_value.get("action", "")
    alert_id = action_value.get("alert_id", "")
    source = action_value.get("source", "jev")  # jev=走 v2_alert 回写；backup=仅本地记录
    token = event.get("token", "")
    operator_id = event.get("operator_id", "")
    card_content = event.get("card_content", "")

    # alert_id 格式校验：卡片 value 理论可伪造，尽早拒绝异常值（DB 侧已有参数绑定兜底）
    if not isinstance(alert_id, str) or not alert_id or len(alert_id) > 64:
        log("alert_id 格式异常，拒绝", alert_id=repr(alert_id), level="warn")
        return

    # 操作者鉴权（P1-5）：非白名单用户点击只记录不处理，卡片按钮保留给授权人
    if ALLOWED_OPERATORS and operator_id not in ALLOWED_OPERATORS:
        log("未授权操作者点击，已拒绝", alert_id=alert_id,
            operator=operator_id, level="warn")
        C_ACTION.labels(action or "unknown", "denied").inc()
        return

    key = f"{token}:{action}:{alert_id}"
    if _DEDUP.has(key):
        log("重复回调，跳过", alert_id=alert_id, action=action)
        return

    if action == "ack":
        log("告警确认处理", alert_id=alert_id, operator=operator_id)
        status_text, template = "✓ 已处理", "green"
    elif action == "ignore":
        log("告警忽略", alert_id=alert_id, operator=operator_id)
        if source != "backup":  # 备份告警无 gate 实体，不写 gate_ignored.json
            ignored = load_ignored()
            ignored.add(alert_id)
            save_ignored(ignored)
        status_text, template = "已忽略", "grey"
    else:
        log("未知 action", alert_id=alert_id, action=action, level="warn")
        return

    # 一致性关键：先持久化后动卡片。失败保留按钮（不记去重），飞书重投/再点击会重试
    if source == "backup":
        # 备份失败告警无 v2_alert 实体：仅追加本地确认记录（低频，直接 append）
        try:
            rec = {"ts": datetime.now().isoformat(timespec="seconds"),
                   "alert_id": alert_id, "action": action, "operator": operator_id}
            with (HERE / "backup_card_acks.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            db_ok = True
            log("备份告警卡片操作已记录", alert_id=alert_id,
                action=action, operator=operator_id)
        except Exception as e:  # noqa: BLE001
            db_ok = False
            log("备份告警确认记录写入失败", alert_id=alert_id,
                err=str(e)[:150], level="error")
    else:
        db_ok = ack_alert_in_db(alert_id)
    if not db_ok:
        log("持久化失败，保留卡片按钮等待重试", alert_id=alert_id, level="warn")
        C_ACTION.labels(action, "db_fail").inc()
        return
    _DEDUP.add(key)
    C_ACTION.labels(action, "ok").inc()

    if not update_card(token, card_content, status_text, template, operator_id):
        append_pending(token, card_content, status_text, template, operator_id)


# ── 主循环 ──────────────────────────────────────────────────
def _looks_like_event(line: str) -> dict | None:
    """stderr 已合并进 stdout：只把含事件特征的 JSON 行当事件，其余当诊断行。"""
    try:
        obj = json.loads(line)
    except Exception:  # noqa: BLE001
        return None  # 豁免（C2批2）：stderr 合并流中非 JSON 诊断行过滤，预期控制流
    if isinstance(obj, dict) and ("action_tag" in obj or "token" in obj):
        return obj
    return None


def main() -> int:
    set_module("card")
    log("飞书卡片回调监听器启动")

    proc = subprocess.Popen(
        ["lark-cli", "event", "consume", "card.action.trigger", "--as", "bot"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # 合并单流：防 stderr 缓冲填满导致子进程阻塞死锁
        stdin=subprocess.PIPE,     # 保持 stdin 管道打开，防止 lark-cli EOF 退出
        text=True,
        encoding="utf-8",
        errors="replace",
        env=jc.lark_env(),
        bufsize=1,
    )

    # 后台线程读行入队；主线程带超时消费，避免任何永久阻塞（P0-1）
    q: "queue.Queue[str | None]" = queue.Queue()

    def _pump() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                q.put(line)
        finally:
            q.put(None)  # 流结束标记

    threading.Thread(target=_pump, daemon=True).start()

    # 等 ready 标记（有超时；超时或子进程先退出都立即失败退出，不悬挂）
    ready = False
    deadline = time.time() + READY_TIMEOUT_SEC
    while time.time() < deadline:
        try:
            line = q.get(timeout=max(0.1, deadline - time.time()))
        except queue.Empty:
            break  # 豁免（C2批2）：等待超时即正常出口（ready 标记超时在外层判定）
        if line is None:
            log(f"lark-cli 提前退出（code={proc.poll()}），启动失败")
            return 1
        if "[event] ready" in line:
            ready = True
            log("事件监听就绪")
            break
    if not ready:
        log(f"等待 ready 标记超时（{READY_TIMEOUT_SEC}s），终止子进程退出", level="error")
        try:
            proc.kill()
        except OSError:
            pass  # 豁免（C2批2）：kill 失败仅因进程已自行退出
        return 1

    jc.start_metrics(METRICS_PORT)  # P1-6：ready 后再开指标端点（启动失败不留孤儿端口）

    # 冲刷上次未完成的卡片更新（outbox 补偿）
    flush_pending()

    # 主循环：超时轮询兼顾 _STOP 信号与子进程存活监测
    while not _STOP["flag"]:
        try:
            line = q.get(timeout=1.0)
        except queue.Empty:
            if proc.poll() is not None:
                log(f"lark-cli 已退出（code={proc.returncode}），监听终止")
                break
            continue
        if line is None:
            log(f"事件流结束（lark-cli code={proc.returncode}），监听终止")
            break
        line = line.strip()
        if not line:
            continue
        event = _looks_like_event(line)
        if event is None:
            continue  # 诊断行（ready/心跳/诊断日志），忽略
        try:
            with new_trace("card"):  # P1-2：单事件处理链路串联（鉴权→DB→卡片）
                handle_event(event)
        except Exception as e:  # noqa: BLE001
            log(f"事件处理异常: {e}", level="error")

    # 优雅关闭：关闭 stdin 管道触发 lark-cli 退出
    try:
        if proc.stdin is not None:
            proc.stdin.close()
    except Exception:  # noqa: BLE001
        pass  # 豁免（C2批2）：优雅关闭阶段 stdin 关闭失败无碍（进程即将终止）
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        proc.kill()
    log("监听器已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
