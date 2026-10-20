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
import urllib.request

# K-STOCK-414：旧机用户 Administrator 硬编码换机断链 → expanduser 动态推导
LARK_ROOT = os.path.join(
    os.path.expanduser("~"), ".trae-cn", "plugins",
    "trae-remote-official", "lark")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push_targets  # noqa: E402  K-STOCK-403：收件人单一事实源

USER_ID = push_targets.target_id("个人") or ""  # bot 应用域 open_id（apps[0] 视角）
# D4（N8-2）user 应用域 open_id 参数化：env FEISHU_USER_ID_LARK 优先，
# push_targets「个人(user域)」条目次之（等价机制——条目待老田确认后
# 落入 news_config.json，该文件非本工位独占路径，本期不动），缺省回退
# 历史值；下方 122 行 bot/user 双跳逻辑不动。
USER_ID_LARK = (os.environ.get("FEISHU_USER_ID_LARK")
                or next((t.get("id") for t in push_targets.targets()
                         if t.get("name") == "个人(user域)"), None)
                or "ou_5c9c4f3c015cbdb3475a44e49a9c1866")  # user 应用域 open_id（另一身份维度，不属收件人表）


def find_lark_cli():
    # K-STOCK-396 exe 解耦：仓库 bin/ 固定副本优先（不受 Trae 插件目录
    # 升级/清除影响，2026-10-13 巡检判例），插件目录扫描降为回退。
    _bin = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "bin", "lark-cli.exe")
    if os.path.isfile(_bin):
        return _bin
    if not os.path.isdir(LARK_ROOT):
        return None
    for d in sorted(os.listdir(LARK_ROOT), reverse=True):
        p = os.path.join(LARK_ROOT, d, "bin", "lark-cli.exe")
        if os.path.isfile(p):
            return p
    return None


def _load_app_creds():
    """读 ~/.lark-cli/config.json 静态凭证（backup_alert.load_app_creds 同款）。

    20261016换机：appSecret 非 str（1.0.5+ 外置引用 dict）即不可用
    → (None, None)，通道 1 干净失败走 lark-cli 回退（feishu_client 同款）。"""
    cfg = os.path.join(os.path.expanduser("~"), ".lark-cli", "config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            apps = json.load(f).get("apps") or []
        if apps:
            aid, sec = apps[0].get("appId"), apps[0].get("appSecret")
            if aid and isinstance(sec, str):
                return aid, sec
    except Exception:
        pass  # 豁免（C2批2）：凭证文件缺失→(None,None)，调用方以 SEND_FAIL no app creds 呈现
    return None, None


def _send_openapi(content, msg_type, receive_id=None, receive_id_type="open_id",
                  kind=None):
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


def send_content_to(receive_id, receive_id_type, content, msg_type, kind=None):
    """多目标发送（群 chat_id 等）：仅 OpenAPI 直连，不走 lark-cli 回退
    （回退通道身份域混乱，群发场景明确要求 app 机器人在群内）。
    kind 透传 _send_openapi → feishu_client.send_msg（账目语义）。"""
    ok, why = _send_openapi(content, msg_type, receive_id, receive_id_type,
                            kind=kind)
    print("SEND_OK" if ok else "SEND_FAIL " + why)
    return ok


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
    except Exception:
        pass


def send_text(text, kind=None):
    return send_content(json.dumps({"text": text}, ensure_ascii=False),
                        "text", kind=kind)


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
            r = subprocess.run(
                [exe, "im", "+messages-send"] + ident +
                ["--user-id", uid, "--msg-type", msg_type,
                 "--content", content],
                capture_output=True, text=True, encoding="utf-8", timeout=30)
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
