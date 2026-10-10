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

LARK_PLUGIN_ROOT = r"C:\Users\Administrator\.trae-cn\plugins\trae-remote-official\lark"
DEFAULT_USER_ID = "ou_50a95455c7dffec1090ade400886d0d4"
# group alert (family group, bot cli_aa3e4f1b58f85cdd is a member so it can
# post via OpenAPI chat_id). Override with LARK_ALERT_GROUP_ID, '' disables.
DEFAULT_GROUP_ID = "oc_6b97bee8b5340e071d0fc10cc6c8569f"


def find_lark_cli():
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
    """Card 2.0 for backup failure; buttons carry source=backup so the card
    handler skips the v2_alert DB write (backup alerts have no DB entity).
    with_buttons=False drops the button row (webhook channel: no callbacks)."""
    stamp = fields.get("stamp", "unknown")
    failed = fields.get("failed_count", "?")
    log_path = fields.get("backup_log", "(unknown)")
    alert_id = ("BACKUP_" + stamp)[:64]

    def _metric_col(label, value, size):
        return {
            "tag": "column", "width": "weighted", "weight": 1,
            "background_style": "red-50",
            "padding": "12px", "vertical_spacing": "2px",
            "elements": [
                {"tag": "markdown", "text_size": "notation",
                 "content": "<font color='grey'>" + label + "</font>"},
                {"tag": "markdown", "text_size": size, "content": value},
            ],
        }

    def _btn(content, btype, action):
        return {
            "tag": "column", "width": "weighted", "weight": 1,
            "elements": [
                {"tag": "button",
                 "text": {"tag": "plain_text", "content": content},
                 "type": btype, "width": "fill", "size": "medium",
                 "behaviors": [{"type": "callback",
                                "value": {"source": "backup", "action": action,
                                          "alert_id": alert_id}}]}],
        }

    return {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default"},
        "header": {
            "title": {"tag": "plain_text",
                      "content": "\U0001f6a8 AI\u5de5\u4f5c\u7ad9\u6bcf\u65e5\u5907\u4efd\u5931\u8d25"},
            "subtitle": {"tag": "plain_text",
                         "content": "\u5907\u4efd\u5931\u8d25\u544a\u8b66 \u00b7 " + stamp},
            "template": "red",
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 20px 12px",
            "vertical_spacing": "12px",
            "elements":
                [
                    {
                        "tag": "column_set",
                        "flex_mode": "none",
                        "horizontal_spacing": "8px",
                        "margin": "0px",
                        "columns": [
                            _metric_col("\u5931\u8d25\u9879\u6570", str(failed), "heading-2"),
                            _metric_col("\u65f6\u95f4\u6233", stamp, "heading-3"),
                        ],
                    },
                    {
                        "tag": "markdown",
                        "content": "**\u6709\u5907\u4efd\u9879\u672a\u5b8c\u6210\uff0c\u8bf7\u68c0\u67e5\u5907\u4efd\u65e5\u5fd7\uff1a**\n\n`"
                                   + log_path + "`",
                        "margin": "0px",
                    },
                ] + ([{
                    "tag": "column_set",
                    "flex_mode": "none",
                    "horizontal_spacing": "8px",
                    "columns": [
                        _btn("\u2713 \u786e\u8ba4\u77e5\u6653", "primary_filled", "ack"),
                        _btn("\u5ffd\u7565", "default", "ignore"),
                    ],
                }] if with_buttons else []),
        },
    }


def load_app_creds():
    """Read app credentials from ~/.lark-cli/config.json (OPS-1001d followup:
    lark-cli 1.0.5 uses externally injected creds; scheduled tasks have none.
    OpenAPI direct calls with these static creds are unattended-safe)."""
    cfg = os.path.join(os.path.expanduser('~'), '.lark-cli', 'config.json')
    try:
        with open(cfg, encoding='utf-8') as f:
            apps = json.load(f).get('apps') or []
        if apps:
            return apps[0].get('appId'), apps[0].get('appSecret')
    except Exception:
        pass  # 豁免（C2批2）：lark-cli 凭证文件缺失属常态（计划任务无注入），返回 None 走外部兜底
    return None, None


def send_openapi(msg_type, content, user_id, receive_id_type='open_id'):
    """Send via Feishu OpenAPI: tenant_access_token (from static app creds)
    then im/v1/messages. receive_id_type: 'open_id' (user) or 'chat_id' (group).
    Returns True on success."""
    import urllib.request as _rq
    app_id, app_secret = load_app_creds()
    if not app_id or not app_secret:
        return False
    body = json.dumps({'app_id': app_id, 'app_secret': app_secret}).encode('utf-8')
    req = _rq.Request('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                      data=body, headers={'Content-Type': 'application/json'})
    with _rq.urlopen(req, timeout=15) as r:
        tok = json.loads(r.read().decode('utf-8')).get('tenant_access_token')
    if not tok:
        return False
    body2 = json.dumps({'receive_id': user_id, 'msg_type': msg_type,
                        'content': content}).encode('utf-8')
    req2 = _rq.Request('https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=' + receive_id_type,
                       data=body2, headers={'Content-Type': 'application/json',
                                            'Authorization': 'Bearer ' + tok})
    with _rq.urlopen(req2, timeout=15) as r2:
        resp = json.loads(r2.read().decode('utf-8'))
    return resp.get('code') == 0


def load_webhook():
    """Read alert_webhook.txt next to this script: line1=url, line2=secret(opt)."""
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'alert_webhook.txt')
    if os.path.isfile(cfg):
        lines = [l.strip() for l in
                 open(cfg, encoding='utf-8-sig').read().splitlines() if l.strip()]
        if lines and lines[0].startswith('http'):
            return lines[0], (lines[1] if len(lines) > 1 else '')
    return None, None


def send_webhook(url, secret, msg_type, content):
    """POST to Feishu custom robot. Returns True on success.
    Sign algorithm: base64(hmac_sha256(key=timestamp+"\\n"+secret, msg=b''))."""
    import base64 as _b64
    import hashlib as _sha
    import hmac as _hmac
    import time as _time
    import urllib.request as _rq
    if msg_type == 'interactive':
        body = {'msg_type': 'interactive', 'card': json.loads(content)}
    else:
        body = {'msg_type': 'text', 'content': {'text': json.loads(content)['text']}}
    if secret:
        ts = str(int(_time.time()))
        string_to_sign = '{}\n{}'.format(ts, secret)
        digest = _hmac.new(string_to_sign.encode('utf-8'), digestmod=_sha.sha256).digest()
        body['timestamp'] = ts
        body['sign'] = _b64.b64encode(digest).decode('ascii')
    req = _rq.Request(url, data=json.dumps(body).encode('utf-8'),
                      headers={'Content-Type': 'application/json'})
    with _rq.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read().decode('utf-8'))
    return resp.get('code') == 0 or resp.get('StatusCode') == 0


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
                    g_content = json.dumps(build_backup_card(fields, with_buttons=False),
                                           ensure_ascii=False)
                else:
                    g_content = card_content
                try:
                    print("ALERT_GROUP_" + ("OK" if send_openapi(
                        msg_type, g_content, group_id, receive_id_type='chat_id')
                        else "FAIL"))
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
                content = json.dumps(build_backup_card(fields, with_buttons=False),
                                     ensure_ascii=False)
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
    args = [find_lark_cli(), "im", "+messages-send", "--as", "bot",
            "--user-id", user_id, "--msg-type", msg_type, "--content", content]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=60)
    ok = False
    try:
        ok = json.loads(r.stdout).get("ok") is True
    except Exception:
        pass  # 豁免（C2批2）：stdout 非 JSON → ok=False，错误内容随 ALERT_FAIL 打印，未掩盖
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
