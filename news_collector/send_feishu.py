# -*- coding: utf-8 -*-
"""飞书文本消息发送器（复用 backup_alert.py 的已验证模式）

用法: python send_feishu.py <消息文件路径> [interactive] [--kind=X]
消息文件为 UTF-8 文本（避免命令行中文参数在各层 shell 的编码风险）；
第二参数 interactive 时文件内容即卡片 JSON。
--kind=X 可选，透传 feishu_client.send_msg（统一发送流水账目语义，
如 daily:alert / daily:report）；省略时行为与旧两参调用完全一致。
输出: SEND_OK / SEND_FAIL <原因>

通道顺序（OPS-1001d 同款判例）：
  1. OpenAPI 直连——读 ~/.lark-cli/config.json 静态 app 凭证
     （tenant_access_token + im/v1/messages），无人值守安全；
     lark-cli 1.0.5 起凭证由宿主外部注入，计划任务等独立进程
     无凭证可用（token_missing），OpenAPI 通道不受影响。
  2. lark-cli 回退——身份自适应 bot→user 双跳（保留原通路，
     Trae 宿主内调用时仍可用）。
"""

import json
import os
import subprocess
import sys
import time

# K-STOCK-414：旧机用户 Administrator 硬编码换机断链 → expanduser 动态推导
LARK_ROOT = os.path.join(
    os.path.expanduser("~"), ".trae-cn", "plugins", "trae-remote-official", "lark"
)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push_targets  # noqa: E402
import logging

USER_ID = push_targets.target_id("个人") or ""  # bot 应用域 open_id（apps[0] 视角）
# D4（N8-2）user 应用域 open_id 参数化：env FEISHU_USER_ID_LARK 优先，
# push_targets「个人(user域)」条目次之（等价机制——条目待老田确认后
# 落入 news_config.json，该文件非本工位独占路径，本期不动），缺省回退
# 历史值；下方 122 行 bot/user 双跳逻辑不动。
USER_ID_LARK = (
    os.environ.get("FEISHU_USER_ID_LARK")
    or next(
        (
            t.get("id")
            for t in push_targets.targets()
            if t.get("name") == "个人(user域)"
        ),
        None,
    )
    or "ou_5c9c4f3c015cbdb3475a44e49a9c1866"
)  # user 应用域 open_id（另一身份维度，不属收件人表）


def find_lark_cli():
    # K-STOCK-396 exe 解耦：仓库 bin/ 固定副本优先（不受 Trae 插件目录
    # 升级/清除影响，2026-10-13 巡检判例），插件目录扫描降为回退。
    _bin = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bin",
        "lark-cli.exe",
    )
    if os.path.isfile(_bin):
        return _bin
    if not os.path.isdir(LARK_ROOT):
        return None
    for d in sorted(os.listdir(LARK_ROOT), reverse=True):
        p = os.path.join(LARK_ROOT, d, "bin", "lark-cli.exe")
        if os.path.isfile(p):
            return p
    return None


def _cred_candidates():
    """按可靠度列出 lark 静态凭据的候选路径。

    2026-11-05 修（S4U 告警静默链）：无 profile 的环境（S4U 计划任务、
    run-as-service）里 USERPROFILE/HOMEDRIVE/HOMEPATH 可能全缺，此时
    os.path.expanduser("~") 会**原样返回 '~'**，旧版直接拼出
    "~/.lark-cli/config.json" 去打开 → 必然失败 → 返回 (None, None) →
    转向 lark-cli 回退（同样依赖 profile）→ 任务失败后告警彻底没人知道。
    实测：清空三个变量后 expanduser 返回 '~'，旧版本 (None, None)。

    候选顺序：环境变量显式指定 > 正常家目录 > C:\\Users\\<USERNAME> >
    扫 C:\\Users\\*\\.lark-cli（本机只有少数账户，开销可忽略）。
    """
    cands = []
    envp = os.environ.get("LARK_CLI_CONFIG")
    if envp:
        cands.append(envp)
    home = os.path.expanduser("~")
    if home and home not in ("~", ""):
        cands.append(os.path.join(home, ".lark-cli", "config.json"))
    user = os.environ.get("USERNAME")
    if user:
        cands.append(os.path.join("C:\\Users", user, ".lark-cli", "config.json"))
    try:
        import glob

        cands.extend(sorted(glob.glob(os.path.join("C:\\Users", "*", ".lark-cli", "config.json"))))
    except Exception:  # noqa: BLE001
        pass
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _load_app_creds():
    """读 lark 静态凭据 config.json（backup_alert.load_app_creds 同款）。

    20261016换机：appSecret 非 str（1.0.5+ 外置引用 dict）即不可用
    → (None, None)，通道 1 干净失败走 lark-cli 回退（feishu_client 同款）。
    2026-11-05：家目录解析不再只认 ~（原因见 _cred_candidates）。
    """
    for cfg in _cred_candidates():
        try:
            with open(cfg, encoding="utf-8") as f:
                apps = json.load(f).get("apps") or []
            if apps:
                aid, sec = apps[0].get("appId"), apps[0].get("appSecret")
                if aid and isinstance(sec, str):
                    return aid, sec
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s (%s)", "news_collector/send_feishu.py", exc, cfg
            )
    return None, None


def _send_openapi(
    content, msg_type, receive_id=None, receive_id_type="open_id", kind=None
):
    """OpenAPI 直连：K-STOCK-228 起内部委托 feishu_client（全仓唯一底座）。

    receive_id 缺省=USER_ID（OpenAPI 应用自身域 open_id，与凭证同应用）；
    K42-b 起可传群 chat_id（receive_id_type=chat_id）等多目标。
    P3.6-lite K-A：kind 非 None 时透传 feishu_client.send_msg（统一发送
    流水账目语义 daily:alert/daily:report）；None 时不传，旧调用零变化。"""
    if receive_id is None:
        receive_id = USER_ID
    from feishu_client import send_msg  # 同目录（K-STOCK-228 出口收敛）

    if kind is None:
        return send_msg(content, msg_type, receive_id, receive_id_type)
    return send_msg(content, msg_type, receive_id, receive_id_type, kind=kind)


def _run_lark_cli(args, timeout=30, attempts=2):
    """lark-cli 子进程执行：网络类失败自动重试一次（2026-10-25 收口）。

    背景：夜间网络抖动时 lark-cli 兜底报 network/transport 错误，单次失败
    即丢消息（10-25 04:07/05:06/07:10 三批晨间推送实锤）。重试一次显著
    降低瞬时丢送；成功路径语义不变。
    """
    last = None
    for i in range(attempts):
        try:
            r = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8", timeout=timeout
            )
            blob = ((r.stdout or "") + (r.stderr or "")).lower()
            transient = any(
                k in blob
                for k in (
                    "network",
                    "transport",
                    "timeout",
                    "timed out",
                    "connection",
                    "econnreset",
                )
            )
            if transient and i + 1 < attempts:
                time.sleep(2)
                last = r
                continue
            return r
        except Exception as e:  # noqa: BLE001
            last = e
            if i + 1 < attempts:
                time.sleep(2)
                continue
            raise
    return last


def _lark_cli_send_chat(content, msg_type, chat_id):
    """W3 清债：群目标兜底——lark-cli 以 bot 身份向 chat_id 发送。

    背景（2026-10-24）：OpenAPI 直发群目标失败时原来**没有任何兜底**，
    群副本直接丢失（FutQuantNotify 24h 丢 6 条）。lark-cli 已实测可用
    （`--as bot --chat-id oc_... ` 返回 ok=true）。→ (ok, detail)。
    """
    exe = find_lark_cli()
    if not exe:
        return False, "no lark-cli"
    try:
        r = _run_lark_cli(
            [
                exe,
                "im",
                "+messages-send",
                "--as",
                "bot",
                "--chat-id",
                chat_id,
                "--msg-type",
                msg_type,
                "--content",
                content,
            ],
            timeout=40,
        )
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]
    out = (r.stdout or "").strip()
    lines = out.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        seg = "\n".join(lines[i:]).strip()
        if not seg.startswith("{"):
            continue
        try:
            d = json.loads(seg)
        except ValueError:
            continue
        return d.get("ok") is True, str(d.get("message_id") or "")[:80]
    return False, (out or r.stderr or "")[:160]


def send_content_to(receive_id, receive_id_type, content, msg_type, kind=None):
    """多目标发送（群 chat_id 等）：OpenAPI 直连失败时**群兜底** lark-cli bot 身份。

    W3 清债（2026-10-24）：原实现"仅 OpenAPI、不走回退"——群副本在 400 时
    静默丢失。现改为：仅对 chat_id 目标、失败后尝试一次 lark-cli 群发
    （身份域仍为 bot app），失败如实 SEND_FAIL 留证。
    """
    ok, why = _send_openapi(content, msg_type, receive_id, receive_id_type, kind=kind)
    if ok:
        print("SEND_OK")
        return True
    if (receive_id_type or "") == "chat_id" and receive_id:
        fok, detail = _lark_cli_send_chat(content, msg_type, receive_id)
        if fok:
            _flow_record(True, receive_id)
            print("SEND_OK (lark-cli chat fallback)")
            return True
        _flow_record(False, receive_id)
        print("SEND_FAIL openapi:%s lark-cli:%s" % (str(why)[:120], str(detail)[:120]))
        return False
    print("SEND_FAIL " + str(why))
    _flow_record(False, receive_id)
    return False


def _flow_record(ok, target):
    """K-PUSH-001 底座埋点：lark-cli 回退通道结果落 push_flow 追溯账
    （OpenAPI 通道由 feishu_client.send_msg 埋点，本函数只记回退路）。
    fail-safe：任何异常吞掉，绝不影响发送主链路。"""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        import push_flow

        push_flow.record(push_flow.infer_kind(), "lark-cli", target, 1, ok)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "news_collector/send_feishu.py", exc)


def send_text(text, kind=None):
    return send_content(
        json.dumps({"text": text}, ensure_ascii=False), "text", kind=kind
    )


def send_content(content, msg_type, kind=None):
    """msg_type: text / interactive（content 为消息 JSON 字符串）。

    通道 1 OpenAPI 直连（静态凭证，无人值守安全）；失败回退通道 2
    lark-cli：身份自适应（strict-mode 放开时 bot+bot域ID 优先；
    strict user 模式回退 user+user域ID——两应用域 open_id 不通用，
    跨域发送报 99992361 open_id cross app）。
    kind 仅透传 OpenAPI 通道（feishu_client.send_msg 账目语义），
    lark-cli 回退通道无 kind 概念，不受影响。"""
    ok, why = _send_openapi(content, msg_type, kind=kind)
    if ok:
        print("SEND_OK")
        return True
    exe = find_lark_cli()
    if not exe:
        _flow_record(False, USER_ID)  # K-PUSH-001 埋点：openapi败且无回退
        print("SEND_FAIL openapi:%s no lark-cli" % why)
        return False
    last = why
    for ident, uid in ((["--as", "bot"], USER_ID), ([], USER_ID_LARK)):
        try:
            r = _run_lark_cli(
                [exe, "im", "+messages-send"]
                + ident
                + ["--user-id", uid, "--msg-type", msg_type, "--content", content],
                timeout=30,
            )
            try:
                # K-STOCK-400：末尾 JSON 优先——lark-cli stdout 若混入告警/
                # 版本提示/BOM，整体解析必败 → 误判失败 → 换 user 域重发
                # → 用户双收。20261016换机补充：新版 lark-cli 输出 pretty-
                # print 多行 JSON（末行仅 "}"），需从末个 "{" 行拼接解析。
                out = (r.stdout or "").strip()
                ok = False
                lines = out.splitlines()
                for i in range(len(lines) - 1, -1, -1):
                    seg = "\n".join(lines[i:]).strip()
                    if not seg.startswith("{"):
                        continue
                    try:
                        ok = json.loads(seg).get("ok") is True
                        break
                    except Exception:
                        continue
            except Exception:
                ok = False
            if ok:
                _flow_record(True, uid)  # K-PUSH-001 埋点：回退成功
                print("SEND_OK")
                return True
            last = (r.stdout or r.stderr or "")[:200]
        except Exception as e:
            last = str(e)[:200]
    _flow_record(False, USER_ID)  # K-PUSH-001 埋点：回退双身份均失败
    print("SEND_FAIL " + last)
    return False


if __name__ == "__main__":
    # P3.6-lite K-A：argv 追加可选 --kind=X（flag 形式，位置参已占用，
    # 且旧一参/两参调用零变化）；非 --kind= 前缀参数按原位置参解析。
    kind = None
    pos = []
    for a in sys.argv[1:]:
        if a.startswith("--kind="):
            kind = a.split("=", 1)[1] or None
        else:
            pos.append(a)
    if not pos:
        print("SEND_FAIL no message file arg")
        sys.exit(2)
    from alert_io import sniff_read_text  # E1: BOM 嗅探（utf-16/utf-8-sig/utf-8）

    msg = sniff_read_text(pos[0]).strip()  # 兼容旧 BOM 防御语义
    if not msg:
        print("SEND_FAIL empty message")
        sys.exit(2)
    if len(pos) > 1 and pos[1] == "interactive":
        # 文件内容即卡片 JSON
        sys.exit(0 if send_content(msg, "interactive", kind=kind) else 1)
    sys.exit(0 if send_text(msg, kind=kind) else 1)
