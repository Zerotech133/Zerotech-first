# Webhook direct probe: POST a test message, print full Feishu response
# (backup_alert.send_webhook only returns bool; this shows the error body
#  for diagnosis: invalid url / sign fail / keyword miss etc.)
import json
import sys
import time
import urllib.request as rq

sys.path.insert(0, r"C:\项目\老田的AI助手\jev-eval")
import backup_alert as BA
import logging

url, secret = BA.load_webhook()
if not url:
    print("WEBHOOK_TEST_NO_CFG")
    sys.exit(1)

body = {
    "msg_type": "text",
    "content": {
        "text": "[ALERT] webhook channel test "
        + time.strftime("%Y%m%d_%H%M%S")
        + " (no action needed)"
    },
}
if secret:
    import base64 as b64
    import hashlib as sha
    import hmac as hmc

    ts = str(int(time.time()))
    dig = hmc.new(ts + "\n" + secret, digestmod=sha.sha256).digest()
    body["timestamp"] = ts
    body["sign"] = b64.b64encode(dig).decode("ascii")

req = rq.Request(
    url,
    data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)
try:
    with rq.urlopen(req, timeout=15) as r:
        print("HTTP", r.status)
        print(r.read().decode("utf-8"))
except Exception as e:
    body_txt = ""
    if hasattr(e, "read"):
        try:
            body_txt = e.read().decode("utf-8")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "jev-eval/webhook_probe.py", exc)
    print("WEBHOOK_TEST_ERR", str(e)[:200])
    print(body_txt)
    sys.exit(1)
