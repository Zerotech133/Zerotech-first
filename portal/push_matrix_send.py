# -*- coding: utf-8 -*-
"""客户推送发送器（K-ORG-197 轨道C，工单 W34 提前实装）。

职责：按市场窗口把既有产物渲染成统一 v3 卡片并投递——
  - load_subscribers()  读 portal/push_subscribers.json（坏 JSON fail-visible）
  - pick_groups()       按市场 + tier 选群（daily 窗只发 daily 群，critical 窗发全部）
  - build_payload()     模板取 push_matrix.json templates 节，标题 = 模板title · 市场中文名；
                        快照链接 env PUSH_PUBLIC_BASE 优先（缺省 http://127.0.0.1:8080/ +
                        snapshot 文件名）；国灶模板附对账摘要行（fail-soft，读不到就省略）
  - send_cards()        静默窗（critical 豁免）+ 日频控 + DRYRUN/live 双门禁 + 发送/落账

三重门（W34）：客户名单拍板 / 模板过会 / 灰度逐个确认——拍板前
push_subscribers.json 的 groups 保持空、dryrun_default=true，一切发送止步
DRYRUN 预览（原子写 portal/work/matrix_send_preview.json，零 HTTP）。
live 另需 env MATRIX_SEND_LIVE=1 且组 chat_id 非空；仅真实发送成功才追加
portal/work/matrix_send_log.json（DRYRUN 与 skip 一律不写 log）。

发送通道 = K-PUSH-012 推送中台 127.0.0.1:8097 /api/send（请求 schema 实读
push_admin_server.py：入口 L560-575 鉴权头 X-Push-Hub-Key，api_send L656-711
字段 content/msg_type/receive_id/receive_id_type/kind/critical，响应
{"ok": bool, "why", "via"}）；HUB 密钥供钥语义镜像 push_admin_server.py
L259-292（env PUSH_HUB_KEY 优先 → portal/work/push_secrets.json 的
push_hub_key 兜底，60s 缓存；该模块顶层依赖重，不宜直接 import）。
卡片一律经项目统一构造器 feishu_card_v2.build_v3（portal/feishu_card_v2.py
L154）生成，禁止自绘卡片 JSON。

用法：
  python -X utf8 portal/push_matrix_send.py --market cn --window morning [--live]
  退出码 0=完成（含 skip），1=错误。--now/--matrix/--subs 仅供测试与演练。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# 跨平台文件锁（2026-10-26 服务器发布阻塞修复）：Windows msvcrt / POSIX fcntl.flock
try:
    import msvcrt as _msvcrt
except ImportError:  # POSIX
    _msvcrt = None
    import fcntl as _fcntl


def _os_lock(fd, nonblock=False):
    if _msvcrt is not None:
        _msvcrt.locking(fd, _msvcrt.LK_NBLCK if nonblock else _msvcrt.LK_LOCK, 1)
    else:
        _fcntl.flock(fd, _fcntl.LOCK_EX | (_fcntl.LOCK_NB if nonblock else 0))


def _os_unlock(fd):
    if _msvcrt is not None:
        try:
            _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/push_matrix_send.py", exc)
    else:
        _fcntl.flock(fd, _fcntl.LOCK_UN)


sys.path.insert(0, str(Path(__file__).resolve().parent))
import push_matrix  # noqa: E402
import logging

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen


MKT_CN = {
    "cn": "A股",
    "hk": "港股",
    "us": "美股",
    "commodity": "商品",
    "crypto": "加密",
    "fx": "外汇",
}

REPO = push_matrix.REPO
SUBS_JSON = REPO / "portal" / "push_subscribers.json"
WORK_DIR = REPO / "portal" / "work"
PREVIEW_JSON = WORK_DIR / "matrix_send_preview.json"
LOG_JSON = WORK_DIR / "matrix_send_log.json"
# 2026-10-19 审计修复（P1-3）：现役流水改 JSONL 追加（并发原子），
# 旧 JSON 数组档冻结为只读历史，读侧合并兼容。
LOG_JSONL = WORK_DIR / "matrix_send_log.jsonl"
SECRETS_JSON = WORK_DIR / "push_secrets.json"
HUB_URL = "http://127.0.0.1:8097/api/send"
_KIND_PREFIX = "matrix"  # /api/send kind = matrix:<market>，流水归因用


# ---------- 密钥供钥（镜像 push_admin_server.py L259-292 语义） ----------

_SEC_CACHE = {"ts": 0.0, "data": {}}


def _hub_key() -> str:
    """HUB 发送密钥：env PUSH_HUB_KEY 优先 → portal/work/push_secrets.json
    的 push_hub_key 兜底（60s 缓存降读盘频次）。语义出处：
    push_admin_server.py L259-292（K-PUSH-012 供钥链，work/ 已 gitignore）。"""
    v = (os.environ.get("PUSH_HUB_KEY") or "").strip()
    if v:
        return v
    now = time.time()
    if now - _SEC_CACHE["ts"] > 60.0:
        try:
            _SEC_CACHE["data"] = json.loads(
                Path(SECRETS_JSON).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            _SEC_CACHE["data"] = {}
        _SEC_CACHE["ts"] = now
    return str((_SEC_CACHE["data"] or {}).get("push_hub_key") or "").strip()


# ---------- 订阅解析与选群 ----------


def load_subscribers(path=None) -> dict:
    """读订阅配置；缺失/坏 JSON fail-visible（OSError/ValueError 上抛）。"""
    p = Path(path) if path else SUBS_JSON
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "groups" not in data:
        raise ValueError("push_subscribers.json 缺少 groups 键: %s" % p)
    return data


def pick_groups(subs: dict, market: str, tier: str) -> list:
    """按市场 + tier 选群：markets 含该市场，且 tier 匹配——
    窗口 tier=daily 只发群 tier=daily；tier=critical 发全部订阅该市场的群。
    返回 [(group_id, group_cfg), ...]（保持配置声明序）。"""
    out = []
    for gid, g in (subs.get("groups") or {}).items():
        if market not in (g.get("markets") or []):
            continue
        if tier != "critical" and (g.get("tier") or "daily") != "daily":
            continue
        out.append((gid, g))
    return out


# ---------- 卡片渲染 ----------


def _build_v3():
    # 项目统一 v3 卡片构造器（portal/feishu_card_v2.py L154）；惰性 import
    # 保持本模块纯逻辑可测（feishu_card_v2 顶层挂 send_feishu 收件人链）。
    from feishu_card_v2 import build_v3

    return build_v3


def _guojia_digest(repo) -> str | None:
    """国灶对账摘要一行（overall.hit_rate / n_reconciled）；
    文件缺失/坏 JSON/字段缺一律省略（fail-soft，绝不抛错）。"""
    p = Path(repo) / "news_collector" / "data" / "guojia" / "summary.json"
    try:
        ov = json.loads(p.read_text(encoding="utf-8")).get("overall") or {}
        hr, n = ov.get("hit_rate"), ov.get("n_reconciled")
        if hr is None and n is None:
            return None
        return "国灶对账：命中率 %s · 对账 %s 条" % (hr, n)
    except Exception:
        return None


def build_payload(group: dict, market: str, window: dict, matrix: dict, repo) -> dict:
    """渲染单群 payload（纯函数，零触网）。

    模板 = matrix["templates"][window["template"]]（缺模板 fail-visible）；
    标题 = f"{模板title} · {MKT_CN[market]}"；snapshot_url = env
    PUSH_PUBLIC_BASE（缺省 http://127.0.0.1:8080/）+ snapshot 文件名；
    模板 snapshot 含 data_guojia 时附国灶对账摘要行（fail-soft）。
    返回 dict 含 card（feishu_card_v2.build_v3 产物）与 request
    （8097 /api/send 请求体，schema 出处 push_admin_server.py L656-711）。
    """
    tpl = matrix["templates"][window["template"]]
    title = "%s · %s" % (tpl["title"], MKT_CN[market])
    snap = tpl.get("snapshot") or ""
    base = (os.environ.get("PUSH_PUBLIC_BASE") or "http://127.0.0.1:8080").rstrip("/")
    snapshot_url = "%s/%s" % (base, os.path.basename(snap))
    digest = _guojia_digest(repo) if "data_guojia" in snap else None
    critical = window.get("tier") == "critical"
    rows = [
        ("市场", MKT_CN[market]),
        ("快照", "[%s](%s)" % (os.path.basename(snap) or "快照", snapshot_url)),
    ]
    # 域色规范（feishu_card_v2 V3_DOMAINS）：例行=绿，critical 预警=红
    card, audit = _build_v3()(
        "alert" if critical else "daily",
        title,
        "matrix_send",
        rows=rows,
        md=digest,
        tags="#matrix",
    )
    req = {
        "content": json.dumps(card, ensure_ascii=False),
        "msg_type": "interactive",
        "receive_id": group.get("chat_id") or "",
        "receive_id_type": group.get("chat_id_type") or "chat_id",
        "kind": "%s:%s" % (_KIND_PREFIX, market),
        "critical": critical,  # 中台侧 critical 豁免其静默窗（与本模块口径一致）
        # K-ORG-204：push_targets 群列表透传给 Agent 侧路由参考
        "route_targets": [
            {
                "name": group.get("name") or "",
                "id": group.get("chat_id") or "",
                "type": group.get("chat_id_type") or "chat_id",
            }
        ],
    }
    return {
        "group_name": group.get("name") or "",
        "market": market,
        "window": window.get("name") or "",
        "tier": window.get("tier") or "daily",
        "title": title,
        "snapshot_url": snapshot_url,
        "digest": digest,
        "audit": audit,
        "card": card,
        "request": req,
    }


# ---------- 落盘辅助（原子写，tmp + os.replace） ----------


def _atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = "%s.tmp" % path
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_log() -> list:
    """合并读流水：旧 JSON 数组档（只读历史）+ 现役 JSONL。

    2026-10-19 审计修复（P1-3）：原实现整文件读-改-写且无锁，双市场
    窗口进程并发发送时后写覆盖先写 → 日频控 sent_cnt 漏计、daily_cap
    超发。现役流水改 JSONL 单行追加（append 原子），旧档冻结只读。
    """
    rows: list = []
    try:
        data = json.loads(LOG_JSON.read_text(encoding="utf-8"))
        if isinstance(data, list):
            rows = [r for r in data if isinstance(r, dict)]
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/push_matrix_send.py", exc)
    try:
        with open(LOG_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue  # 半行（进程中断）跳过不阻塞频控
                if isinstance(r, dict):
                    rows.append(r)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/push_matrix_send.py", exc)
    return rows


def _append_log(entry):
    """JSONL 单行追加，跨进程互斥（2026-10-19 审计 P1-3）。

    Windows 上 "a"/"ab" 模式的 write 不保证跨句柄原子（实测两线程各
    50 笔并发追加丢 5 笔：多句柄各自定位 EOF 后写入互相覆盖）。用
    msvcrt.locking 锁 EOF 后 1 字节区做互斥——锁随句柄关闭自动释放，
    进程崩溃无残留；等待上限约 10s（LK_LOCK 语义），超时抛错不静默。
    """
    data = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
    # Windows 并发实测（2026-10-21 回归判例）：open("a+b") 本身偶发
    # PermissionError(Errno 13)——与另一句柄的锁定/打开时序撞 sharing
    # violation（同影子链 WO-TF-02 判例家族，此前该异常在线程内未捕获
    # 直接丢整段账）。有界重试吸收瞬态：每次 50ms、上限 20 次（约 1s）；
    # 重试耗尽仍失败则原样抛错——丢账显性化，不静默不吞。
    f = None
    for attempt in range(20):
        try:
            f = open(LOG_JSONL, "a+b")
            break
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05)
    with f:
        f.seek(0, 2)
        pos = f.tell()
        _os_lock(f.fileno())
        try:
            f.write(data)
        finally:
            # 解锁必须与加锁同区域：写后 EOF 已变，须回到锁定时刻的 pos
            f.seek(pos)
            _os_unlock(f.fileno())


# ---------- 发送通道 ----------


def _default_poster(url, payload, key) -> dict:
    """8097 /api/send 直发：POST JSON，鉴权头 X-Push-Hub-Key
    （schema 出处 push_admin_server.py L560-575）。返回响应 dict。"""
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST"
    )
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("X-Push-Hub-Key", key)
    with safe_urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------- 闸门 + 发送 ----------


def _first_tier(payloads: dict) -> str:
    for pl in payloads.values():
        return pl.get("tier") or "daily"
    return "daily"


def _cap_of(matrix: dict, market: str) -> int:
    try:
        return int((matrix["markets"][market] or {}).get("daily_cap") or 0)
    except KeyError:
        return 0


def send_cards(groups, payloads, live, now, poster=None, matrix=None) -> dict:
    """闸门 + 发送/预览（轨道C 主入口）。

    groups:   [(group_id, group_cfg), ...]（pick_groups 产物）
    payloads: {group_id: build_payload 产物}
    live:     调用方意图；实际生效还需 env MATRIX_SEND_LIVE=1
              （意图 live 但门禁未开 → 全部 skip 记因，零发送）
    matrix:   传入轨道A 矩阵则启用静默窗（is_quiet，critical 豁免）与
              日频控（按 日期+市场+群 计 market.daily_cap；log 内 group
              字段即为此口径服务）
    poster:   可注入发送函数 poster(url, payload, key) -> {"ok": bool}；
              缺省 _default_poster（8097 /api/send）。

    live=False（生效）→ DRYRUN：原子写 portal/work/matrix_send_preview.json
    （{generated_at, would_send, skipped}），零 HTTP，不写 log；
    live=True（生效）→ 逐群 poster，仅响应 ok=true 才追加
    portal/work/matrix_send_log.json（{date, market, window, group, ts}）。
    live 生效但缺 HUB 密钥 → fail-visible 抛 RuntimeError。
    """
    poster = poster or _default_poster
    want_live = bool(live)
    live_ok = want_live and os.environ.get("MATRIX_SEND_LIVE") == "1"
    today = now.strftime("%Y-%m-%d")
    quiet = False
    if matrix is not None:
        quiet = push_matrix.is_quiet(now, matrix, _first_tier(payloads))
    # 日频控基线：log 当日已成功发送条数（DRYRUN 也预演，skip 记因不发送）
    sent_cnt: dict = {}
    if matrix is not None:
        for e in _read_log():
            if e.get("date") == today:
                k = (e.get("market"), e.get("group"))
                sent_cnt[k] = sent_cnt.get(k, 0) + 1
    key = ""
    if live_ok:
        key = _hub_key()
        if not key:
            raise RuntimeError(
                "live 发送缺少 HUB 密钥（env PUSH_HUB_KEY / "
                "portal/work/push_secrets.json 的 push_hub_key）"
            )

    would, sent, skipped = [], [], []
    for gid, g in groups:
        pl = payloads.get(gid)
        if pl is None:
            skipped.append({"group": gid, "reason": "payload 缺失"})
            continue
        if quiet:
            skipped.append(
                {
                    "group": gid,
                    "reason": "静默窗内且非 critical 档（tier=%s）" % pl["tier"],
                }
            )
            continue
        cap = _cap_of(matrix, pl["market"]) if matrix is not None else 0
        n = sent_cnt.get((pl["market"], gid), 0)
        if cap and n >= cap:
            skipped.append(
                {
                    "group": gid,
                    "reason": "日频控：今日已发 %d/%d（daily_cap）" % (n, cap),
                }
            )
            continue
        if not live_ok:
            if want_live:  # 意图 live 但 env 门禁未开：拒绝发送，记因
                skipped.append(
                    {
                        "group": gid,
                        "reason": "live 门禁未开（需 env MATRIX_SEND_LIVE=1）",
                    }
                )
                continue
            would.append(dict({"group": gid}, **pl))
            continue
        if not (g.get("chat_id") or "").strip():
            skipped.append(
                {"group": gid, "reason": "chat_id 为空（live 需显式收件人）"}
            )
            continue
        resp = poster(HUB_URL, pl["request"], key)
        if isinstance(resp, dict) and resp.get("ok"):
            sent.append({"group": gid, "audit": pl["audit"]})
            _append_log(
                {
                    "date": today,
                    "market": pl["market"],
                    "window": pl["window"],
                    "group": gid,
                    "ts": now.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        else:
            why = (
                resp.get("why") or resp.get("error") if isinstance(resp, dict) else resp
            )
            skipped.append({"group": gid, "reason": "发送失败: %s" % why})

    preview = None
    if not live_ok:  # DRYRUN：原子写预览（含被门禁拒收的记因），零 HTTP
        _atomic_json(
            PREVIEW_JSON,
            {
                "generated_at": now.isoformat(timespec="seconds"),
                "would_send": would,
                "skipped": skipped,
            },
        )
        preview = str(PREVIEW_JSON)
    return {
        "live": live_ok,
        "sent": sent,
        "skipped": skipped,
        "would_send": would,
        "preview": preview,
    }


# ---------- 编排与 CLI ----------


def run(
    market,
    window_name,
    live=False,
    now=None,
    matrix_path=None,
    subs_path=None,
    poster=None,
) -> dict:
    """一次窗口投递编排：矩阵 → 窗口解析 → 订阅 → 渲染 → 闸门发送。"""
    now = now or datetime.now()
    matrix = push_matrix.load_matrix(matrix_path)
    mcfg = (matrix.get("markets") or {}).get(market)
    if mcfg is None:
        raise ValueError("矩阵无市场 %s" % market)
    window = next(
        (
            w
            for w in push_matrix.resolve_windows(mcfg, now.date())
            if w.get("name") == window_name
        ),
        None,
    )
    if window is None:
        raise ValueError("市场 %s 无窗口 %s" % (market, window_name))
    subs = load_subscribers(subs_path)
    if subs.get("dryrun_default", True):
        live = False  # 三重门之一：名单未拍板，订阅配置强制 DRYRUN
    picked = pick_groups(subs, market, window.get("tier") or "daily")
    # 国灶摘要的仓库根：--matrix 注入时取其上上级（<repo>/portal/x.json），否则 REPO
    repo = Path(matrix_path).parent.parent if matrix_path else REPO
    payloads = {
        gid: build_payload(g, market, window, matrix, repo) for gid, g in picked
    }
    res = send_cards(picked, payloads, live, now, poster=poster, matrix=matrix)
    res.update(
        {
            "market": market,
            "window": window_name,
            "tier": window.get("tier") or "daily",
            "groups_picked": [gid for gid, _ in picked],
        }
    )
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="市场推送矩阵发送器（默认 DRYRUN 预览，K-ORG-197 轨道C）"
    )
    ap.add_argument("--market", required=True, choices=sorted(MKT_CN))
    ap.add_argument("--window", required=True)
    ap.add_argument(
        "--live",
        action="store_true",
        help="真实发送（另需 env MATRIX_SEND_LIVE=1 且订阅未锁 DRYRUN）",
    )
    ap.add_argument("--now", default=None, help="ISO 时间，测试/演练用")
    ap.add_argument("--matrix", default=None, help="矩阵 JSON 路径，测试/演练用")
    ap.add_argument("--subs", default=None, help="订阅 JSON 路径，测试/演练用")
    a = ap.parse_args(argv)
    try:
        now = datetime.fromisoformat(a.now) if a.now else datetime.now()
        res = run(
            a.market,
            a.window,
            live=a.live,
            now=now,
            matrix_path=Path(a.matrix) if a.matrix else None,
            subs_path=Path(a.subs) if a.subs else None,
        )
    except Exception as e:  # noqa: BLE001
        print("FAIL %s" % e)
        return 1
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
