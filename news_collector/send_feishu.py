# -*- coding: utf-8 -*-
"""飞书文本消息发送器（复用 backup_alert.py 的已验证模式）

用法: python send_feishu.py <消息文件路径> [interactive]
消息文件为 UTF-8 文本（避免命令行中文参数在各层 shell 的编码风险）；
第二参数 interactive 时文件内容即卡片 JSON。
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

LARK_ROOT = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark"
USER_ID = "ou_50a95455c7dffec1090ade400886d0d4"      # bot 应用域 open_id
USER_ID_LARK = "ou_5c9c4f3c015cbdb3475a44e49a9c1866"  # user 应用域 open_id


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
    """读 ~/.lark-cli/config.json 静态凭证（backup_alert.load_app_creds 同款）。"""
    cfg = os.path.join(os.path.expanduser("~"), ".lark-cli", "config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            apps = json.load(f).get("apps") or []
        if apps:
            return apps[0].get("appId"), apps[0].get("appSecret")
    except Exception:
        pass  # 豁免（C2批2）：凭证文件缺失→(None,None)，调用方以 SEND_FAIL no app creds 呈现
    return None, None


def _send_openapi(content, msg_type, receive_id=None, receive_id_type="open_id"):
    """OpenAPI 直连：K-STOCK-228 起内部委托 feishu_client（全仓唯一底座）。

    receive_id 缺省=USER_ID（OpenAPI 应用自身域 open_id，与凭证同应用）；
    K42-b 起可传群 chat_id（receive_id_type=chat_id）等多目标。"""
    if receive_id is None:
        receive_id = USER_ID
    from feishu_client import send_msg  # 同目录（K-STOCK-228 出口收敛）
    return send_msg(content, msg_type, receive_id, receive_id_type)


def send_content_to(receive_id, receive_id_type, content, msg_type):
    """多目标发送（群 chat_id 等）：仅 OpenAPI 直连，不走 lark-cli 回退
    （回退通道身份域混乱，群发场景明确要求 app 机器人在群内）。"""
    ok, why = _send_openapi(content, msg_type, receive_id, receive_id_type)
    print("SEND_OK" if ok else "SEND_FAIL " + why)
    return ok


def send_text(text):
    return send_content(json.dumps({"text": text}, ensure_ascii=False), "text")


def send_content(content, msg_type):
    """msg_type: text / interactive（content 为消息 JSON 字符串）。

    通道 1 OpenAPI 直连（静态凭证，无人值守安全）；失败回退通道 2
    lark-cli：身份自适应（strict-mode 放开时 bot+bot域ID 优先；
    strict user 模式回退 user+user域ID——两应用域 open_id 不通用，
    跨域发送报 99992361 open_id cross app）。"""
    ok, why = _send_openapi(content, msg_type)
    if ok:
        print("SEND_OK")
        return True
    exe = find_lark_cli()
    if not exe:
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
                ok = json.loads(r.stdout).get("ok") is True
            except Exception:
                ok = False
            if ok:
                print("SEND_OK")
                return True
            last = (r.stdout or r.stderr or "")[:200]
        except Exception as e:
            last = str(e)[:200]
    print("SEND_FAIL " + last)
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("SEND_FAIL no message file arg")
        sys.exit(2)
    from alert_io import sniff_read_text  # E1: BOM 嗅探（utf-16/utf-8-sig/utf-8）
    msg = sniff_read_text(sys.argv[1]).strip()  # 兼容旧 BOM 防御语义
    if not msg:
        print("SEND_FAIL empty message")
        sys.exit(2)
    if len(sys.argv) > 2 and sys.argv[2] == "interactive":
        # 文件内容即卡片 JSON
        sys.exit(0 if send_content(msg, "interactive") else 1)
    sys.exit(0 if send_text(msg) else 1)
