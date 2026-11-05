# backup_alert.py - send Feishu message via OpenAPI, webhook, then lark-cli
# usage:
#   python backup_alert.py "<message text>"
#   python backup_alert.py <msgfile.txt>
# Channel priority (OPS-1001d F-followup: lark-cli 1.0.5 moved to externally
# injected credentials, unattended scheduled tasks get no bot token):
#   1. OpenAPI direct: tenant_access_token from ~/.lark-cli/config.json app
#      creds -> im/v1/messages (unattended-safe, buttons keep callbacks).
#      On success a group copy goes to LARK_ALERT_GROUP_ID chat (no buttons);
#      group failure never changes the exit code.
#   2. webhook robot if alert_webhook.txt exists (line1=url, line2=secret optional)
#   3. lark-cli im +messages-send --as bot (only where plugin injects creds)
# Webhook cards have no button callbacks, so ack/ignore buttons are dropped
# on the webhook channel (plain notification only).
# exit codes: 0 sent / 1 send failed / 2 exception
import json
import os
import subprocess
import sys

# 2026-10-16 换机修复：旧机硬编码 C:\Users\Administrator\... 新机不存在
# （K-STOCK-414 send_feishu 同款判例）——expanduser 跟随当前用户。
LARK_PLUGIN_ROOT = os.path.join(
    os.path.expanduser("~"), ".trae-cn", "plugins", "trae-remote-official", "lark"
)
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "news_collector"
    ),
)
import push_targets  # noqa: E402
import logging

DEFAULT_USER_ID = push_targets.target_id("个人") or ""
# group alert (family group, bot cli_aa3e4f1b58f85cdd is a member so it can
# post via OpenAPI chat_id). Override with LARK_ALERT_GROUP_ID, '' disables.
DEFAULT_GROUP_ID = push_targets.target_id("内部测试") or ""


def find_lark_cli():
    # K-STOCK-396 exe 解耦：仓库 bin/ 固定副本优先（2026-10-13 巡检判例：
    # 插件目录不在 PATH 注册表，升级/清除即断链），插件扫描降为回退。
    _bin = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "bin",
        "lark-cli.exe",
    )
    if os.path.isfile(_bin):
        return _bin
    if os.path.isdir(LARK_PLUGIN_ROOT):
        best = None
        for d in os.listdir(LARK_PLUGIN_ROOT):
            p = os.path.join(LARK_PLUGIN_ROOT, d, "bin", "lark-cli.exe")
            if not os.path.isfile(p):
                continue
            try:
                key = tuple(int(x) for x in d.split("."))
            except ValueError:
                key = (0,)
            if best is None or key > best[0]:
                best = (key, p)
        if best:
            return best[1]
    return "lark-cli"


def parse_fields(text):
    """Parse key=value lines; a leading '[header]' line (forwarder format) is skipped."""
    fields = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        fields[k.strip()] = v.strip()
    return fields


def build_backup_card(fields, with_buttons=True):
    """v1 卡片 for backup failure; buttons carry source=backup so the card
    handler skips the v2_alert DB write (backup alerts have no DB entity).
    with_buttons=False drops the button row (webhook channel: no callbacks).
    K-STOCK-370：schema 2.0（behaviors callback）在告警卡片助手 app 下点击
    零投递（平台侧缺口，事件日志检索无任何推送记录），v1 tag:action 按钮
    实测端到端可用（2026-10-14 V1TEST368 四条回调全到）——全量降级 v1。"""
    stamp = fields.get("stamp", "unknown")
    failed = fields.get("failed_count", "?")
    log_path = fields.get("backup_log", "(unknown)")
    alert_id = ("BACKUP_" + stamp)[:64]

    def _btn(content, btype, action):
        return {
            "tag": "button",
            "text": {"tag": "plain_text", "content": content},
            "type": btype,
            "value": {"source": "backup", "action": action, "alert_id": alert_id},
        }

    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {
                "tag": "plain_text",
                "content": "\U0001f6a8 AI\u5de5\u4f5c\u7ad9\u6bcf\u65e5\u5907\u4efd\u5931\u8d25",
            },
            "template": "red",
        },
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": "**\u5931\u8d25\u9879\u6570\uff1a**{0}\u3000\u3000**\u65f6\u95f4\u6233\uff1a**{1}".format(
                        failed, stamp
                    ),
                },
            },
            {
                "tag": "markdown",
                "content": "**\u6709\u5907\u4efd\u9879\u672a\u5b8c\u6210\uff0c\u8bf7\u68c0\u67e5\u5907\u4efd\u65e5\u5fd7\uff1a**\n\n`"
                + log_path
                + "`",
            },
        ]
        + (
            [
                {
                    "tag": "action",
                    "actions": [
                        _btn("\u2713 \u786e\u8ba4\u77e5\u6653", "primary", "ack"),
                        _btn("\u5ffd\u7565", "default", "ignore"),
                    ],
                }
            ]
            if with_buttons
            else []
        ),
    }


def _cred_candidates():
    """候选凭据路径（2026-11-05 与 news_collector/send_feishu.py 同修）。

    无 profile 环境（S4U 计划任务）里 USERPROFILE/HOMEDRIVE/HOMEPATH 可能全缺，
    os.path.expanduser("~") 原样返回 '~'，旧版拼出 "~/.lark-cli/config.json"
    必然打不开 → 备份告警静默失效。
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


def load_app_creds():
    """Read app credentials from the lark config.json (OPS-1001d followup:
    lark-cli 1.0.5 uses externally injected creds; scheduled tasks have none.
    OpenAPI direct calls with these static creds are unattended-safe)."""
    for cfg in _cred_candidates():
        try:
            with open(cfg, encoding="utf-8") as f:
                apps = json.load(f).get("apps") or []
            if apps:
                aid, sec = apps[0].get("appId"), apps[0].get("appSecret")
                # 20261016换机：appSecret 非 str（1.0.5+ 外置引用 dict）即不可用
                if aid and isinstance(sec, str):
                    return aid, sec
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "silent-except %s: %s (%s)", "jev-eval/backup_alert.py", exc, cfg
            )
    return None, None


def send_openapi(msg_type, content, user_id, receive_id_type="open_id"):
    """Send via Feishu OpenAPI. K-STOCK-228: delegate to
    news_collector/feishu_client (single API base for the whole repo).
    receive_id_type: 'open_id' (user) or 'chat_id' (group).
    Returns True on success (network errors degrade to False, not raise)."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.join(os.path.dirname(here), "news_collector"))
    from feishu_client import send_msg

    ok, _why = send_msg(content, msg_type, user_id, receive_id_type)
    return ok


def load_webhook():
    """Read alert_webhook.txt next to this script: line1=url, line2=secret(opt)."""
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alert_webhook.txt")
    if os.path.isfile(cfg):
        lines = [
            l.strip()
            for l in open(cfg, encoding="utf-8-sig").read().splitlines()
            if l.strip()
        ]
        if lines and lines[0].startswith("http"):
            return lines[0], (lines[1] if len(lines) > 1 else "")
    return None, None


def send_webhook(url, secret, msg_type, content):
    """POST to Feishu custom robot. Returns True on success.
    Sign algorithm: base64(hmac_sha256(key=timestamp+"\\n"+secret, msg=b''))."""
    import base64 as _b64
    import hashlib as _sha
    import hmac as _hmac
    import time as _time
    import urllib.request as _rq

    if msg_type == "interactive":
        body = {"msg_type": "interactive", "card": json.loads(content)}
    else:
        body = {"msg_type": "text", "content": {"text": json.loads(content)["text"]}}
    if secret:
        ts = str(int(_time.time()))
        string_to_sign = "{}\n{}".format(ts, secret)
        digest = _hmac.new(
            string_to_sign.encode("utf-8"), digestmod=_sha.sha256
        ).digest()
        body["timestamp"] = ts
        body["sign"] = _b64.b64encode(digest).decode("ascii")
    req = _rq.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with _rq.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp.get("code") == 0 or resp.get("StatusCode") == 0


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "AI workstation alert"
    if os.path.isfile(arg):
        with open(arg, encoding="utf-8-sig") as f:
            text = f.read().strip()
    else:
        text = arg
    if not text:
        text = "(empty alert)"
    fields = parse_fields(text)
    user_id = os.environ.get("LARK_ALERT_OPEN_ID", DEFAULT_USER_ID)
    if fields.get("type") == "ai_workstation_backup_failed":
        msg_type = "interactive"
    else:
        msg_type = "text"

    # channel 1: Feishu OpenAPI direct (unattended-safe, static app creds)
    if msg_type == "interactive":
        card_content = json.dumps(build_backup_card(fields), ensure_ascii=False)
    else:
        card_content = json.dumps({"text": text})
    try:
        if send_openapi(msg_type, card_content, user_id):
            print("ALERT_OK_OPENAPI")
            # group copy: same message to the group chat (no buttons on group
            # cards - family members cannot act on ack/retry buttons). Group
            # failure never changes the exit code: user copy already delivered.
            group_id = os.environ.get("LARK_ALERT_GROUP_ID", DEFAULT_GROUP_ID)
            if group_id:
                if msg_type == "interactive":
                    g_content = json.dumps(
                        build_backup_card(fields, with_buttons=False),
                        ensure_ascii=False,
                    )
                else:
                    g_content = card_content
                try:
                    print(
                        "ALERT_GROUP_"
                        + (
                            "OK"
                            if send_openapi(
                                msg_type, g_content, group_id, receive_id_type="chat_id"
                            )
                            else "FAIL"
                        )
                    )
                except Exception as ge:
                    print("ALERT_GROUP_ERROR " + str(ge)[:150])
            sys.exit(0)
        print("ALERT_OPENAPI_FAIL (fall back)")
    except Exception as e:
        print("ALERT_OPENAPI_ERROR " + str(e)[:200] + " (fall back)")

    # channel 2: webhook robot if configured (no button callbacks)
    url, secret = load_webhook()
    if url:
        try:
            if msg_type == "interactive":
                content = json.dumps(
                    build_backup_card(fields, with_buttons=False), ensure_ascii=False
                )
            else:
                content = json.dumps({"text": text})
            if send_webhook(url, secret, msg_type, content):
                print("ALERT_OK_WEBHOOK")
                sys.exit(0)
            print("ALERT_WEBHOOK_FAIL (fall back to lark-cli)")
        except Exception as e:
            print("ALERT_WEBHOOK_ERROR " + str(e)[:200] + " (fall back to lark-cli)")

    # channel 3: lark-cli bot (works only where plugin injects credentials)
    if msg_type == "interactive":
        content = json.dumps(build_backup_card(fields), ensure_ascii=False)
    else:
        content = json.dumps({"text": text})
    args = [
        find_lark_cli(),
        "im",
        "+messages-send",
        "--as",
        "bot",
        "--user-id",
        user_id,
        "--msg-type",
        msg_type,
        "--content",
        content,
    ]
    r = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    ok = False
    # K-STOCK-400（2026-10-16 移植 send_feishu）：整体 json.loads 遇新版
    # lark-cli pretty-print 多行 JSON/混入提示行必败 → 误判失败。
    # 从末个 "{" 行拼接解析，成功即 ok。
    try:
        lines = (r.stdout or "").strip().splitlines()
        for i in range(len(lines) - 1, -1, -1):
            seg = "\n".join(lines[i:]).strip()
            if not seg.startswith("{"):
                continue
            try:
                ok = json.loads(seg).get("ok") is True
                break
            except Exception:
                continue
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "jev-eval/backup_alert.py", exc)
    if ok:
        print("ALERT_OK")
        sys.exit(0)
    print("ALERT_FAIL " + ((r.stdout or r.stderr) or "")[:300].replace("\n", " "))
    sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ALERT_ERROR " + str(e)[:200])
        sys.exit(2)
